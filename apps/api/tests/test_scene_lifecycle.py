import asyncio

import pytest

from app.event_store import EventStore
from app.ws import ws_session
from tests.ws_helpers import FakeWS, make_app


def _skeleton_msg(ws) -> dict | None:
    return next((m for m in ws.sent if isinstance(m, dict) and m.get("type") == "scene.skeleton"), None)


async def test_connect_enters_plaza_and_sends_playable_skeleton(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    ws = FakeWS([], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    msg = _skeleton_msg(ws)
    assert msg is not None
    assert msg["archetypeId"] == "plaza"
    assert msg["status"] == "skeleton"
    assert any(e["component"] == "npc" for e in msg["entities"])          # 默认 NPC 可点
    assert len(msg["exits"]) == 4
    st = app.state.sessions["sess-x"]
    assert st.scene is not None and st.scene.archetype_id == "plaza"
    assert st.actor is not None
    entered = [e["payload"] for e in events.list_after("sess-x", 0) if e["event_type"] == "scene.entered"]
    assert entered[-1]["archetypeId"] == "plaza"


async def test_scene_request_transitions_and_increments_generation(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    ws = FakeWS([
        {"type": "websocket.receive", "text": '{"type":"scene.request","exitId":"left"}'},
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    st = app.state.sessions["sess-x"]
    assert st.scene is not None and st.scene.archetype_id == "bakery"     # plaza.left → bakery
    skels = [m for m in ws.sent if isinstance(m, dict) and m.get("type") == "scene.skeleton"]
    assert len(skels) == 2
    gens = {m["generationId"] for m in skels}
    assert len(gens) == 2                                                  # 每进场 generationId 递增
