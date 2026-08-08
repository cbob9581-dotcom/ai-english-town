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
    # 连接即进场 plaza（scene.entered）+ 填充（scene.patch）；无活跃回合时，显式打断不追加 dialogue 事件
    evs = [e["event_type"] for e in events.list_after("sess-x", 0)]
    assert "scene.entered" in evs
    assert not any(t.startswith("dialogue") for t in evs)


async def test_spurious_audio_start_without_frames_ignored(tmp_path) -> None:
    # u1 回合逐句播放中，u2 的 audio.start（barge_in_armed=True）到达但窗口期内无任何帧 →
    # spurious 守卫触发并清掉 armed 旗标；绝不打断 u1，也不写 interrupted。
    events, app = make_app(tmp_path, scenario="ok", slow_delta_s=0.3,
                           stream_text_override="Hi there. ")
    ws = FakeWS([
        audio_start("u1"), audio_frame(), audio_end("u1"),
        {"type": "sleep", "seconds": 0.15},   # u1 回合启动，首句 delta 尚未发出（回合仍在跑）
        audio_start("u2"),                    # 播放中 stray start → barge_in_armed=True，无帧、无 audio.end
        {"type": "sleep", "seconds": 1.0},    # u2 窗口期过 → 守卫触发；u1 继续播放并完成
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0)   # 让 ws_session 初始化 session state
    st = app.state.sessions["sess-x"]
    st.spurious_window_s = 0.02
    await asyncio.sleep(1.2)   # 守卫已触发 + u1 回合完成
    # 守卫确实触发了：窗口期后 stray start 的 armed 旗标被清空（无 audio.end、无帧去清）
    assert st.audio_start_armed is False
    assert st.barge_in_armed is False
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancel_round(app)

    types = [e["event_type"] for e in events.list_after("sess-x", 0)]
    assert "dialogue.turn" in types                  # u1 正常完成
    assert "dialogue.turn.interrupted" not in types  # 守卫不打断、不记事件
    assert "npc.speech.commit" in _sent_types(ws)    # u1 的 commit 确实发出（回合未被取消）


async def test_round_failure_then_interrupt_writes_no_interrupted(tmp_path) -> None:
    # 回合以非 CancelledError 失败（ASR 网络错误）→ _run_round 发 round.error 并清 active_turn_id；
    # 之后到达的 playback.interrupted 不得再为这个从未 commit 的回合写 dialogue.turn.interrupted。
    async def failing_asr(samples: bytes):
        raise RuntimeError("asr worker network error")

    events, app = make_app(tmp_path)
    app.state.asr_client = failing_asr
    ws = FakeWS([
        audio_start("u1"), audio_frame(), audio_end("u1"),
        {"type": "sleep", "seconds": 0.2},   # u1 回合任务启动即失败（round.error + 清 active_turn_id）
        {"type": "websocket.receive",
         "text": json.dumps({"type": "playback.interrupted"})},
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.6)   # 跑完整个脚本
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancel_round(app)

    err = [m for m in ws.sent if isinstance(m, dict) and m["type"] == "round.error"]
    assert len(err) == 1
    assert err[0]["turnId"]  # round.error 仍携带失败回合的 turnId（在清 active_turn_id 前捕获）
    assert app.state.sessions["sess-x"].active_turn_id is None  # 失败后 active_turn_id 已被清

    evs = events.list_after("sess-x", 0)
    assert all(e["event_type"] != "dialogue.turn.interrupted" for e in evs)


async def test_interrupted_played_ms_is_per_turn(tmp_path) -> None:
    # 第 1 回合完整播放（累计 played_ms=90），第 2 回合首句后被打断 →
    # interrupted 的 playedMs 只反映第 2 回合已播放的 ms（30），而非会话累计（120）。
    events, app = make_app(tmp_path, scenario="ok", slow_delta_s=0.3)
    ws = FakeWS([
        audio_start("u1"), audio_frame(), audio_end("u1"),
        {"type": "sleep", "seconds": 1.2},   # u1 完整跑完（3 句 × slow_delta 0.3 → done ≈0.91）
        audio_start("u2"), audio_frame(), audio_end("u2"),   # 第 2 回合启动
        {"type": "sleep", "seconds": 0.4},   # u2 首句 delta（≈0.3s 后）已发出并计 ms
        audio_start("u3"), audio_frame(),    # 播放中 → 打断 u2（无 audio.end，不启动新回合）
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(1.8)   # 打完整个脚本
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancel_round(app)

    evs = events.list_after("sess-x", 0)
    intr = [e for e in evs if e["event_type"] == "dialogue.turn.interrupted"]
    assert len(intr) == 1
    # 仅本回合（u2）已播放 1 句的 TTS ms（fake_tts ms=30）；u1 累计的 90ms 不计入
    assert intr[0]["payload"]["playedMs"] == 30
