from datetime import datetime, timezone

from app.event_store import EventStore
from app.learning.encounters import list_spontaneous, promote, record_ask, record_exposure
from app.learning.store import LearningStore

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
U = "local"


def _store(tmp_path) -> LearningStore:
    return LearningStore(EventStore(tmp_path / "e.db").connection)


def test_exposure_detail_and_aggregate(tmp_path) -> None:
    s = _store(tmp_path)
    record_exposure(s, U, "s1", "pigeon", "n", "t1", now=NOW)
    record_exposure(s, U, "s1", "pigeon", "n", "t2", now=NOW)
    agg = list_spontaneous(s, U)
    assert agg[0]["lemma"] == "pigeon"
    assert agg[0]["encounterCount"] == 2
    rows = s.conn.execute("SELECT COUNT(*) FROM spontaneous_encounters").fetchone()[0]
    assert rows == 2                           # 明细行保留


def test_ask_marks_aggregate(tmp_path) -> None:
    s = _store(tmp_path)
    record_ask(s, U, "s1", "pigeon", "n", "t1", now=NOW)
    assert list_spontaneous(s, U)[0]["asked"] == True


def test_promote_creates_learning_item(tmp_path) -> None:
    s = _store(tmp_path)
    record_exposure(s, U, "s1", "pigeon", "n", "t1", now=NOW)
    n = promote(s, U, ["pigeon"], now=NOW)
    assert n == 1
    item = s.get_item(U, "word_pigeon_n_1")
    assert item["source"] == "free"
    m = s.get_mastery(U, "word_pigeon_n_1")
    assert m is not None and m["state"] == "new"
    assert list_spontaneous(s, U)[0]["promoted"] == True
