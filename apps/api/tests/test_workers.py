import asyncio

import pytest

from app.workers import tts_client


async def test_tts_client_calls_endpoint(tmp_path, monkeypatch) -> None:
    async def fake_post(self, url, json):
        class R:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"audioBase64": "AA==", "ms": 5, "sampleRate": 16000}
        return R()
    monkeypatch.setattr("app.workers.httpx.AsyncClient.post", fake_post)
    result = await tts_client("hello", base_url="http://127.0.0.1:8002")
    assert result["sampleRate"] == 16000
