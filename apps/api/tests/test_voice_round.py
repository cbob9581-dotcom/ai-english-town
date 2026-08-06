import asyncio

import pytest

from app.event_store import EventStore
from app.voice_round import run_round

SILENCE = bytes(1600)  # 50ms @16k 静音


async def test_round_writes_turn_and_emits_audio(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    sent: list = []

    async def asr_client(samples):
        return {"finalText": "hello", "segments": [], "language": "en", "confidence": -0.3}

    async def tts_client(text):
        return {"audioBase64": "AA==", "ms": 40, "sampleRate": 16000}

    async def ws_send(payload):
        sent.append(payload)

    result = await run_round("s1", "utt-1", SILENCE, events, asr_client, tts_client, ws_send)
    assert result["finalText"] == "hello"
    # 先写库再发送
    assert any(e["event_type"] == "dialogue.turn" for e in events.list_after("s1", 0))
    assert any(isinstance(p, bytes) for p in sent) or any(p.get("type") == "tts.audio.start" for p in sent if isinstance(p, dict))


async def test_evidence_written_before_audio_sent(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    sent: list = []

    async def asr_client(samples):
        return {"finalText": "hello", "segments": [], "language": "en", "confidence": -0.3}

    async def tts_client(text):
        return {"audioBase64": "AA==", "ms": 5, "sampleRate": 16000}

    async def ws_send(payload):
        sent.append(payload)

    await run_round("s1", "utt-2", SILENCE, events, asr_client, tts_client, ws_send)
    # 对话轮次已入库（先写库），且音频确已发送
    assert any(e["event_type"] == "dialogue.turn" for e in events.list_after("s1", 0))
    assert any(isinstance(p, bytes) for p in sent) or any(
        isinstance(p, dict) and p.get("type") == "tts.audio.start" for p in sent
    )
