import base64
from collections.abc import Awaitable, Callable
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


def make_tutor(tmp_path: Path, scenario: str = "ok", tts_calls: list | None = None,
               tts_client: Callable[[str], Awaitable[dict]] | None = None):
    events = EventStore(tmp_path / "e.db")
    cache = TutorCache(events.connection, tmp_path / "audio")
    log = FakeLlmLog()
    calls = tts_calls if tts_calls is not None else []
    client = tts_client if tts_client is not None else make_tts(calls)
    tutor = CompanionTutor(MockAdapter(scenario), Settings(llm_total_timeout_tutor_s=1.0),
                           log, cache, client)
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
    calls: list = []
    tutor2, log2, _ = make_tutor(tmp_path, tts_calls=calls)
    await tutor2.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    rows_after_miss = len(log2.rows)          # 第一次 miss：LLM 调用已记录
    res = await tutor2.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.from_cache is True
    assert res.audio_base64 is not None
    assert calls == ["loaf"]                  # 第二次命中：0 TTS
    assert len(log2.rows) == rows_after_miss  # 命中缓存：0 LLM 调用（不写 llm_calls）


async def test_tts_failure_degrades_keeps_scaffold(tmp_path) -> None:
    async def failing_tts(text: str):
        raise RuntimeError("tts down")

    tutor, log, cache = make_tutor(tmp_path, tts_client=failing_tts)
    res = await tutor.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert "loaf" in res.scaffold              # LLM 成功 → scaffold 保留
    assert res.audio_base64 is None            # TTS 失败 → 无音频
    assert res.sample_rate is None
    assert res.degraded is True and res.from_cache is False
    assert log.rows[-1]["role"] == "companion_tutor" and log.rows[-1]["ok"] is True
    assert log.rows[-1]["fallback_reason"] == "none"
    assert cache.get("word_loaf_n_1") is None  # TTS 失败 → 缓存写不入


async def test_invalid_json_degrades_to_word_only(tmp_path) -> None:
    tutor, log, cache = make_tutor(tmp_path, "invalid_json")
    res = await tutor.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.word == "loaf" and res.scaffold == ""
    assert res.degraded is True
    assert res.audio_base64 is not None   # 仍读单词
    assert log.rows[-1]["fallback_reason"] == "invalid_json" and log.rows[-1]["ok"] is False


async def test_api_status_error_degrades_to_word_only(tmp_path) -> None:
    # 4xx openai.APIStatusError（401/403/429/422…）不重试原样上抛 → 按 connect 类降级只读单词
    tutor, log, cache = make_tutor(tmp_path, "status_error")
    res = await tutor.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.word == "loaf" and res.scaffold == ""
    assert res.degraded is True
    assert res.audio_base64 is not None   # 仍读单词
    assert log.rows[-1]["fallback_reason"] == "connect" and log.rows[-1]["ok"] is False


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


def test_tutor_prompt_uses_setting_char_limit() -> None:
    from app.llm import tutor as tutor_mod
    class S:
        llm_max_scaffold_chars = 80
    # TUTOR_SYSTEM_PROMPT 是带 {max_scaffold_chars} 占位符的常量模板，动态值在消息构造时 .format() 插入。
    prompt = tutor_mod.TUTOR_SYSTEM_PROMPT.format(max_scaffold_chars=S.llm_max_scaffold_chars)
    assert "under 80 characters" in prompt
