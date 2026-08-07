"""启动自检用的 LLM 探活：极短 completion。"""
from __future__ import annotations

import asyncio
import time

from app.llm.client import OpenAIClient
from app.settings import Settings


async def run(settings: Settings) -> dict:
    if not settings.llm_api_key:
        return {"ok": False, "scenario": "no_key", "model": settings.llm_model,
                "message": "DEEPSEEK_API_KEY 未设置，运行时将落 mock"}
    client = OpenAIClient(settings)
    t0 = time.perf_counter()
    try:
        ttft_ms = None
        async for delta in client.stream_text(
                [{"role": "user", "content": "hi"}],
                max_tokens=settings.llm_max_tokens_npc,
                temperature=settings.llm_temperature_npc):
            if ttft_ms is None and delta.text:
                ttft_ms = int((time.perf_counter() - t0) * 1000)
        return {"ok": True, "scenario": "ok", "model": settings.llm_model,
                "ttft_ms": ttft_ms, "latency_ms": int((time.perf_counter() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "scenario": "connect", "model": settings.llm_model,
                "error": str(e)[:300]}


async def main() -> None:
    import json
    print(json.dumps(await run(Settings.from_env()), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
