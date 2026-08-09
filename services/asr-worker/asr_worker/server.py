from __future__ import annotations

import json
import os

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from asr_worker.pronunciation import PhonemeAligner, expected_phonemes_from_ipa, score_gop
from asr_worker.streaming import RollingTranscriber, UtteranceState
from asr_worker.whisper_engine import WhisperEngine, word_timestamps_active

app = FastAPI(title="asr-worker")
ENGINE: WhisperEngine | None = None
PRONUNCIATION: "PhonemeAligner | None" = None


@app.on_event("startup")
def _load() -> None:
    global ENGINE
    ENGINE = WhisperEngine.load("auto", model=os.environ.get("ASR_MODEL"))
    ENGINE.word_timestamps_enabled = word_timestamps_active(
        ENGINE,
        os.environ.get("ENABLE_WORD_TIMESTAMPS", "").lower() == "true",
        os.environ.get("WORD_TIMESTAMP_MIN_MODEL", "whisper-large-v3"))


@app.websocket("/ws/asr")
async def ws_asr(ws: WebSocket) -> None:
    await ws.accept()
    utterance: UtteranceState | None = None
    samples: list[float] = []
    rt = RollingTranscriber(ENGINE.transcribe, word_timestamps=getattr(ENGINE, "word_timestamps_enabled", False))  # type: ignore[arg-type]
    try:
        while True:
            msg = await ws.receive()
            if msg.get("text"):
                ctrl = json.loads(msg["text"])
                if ctrl["type"] == "audio.start":
                    utterance = UtteranceState(ctrl["utteranceId"])
                    samples = []
                elif ctrl["type"] == "audio.end":
                    if utterance is not None and samples:
                        await ws.send_json(rt.finalize(utterance, np.array(samples, dtype=np.float32), 16000))
                    utterance = None
            else:
                raw = msg.get("bytes")
                if raw and utterance is not None:
                    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    samples.extend(arr.tolist())
                    if len(samples) >= 16000 * 6:
                        events = rt.feed(utterance, np.array(samples, dtype=np.float32), 16000, now_ms=len(samples) / 16.0)
                        for ev in events:
                            await ws.send_json(ev)
    except WebSocketDisconnect:
        return


class TranscribeRequest(BaseModel):
    audio_base64: str


@app.post("/transcribe")
async def transcribe(req: "TranscribeRequest") -> dict:
    """阶段 1 兜底端点：一次性提交 base64 PCM16 → 直接 final。api 层走这个而非流式 WS。"""
    from asr_worker.streaming import UtteranceState
    import base64
    import numpy as np

    samples = np.frombuffer(base64.b64decode(req.audio_base64), dtype=np.int16).astype(np.float32) / 32768.0
    return rt_finalize(samples)


# 模块级共享状态：让 /transcribe 与 WS 复用同一 transcriber 逻辑
def rt_finalize(samples) -> dict:
    u = UtteranceState("one-shot")
    return RollingTranscriber(ENGINE.transcribe, word_timestamps=getattr(ENGINE, "word_timestamps_enabled", False)).finalize(u, samples, 16000)  # type: ignore[union-attr]


class PronounceRequest(BaseModel):
    wav_b64: str
    ipa: str
    device: str = "cpu"


@app.post("/pronounce")
async def pronounce(req: "PronounceRequest") -> dict:
    """GOP 评分：16k mono PCM16 词窗段 + 词典 IPA → 音素级 GOP。
    模型缺失/缺映射/对齐异常 → degraded 响应（api 侧据此回退词级代理，不产生证据）。"""
    import base64
    import logging
    global PRONUNCIATION
    if PRONUNCIATION is None:
        PRONUNCIATION = PhonemeAligner(device=req.device)
    if not PRONUNCIATION.available:
        return {"gop": None, "phoneme_scores": {}, "degraded": True}
    phones = expected_phonemes_from_ipa(req.ipa)
    if phones is None:
        return {"gop": None, "phoneme_scores": {}, "degraded": True}
    try:
        result = score_gop(base64.b64decode(req.wav_b64), phones, PRONUNCIATION, device=req.device)
    except Exception as e:  # noqa: BLE001 —— 评分失败降级，不污染证据流
        logging.getLogger("asr_worker").warning("pronounce failed: %s", e)
        return {"gop": None, "phoneme_scores": {}, "degraded": True}
    if result is None:
        return {"gop": None, "phoneme_scores": {}, "degraded": True}
    return result
