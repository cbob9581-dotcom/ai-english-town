import asyncio
import re

import pytest

from app.settings import Settings
from app.ws import SessionState, ws_session
from tests.ws_helpers import (FakeWS, audio_end, audio_frame, audio_start,
                              cancel_round, make_app)


class _FakeScene:
    generation_id = "gen_abcd1234"


def test_generation_id_property_falls_back_then_tracks_scene() -> None:
    st = SessionState(Settings())
    assert re.fullmatch(r"gen_[0-9a-f]{8}", st.generation_id)   # 无 scene → 回退
    st.scene = _FakeScene()
    assert st.generation_id == "gen_abcd1234"                   # 有 scene → 跟随


async def test_invalid_exit_id_does_not_transition(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    ws = FakeWS([
        {"type": "websocket.receive", "text": '{"type":"scene.request","exitId":"nowhere"}'},
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    assert st.scene is not None and st.scene.archetype_id == "plaza"   # 未知出口 → 不转场
    skels = [m for m in ws.sent if isinstance(m, dict) and m.get("type") == "scene.skeleton"]
    assert len(skels) == 1                                              # 仅进场那次 skeleton


async def test_transition_cancels_round_and_no_spurious_interrupted(tmp_path) -> None:
    # 转场取消进行中回合 → _cancel_work 清 active_turn_id/is_playing；
    # 之后到达的 playback.interrupted 不得再为已死旧回合写 dialogue.turn.interrupted
    # （否则会把旧 turnId 配新 generationId，违反门控矩阵 genId∧turnId 配对）。
    events, app = make_app(tmp_path, scenario="ok", slow_delta_s=0.3)
    ws = FakeWS([
        audio_start("u1"), audio_frame(), audio_end("u1"),
        {"type": "sleep", "seconds": 0.4},   # u1 回合启动并在逐句播放中（active_turn_id 已设）
        {"type": "websocket.receive", "text": '{"type":"scene.request","exitId":"left"}'},
        {"type": "sleep", "seconds": 0.2},   # 转场取消 u1、进场 bakery
        {"type": "websocket.receive", "text": '{"type":"playback.interrupted"}'},
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(1.2)   # 跑完整个脚本
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancel_round(app)

    st = app.state.sessions["sess-x"]
    assert st.scene is not None and st.scene.archetype_id == "bakery"   # 已转场
    assert st.active_turn_id is None                                    # 转场后旧回合状态已清
    evs = events.list_after("sess-x", 0)
    assert all(e["event_type"] != "dialogue.turn.interrupted" for e in evs)
