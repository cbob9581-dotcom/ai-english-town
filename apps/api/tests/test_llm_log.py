from pathlib import Path

import pytest

from app.event_store import EventStore
from app.llm_log import LlmLog, FALLBACK_REASONS
from app.settings import Settings


def test_settings_llm_defaults() -> None:
    s = Settings()
    assert s.llm_base_url == "https://api.deepseek.com"
    assert s.llm_api_key == ""
    assert s.llm_model == "deepseek-chat"
    assert s.llm_connect_timeout_s == 1.5
    assert s.llm_ttft_timeout_s == 2.0
    assert s.llm_total_timeout_npc_s == 15.0
    assert s.llm_total_timeout_tutor_s == 6.0
    assert s.llm_total_timeout_director_s == 15.0
    assert s.llm_max_tokens_director == 800
    assert s.llm_max_speech_chars == 200
    assert s.llm_max_scaffold_chars == 120
    assert s.llm_temperature_npc == 0.8
    assert s.llm_temperature_tutor == 0.3
    assert s.llm_max_tokens_npc == 320
    assert s.llm_max_tokens_tutor == 320
    assert s.llm_session_call_cap == 200
    assert s.llm_concurrency_limit == 2
    assert s.tutor_cache_dir == Path("data/tutor-audio")


def test_settings_from_env_override(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat-v3")
    monkeypatch.setenv("LLM_SESSION_CALL_CAP", "42")
    s = Settings.from_env()
    assert s.llm_api_key == "sk-env"
    assert s.llm_model == "deepseek-chat-v3"
    assert s.llm_session_call_cap == 42
    assert s.llm_connect_timeout_s == 1.5  # 未覆盖的保留默认


def test_record_and_count(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    log = LlmLog(events.connection)
    log.record(session_id="s1", role="npc_actor", model="deepseek-chat",
               generation_id="g1", turn_id="t1", utterance_id="u1",
               prompt_tokens=40, completion_tokens=9, latency_ms=900, ttft_ms=120,
               finish_reason="stop", fallback_reason="none", attempt=1, ok=True)
    log.record(session_id="s1", role="npc_actor", model="deepseek-chat",
               fallback_reason="timeout", attempt=1, ok=False, error="boom")
    assert log.count_session_calls("s1") == 2
    assert log.count_session_calls("other") == 0
    rows = log.recent("s1")
    assert len(rows) == 2
    assert rows[0]["fallback_reason"] == "none" and rows[0]["ok"] == 1
    assert rows[1]["fallback_reason"] == "timeout" and rows[1]["ok"] == 0


def test_fallback_reason_enum_rejected(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    log = LlmLog(events.connection)
    assert FALLBACK_REASONS == frozenset({
        "timeout", "connect", "invalid_json", "schema_reject",
        "length_truncated", "no_key", "budget", "none",
    })
    with pytest.raises(ValueError):
        log.record(session_id="s1", role="npc_actor", model="m", fallback_reason="bogus")


def test_table_created_with_index(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    LlmLog(events.connection)
    tables = {r[0] for r in events.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "llm_calls" in tables
    idx = {r[0] for r in events.connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_llm_calls_session" in idx
