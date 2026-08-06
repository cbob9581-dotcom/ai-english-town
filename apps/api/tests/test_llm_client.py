import json

import httpx
import pytest

from app.llm.client import JsonParseError, LLMConnectError, OpenAIClient, get_client
from app.llm.mock import MockAdapter
from app.settings import Settings


def _sse_stream(parts: list[str]) -> bytes:
    """把一段文本按字符切成分块 SSE 响应（OpenAI 流式格式）。"""
    chunks = []
    for i, ch in enumerate(parts):
        payload = json.dumps({"id": "x", "object": "chat.completion.chunk", "model": "deepseek-chat",
                              "choices": [{"index": 0, "delta": {"content": ch}, "finish_reason": None}]})
        chunks.append("data: " + payload + "\n\n")
    final = json.dumps({"id": "x", "object": "chat.completion.chunk", "model": "deepseek-chat",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
    chunks.append("data: " + final + "\n\n")
    chunks.append("data: [DONE]\n\n")
    return "".join(chunks).encode()


def _mock_openai_client(handler) -> OpenAIClient:
    transport = httpx.MockTransport(handler)
    settings = Settings(llm_api_key="sk-test")
    return OpenAIClient(settings, http_client=httpx.AsyncClient(transport=transport))


async def test_factory_mock_without_key() -> None:
    assert isinstance(get_client(Settings(llm_api_key="")), MockAdapter)


async def test_factory_openai_with_key() -> None:
    assert isinstance(get_client(Settings(llm_api_key="sk-test")), OpenAIClient)


async def test_reasoner_rejected() -> None:
    with pytest.raises(ValueError, match="reasoner"):
        OpenAIClient(Settings(llm_api_key="sk-test", llm_model="deepseek-reasoner"))


async def test_stream_text_request_shape_and_parse() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse_stream(["Hello! ", "Welcome to the bakery."]),
                              headers={"content-type": "text/event-stream"})

    client = _mock_openai_client(handler)
    deltas = [d async for d in client.stream_text(
        [{"role": "user", "content": "hi"}], max_tokens=320, temperature=0.8)]
    assert captured["url"].endswith("/chat/completions")
    body = captured["body"]
    assert body["model"] == "deepseek-chat"
    assert body["stream"] is True
    assert body["temperature"] == 0.8
    assert body["max_tokens"] == 320
    assert body["stream_options"] == {"include_usage": True}
    assert "".join(d.text for d in deltas) == "Hello! Welcome to the bakery."
    assert deltas[-1].finish_reason == "stop"
    assert deltas[-1].usage["completion_tokens"] == 2


async def test_stream_connect_error_retries_once_then_raises() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, text="unavailable")

    client = _mock_openai_client(handler)
    with pytest.raises(LLMConnectError):
        async for _ in client.stream_text([{"role": "user", "content": "hi"}],
                                          max_tokens=320, temperature=0.8):
            pass
    assert len(calls) == 2  # 连接错误只重试一次


async def test_complete_json_parses_object() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "x", "object": "chat.completion",
                                         "model": "deepseek-chat",
                                         "choices": [{"index": 0, "message": {"role": "assistant",
                                                                              "content": '{"word": "loaf"}'},
                                                      "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 3, "completion_tokens": 2}})

    client = _mock_openai_client(handler)
    res = await client.complete_json([{"role": "user", "content": "x"}], max_tokens=320, temperature=0.3)
    assert res.json == {"word": "loaf"}
    assert res.finish_reason == "stop"


async def test_complete_json_bad_json_raises_parse_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "x", "object": "chat.completion",
                                         "model": "deepseek-chat",
                                         "choices": [{"index": 0, "message": {"role": "assistant",
                                                                              "content": "not json"},
                                                      "finish_reason": "stop"}],
                                         "usage": None})

    client = _mock_openai_client(handler)
    with pytest.raises(JsonParseError):
        await client.complete_json([{"role": "user", "content": "x"}], max_tokens=320, temperature=0.3)


async def test_mock_ok_streams_sentences() -> None:
    m = MockAdapter("ok")
    deltas = [d async for d in m.stream_text([], max_tokens=320, temperature=0.8)]
    assert "".join(d.text for d in deltas) == "Hello! Welcome to the bakery. Can I help you? "
    assert deltas[-1].finish_reason == "stop"
    assert deltas[-1].usage["completion_tokens"] > 0


async def test_mock_ok_complete_json_echoes_word() -> None:
    m = MockAdapter("ok")
    res = await m.complete_json(
        [{"role": "user", "content": json.dumps({"word": "apple", "scene": "bakery"})}],
        max_tokens=320, temperature=0.3)
    assert res.json["word"] == "apple"
    assert "apple" in res.json["scaffold"]


async def test_mock_connect_error_raises() -> None:
    m = MockAdapter("connect_error")
    with pytest.raises(LLMConnectError):
        async for _ in m.stream_text([], max_tokens=320, temperature=0.8):
            pass


async def test_mock_invalid_json_raises_parse_error() -> None:
    m = MockAdapter("invalid_json")
    with pytest.raises(JsonParseError):
        await m.complete_json([], max_tokens=320, temperature=0.3)


async def test_mock_bad_scenario_rejected() -> None:
    with pytest.raises(ValueError):
        MockAdapter("nope")
