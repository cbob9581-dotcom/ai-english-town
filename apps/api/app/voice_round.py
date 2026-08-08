"""语音回合（流式）：PCM → ASR final → NpcActor 流式回复 → 逐句 TTS → 音频回传。
回合跑在独立 asyncio 任务里（ws.py 用 create_task 调度），可被取消。
asr_client/tts_client/ws_send/actor/state 可注入（测试用 mock/可控状态）。"""
from __future__ import annotations

import asyncio
import base64
import uuid
from typing import Awaitable, Callable

from app.llm.npc_actor import NpcActor, build_history

ASRClient = Callable[[bytes], Awaitable[dict]]
TTSClient = Callable[[str], Awaitable[dict]]
WsSend = Callable[[object], Awaitable[None]]


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
                "npcText": "", "confidence": conf}

    committed = False
    audio_bytes = 0
    chunk_index = 0
    accumulated = ""
    try:
        async for msg in actor.stream_reply(
                session_id=session_id, generation_id=state.generation_id, turn_id=turn_id,
                utterance_id=utterance_id, user_text=final_text,
                recent_turns=build_history(events, session_id),
                budget_exceeded=budget_exceeded):
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
        return {"finalText": final_text, "turnId": turn_id, "replied": True,
                "npcText": accumulated.strip(), "confidence": conf}
    except asyncio.CancelledError:
        # 未 commit 就被打断 → 补写部分轮次（用户输入 + 已产生的 npcText），保持证据不丢
        if not committed:
            events.append(session_id, "dialogue.turn", {
                "turnId": turn_id, "utteranceId": utterance_id,
                "userText": final_text, "npcText": accumulated.strip(), "audioBytes": audio_bytes,
            })
        raise
