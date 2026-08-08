import asyncio

import pytest

from app.event_store import EventStore
from app.llm.mock import MockSceneDirector
from app.llm.proposals import ProposalError
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


def _sent_types(ws) -> list[str]:
    return [m.get("type") for m in ws.sent if isinstance(m, dict)]


async def test_fill_task_applies_patch_and_sets_filled(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok", scene_director=MockSceneDirector("ok"))
    ws = FakeWS([{"type": "sleep", "seconds": 0.3}], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.4)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    assert st.scene is not None and st.scene.status == "filled"
    types = _sent_types(ws)
    assert types.count("scene.skeleton") == 1
    assert "scene.patch" in types
    assert "scene.degraded" not in types
    patch_events = [e["payload"] for e in events.list_after("sess-x", 0) if e["event_type"] == "scene.patch"]
    assert patch_events and patch_events[-1]["ops"]


async def test_director_timeout_degrades_to_skeleton(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok", scene_director=MockSceneDirector("timeout"))
    ws = FakeWS([{"type": "sleep", "seconds": 0.15}], app)
    # 用极短 director timeout 让 fill 快速失败降级
    app.state.settings = app.state.settings.__class__(**{
        **app.state.settings.__dict__,
        "llm_total_timeout_director_s": 0.05,
    })
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    assert st.scene is not None and st.scene.status == "degraded"
    assert "scene.degraded" in _sent_types(ws)
    # 骨架仍完整可玩
    assert any(e["component"] == "npc" for e in st.scene.entities)
    # F1：精确降级原因 —— DB 事件与 sent 消息的 reason/fallbackReason 都是 timeout
    degraded_events = [e["payload"] for e in events.list_after("sess-x", 0)
                       if e["event_type"] == "scene.degraded"]
    assert degraded_events and degraded_events[-1]["reason"] == "timeout"
    assert degraded_events[-1]["fallbackReason"] == "timeout"
    sent = [m for m in ws.sent if isinstance(m, dict) and m["type"] == "scene.degraded"]
    assert sent and sent[-1]["reason"] == "timeout" and sent[-1]["fallbackReason"] == "timeout"


async def test_director_connect_error_degrades_reason_connect(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok", scene_director=MockSceneDirector("connect_error"))
    ws = FakeWS([{"type": "sleep", "seconds": 0.15}], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    assert st.scene is not None and st.scene.status == "degraded"
    assert "scene.degraded" in _sent_types(ws)
    degraded_events = [e["payload"] for e in events.list_after("sess-x", 0)
                       if e["event_type"] == "scene.degraded"]
    assert degraded_events and degraded_events[-1]["reason"] == "connect"
    assert degraded_events[-1]["fallbackReason"] == "connect"


async def test_scene_transition_cancels_stale_fill(tmp_path) -> None:
    """转场中途旧 fill 晚到 → 不应用（sceneId 不匹配被丢弃）。"""
    events, app = make_app(tmp_path, scenario="ok", scene_director=MockSceneDirector("timeout"))
    app.state.settings = app.state.settings.__class__(**{
        **app.state.settings.__dict__, "llm_total_timeout_director_s": 0.3,
    })
    ws = FakeWS([
        {"type": "sleep", "seconds": 0.05},
        {"type": "websocket.receive", "text": '{"type":"scene.request","exitId":"left"}'},
        {"type": "sleep", "seconds": 0.5},
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.8)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    assert st.scene is not None and st.scene.archetype_id == "bakery"
