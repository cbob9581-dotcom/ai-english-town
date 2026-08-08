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
