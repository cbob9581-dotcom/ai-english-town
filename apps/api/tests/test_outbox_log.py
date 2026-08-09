import json
from datetime import datetime, timezone

from app.event_store import EventStore
from app.learning import fsrs as fsrs_mod
from app.learning.evidence import classify_round
from app.learning.engine import LearningEngine
from app.learning.store import LearningStore
from app.settings import Settings


def _seed(conn):
    conn.execute("INSERT INTO word_lists(user_id,list_id,name,created_at) "
                 "VALUES('local','l1','q','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO learning_items(word_id,user_id,lemma,pos,ipa,scene_tags,source,list_id,created_at) "
                 "VALUES('word_loaf_n_1','local','loaf','n','/loʊf/','[\"bakery\"]','quest','l1','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO mastery_states(user_id,word_id,state,stability,difficulty,due,last_review,updated_at) "
                 "VALUES('local','word_loaf_n_1','learning',3.0,5.0,'2026-08-08T00:00:00Z','2026-08-08T00:00:00Z','2026-08-08T12:00:00Z')")


def test_evidence_failure_logs_and_lands_outbox(tmp_path, monkeypatch, capsys):
    """apply_evidence 内部抛错（FSRS 排期）→ 事务回滚、入 outbox、且有一行日志（不再静默吞）。"""
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    eng = LearningEngine(store, events, Settings())

    def boom(*a, **k):
        raise RuntimeError("scheduler boom")

    monkeypatch.setattr(fsrs_mod, "schedule", boom)
    draft = classify_round({"word_loaf_n_1": "loaf"}, "what do you need", "I want a loaf",
                           0.9, target_word_ids={"word_loaf_n_1"})[0]
    evidence = {"evidence_id": "ev_x", "event_seq": 0, "session_id": "s1", "attempt_id": "a",
                "turn_id": "t", "objective_id": None, "word_id": draft["word_id"],
                "source": draft["source"], "prompt_level": draft["prompt_level"],
                "axis": draft["axis"], "result": draft["result"], "confidence": draft["confidence"],
                "evidence_policy_version": "v1", "fsrs_algorithm_version": "fsrs-5",
                "created_at": "2026-08-08T12:00:00Z"}
    seq = eng.record_evidence("s1", evidence, event_id="ev_x")
    assert seq is None
    out = store.outbox_drain("local")
    assert len(out) == 1
    assert json.loads(out[0])["evidence_id"] == "ev_x"
    assert "evidence dropped to outbox" in capsys.readouterr().out
