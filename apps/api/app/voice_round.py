"""语音回合（流式）：PCM → ASR final → NpcActor 流式回复 → 逐句 TTS → 音频回传。
回合跑在独立 asyncio 任务里（ws.py 用 create_task 调度），可被取消。
asr_client/tts_client/ws_send/actor/state 可注入（测试用 mock/可控状态）。"""
from __future__ import annotations

import asyncio
import base64
import json
import uuid
import wave
from pathlib import Path
from typing import Awaitable, Callable

from app.llm.npc_actor import NpcActor, build_history

ASRClient = Callable[[bytes], Awaitable[dict]]
TTSClient = Callable[[str], Awaitable[dict]]
WsSend = Callable[[object], Awaitable[None]]


def _write_consent_audio(session_id: str, utterance_id: str, pcm: bytes,
                         settings, meta: dict) -> None:
    """授权时写 WAV + 元数据；失败仅日志。调用方仅在回合完全成功（replied=True）时调用。"""
    if not getattr(settings, "pronunciation_audio_consent", False):
        return
    try:
        root = Path(settings.tutor_cache_dir).parent / "pronunciation-audio"
        dirpath = root / session_id
        dirpath.mkdir(parents=True, exist_ok=True)
        with wave.open(str(dirpath / f"{utterance_id}.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(pcm)
        (dirpath / f"{utterance_id}.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001 —— 落盘失败不影响回合/评分
        pass


async def run_round(
    session_id: str,
    utterance_id: str,
    audio_pcm16: bytes,
    events,
    asr_client: ASRClient,
    tts_client: TTSClient,
    ws_send: WsSend,
    actor: NpcActor,
    state,  # SessionState（Task 7 定义）
    *,
    budget_exceeded: bool = False,
    world_summary: dict | None = None,
) -> dict:
    turn_id = state.new_turn_id()
    state.active_turn_id = turn_id
    # per-turn：interrupted 事件的 playedMs 只反映本回合已播放 ms，而非会话累计
    state.played_ms = 0
    asr_result = await asr_client(audio_pcm16)
    conf = float(asr_result.get("confidence", -0.5))
    final_text = asr_result["finalText"].strip()
    if not final_text:
        state.active_turn_id = None
        return {"finalText": "", "turnId": turn_id, "replied": False,
                "npcText": "", "confidence": conf, "words": asr_result.get("words")}

    committed = False
    audio_bytes = 0
    chunk_index = 0
    accumulated = ""
    try:
        async for msg in actor.stream_reply(
                session_id=session_id, generation_id=state.generation_id, turn_id=turn_id,
                utterance_id=utterance_id, user_text=final_text,
                recent_turns=build_history(events, session_id),
                budget_exceeded=budget_exceeded, world_summary=world_summary):
            mtype = msg["type"]
            if mtype == "npc.speech.delta":
                accumulated += msg["text"] + " "
                await ws_send(msg)
                tts = await tts_client(msg["text"])
                audio = base64.b64decode(tts["audioBase64"])
                audio_bytes += len(audio)
                state.played_ms += int(tts.get("ms", 0))
                chunk_index += 1
                state.is_playing = True
                await ws_send({"type": "tts.audio.start", "generationId": state.generation_id,
                               "turnId": turn_id, "chunkId": f"s{chunk_index}",
                               "sampleRate": tts["sampleRate"]})
                await ws_send(audio)
                await ws_send({"type": "tts.audio.end", "generationId": state.generation_id,
                               "turnId": turn_id, "chunkId": f"s{chunk_index}"})
                state.is_playing = False
            elif mtype == "npc.speech.commit":
                # 先写库（含全文 npcText），再对外确认
                events.append(session_id, "dialogue.turn", {
                    "turnId": turn_id, "utteranceId": utterance_id,
                    "userText": final_text, "npcText": msg["text"], "audioBytes": audio_bytes,
                })
                committed = True
                await ws_send(msg)
            elif mtype == "npc.turn.metadata":
                await ws_send(msg)
        state.active_turn_id = None
        _write_consent_audio(session_id, utterance_id, audio_pcm16, state.settings,
                             {"turnId": turn_id, "finalText": final_text, "sampleRate": 16000})
        return {"finalText": final_text, "turnId": turn_id, "replied": True,
                "npcText": accumulated.strip(), "confidence": conf,
                "words": asr_result.get("words")}
    except asyncio.CancelledError:
        # 未 commit 就被打断 → 补写部分轮次（用户输入 + 已产生的 npcText），保持证据不丢
        if not committed:
            events.append(session_id, "dialogue.turn", {
                "turnId": turn_id, "utteranceId": utterance_id,
                "userText": final_text, "npcText": accumulated.strip(), "audioBytes": audio_bytes,
            })
        raise
