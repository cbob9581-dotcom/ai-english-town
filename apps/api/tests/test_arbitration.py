import asyncio
import json

import pytest

from app.arbitration import ArbitrationState
from app.ws import ws_session
from tests.ws_helpers import FakeWS, make_app


def test_reset_to_scene_default() -> None:
    a = ArbitrationState()
    a.set_focus("npc_rosa", "user_click")
    a.reset("npc_tom")
    assert a.active_speaker == "npc:npc_tom"
    assert a.conversation_focus == "npc:npc_tom"
    assert a.pending_speakers == []


def test_set_focus_switches_and_sets_expiry() -> None:
    a = ArbitrationState()
    speaker = a.set_focus("npc_rosa", "user_click")
    assert speaker == "npc:npc_rosa"
    assert a.focus_source == "user_click"
    assert a.focus_expires_ms is not None


async def test_npc_focus_switches_active_speaker_and_broadcasts(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    ws = FakeWS([{"type": "sleep", "seconds": 0.05}], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.1)
    st = app.state.sessions["sess-x"]
    assert st.scene is not None
    gen = st.scene.generation_id
    sid = st.scene.scene_id
    # 直接注入 focus 消息（FakeWS 已消费 sleep，追加一条新消息需要重建 ws 会话）
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    ws2 = FakeWS([
        {"type": "websocket.receive", "text": json.dumps({"type": "npc.focus", "sceneId": sid,
                                                          "generationId": gen, "characterId": "npc_tom"})},
    ], app)
    t2 = asyncio.create_task(ws_session(ws2))
    await asyncio.sleep(0.1)
    t2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t2
    st2 = app.state.sessions["sess-x"]
    assert st2.arbitration.active_speaker == "npc:npc_tom"
    assert st2.actor is not None
    focus_msgs = [m for m in ws2.sent if isinstance(m, dict) and m.get("type") == "scene.focus"]
    assert focus_msgs and focus_msgs[-1]["activeSpeaker"] == "npc:npc_tom"


async def test_stale_generation_focus_ignored(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    ws = FakeWS([
        {"type": "websocket.receive", "text": '{"type":"npc.focus","sceneId":"old","generationId":"gen_old","characterId":"npc_tom"}'},
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    # 场景默认即 plaza → Tom；旧 gen 的 npc.focus 必须整体忽略：不切 focus_source、不设过期、
    # 不广播 scene.focus（若被处理，focus_source 会变 user_click 且 focus_expires_ms 被设值）
    assert st.arbitration.focus_source == "scene_default"
    assert st.arbitration.focus_expires_ms is None
    assert not any(isinstance(m, dict) and m.get("type") == "scene.focus" for m in ws.sent)
