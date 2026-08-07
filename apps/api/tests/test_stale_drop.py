import asyncio

import pytest

from app.ws import ws_session
from tests.ws_helpers import FakeWS, audio_end, audio_frame, audio_start, cancel_round, make_app


async def test_old_round_sends_nothing_after_cancel(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok", slow_delta_s=0.3)
    ws = FakeWS([
        audio_start("u1"), audio_frame(), audio_end("u1"),
        {"type": "sleep", "seconds": 0.4},   # u1 回合已启动并发出第一个 delta
        audio_start("u2"), audio_frame(), audio_end("u2"),
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.5)   # u1 逐句播放中 → u2 首帧到达取消 u1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    if st.round_task and not st.round_task.done():
        st.round_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await st.round_task

    msgs = [m for m in ws.sent if isinstance(m, dict)]
    assert "npc.speech.commit" not in [m.get("type") for m in msgs]   # u1 未 commit 即被打断
    assert "dialogue.turn.interrupted" in [e["event_type"] for e in events.list_after("sess-x", 0)]
