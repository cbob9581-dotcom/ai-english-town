"""浏览器实时连接：音频二进制 + 控制 JSON。阶段 1 的浏览器端 VAD = 前端 RMS 门限；
服务端 VAD 状态机在此驱动（阶段 2 换真实 Silero 帧标签）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.voice_round import run_round
from app.workers import asr_client, tts_client

router = APIRouter()

ASR_URL = "http://127.0.0.1:8001/transcribe"
TTS_BASE = "http://127.0.0.1:8002"


@router.websocket("/ws/sessions/{session_id}")
async def ws_session(ws: WebSocket) -> None:
    await ws.accept()
    events = ws.app.state.events
    session_id = ws.path_params["session_id"]
    utterance_id: str | None = None
    frames: list[bytes] = []

    async def send(payload: object) -> None:
        if isinstance(payload, bytes):
            await ws.send_bytes(payload)
        else:
            await ws.send_json(payload)

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return  # 原始 receive() 不抛 WebSocketDisconnect，这里显式退出，避免下一次 receive 抛 RuntimeError
            if msg.get("text"):
                ctrl = json.loads(msg["text"])
                if ctrl["type"] == "audio.start":
                    utterance_id = ctrl["utteranceId"]
                    frames = []
                elif ctrl["type"] == "audio.end":
                    if utterance_id is not None and frames:
                        await run_round(
                            session_id, utterance_id, b"".join(frames), events,
                            lambda audio: asr_client(audio, ASR_URL),
                            lambda text: tts_client(text, TTS_BASE),
                            send,
                        )
                    utterance_id = None
                elif ctrl["type"] == "playback.interrupted":
                    events.append(session_id, "playback.interrupted", {"utteranceId": utterance_id})
            else:
                raw = msg.get("bytes")
                if raw and utterance_id is not None:
                    frames.append(raw)
    except WebSocketDisconnect:
        return
