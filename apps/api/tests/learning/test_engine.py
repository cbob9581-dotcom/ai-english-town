import json
from datetime import datetime, timezone
from pathlib import Path

from app.event_store import EventStore
from app.learning.engine import LearningEngine
from app.learning.store import LearningStore

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
U = "local"


def _engine(tmp_path) -> tuple[LearningEngine, EventStore]:
    events = EventStore(tmp_path / "e.db")
    store = LearningStore(events.connection)
    return LearningEngine(store, events, _fake_settings()), events


def _fake_settings():
    class S:
        score_alpha = 0.35
        fsrs_retention = 0.9
        fsrs_min_confidence = 0.6
        evidence_policy_version = "v1"
        fsrs_algorithm_version = "fsrs-5"
    return S()


def test_record_round_single_transaction(tmp_path) -> None:
    eng, events = _engine(tmp_path)
    eng.store.import_words(U, "l1", "x", [{"lemma": "loaf", "pos": "n", "sense": "1",
                                            "scene_tags": "[\"bakery\"]", "carrier": "phrase",
                                            "slot_categories": "[]", "source": "quest",
                                            "created_at": NOW.isoformat()}])
    drafts = eng.record_round("s1", {"word_loaf_n_1": "loaf"}, npc_text="a loaf please",
                              user_text="I want a loaf", confidence=-0.2,
                              target_word_ids={"word_loaf_n_1"}, turn_id="t1", attempt_id="a1")
    assert drafts == 1
    evs = events.list_after("s1", 0)
    assert any(e["event_type"] == "evidence" and e["payload"]["internal"] for e in evs)
    assert len(eng.store.evidence_for_word(U, "word_loaf_n_1")) == 1


def test_outbox_on_failure(tmp_path) -> None:
    eng, events = _engine(tmp_path)
    # 用一个不存在的 word_id 制造失败（record_evidence 内部 FK 失败 → rollback → outbox）
    payload = {"evidence_id": "ev_bad", "event_seq": 99, "session_id": "s1",
               "attempt_id": "a1", "turn_id": "t1", "objective_id": None,
               "word_id": "word_missing_n_1", "source": "prompted_production",
               "prompt_level": 1, "axis": "productive", "result": "success",
               "confidence": 0.86, "evidence_policy_version": "v1",
               "fsrs_algorithm_version": "fsrs-5", "created_at": NOW.isoformat()}
    seq = eng.record_evidence("s1", payload, event_id="ev_bad")
    assert seq is None                          # 失败不阻断
    rows = eng.store.outbox_drain(U)
    assert len(rows) == 1
    assert json.loads(rows[0])["evidence_id"] == "ev_bad"
