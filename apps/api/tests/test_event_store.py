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
