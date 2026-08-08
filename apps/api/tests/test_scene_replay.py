import asyncio
import json

import pytest

from app.event_store import EventStore
from app.scene_lifecycle import rebuild_from_events
from app.ws import ws_session
from tests.ws_helpers import FakeWS, make_app


async def test_reconnect_replays_previous_scene(tmp_path) -> None:
    """先进入 bakery 并完成 filled；断开重建同一场景。"""
    events, app = make_app(tmp_path, scenario="ok")
    ws1 = FakeWS([
        {"type": "websocket.receive", "text": '{"type":"scene.request","exitId":"left"}'},
        {"type": "sleep", "seconds": 0.5},
    ], app)
    t1 = asyncio.create_task(ws_session(ws1))
    await asyncio.sleep(0.6)
    t1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t1
    st1 = app.state.sessions["sess-x"]
    assert st1.scene and st1.scene.archetype_id == "bakery"
    gen = st1.scene.generation_id

    # 模拟进程重启：清空内存会话状态（事件库保留）
    app.state.sessions.pop("sess-x")
    ws2 = FakeWS([{"type": "sleep", "seconds": 0.1}], app)
    t2 = asyncio.create_task(ws_session(ws2))
    await asyncio.sleep(0.2)
    t2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t2

    st2 = app.state.sessions["sess-x"]
    assert st2.scene is not None and st2.scene.archetype_id == "bakery"
    assert st2.scene.generation_id == gen          # 同一 generationId（重放，不重新分配）
    skels = [m for m in ws2.sent if isinstance(m, dict) and m.get("type") == "scene.skeleton"]
    assert len(skels) == 1 and skels[0]["sceneId"] == st1.scene.scene_id


def test_rebuild_from_events_without_entered_returns_false(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    events.append("s", "dialogue.turn", {"turnId": "t1", "npcText": "hi", "userText": "x", "audioBytes": 1})
    class _App:  # 只跑 events 投影路径，不需完整 app
        pass
    app = _App()
    app.state = type("S", (), {})()
    # 同步 def（返回 bool）：brief 的 asyncio.run(...) 包装与 Step 3 的同步签名冲突
    # （asyncio.run 要求协程），改直接同步调用，断言意图不变。
    ok = rebuild_from_events(app, events, None, "s", None)
    assert ok is False
