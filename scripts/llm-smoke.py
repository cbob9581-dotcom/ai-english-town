"""LLM golden 测量：真 key 跑 20 次 NPC 流式 + 20 次 Tutor JSON，
记录 TTFT / 总延迟 / token 分布 → tests/fixtures/llm-golden/。"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.llm.client import get_client  # noqa: E402
from app.settings import Settings  # noqa: E402

RUNS = 20


async def sample_npc(client, settings) -> dict:
    t0 = time.perf_counter()
    ttft_ms = None
    tokens = None
    async for delta in client.stream_text(
            [{"role": "user", "content": json.dumps({"transcript": "hello", "recent_turns": [], "scene": "bakery"})}],
            max_tokens=settings.llm_max_tokens_npc, temperature=settings.llm_temperature_npc):
        if ttft_ms is None and delta.text:
            ttft_ms = int((time.perf_counter() - t0) * 1000)
        if delta.usage:
            tokens = delta.usage
    return {"ttft_ms": ttft_ms, "latency_ms": int((time.perf_counter() - t0) * 1000),
            "completion_tokens": (tokens or {}).get("completion_tokens")}


async def sample_tutor(client, settings) -> dict:
    t0 = time.perf_counter()
    res = await client.complete_json(
        [{"role": "user", "content": json.dumps({"word": "loaf"})}],
        max_tokens=settings.llm_max_tokens_tutor, temperature=settings.llm_temperature_tutor)
    return {"latency_ms": int((time.perf_counter() - t0) * 1000),
            "completion_tokens": (res.usage or {}).get("completion_tokens")}


def summarize(samples: list[dict], key: str) -> dict:
    vals = sorted(s for s in samples if s.get(key) is not None)
    if not vals:
        return {}
    return {"min": vals[0], "p50": statistics.median(vals),
            "p95": vals[int(len(vals) * 0.95) - 1], "max": vals[-1], "n": len(vals)}


async def main() -> None:
    settings = Settings.from_env()
    if not settings.llm_api_key:
        raise SystemExit("llm-smoke 需要 DEEPSEEK_API_KEY（真 key），无 key 用 mock 无意义")
    client = get_client(settings)
    npc = [await sample_npc(client, settings) for _ in range(RUNS)]
    tutor = [await sample_tutor(client, settings) for _ in range(RUNS)]
    today = date.today().isoformat()
    out_dir = ROOT / "tests" / "fixtures" / "llm-golden"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"npc-{today}.json").write_text(
        json.dumps({"meta": {"model": settings.llm_model, "runs": RUNS}, "samples": npc}, indent=2),
        encoding="utf-8")
    (out_dir / f"tutor-{today}.json").write_text(
        json.dumps({"meta": {"model": settings.llm_model, "runs": RUNS}, "samples": tutor}, indent=2),
        encoding="utf-8")
    print(json.dumps({
        "npc": {"ttft_ms": summarize(npc, "ttft_ms"), "latency_ms": summarize(npc, "latency_ms")},
        "tutor": {"latency_ms": summarize(tutor, "latency_ms")},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
