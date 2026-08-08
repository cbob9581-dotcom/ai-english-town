import json, sqlite3
from datetime import datetime, timezone
from app.event_store import EventStore
from app.learning.memory import MemoryStore, build_world_summary, format_world_summary
from app.learning.store import LearningStore

def _now() -> datetime:
    return datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)

def _seed(conn):
    conn.execute("INSERT INTO word_lists(user_id,list_id,name,created_at) VALUES('local','l1','q','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO learning_items(word_id,user_id,lemma,pos,ipa,scene_tags,source,list_id,created_at) "
                 "VALUES('word_loaf_n_1','local','loaf','n','/loʊf/','[\"bakery\"]','quest','l1','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO learning_items(word_id,user_id,lemma,pos,ipa,scene_tags,source,list_id,created_at) "
                 "VALUES('word_shelf_n_1','local','shelf','n','/ʃɛlf/','[\"bakery\"]','quest','l1','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO mastery_states(user_id,word_id,state,due,productive_score,receptive_score,updated_at) "
                 "VALUES('local','word_loaf_n_1','learning','2026-08-08T11:00:00Z',0.6,0.5,'2026-08-08T12:00:00Z')")
    conn.execute("INSERT INTO mastery_states(user_id,word_id,state,updated_at) "
                 "VALUES('local','word_shelf_n_1','new','2026-08-08T12:00:00Z')")

def test_build_world_summary_from_events_and_tables(tmp_path):
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    events.append("s1", "scene.entered", {"archetypeId": "bakery", "sceneId": "sc1", "generationId": "g1", "revision": 1, "source": "connect"})
    events.append("s1", "scene.entered", {"archetypeId": "bakery", "sceneId": "sc2", "generationId": "g2", "revision": 1, "source": "exit"})
    summary = build_world_summary(events, conn, "local", now=_now())
    assert summary["memoryPolicyVersion"] == "v1"
    assert summary["scenes"]["bakery"]["count"] == 2
    assert summary["wordMastery"]["known"] == 1          # learning 计入 known
    assert summary["wordMastery"]["learning"] == 1
    assert summary["wordMastery"]["reviewDue"] == 1      # loaf due 11:00 <= now 12:00
    assert [w["lemma"] for w in summary["wordMastery"]["weakWords"]] == ["loaf"]  # 唯一非 new 词
    assert summary["userProfile"]["productiveAvg"] == 0.6
    assert summary["userProfile"]["helpCount"] == 0

def test_format_world_summary_template():
    s = {"scenes": {"plaza": {"count": 3}, "bakery": {"count": 2}},
         "wordMastery": {"known": 8, "learning": 5, "reviewDue": 3,
                         "weakWords": [{"lemma": "shelf"}, {"lemma": "jar"}]},
         "userProfile": {"helpCount": 4},
         "askedWords": [{"lemma": "loaf"}]}
    block = format_world_summary(s)
    assert "Visited scenes: plaza × 3, bakery × 2" in block
    assert "Known words: 8, learning: 5, review due: 3" in block
    assert "Top weak words: shelf, jar" in block
    assert "Words user explicitly asked about: loaf" in block

def test_new_scene_enter_bumps_revision_once(tmp_path):
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    mem = MemoryStore(conn)
    now = _now()
    events.append("s1", "scene.entered", {"archetypeId": "plaza", "sceneId": "s1", "generationId": "g1", "revision": 1, "source": "connect"})
    with events.write_lock:
        assert mem.apply_memory_updates(conn, events, "local", scene_enter="plaza", now=now) is True
        conn.commit()
    assert mem.get_revision("local") == 1
    events.append("s1", "scene.entered", {"archetypeId": "plaza", "sceneId": "s2", "generationId": "g2", "revision": 1, "source": "exit"})
    with events.write_lock:
        assert mem.apply_memory_updates(conn, events, "local", scene_enter="plaza", now=now) is False
        conn.commit()
    assert mem.get_revision("local") == 1          # 已知场景再进 → 不变（prefetch 不误失效）
    events.append("s1", "scene.entered", {"archetypeId": "bakery", "sceneId": "s3", "generationId": "g3", "revision": 1, "source": "exit"})
    with events.write_lock:
        assert mem.apply_memory_updates(conn, events, "local", scene_enter="bakery", now=now) is True
        conn.commit()
    assert mem.get_revision("local") == 2          # 新场景 → +1

def test_apply_memory_updates_store_and_snapshot(tmp_path):
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    mem = MemoryStore(conn)
    now = _now()
    assert mem.get_revision("local") == 0
    assert mem.get_world_summary("local") is None
    with events.write_lock:
        mem.apply_memory_updates(conn, events, "local", scene_enter="plaza", now=now)
        conn.commit()
    assert mem.get_revision("local") == 1
    assert mem.get_world_summary("local")["memoryPolicyVersion"] == "v1"
    snaps = [e for e in events.list_after("s1", 0) if e["event_type"] == "world_summary.snapshot"]
    assert len(snaps) == 1

# --- apply_memory_updates should_touch 分支：evidence / help / ask + at-most-one 不变式 ---

def test_evidence_bump_at_most_once(tmp_path):
    """state='new' 词 → 交易内升为 learning：证据使词进 known → 只 bump 一次。"""
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)                                     # shelf 为 'new' → cache 不含它
    mem = MemoryStore(conn)
    now = _now()
    wid = "word_shelf_n_1"
    ev = {"word_id": wid, "source": "classify", "session_id": "s1"}
    with events.write_lock:
        conn.execute("UPDATE mastery_states SET state='learning' WHERE user_id='local' AND word_id=?",
                     (wid,))                        # 模拟 FSRS 进入学习周期
        assert mem.apply_memory_updates(conn, events, "local", evidence=ev, now=now) is True
        # _store_summary 内 cache.refresh() → wid 已进 known_word_ids → 同轮不重复 +revision
        assert mem.apply_memory_updates(conn, events, "local", evidence=ev, now=now) is False
        conn.commit()
    assert mem.get_revision("local") == 1
    snaps = [e for e in events.list_after("s1", 0) if e["event_type"] == "world_summary.snapshot"]
    assert len(snaps) == 1                           # snapshot 不重复

