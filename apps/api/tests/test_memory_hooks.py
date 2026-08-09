from datetime import datetime, timezone
from app.event_store import EventStore
from app.learning.encounters import record_ask
from app.learning.engine import LearningEngine
from app.learning.memory import MemoryStore, build_world_summary
from app.learning.store import LearningStore
from app.settings import Settings

def _now() -> datetime:
    return datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)

def _seed(conn):
    conn.execute("INSERT INTO word_lists(user_id,list_id,name,created_at) VALUES('local','l1','q','2026-08-08T00:00:00Z')")
    for wid, lemma in (("word_loaf_n_1", "loaf"), ("word_jar_n_1", "jar")):
        conn.execute("INSERT INTO learning_items(word_id,user_id,lemma,pos,ipa,scene_tags,source,list_id,created_at) "
                     "VALUES(?,'local',?,'n','/x/','[\"bakery\"]','quest','l1','2026-08-08T00:00:00Z')",
                     (wid, lemma))

def _evidence(word_id="word_loaf_n_1", source="prompted_production", result="success",
              prompt_level=1, confidence=0.9, evidence_id="ev_x"):
    return {"evidence_id": evidence_id, "event_seq": 0, "session_id": "s1", "attempt_id": "a",
            "turn_id": "t", "objective_id": None, "word_id": word_id, "source": source,
            "prompt_level": prompt_level, "axis": "productive", "result": result,
            "confidence": confidence, "evidence_policy_version": "v1",
            "fsrs_algorithm_version": "fsrs-5", "created_at": "2026-08-08T12:00:00Z"}

def test_one_round_multi_evidence_revision_at_most_one(tmp_path):
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    # 预置 loaf 已 known（learning）+ 正 stability：prompted 证据经日闸真实排期（非空转）
    conn.execute("INSERT INTO mastery_states(user_id,word_id,state,stability,difficulty,due,last_review,updated_at) "
                 "VALUES('local','word_loaf_n_1','learning',3.0,5.0,'2026-08-08T00:00:00Z',"
                 "'2026-08-08T00:00:00Z','2026-08-08T12:00:00Z')")
    eng = LearningEngine(store, events, Settings())
    eng.record_evidence("s1", _evidence(source="help", result="neutral", evidence_id="ev_h1"), event_id="h1")
    assert eng.memory.get_revision("local") == 1
    eng.record_evidence("s1", _evidence(evidence_id="ev_p1"), event_id="p1")
    assert eng.memory.get_revision("local") == 1
    eng.record_evidence("s1", _evidence(source="action_understanding", prompt_level=1, evidence_id="ev_c1"), event_id="c1")
    assert eng.memory.get_revision("local") == 1
    # 三条证据全部真实落库（evidence_id 唯一，不再被 INSERT OR IGNORE 丢行）
    assert len(store.evidence_for_word("local", "word_loaf_n_1")) == 3

def test_new_word_entering_learning_bumps_revision(tmp_path):
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    # 预置 loaf: attempts=1, scaffolded=1, state=new → 一次 prompted success 即进入 learning
    conn.execute("INSERT INTO mastery_states(user_id,word_id,state,attempts,scaffolded_success_count,updated_at) "
                 "VALUES('local','word_loaf_n_1','new',1,1,'2026-08-08T12:00:00Z')")
    eng = LearningEngine(store, events, Settings())
    assert eng.memory.get_revision("local") == 0
    eng.record_evidence("s1", _evidence(), event_id="e1")
    assert eng.memory.get_revision("local") == 1          # loaf 进入 learning → +1
    eng.record_evidence("s1", _evidence(), event_id="e2")  # 同词已 known → 不变
    assert eng.memory.get_revision("local") == 1

def test_snapshot_events_replay_rebuilds_summary(tmp_path):
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    eng = LearningEngine(store, events, Settings())
    eng.record_evidence("s1", _evidence(source="help", result="neutral"), event_id="e1")
    assert eng.memory.get_revision("local") == 1
    rebuilt = build_world_summary(events, conn, "local", now=_now())
    saved = eng.memory.get_world_summary("local")
    assert rebuilt["wordMastery"] == saved["wordMastery"]
    snaps = [e for e in events.list_after("s1", 0) if e["event_type"] == "world_summary.snapshot"]
    assert len(snaps) == 1

def test_record_ask_with_events_fires_ask_hook(tmp_path):
    events = EventStore(tmp_path / "e.db")
    store = LearningStore(events.connection)
    store.memory = MemoryStore(events.connection)
    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)

    record_ask(store, "local", "s1", "torch", "n", "turn1", now=now, events=events)
    assert store.memory.get_revision("local") == 1
    summary = store.memory.get_world_summary("local")
    assert {"lemma": "torch"} in summary["askedWords"]

    record_ask(store, "local", "s1", "torch", "n", "turn2", now=now, events=events)
    assert store.memory.get_revision("local") == 1   # 已求助 → 不重复 +

def test_review_due_parsed_as_datetime_z_suffix(tmp_path):
    """due 带 Z 后缀：字符串比较会因 'Z' > '+' 而错判为未到期；datetime 解析后精确比较。"""
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    conn.execute("INSERT INTO mastery_states(user_id,word_id,state,due,updated_at) "
                 "VALUES('local','word_loaf_n_1','review','2026-08-08T00:00:00Z','2026-08-08T12:00:00Z')")
    summary = build_world_summary(events, conn, "local",
                                  now=datetime(2026, 8, 8, 0, 0, 0, tzinfo=timezone.utc))
    assert summary["wordMastery"]["reviewDue"] == 1
