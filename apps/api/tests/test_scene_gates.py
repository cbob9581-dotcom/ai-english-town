import asyncio
import re

import pytest

from app.settings import Settings
from app.ws import SessionState, ws_session
from tests.ws_helpers import FakeWS, make_app


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
