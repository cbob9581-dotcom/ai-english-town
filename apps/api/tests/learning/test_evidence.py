from datetime import datetime, timezone

from app.event_store import EventStore
from app.learning.evidence import apply_evidence, classify_round
from app.learning.store import LearningStore

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
U = "local"


def _store(tmp_path) -> LearningStore:
    s = LearningStore(EventStore(tmp_path / "e.db").connection)
    s.import_words(U, "l1", "x", [{"lemma": "loaf", "pos": "n", "sense": "1",
                                   "scene_tags": "[]", "carrier": "phrase",
                                   "slot_categories": "[]", "source": "quest",
                                   "created_at": "2026-08-08T00:00:00+00:00"}])
    return s


def test_classify_prompted_vs_spontaneous_vs_no_attempt() -> None:
    scene = {"word_loaf_n_1": "loaf"}
    drafts = classify_round(scene, npc_text="Can I get a loaf?", user_text="I want a loaf",
                            confidence=0.86, target_word_ids={"word_loaf_n_1"})
    assert drafts[0]["source"] == "prompted_production"
    assert drafts[0]["prompt_level"] == 1
    assert drafts[0]["result"] == "success"

    drafts = classify_round(scene, npc_text="Good morning", user_text="a loaf please",
                            confidence=0.86, target_word_ids={"word_loaf_n_1"})
    assert drafts[0]["source"] == "spontaneous_production"
    assert drafts[0]["prompt_level"] == 0

    drafts = classify_round(scene, npc_text="Do you see the loaf?", user_text="hello",
                            confidence=0.86, target_word_ids={"word_loaf_n_1"})
    assert drafts[0]["result"] == "no_attempt"       # 教过但 ASR 未检出 → 不惩罚


def test_classify_low_confidence_uncertain() -> None:
    scene = {"word_loaf_n_1": "loaf"}
    drafts = classify_round(scene, "I want a loaf", "I want a loaf", confidence=0.4,
                            target_word_ids={"word_loaf_n_1"})
    assert drafts[0]["result"] == "uncertain"


def test_record_success_updates_score_and_enters_fsrs(tmp_path) -> None:
    s = _store(tmp_path)
    ev = {"evidence_id": "ev_1", "event_seq": 1, "session_id": "s1", "attempt_id": "a1",
          "turn_id": "t1", "objective_id": "obj_plaza_w1", "word_id": "word_loaf_n_1",
          "source": "prompted_production", "prompt_level": 1, "axis": "productive",
          "result": "success", "confidence": 0.86, "evidence_policy_version": "v1",
          "fsrs_algorithm_version": "fsrs-5", "created_at": NOW.isoformat()}
    # 第一次成功：attempts=1，未达进入条件
    apply_evidence(s, U, ev, now=NOW)
    m = s.get_mastery(U, "word_loaf_n_1")
    assert m["attempts"] == 1 and m["success_count"] == 1
    assert m["productive_score"] > 0.0
    assert m["state"] == "new"                     # 未达条件

    # 第二次（另一 attempt_id）：scaffolded_success=2, attempts=2 → 进入排期
    ev2 = {**ev, "evidence_id": "ev_2", "attempt_id": "a2"}
    apply_evidence(s, U, ev2, now=NOW)
    m = s.get_mastery(U, "word_loaf_n_1")
    assert m["state"] == "review"
    assert m["due"] and m["due"] > NOW.isoformat()
    assert m["reps"] == 1


def test_daily_gate_lower_rating_reschedules(tmp_path) -> None:
    s = _store(tmp_path)
    mk = lambda eid, aid, src, result, conf: {
        "evidence_id": eid, "event_seq": 1, "session_id": "s1", "attempt_id": aid,
        "turn_id": "t1", "objective_id": "obj_w", "word_id": "word_loaf_n_1",
        "source": src, "prompt_level": 1, "axis": "productive", "result": result,
        "confidence": conf, "evidence_policy_version": "v1",
        "fsrs_algorithm_version": "fsrs-5", "created_at": NOW.isoformat()}
    # 进入排期（两次成功）
    apply_evidence(s, U, mk("e1", "a1", "prompted_production", "success", 0.86), now=NOW)
    apply_evidence(s, U, mk("e2", "a2", "prompted_production", "success", 0.86), now=NOW)
    m = s.get_mastery(U, "word_loaf_n_1")
    assert m["state"] == "review"
    due_after_good = m["due"]
    # 同日更低评分（error）→ 降级重排
    apply_evidence(s, U, mk("e3", "a3", "error", "error", 1.0), now=NOW)
    m = s.get_mastery(U, "word_loaf_n_1")
    assert m["due"] <= due_after_good             # 保守：due 不变早
    assert m["lapses"] == 1
    # 同日更高评分（spontaneous）→ 不重排
    apply_evidence(s, U, mk("e4", "a4", "spontaneous_production", "success", 0.86), now=NOW)
    m2 = s.get_mastery(U, "word_loaf_n_1")
    assert m2["due"] == m["due"]
