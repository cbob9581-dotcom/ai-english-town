import json
from pathlib import Path

import pytest

from app.event_store import EventStore


@pytest.fixture()
def store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "events.db")


def test_append_returns_increasing_sequence(store: EventStore) -> None:
    s1 = store.append("s1", "scene.entered", {"sceneId": "scene_bakery_001"})
    s2 = store.append("s1", "dialogue.turn", {"text": "hello"})
    assert s2 == s1 + 1


def test_dedupe_by_event_id(store: EventStore) -> None:
    seq1 = store.append("s1", "scene.entered", {"x": 1}, event_id="ev-1")
    seq2 = store.append("s1", "scene.entered", {"x": 1}, event_id="ev-1")
    assert seq1 == seq2  # 幂等：重复提交不新增 sequence


def test_wal_enabled(store: EventStore) -> None:
    mode = store.connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_list_after(store: EventStore) -> None:
    store.append("s1", "a", {})
    mid = store.append("s1", "b", {})
    store.append("s1", "c", {})
    assert [e["event_type"] for e in store.list_after("s1", mid)] == ["c"]


def test_append_internal_flag_and_in_tx(tmp_path) -> None:
    store = EventStore(tmp_path / "e.db")
    with store.write_lock:
        seq = store.append_in_tx(store.connection, "s1", "evidence",
                                 {"k": "v"}, event_id="ev_1", internal=True)
        assert seq == 1
        dup = store.append_in_tx(store.connection, "s1", "evidence",
                                 {"k": "v"}, event_id="ev_1", internal=True)
        assert dup == seq                      # 幂等
    store.connection.commit()
    rows = store.list_after("s1", 0)
    assert rows[0]["event_type"] == "evidence"
    assert rows[0]["payload"] == {"k": "v"}
    internal = store.connection.execute(
        "SELECT internal FROM session_events WHERE event_id='ev_1'").fetchone()[0]
    assert internal == 1


def test_append_in_tx_rollback_keeps_nothing(tmp_path) -> None:
    store = EventStore(tmp_path / "e.db")
    with store.write_lock:
        store.append_in_tx(store.connection, "s1", "evidence", {"k": 1}, event_id="ev_x")
    store.connection.rollback()               # 调用方事务失败
    assert store.list_after("s1", 0) == []    # 未 COMMIT 不落库


def test_append_default_internal_zero(tmp_path) -> None:
    store = EventStore(tmp_path / "e.db")
    store.append("s1", "scene.patch", {"op": []})
    internal = store.connection.execute(
        "SELECT internal FROM session_events ORDER BY sequence").fetchone()[0]
    assert internal == 0
