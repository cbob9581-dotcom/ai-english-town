from app.event_store import EventStore
from app.learning.engine import LearningEngine
from app.learning.store import LearningStore
from app.learning.word_confidence import score_word_confidence
from app.settings import Settings


def test_hit_returns_word_probability():
    scene = {"word_loaf_n_1": "loaf", "word_jar_n_1": "jar"}
    words = [{"word": "loaf", "start": 0.4, "end": 0.9, "probability": 0.95}]
    out = score_word_confidence(words, scene, "I want a loaf")
    assert abs(out["word_loaf_n_1"] - 0.95) < 1e-6
    assert "word_jar_n_1" not in out          # 用户没说 jar → 无词级信号


def test_misrecognized_low_score():
    scene = {"word_loaf_n_1": "loaf"}
    words = [{"word": "roof", "start": 0.4, "end": 0.9, "probability": 0.9}]
    out = score_word_confidence(words, scene, "I want a loaf")   # 说了 loaf，ASR 听成 roof
    assert out["word_loaf_n_1"] == 0.15


def test_word_not_in_user_text_no_evidence():
    scene = {"word_loaf_n_1": "loaf", "word_jar_n_1": "jar"}
    words = [{"word": "loaf", "start": 0.4, "end": 0.9, "probability": 0.9}]
    out = score_word_confidence(words, scene, "show me the jar")
    assert "word_loaf_n_1" not in out           # loaf 未在 user_text → 无词级信号
    assert out["word_jar_n_1"] == 0.15          # jar 在 user_text 但 words 未检出 → 0.15（说但误识）


def test_no_words_returns_empty():
    scene = {"word_loaf_n_1": "loaf"}
    assert score_word_confidence([], scene, "I want a loaf") == {}
    assert score_word_confidence(None, scene, "I want a loaf") == {}


# ---- 端到端：real LearningEngine.record_round(words=...) → word_production 证据 ----

def _build(tmp_path, name):
    """EventStore + LearningStore + 预置 loaf 已 known（learning）。
    必须带正 stability/difficulty + last_review：draft 证据经日闸重排期时 py-fsrs 需有效
    retrievability；stability=0.0 的 learning 卡会触发 ZeroDivisionError（0**负幂），
    与生产不同（真词进 learning 时首次排期已写入正 stability）。"""
    events = EventStore(tmp_path / name)
    conn = events.connection
    store = LearningStore(conn)
    conn.execute("INSERT INTO word_lists(user_id,list_id,name,created_at) "
                 "VALUES('local','l1','q','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO learning_items(word_id,user_id,lemma,pos,ipa,scene_tags,source,list_id,created_at) "
                 "VALUES('word_loaf_n_1','local','loaf','n','/l/','[\"bakery\"]','quest','l1','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO mastery_states(user_id,word_id,state,stability,difficulty,due,last_review,updated_at) "
                 "VALUES('local','word_loaf_n_1','learning',3.0,5.0,'2026-08-08T00:00:00Z','2026-08-08T00:00:00Z','2026-08-08T12:00:00Z')")
    eng = LearningEngine(store, events, Settings())
    return store, eng


def test_word_production_evidence_persisted_through_record_round(tmp_path):
    store, eng = _build(tmp_path, "e.db")
    n = eng.record_round(
        "s1", {"word_loaf_n_1": "loaf"}, "what do you need", "I want a loaf", 0.9,
        turn_id="t1", target_word_ids={"word_loaf_n_1"},
        words=[{"word": "loaf", "start": 0.4, "end": 0.9, "probability": 0.95}])
    assert n == 1
    rows = [r for r in store.evidence_for_word("local", "word_loaf_n_1")
            if r["source"] == "word_production"]
    assert len(rows) == 1
    assert rows[0]["word_id"] == "word_loaf_n_1"
    assert rows[0]["axis"] == "asr_word_confidence"
    assert rows[0]["result"] == "success"
    assert abs(rows[0]["confidence"] - 0.95) < 1e-6
    m = store.get_mastery("local", "word_loaf_n_1")
    # update_score(0.0, 0.4, 0.95) = min(1, 0 + 0.35*0.4*0.95*(1-0)) = 0.133
    assert abs(m["asr_word_confidence_score"] - 0.133) < 1e-6


def test_word_production_non_counting_and_non_scheduling(tmp_path):
    store_a, eng_a = _build(tmp_path, "a.db")
    store_b, eng_b = _build(tmp_path, "b.db")
    kw = {"session_id": "s1", "scene_words": {"word_loaf_n_1": "loaf"},
          "npc_text": "what do you need", "user_text": "I want a loaf", "confidence": 0.9,
          "turn_id": "t1", "target_word_ids": {"word_loaf_n_1"}}
    eng_a.record_round(**kw)                                       # words=None → 仅 draft 证据
    eng_b.record_round(**kw, words=[{"word": "loaf", "start": 0.4, "end": 0.9, "probability": 0.95}])
    a = store_a.get_mastery("local", "word_loaf_n_1")
    b = store_b.get_mastery("local", "word_loaf_n_1")
    # 不计数：word_production 不增 attempts/success/exposure → 两 run 完全一致（draft 各计一次）
    for col in ("attempts", "success_count", "exposure_count",
                "scaffolded_success_count", "help_count"):
        assert a[col] == b[col], col
    assert b["attempts"] == 1 and b["exposure_count"] == 1
    # 轴分：仅带 words 的 run 落 asr_word_confidence_score
    assert a["asr_word_confidence_score"] == 0.0
    assert b["asr_word_confidence_score"] > 0.0
    # 不进排期：排期字段两 run 一致（due 比较日期精度，规避两次 now 的毫秒抖动）
    for col in ("state", "stability", "difficulty", "reps", "lapses",
                "last_scheduled_rating", "last_scheduled_date"):
        assert a[col] == b[col], col
    assert a["due"] and b["due"]
    assert a["due"][:10] == b["due"][:10]
    # revision：已 known 词 → 两 run 均不 bump
    assert eng_a.memory.get_revision("local") == eng_b.memory.get_revision("local") == 0
