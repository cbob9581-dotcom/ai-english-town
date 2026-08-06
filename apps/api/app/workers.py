"""生产客户端：连接 asr-worker（WS）/ tts-worker（HTTP）。"""
from __future__ import annotations

import base64
import json

import httpx


async def asr_client(samples: bytes, url: str) -> dict:
    # 阶段 1 简化：一次性发送整段 → 等 final
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(url.replace("/ws/asr", "/transcribe"), json={"audio_base64": base64.b64encode(samples).decode()})
        resp.raise_for_status()
        return resp.json()


async def tts_client(text: str, base_url: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{base_url}/tts", json={"text": text, "voice": "af_bella"})
        resp.raise_for_status()
        return resp.json()
