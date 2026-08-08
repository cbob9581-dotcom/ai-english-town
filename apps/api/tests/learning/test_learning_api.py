"""HTTP 端点测试：word-lists import / spontaneous 提升 / progress summary / evidence。
用 fastapi.testclient（不进入 lifespan，不测 outbox drain）。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.event_store import EventStore
from app.learning.dictionary import Dictionary
from app.learning.encounters import record_ask
from app.learning.engine import LearningEngine
from app.learning.store import LearningStore
from app.main import create_app
from app.settings import Settings

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)


def _client(tmp_path) -> TestClient:
    events = EventStore(tmp_path / "e.db")
    app = create_app(events, Settings(asset_root=Path(__file__).resolve().parents[4] / "assets"))
    store = LearningStore(events.connection)
    app.state.learning = LearningEngine(store, events, app.state.settings)
    app.state.dictionary = Dictionary.load(app.state.settings.asset_root)
    return TestClient(app)


def test_import_words_returns_counts_and_is_queryable(tmp_path) -> None:
    c = _client(tmp_path)
    res = c.post("/api/word-lists/import",
                 json={"words": ["loaf", "bread/n"], "name": "bakery"})
    assert res.status_code == 200
    body = res.json()
    assert body["imported"] == 2
    assert body["known"] == 0
    assert body["missingMetadata"] == 0
    assert body["total"] == 2
    # 导入的词进入 summary（wordId 从词典匹配派生）
    s = c.get("/api/progress/summary").json()
    assert s["totalWords"] == 2
    lemmas = {w["lemma"] for w in s["words"]}
    assert lemmas == {"loaf", "bread"}


def test_import_invalid_word_422(tmp_path) -> None:
    c = _client(tmp_path)
    res = c.post("/api/word-lists/import", json={"words": ["a!!b"]})
    assert res.status_code == 422
    assert "invalid word" in res.json()["detail"]


def test_spontaneous_promote_by_lemma(tmp_path) -> None:
    c = _client(tmp_path)
    store = c.app.state.learning.store
    record_ask(store, "local", "s1", "gizmo", "n", "t1", now=NOW)
    res = c.post("/api/word-lists/spontaneous/import", json={"lemmas": ["gizmo"]})
    assert res.status_code == 200
    assert res.json() == {"promoted": 1, "unknown": 0}
    spont = c.get("/api/word-lists/spontaneous").json()["items"]
    assert spont[0]["lemma"] == "gizmo"
    assert spont[0]["promoted"] is True


def test_summary_strategy_and_due_today(tmp_path) -> None:
    c = _client(tmp_path)
    c.post("/api/word-lists/import", json={"words": ["loaf", "bread/n"]})
    s = c.get("/api/progress/summary").json()
    assert s["strategy"]["evidencePolicyVersion"] == "v1"
    assert s["strategy"]["fsrsAlgorithmVersion"] == "fsrs-5"
    assert s["totals"]["quest"] == 2
    assert s["totals"]["free"] == 0
    assert s["totals"]["dueToday"] == 0
    assert len(s["words"]) == 2
    assert s["words"][0]["evidenceCount"] == 0
    assert s["words"][0]["fsrs"]["state"] == "new"


def test_word_evidence_after_record(tmp_path) -> None:
    c = _client(tmp_path)
    c.post("/api/word-lists/import", json={"words": ["loaf"]})
    eng = c.app.state.learning
    ev = {
        "evidence_id": "ev_api_1",
        "event_seq": 0,
        "session_id": "s1",
        "attempt_id": "attempt_t1",
        "turn_id": "t1",
        "objective_id": "obj_loaf",
        "word_id": "word_loaf_n_1",
        "source": "prompted_production",
        "prompt_level": 1,
        "axis": "productive",
        "result": "success",
        "confidence": 0.9,
        "evidence_policy_version": eng.settings.evidence_policy_version,
        "fsrs_algorithm_version": eng.settings.fsrs_algorithm_version,
        "created_at": NOW.isoformat(),
    }
    seq = eng.record_evidence("s1", ev, event_id="ev_api_1")
    assert seq is not None
    res = c.get("/api/progress/words/word_loaf_n_1/evidence")
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["evidence_id"] == "ev_api_1"
    assert items[0]["source"] == "prompted_production"