def test_evidence_no_bump_when_word_already_known(tmp_path):
    """词已在 known_word_ids（cache 构造前即为 learning/review）→ 证据不 bump。"""
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    conn.execute("UPDATE mastery_states SET state='review' WHERE user_id='local' AND word_id='word_shelf_n_1'")
    conn.commit()
    mem = MemoryStore(conn)                          # 构造时 shelf 已是 review → 在 known_word_ids
    now = _now()
    wid = "word_shelf_n_1"
    with events.write_lock:
        assert mem.apply_memory_updates(conn, events, "local",
                                        evidence={"word_id": wid, "source": "classify", "session_id": "s1"},
                                        now=now) is False
        conn.commit()
    assert mem.get_revision("local") == 0

_HELP_INSERT = (
    "INSERT INTO evidence_events(evidence_id, user_id, event_seq, session_id, attempt_id, turn_id, "
    "word_id, source, prompt_level, axis, result, confidence, created_at) "
    "VALUES('ev_help_1','local',1,'s1','att_help','turn_help','word_loaf_n_1','help',0,'prod','correct',1.0,'2026-08-08T12:00:00Z')"
)

def test_help_bump_at_most_once(tmp_path):
    """help 证据使 (lemma,pos) 进 helped → 只 bump 一次。"""
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    mem = MemoryStore(conn)                          # helped 为空
    now = _now()
    wid = "word_loaf_n_1"
    ev = {"word_id": wid, "source": "help", "session_id": "s1"}
    with events.write_lock:
        conn.execute(_HELP_INSERT)
        assert mem.apply_memory_updates(conn, events, "local", evidence=ev, now=now) is True
        # cache.refresh() → helped 已含 ('loaf','n') → 同轮不重复
        assert mem.apply_memory_updates(conn, events, "local", evidence=ev, now=now) is False
        conn.commit()
    assert mem.get_revision("local") == 1
    snaps = [e for e in events.list_after("s1", 0) if e["event_type"] == "world_summary.snapshot"]
    assert len(snaps) == 1

def test_help_no_bump_when_already_helped(tmp_path):
    """cache 构造前已有该词 help 证据 → (lemma,pos) 在 helped → 不 bump。"""
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    conn.execute(_HELP_INSERT)
    conn.commit()
    mem = MemoryStore(conn)                          # helped 已含 ('loaf','n')
    now = _now()
    with events.write_lock:
        assert mem.apply_memory_updates(conn, events, "local",
                                        evidence={"word_id": "word_loaf_n_1", "source": "help", "session_id": "s1"},
                                        now=now) is False
        conn.commit()
    assert mem.get_revision("local") == 0

_SPONTANEOUS_INSERT = (
    "INSERT INTO spontaneous_words(user_id, lemma, pos, first_seen_at, last_seen_at, encounter_count, asked) "
    "VALUES('local','croissant','n','2026-08-08T11:00:00Z','2026-08-08T12:00:00Z',1,1)"
)

def test_ask_bump_at_most_once(tmp_path):
    """ask 使 (lemma,pos) 进 helped → 只 bump 一次。"""
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    mem = MemoryStore(conn)                          # helped 为空
    now = _now()
    ask = {"lemma": "croissant", "pos": "n", "session_id": "s1"}
    with events.write_lock:
        conn.execute(_SPONTANEOUS_INSERT)
        assert mem.apply_memory_updates(conn, events, "local", ask=ask, now=now) is True
        # cache.refresh() → helped 已含 ('croissant','n') → 同轮不重复
        assert mem.apply_memory_updates(conn, events, "local", ask=ask, now=now) is False
        conn.commit()
    assert mem.get_revision("local") == 1
    snaps = [e for e in events.list_after("s1", 0) if e["event_type"] == "world_summary.snapshot"]
    assert len(snaps) == 1

def test_ask_no_bump_when_already_asked(tmp_path):
    """cache 构造前 spontaneous_words 已 asked=1 → (lemma,pos) 在 helped → 不 bump。"""
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    conn.execute(_SPONTANEOUS_INSERT)
    conn.commit()
    mem = MemoryStore(conn)                          # helped 已含 ('croissant','n')
    now = _now()
    with events.write_lock:
        assert mem.apply_memory_updates(conn, events, "local",
                                        ask={"lemma": "croissant", "pos": "n", "session_id": "s1"},
                                        now=now) is False
        conn.commit()
    assert mem.get_revision("local") == 0

def test_build_world_summary_skips_malformed_scene_event(tmp_path):
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    LearningStore(conn)
    events.append("s1", "scene.entered", {"sceneId": "sc1", "generationId": "g1",
                                          "revision": 1, "source": "connect"})   # 缺 archetypeId
    events.append("s1", "scene.entered", {"archetypeId": "bakery", "sceneId": "sc2",
                                          "generationId": "g2", "revision": 1, "source": "connect"})
    summary = build_world_summary(events, conn, "local", now=_now())
    assert "bakery" in summary["scenes"]
    assert None not in summary["scenes"]      # 缺 archetypeId 的事件被跳过 → 无 None/"null" 键
    assert "null" not in summary["scenes"]
