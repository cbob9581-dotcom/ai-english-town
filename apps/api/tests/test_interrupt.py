import asyncio
import json

import pytest

from app.ws import ws_session
from tests.ws_helpers import FakeWS, audio_end, audio_frame, audio_start, cancel_round, make_app


def _sent_types(ws: FakeWS) -> list[str]:
    return [m["type"] for m in ws.sent if isinstance(m, dict)]


async def test_barge_in_cancels_round_and_appends_interrupted(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok", slow_delta_s=0.3)
    ws = FakeWS([
        audio_start("u1"), audio_frame(), audio_end("u1"),
        {"type": "sleep", "seconds": 0.4},   # 让 u1 回合真正跑起来（发出第一个 delta、set active_turn_id）
        audio_start("u2"), audio_frame(), audio_end("u2"),
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.8)   # u1 回合已在逐句播放中，u2 首帧已打断
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancel_round(app)

    evs = events.list_after("sess-x", 0)
    types = [e["event_type"] for e in evs]
    assert "dialogue.turn.interrupted" in types
    intr = [e for e in evs if e["event_type"] == "dialogue.turn.interrupted"][0]
    assert intr["payload"]["turnId"]
    assert intr["payload"]["playedMs"] >= 0
    assert intr["payload"]["generationId"] == app.state.sessions["sess-x"].generation_id


async def test_explicit_interrupt_without_turn_writes_nothing(tmp_path) -> None:
    events, app = make_app(tmp_path)
    ws = FakeWS([{"type": "websocket.receive",
                  "text": json.dumps({"type": "playback.interrupted"})}], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0)   # 让 ws_session 真正处理 playback.interrupted
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events.list_after("sess-x", 0) == []


async def test_spurious_audio_start_without_frames_ignored(tmp_path) -> None:
    # 1 句短回复 + slow_delta：u1 回合在 sleep 标记内跑完并 commit
    events, app = make_app(tmp_path, scenario="ok", slow_delta_s=0.3,
                           stream_text_override="Hi there. ")
    ws = FakeWS([
        audio_start("u1"), audio_frame(), audio_end("u1"),
        {"type": "sleep", "seconds": 0.5},   # u1 回合完成（delta + commit）
        audio_start("u2"), audio_end("u2"),  # u2 无任何帧
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0)   # 让 ws_session 初始化 session state
    app.state.sessions["sess-x"].spurious_window_s = 0.02
    await asyncio.sleep(0.8)   # u2 的 start 因无帧被 spurious 守卫忽略
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancel_round(app)

    types = [e["event_type"] for e in events.list_after("sess-x", 0)]
    assert "dialogue.turn" in types       # u1 正常完成
    assert "dialogue.turn.interrupted" not in types
