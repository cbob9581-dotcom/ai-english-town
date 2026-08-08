"""WS 级学习证据：回合证据（record_round）经 ws 流写入 learning 表。
learning 只注入本测试自己的 app 实例（不改 make_app 全局）——否则审计测试也会带
learning、companion.ask 会写 spontaneous 表、Task 15 前全红。"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.learning.engine import LearningEngine
from app.learning.store import LearningStore
from app.ws import ws_session
from tests.ws_helpers import FakeWS, audio_end, audio_frame, audio_start, make_app


async def test_round_writes_evidence(tmp_path) -> None:
    events, app = make_app(tmp_path, asr_text="a loaf please")
    store = LearningStore(events.connection)
    app.state.learning = LearningEngine(store, events, app.state.settings)
    store.import_words("local", "l1", "x", [{"lemma": "loaf", "pos": "n", "sense": "1",
                                              "scene_tags": json.dumps(["plaza"]), "carrier": "phrase",
                                              "slot_categories": "[]", "source": "quest",
                                              "created_at": "2026-08-08T00:00:00+00:00"}])
    ws = FakeWS([audio_start("u1"), audio_frame(), audio_end("u1")], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.6)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    if st.round_task and not st.round_task.done():
        st.round_task.cancel()
    evs = store.evidence_for_word("local", "word_loaf_n_1")
    assert len(evs) == 1                      # spontaneous_production success（fake_asr 说了 loaf，NPC 未必教）
    m = store.get_mastery("local", "word_loaf_n_1")
    assert m["productive_score"] > 0.0        # exp(-0.3)=0.74 ≥ 0.6 → success 计分
    rows = events.list_after("sess-x", 0)
    assert any(e["event_type"] == "evidence" and e["payload"].get("internal") for e in rows)
