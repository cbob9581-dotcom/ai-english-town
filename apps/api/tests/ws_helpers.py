"""WS 级测试工具：FakeWS（可控消息源）+ make_app（注入 mock asr/tts/llm）。"""
from __future__ import annotations

import asyncio
import base64
import json

from app.event_store import EventStore
from app.llm.mock import MockAdapter
from app.main import create_app
from app.settings import Settings


class FakeWS:
    """最小 WebSocket 替身：脚本化 receive + 记录 send。receive 消费完挂起。
    支持 `{"type": "sleep", "seconds": N}` 标记消息：让 ws_session 让出事件循环
    （回合任务才能真正启动/推进），用于打断类测试。"""

    def __init__(self, messages: list[dict], app, session_id: str = "sess-x") -> None:
        self._in = list(messages)
        self.app = app
        self.path_params = {"session_id": session_id}
        self.sent: list = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict:
        while self._in:
            msg = self._in.pop(0)
            if isinstance(msg, dict) and msg.get("type") == "sleep":
                await asyncio.sleep(msg.get("seconds", 0.1))
                continue
            return msg
        await asyncio.sleep(999)  # 挂起直到被取消

    async def send_json(self, payload) -> None:
        self.sent.append(payload)

    async def send_bytes(self, payload) -> None:
        self.sent.append(payload)


def audio_start(utterance_id: str) -> dict:
    return {"type": "websocket.receive",
            "text": json.dumps({"type": "audio.start", "utteranceId": utterance_id})}


def audio_end(utterance_id: str) -> dict:
    return {"type": "websocket.receive",
            "text": json.dumps({"type": "audio.end", "utteranceId": utterance_id})}


def audio_frame() -> dict:
    return {"type": "websocket.receive", "bytes": b"\x00\x00" * 400}


def make_app(tmp_path, scenario: str = "ok", slow_delta_s: float = 0.0,
             stream_text_override: str | None = None):
    events = EventStore(tmp_path / "e.db")

    class SlowActor:
        def __init__(self, inner):
            self._inner = inner

        async def stream_reply(self, **kw):
            async for m in self._inner.stream_reply(**kw):
                if m["type"] == "npc.speech.delta" and slow_delta_s:
                    await asyncio.sleep(slow_delta_s)
                yield m

    async def fake_asr(samples: bytes):
        return {"finalText": "hello", "segments": [], "language": "en", "confidence": -0.3}

    async def fake_tts(text: str):
        return {"audioBase64": base64.b64encode(b"\x00\x00\x00\x00").decode(), "ms": 30, "sampleRate": 16000}

    # tutor 缓存目录指向 tmp_path，避免测试把 wav 落进仓库 data/tutor-audio（hermetic）
    app = create_app(events, Settings(tutor_cache_dir=tmp_path / "tutor-audio"),
                     asr_client=fake_asr, tts_client=fake_tts,
                     llm_client=MockAdapter(scenario, stream_text_override=stream_text_override))
    app.state.actor = SlowActor(app.state.actor)
    return events, app


def cancel_round(app, session_id: str = "sess-x") -> None:
    st = app.state.sessions.get(session_id)
    if st and st.round_task and not st.round_task.done():
        st.round_task.cancel()
