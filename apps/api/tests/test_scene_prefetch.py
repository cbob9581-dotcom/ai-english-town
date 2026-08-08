import asyncio
import time

import pytest

from app.llm.mock import MockSceneDirector
from app.scene_prefetch import ScenePrefetchCache
from app.ws import ws_session
from tests.ws_helpers import FakeWS, make_app


def test_cache_hit_and_miss() -> None:
    c = ScenePrefetchCache(ttl_s=60)
    assert c.get("bakery") is None
    c.put("bakery", {"fills": []})
    assert c.get("bakery") == {"fills": []}


def test_cache_ttl_expiry() -> None:
    c = ScenePrefetchCache(ttl_s=0.01)
    c.put("bakery", {"fills": []})
    time.sleep(0.02)
    assert c.get("bakery") is None


def test_cache_lru_eviction() -> None:
    c = ScenePrefetchCache(maxsize=2)
    c.put("a", {1}); c.put("b", {2}); c.put("c", {3})
    assert c.get("a") is None and c.get("b") == {2} and c.get("c") == {3}


async def test_spoke_entry_prefetches_back_and_next_enter_is_cached(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok", scene_director=MockSceneDirector("ok"))
    ws = FakeWS([
        {"type": "websocket.receive", "text": '{"type":"scene.request","exitId":"left"}'},   # plaza→bakery
        {"type": "sleep", "seconds": 0.5},
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.7)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # bakery 进场 → 其唯一出口指向 plaza → plaza 提案被预取缓存
    assert app.state.prefetch.get("plaza") is not None


async def test_hint_prefetches_and_later_enter_uses_cache_without_new_director_call(tmp_path) -> None:
    calls_before = {"n": 0}
    class CountingDirector(MockSceneDirector):
        async def propose(self, **kw):
            # 只计 bakery（本场景）：connect 进场 plaza 的 fill 也走 director，
            # 会把它计入（n=2），断言 n==1 就 FAIL。
            if kw.get("archetype_id") == "bakery":
                calls_before["n"] += 1
            return await super().propose(**kw)

    events, app = make_app(tmp_path, scenario="ok", scene_director=CountingDirector("ok"))
    ws = FakeWS([
        {"type": "websocket.receive", "text": '{"type":"scene.hint","exitId":"left"}'},   # plaza hover bakery
        {"type": "sleep", "seconds": 0.4},   # 让 hint 预取先完成（bakery 提案已入缓存）
        {"type": "websocket.receive", "text": '{"type":"scene.request","exitId":"left"}'},   # plaza→bakery 进场
        {"type": "sleep", "seconds": 0.5},   # 进场 bakery → fill_scene 命中缓存 → 应用 patch
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert app.state.prefetch.get("bakery") is not None
    assert calls_before["n"] == 1   # hint 一次调用已缓存；进场命中缓存 → 无新增 Director 调用
    st = app.state.sessions["sess-x"]
    assert st.scene is not None and st.scene.archetype_id == "bakery"   # 已转场进 bakery
    assert st.scene.status == "filled"                                   # 缓存命中路径正常应用了 patch
    patches = [e["payload"] for e in events.list_after("sess-x", 0) if e["event_type"] == "scene.patch"]
    assert any(p["sceneId"].startswith("scene_bakery_") for p in patches)  # bakery 有 scene.patch 事件


async def test_bad_cached_proposal_falls_back_to_director_and_invalidates(tmp_path) -> None:
    # 缓存里是坏提案（unknown slotId → validate_proposal 抛 ProposalError、整 plan 拒绝）：
    # _apply_proposal 必须先 invalidate 再回退 Director，否则 fill_scene 重读同一坏提案 → 无限递归。
    calls_before = {"n": 0}
    class CountingDirector(MockSceneDirector):
        async def propose(self, **kw):
            if kw.get("archetype_id") == "bakery":
                calls_before["n"] += 1
            return await super().propose(**kw)

    events, app = make_app(tmp_path, scenario="ok", scene_director=CountingDirector("ok"))
    app.state.prefetch.put("bakery", {
        "fills": [{"slotId": "no.such", "conceptId": "concept.ghost"}],
        "characters": [], "setting": {"displayName": "Bad", "time": "morning"},
    })
    ws = FakeWS([
        {"type": "websocket.receive", "text": '{"type":"scene.request","exitId":"left"}'},   # plaza→bakery
        {"type": "sleep", "seconds": 0.5},
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.7)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    assert st.scene is not None and st.scene.archetype_id == "bakery"
    assert st.scene.status == "filled"                                        # 回退 Director 成功填充
    assert calls_before["n"] == 1                                             # 回退那次 bakery 调用（坏提案不再重试）
    cached = app.state.prefetch.get("bakery")
    assert cached is not None and cached["setting"]["displayName"] == "Bakery Scene"  # 坏提案已被 cleaned 替换
    patches = [e["payload"] for e in events.list_after("sess-x", 0) if e["event_type"] == "scene.patch"]
    assert any(p["sceneId"].startswith("scene_bakery_") for p in patches)
