"""语音回合：PCM → ASR final → 本地回复 → TTS → 音频回传。
asr_client/tts_client/ws_send 可注入（测试用 mock；生产接 worker）。"""
from __future__ import annotations

import base64
import uuid
from typing import Awaitable, Callable

from app.scripted_npc import reply as scripted_reply

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
) -> dict:
    turn_id = f"turn_{uuid.uuid4().hex[:8]}"
    asr_result = await asr_client(audio_pcm16)
    final_text = asr_result["finalText"]
    if not final_text.strip():
        return {"finalText": "", "turnId": turn_id, "replied": False}

    npc = scripted_reply(final_text)
    await ws_send({"type": "npc.speech.commit", "turnId": turn_id, "text": npc["speech"]})

    tts = await tts_client(npc["speech"])
    audio = base64.b64decode(tts["audioBase64"])
    # 先写库（学习证据/对话历史），再发音频
    events.append(session_id, "dialogue.turn", {
        "turnId": turn_id, "utteranceId": utterance_id,
        "userText": final_text, "npcText": npc["speech"], "audioBytes": len(audio),
    })

    await ws_send({"type": "tts.audio.start", "turnId": turn_id, "chunkId": "c1", "sampleRate": tts["sampleRate"]})
    # 阶段 1 单块发送（"已播放才写历史"由先写库保证；多块分块播放留给阶段 2）
    await ws_send(audio)
    await ws_send({"type": "tts.audio.end", "turnId": turn_id, "chunkId": "c1"})
    return {"finalText": final_text, "turnId": turn_id, "replied": True}
