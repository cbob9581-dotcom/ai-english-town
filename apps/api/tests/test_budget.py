import asyncio

import pytest

from app.scripted_npc import reply as scripted_reply
from app.ws import ws_session
from tests.ws_helpers import FakeWS, audio_end, audio_frame, audio_start, cancel_round, make_app


async def test_call_cap_forces_scripted(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    # 先把 session 的 llm_calls 写满 cap
    cap = app.state.settings.llm_session_call_cap
    for _ in range(cap):
        app.state.llm_log.record(session_id="sess-x", role="npc_actor", model="deepseek-chat")

    ws = FakeWS([audio_start("u1"), audio_frame(), audio_end("u1")], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancel_round(app)

    # recent() 按 id 升序返回最旧 limit 行（plan 定义）；budget 行是 cap 之后的最新行，
    # 需 limit=cap+1 才覆盖到（llm_log.py 为 closed 模块，测试侧放大 limit 而非改实现）
    rows = app.state.llm_log.recent("sess-x", limit=cap + 1)
    assert any(r["fallback_reason"] == "budget" and r["ok"] == 0 for r in rows)
    # dialogue.turn 的 npcText == scripted 兜底（对同一 userText 的 scripted_npc 输出）
    turns = [e["payload"] for e in events.list_after("sess-x", 0) if e["event_type"] == "dialogue.turn"]
    assert turns and turns[-1]["npcText"] == scripted_reply("hello")["speech"]
