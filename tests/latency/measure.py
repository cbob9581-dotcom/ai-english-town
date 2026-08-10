"""端到端语音延迟（服务端链路）：VAD 结束 → ASR final → 回复 → TTS 完成。
浏览器采集与网络不包含（阶段 1 近似）。输出 P50/P95。"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

import httpx  # noqa: E402

ASR = "http://127.0.0.1:8001/transcribe"
TTS = "http://127.0.0.1:8002/tts"
AUDIO = bytes(1600 * 20)  # 1s @16k 静音（占位；真实测试用录音 WAV）


async def one_round(client: httpx.AsyncClient) -> float:
    t0 = time.perf_counter()
    r = await client.post(ASR, json={"audio_base64": __import__("base64").b64encode(AUDIO).decode()})
    text = r.json().get("finalText", "")
    t1 = time.perf_counter()
    r2 = await client.post(TTS, json={"text": text or "Hello.", "voice": "af_bella"})
    t2 = time.perf_counter()
    return (t1 - t0) * 1000 + (t2 - t1) * 1000


async def main() -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(ASR, json={"audio_base64": __import__("base64").b64encode(AUDIO).decode()})  # 预热
        await client.post(TTS, json={"text": "warmup", "voice": "af_bella"})
        samples = [await one_round(client) for _ in range(30)]
    samples.sort()
    p50 = statistics.median(samples)
    p95 = samples[int(len(samples) * 0.95) - 1]
    print(json.dumps({"p50_ms": round(p50, 1), "p95_ms": round(p95, 1), "n": len(samples)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
