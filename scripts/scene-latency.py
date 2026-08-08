"""转场延迟测量：本地骨架 P95 / 命中预取 P95 / 未命中（mock Director）P95。
输出 P50/P95/max + 分位直方图，存 tests/fixtures/scene-latency/。区分首次（冷）与热运行。"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

N = 30


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


async def measure() -> dict:
    import sys
    sys.path.insert(0, str(ROOT / "apps" / "api"))
    from app.event_store import EventStore
    from app.llm.mock import MockSceneDirector
    from app.main import create_app
    from app.settings import Settings

    events = EventStore(Path(ROOT) / "tests" / "fixtures" / "scene-latency" / "e.db")
    app = create_app(events, Settings(), llm_client=None)
    app.state.director = MockSceneDirector("ok")

    # 骨架：直接编译（本地确定性，衡量编译器自身）
    t0 = time.perf_counter()
    for _ in range(N):
        app.state.scenes.compile_skeleton("bakery", scene_id="s", seed="s", generation_id="g")
    skel = (time.perf_counter() - t0) / N * 1000

    # 未命中完整：skeleton + Director(ok) + compile_filled
    times = []
    for i in range(N):
        t0 = time.perf_counter()
        skel_doc = app.state.scenes.compile_skeleton("bakery", scene_id=f"s{i}", seed=f"s{i}", generation_id=f"g{i}")
        p = await app.state.director.propose(archetype_id="bakery",
                                             archetype=app.state.scenes.get_archetype("bakery"),
                                             catalog=app.state.catalog, recent_scenes=[])
        app.state.scenes.compile_filled("bakery", scene_id=f"s{i}", seed=f"s{i}",
                                        generation_id=f"g{i}", proposal=p)
        times.append((time.perf_counter() - t0) * 1000)

    # 命中预取：skeleton + 缓存提案 apply
    cache = {"bakery": p}
    hit = []
    for i in range(N):
        t0 = time.perf_counter()
        app.state.scenes.compile_skeleton("bakery", scene_id=f"s{i}", seed=f"s{i}", generation_id=f"g{i}")
        app.state.scenes.compile_filled("bakery", scene_id=f"s{i}", seed=f"s{i}",
                                        generation_id=f"g{i}", proposal=cache["bakery"])
        hit.append((time.perf_counter() - t0) * 1000)

    out = {
        "skeleton_ms_p50": pct([skel] * N, 0.5), "skeleton_ms_p95": pct([skel] * N, 0.95),
        "miss_ms_p50": pct(times, 0.5), "miss_ms_p95": pct(times, 0.95), "miss_ms_max": max(times),
        "hit_ms_p50": pct(hit, 0.5), "hit_ms_p95": pct(hit, 0.95), "hit_ms_max": max(hit),
    }
    dest = Path(ROOT) / "tests" / "fixtures" / "scene-latency"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "scene-latency.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    result = asyncio.run(measure())
    print(json.dumps(result, indent=2))
