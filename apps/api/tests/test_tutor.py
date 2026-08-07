import base64
from pathlib import Path

import pytest

from app.event_store import EventStore
from app.llm.mock import MockAdapter
from app.llm.tutor import CompanionTutor
from app.llm.tutor_cache import TutorCache
from app.settings import Settings
from tests.fakes import FakeLlmLog

WAV_BYTES = b"RIFF____WAVEfmt "


def make_tts(tts_calls: list):
    async def tts_client(text: str):
        tts_calls.append(text)
        return {"audioBase64": base64.b64encode(WAV_BYTES).decode(), "ms": 30, "sampleRate": 24000}

    return tts_client


def make_tutor(tmp_path: Path, scenario: str = "ok", tts_calls: list | None = None):
    events = EventStore(tmp_path / "e.db")
    cache = TutorCache(events.connection, tmp_path / "audio")
    log = FakeLlmLog()
    calls = tts_calls if tts_calls is not None else []
    tutor = CompanionTutor(MockAdapter(scenario), Settings(llm_total_timeout_tutor_s=1.0),
                           log, cache, make_tts(calls))
    return tutor, log, cache


async def test_cache_miss_calls_llm_and_tts(tmp_path) -> None:
    tutor, log, cache = make_tutor(tmp_path)
    res = await tutor.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.word == "loaf"
    assert "loaf" in res.scaffold
    assert res.from_cache is False and res.degraded is False
    assert res.audio_base64 is not None and res.sample_rate == 24000
    assert log.rows[-1]["role"] == "companion_tutor" and log.rows[-1]["ok"] is True
    assert cache.get("word_loaf_n_1") is not None
    # 音频已落盘
    assert (tmp_path / "audio" / "word_loaf_n_1.wav").read_bytes() == WAV_BYTES


async def test_cache_hit_zero_llm_tts(tmp_path) -> None:
    tutor, log, cache = make_tutor(tmp_path)
    calls: list = []
    tutor2, _, _ = make_tutor(tmp_path, tts_calls=calls)
    await tutor2.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    res = await tutor2.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.from_cache is True
    assert res.audio_base64 is not None
    assert calls == ["loaf"]        # 第二次命中：0 TTS
    assert log.rows == []           # 命中缓存：0 LLM 调用（不写 llm_calls）


async def test_invalid_json_degrades_to_word_only(tmp_path) -> None:
    tutor, log, cache = make_tutor(tmp_path, "invalid_json")
    res = await tutor.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.word == "loaf" and res.scaffold == ""
    assert res.degraded is True
    assert res.audio_base64 is not None   # 仍读单词
    assert log.rows[-1]["fallback_reason"] == "invalid_json" and log.rows[-1]["ok"] is False


async def test_bad_word_degrades(tmp_path) -> None:
    tutor, log, cache = make_tutor(tmp_path, "bad_word_id")
    res = await tutor.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.word == "loaf" and res.scaffold == ""
    assert log.rows[-1]["fallback_reason"] == "schema_reject"


async def test_missing_word_degrades(tmp_path) -> None:
    tutor, log, cache = make_tutor(tmp_path, "missing_word")
    res = await tutor.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.scaffold == ""
    assert log.rows[-1]["fallback_reason"] == "schema_reject"


async def test_too_long_degrades(tmp_path) -> None:
    tutor, log, cache = make_tutor(tmp_path, "too_long")
    res = await tutor.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.scaffold == ""
    assert log.rows[-1]["fallback_reason"] == "schema_reject"


async def test_timeout_degrades(tmp_path) -> None:
    tutor, log, cache = make_tutor(tmp_path, "timeout")
    res = await tutor.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.degraded is True
    assert log.rows[-1]["fallback_reason"] == "timeout"
