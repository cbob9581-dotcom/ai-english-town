from app.event_store import EventStore
from app.learning.engine import LearningEngine
from app.learning.store import LearningStore
from app.settings import Settings


def _build(tmp_path, name):
    """EventStore + LearningStore + 预置 loaf 已 known（learning，正 stability）。
    镜像 test_word_confidence._build 线程化端到端模式。"""
    events = EventStore(tmp_path / name)
    conn = events.connection
    store = LearningStore(conn)
    conn.execute("INSERT INTO word_lists(user_id,list_id,name,created_at) "
                 "VALUES('local','l1','q','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO learning_items(word_id,user_id,lemma,pos,ipa,scene_tags,source,list_id,created_at) "
                 "VALUES('word_loaf_n_1','local','loaf','n','/loʊf/','[\"bakery\"]','quest','l1','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO mastery_states(user_id,word_id,state,stability,difficulty,due,last_review,updated_at) "
                 "VALUES('local','word_loaf_n_1','learning',3.0,5.0,'2026-08-08T00:00:00Z','2026-08-08T00:00:00Z','2026-08-08T12:00:00Z')")
    eng = LearningEngine(store, events, Settings())
    return store, eng


def test_gop_evidence_persisted_through_record_round(tmp_path):
    store, eng = _build(tmp_path, "e.db")
    n = eng.record_round(
        "s1", {"word_loaf_n_1": "loaf"}, "what do you need", "I want a loaf", 0.9,
        turn_id="t1", target_word_ids={"word_loaf_n_1"},
        words=[{"word": "loaf", "start": 0.4, "end": 0.9, "probability": 0.95}],
        gop_scores={"word_loaf_n_1": 0.8})
    assert n == 1
    rows = [r for r in store.evidence_for_word("local", "word_loaf_n_1")
            if r["source"] == "pronunciation_gop"]
    assert len(rows) == 1
    assert rows[0]["axis"] == "pronunciation_gop"
    assert rows[0]["result"] == "success"      # min_conf=None → 只入证据不判
    assert abs(rows[0]["confidence"] - 0.8) < 1e-6
    m = store.get_mastery("local", "word_loaf_n_1")
    # update_score(0.0, 0.5, 0.8) = min(1, 0 + 0.35*0.5*0.8) = 0.14
    assert m["pronunciation_score"] is not None
    assert abs(m["pronunciation_score"] - 0.14) < 1e-6


def test_gop_non_counting_and_non_scheduling(tmp_path):
    store_a, eng_a = _build(tmp_path, "a.db")
    store_b, eng_b = _build(tmp_path, "b.db")
    kw = {"session_id": "s1", "scene_words": {"word_loaf_n_1": "loaf"},
          "npc_text": "what do you need", "user_text": "I want a loaf", "confidence": 0.9,
          "turn_id": "t1", "target_word_ids": {"word_loaf_n_1"},
          "words": [{"word": "loaf", "start": 0.4, "end": 0.9, "probability": 0.95}]}
    eng_a.record_round(**kw)                                            # gop_scores=None
    eng_b.record_round(**kw, gop_scores={"word_loaf_n_1": 0.8})
    a = store_a.get_mastery("local", "word_loaf_n_1")
    b = store_b.get_mastery("local", "word_loaf_n_1")
    # 不计数：attempts/success/exposure 两 run 一致
    for col in ("attempts", "success_count", "exposure_count",
                "scaffolded_success_count", "help_count"):
        assert a[col] == b[col], col
    # 不进排期：排期字段一致（日期精度比较）
    for col in ("state", "stability", "difficulty", "reps", "lapses",
                "last_scheduled_rating", "last_scheduled_date"):
        assert a[col] == b[col], col
    assert a["due"] and b["due"] and a["due"][:10] == b["due"][:10]
    # 轴分：仅带 gop 的 run 落 pronunciation_score；无 gop → NULL（未评测）
    assert a["pronunciation_score"] is None
    assert b["pronunciation_score"] is not None
    # 无 gop_scores → 无 pronunciation_gop 证据；词级代理证据不受影响
    assert len([r for r in store_a.evidence_for_word("local", "word_loaf_n_1")
                if r["source"] == "pronunciation_gop"]) == 0


def test_gop_evidence_absent_when_user_did_not_produce(tmp_path):
    store, eng = _build(tmp_path, "e.db")
    eng.record_round(
        "s1", {"word_loaf_n_1": "loaf"}, "what do you need", "show me the jar", 0.9,
        turn_id="t1", target_word_ids={"word_loaf_n_1"}, words=None,
        gop_scores={"word_loaf_n_1": 0.8})   # 词不在 drafts（user 未产出）→ 不写 GOP 证据
    assert len([r for r in store.evidence_for_word("local", "word_loaf_n_1")
                if r["source"] == "pronunciation_gop"]) == 0
