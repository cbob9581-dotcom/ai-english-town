import asyncio
import json

import pytest

from app.ws import ws_session
from tests.ws_helpers import FakeWS, make_app


def _companion_ask(entity_id: str) -> dict:
    return {"type": "websocket.receive",
            "text": json.dumps({"type": "companion.ask", "entityId": entity_id})}


async def test_companion_ask_returns_reply_and_audio(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    ws = FakeWS([_companion_ask("loaf-1")], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.4)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    replies = [m for m in ws.sent if isinstance(m, dict) and m.get("type") == "companion.reply"]
    assert len(replies) == 1
    assert replies[0]["word"] == "loaf"
    assert "loaf" in replies[0]["scaffold"]
    assert replies[0]["degraded"] is False
    # 音频消息（start + bytes + end）都在
    types = [m["type"] for m in ws.sent if isinstance(m, dict)]
    assert types.count("tts.audio.start") == 1 and types.count("tts.audio.end") == 1
    assert any(isinstance(m, bytes) for m in ws.sent)
    # 缓存已写入
    assert app.state.tutor_cache.get("word_loaf_n_1") is not None


async def test_companion_ask_unknown_entity(tmp_path) -> None:
    events, app = make_app(tmp_path)
    ws = FakeWS([_companion_ask("no-such-entity")], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0)   # 让 ws_session 处理 companion.ask
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    replies = [m for m in ws.sent if isinstance(m, dict) and m.get("type") == "companion.reply"]
    assert replies and replies[0]["error"] == "unknown_entity"


async def test_same_entity_inflight_merged(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    ws = FakeWS([_companion_ask("loaf-1"), _companion_ask("loaf-1")], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    replies = [m for m in ws.sent if isinstance(m, dict) and m.get("type") == "companion.reply"]
    assert len(replies) == 1          # 连点同一实体只回一次（复用同一结果）
    rows = app.state.llm_log.recent("sess-x")
    assert len([r for r in rows if r["role"] == "companion_tutor"]) == 1
