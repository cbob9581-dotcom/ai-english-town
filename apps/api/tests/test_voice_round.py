import pytest

from app.event_store import EventStore
from app.voice_round import run_round

SILENCE = bytes(1600)  # 50ms @16k 静音


def make_asr(asr_final: str = "hello"):
    async def asr_client(samples):
        return {"finalText": asr_final, "segments": [], "language": "en", "confidence": -0.3}

    return asr_client


def make_tts(tts_calls: list):
    async def tts_client(text):
        tts_calls.append(text)
        return {"audioBase64": "AA==", "ms": 40, "sampleRate": 16000}

    return tts_client


def make_spy(events):
    """ws_send spy：记录每帧发送瞬间 DB 中已可见事件数，用于"先写库再发送"排序断言。"""
    sent: list = []

    async def ws_send(payload):
        sent.append({"payload": payload, "eventsVisible": len(events.list_after("s1", 0))})

    return sent, ws_send


@pytest.mark.parametrize("utterance_id", ["utt-1", "utt-2"])
async def test_round_writes_turn_before_audio(tmp_path, utterance_id) -> None:
    events = EventStore(tmp_path / "e.db")
    sent, ws_send = make_spy(events)
    tts_calls: list = []
    result = await run_round("s1", utterance_id, SILENCE, events, make_asr(), make_tts(tts_calls), ws_send)

    assert result["finalText"] == "hello"
    # 证据已入库
    assert any(e["event_type"] == "dialogue.turn" for e in events.list_after("s1", 0))
    # 音频确已发送（bytes 或 tts.audio.start）
    audio_sends = [
        f for f in sent
        if isinstance(f["payload"], bytes)
        or (isinstance(f["payload"], dict) and f["payload"].get("type") == "tts.audio.start")
    ]
    assert audio_sends
    # 先写库再发送：audio 发出的瞬间 dialogue.turn 已在库可见（eventsVisible >= 1）
    assert all(f["eventsVisible"] >= 1 for f in audio_sends)
    # TTS 确实被调用过
    assert len(tts_calls) == 1


async def test_empty_text_sends_nothing_and_writes_nothing(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    sent, ws_send = make_spy(events)
    tts_calls: list = []
    result = await run_round("s1", "utt-empty", SILENCE, events, make_asr(asr_final=""), make_tts(tts_calls), ws_send)

    assert result["replied"] is False
    assert result["finalText"] == ""
    assert tts_calls == []  # 空文本不触发 TTS
    assert sent == []  # 不发送任何 WS 消息
    assert events.list_after("s1", 0) == []  # 不写 dialogue.turn
