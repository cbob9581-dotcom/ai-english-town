"""写实断言：任何合法操作只允许 3 张表变化（session_events / llm_calls / tutor_cache）。
对未来新增表（如 mastery_states）越权写入会立刻失败。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import pytest

from app.event_store import EventStore
from app.ws import ws_session
from tests.ws_helpers import FakeWS, audio_end, audio_frame, audio_start, make_app

ALLOWED_TABLES = {"session_events", "llm_calls", "tutor_cache"}


def _snapshot(events: EventStore) -> dict[str, tuple[int, str]]:
    """所有表 count + 行级 checksum。"""
    snap: dict[str, tuple[int, str]] = {}
    tables = {r[0] for r in events.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    for table in sorted(tables):
        count = events.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        digest = hashlib.sha1()
        for row in events.connection.execute(f"SELECT * FROM {table}"):
            digest.update(repr(row).encode())
        snap[table] = (int(count), digest.hexdigest())
    return snap


def _changed(before: dict, after: dict) -> set[str]:
    return {t for t in before if before[t] != after.get(t)}


async def test_full_round_only_touches_allowed_tables(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    before = _snapshot(events)
    ws = FakeWS([
        audio_start("u1"), audio_frame(), audio_end("u1"),
        {"type": "websocket.receive", "text": json.dumps({"type": "companion.ask", "entityId": "loaf-1"})},
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.6)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    if st.round_task and not st.round_task.done():
        st.round_task.cancel()

    changed = _changed(before, _snapshot(events))
    assert changed <= ALLOWED_TABLES, f"越权写入了表: {changed - ALLOWED_TABLES}"
    assert changed == ALLOWED_TABLES   # 一个合法回合确实动了这三张（含 tutor 缓存）
