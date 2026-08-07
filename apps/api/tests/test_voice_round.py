import pytest

from app.event_store import EventStore
from app.llm.mock import MockAdapter
from app.llm.npc_actor import NpcActor
from app.settings import Settings
from app.voice_round import run_round
from app.ws import SessionState
from tests.fakes import FakeLlmLog

SILENCE = bytes(1600)  # 50ms @16k 静音

ALLOWED = {"word_loaf_n_1": "loaf"}


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
    sent: list = []

    async def ws_send(payload):
        sent.append({"payload": payload, "eventsVisible": len(events.list_after("s1", 0))})

    return sent, ws_send


def make_actor(scenario="ok", override=None):
    actor = NpcActor(MockAdapter(scenario, stream_text_override=override),
                     Settings(llm_total_timeout_npc_s=1.0), FakeLlmLog(),
                     ALLOWED, lambda u: "Sorry, I didn't catch that.")
    return actor


async def test_round_streams_delta_then_commit_then_metadata(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    sent, ws_send = make_spy(events)
    tts_calls: list = []
    state = SessionState(Settings())
    actor = make_actor(override="Hello! A loaf is three dollars. Can I help you?")
    result = await run_round("s1", "u1", SILENCE, events, make_asr(), make_tts(tts_calls),
                             ws_send, actor, state)

    assert result["finalText"] == "hello" and result["replied"] is True
    types = [f["payload"]["type"] for f in sent if isinstance(f["payload"], dict)]
    assert types.count("npc.speech.delta") == 3
    assert "npc.speech.commit" in types and "npc.turn.metadata" in types
    meta = [f["payload"] for f in sent if isinstance(f["payload"], dict) and f["payload"]["type"] == "npc.turn.metadata"][0]
    assert meta["candidateWordIds"] == ["word_loaf_n_1"]
    # 先写库再发 commit
    commit_send = [f for f in sent if isinstance(f["payload"], dict) and f["payload"]["type"] == "npc.speech.commit"][0]
    assert commit_send["eventsVisible"] >= 1
    # 逐句 TTS
    assert tts_calls == ["Hello!", "A loaf is three dollars.", "Can I help you?"]
    # 每句都有 audio.start/end
    assert types.count("tts.audio.start") == 3 and types.count("tts.audio.end") == 3


async def test_empty_text_sends_nothing_and_writes_nothing(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    sent, ws_send = make_spy(events)
    tts_calls: list = []
    state = SessionState(Settings())
    result = await run_round("s1", "u1", SILENCE, events, make_asr(asr_final=""), make_tts(tts_calls),
                             ws_send, make_actor(), state)
    assert result["replied"] is False
    assert tts_calls == [] and sent == []
    assert events.list_after("s1", 0) == []


async def test_budget_exceeded_forces_scripted(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    sent, ws_send = make_spy(events)
    state = SessionState(Settings())
    result = await run_round("s1", "u1", SILENCE, events, make_asr(), make_tts([]),
                             ws_send, make_actor(), state, budget_exceeded=True)
    assert result["replied"] is True
    commit = [f["payload"] for f in sent if isinstance(f["payload"], dict) and f["payload"]["type"] == "npc.speech.commit"][0]
    assert commit["text"] == "Sorry, I didn't catch that."
