"""OpenAI 兼容 LLM 客户端（provider 无关）。DeepSeek deepseek-chat 为默认端点。
base_url/model/api_key 全部可配置；无 key 时 get_client 返回 mock。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI

from app.settings import Settings


class LLMConnectError(ConnectionError):
    """连接类错误（重试一次仍失败后抛）。"""


class JsonParseError(ValueError):
    """complete_json 的输出不是合法 JSON 对象。"""


@dataclass
class TextDelta:
    text: str = ""
    finish_reason: str | None = None
    usage: dict | None = None


@dataclass
class JsonResult:
    json: dict
    usage: dict | None
    finish_reason: str | None


class LLMAdapter(Protocol):
    async def stream_text(self, messages: list[dict], *, max_tokens: int,
                          temperature: float) -> AsyncIterator[TextDelta]: ...

    async def complete_json(self, messages: list[dict], *, max_tokens: int,
                            temperature: float) -> JsonResult: ...


class OpenAIClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        if settings.llm_model.startswith("deepseek-reasoner"):
            raise ValueError("deepseek-reasoner 不支持 JSON Output；请配置 llm_model=deepseek-chat")
        self._settings = settings
        self._http_client = http_client
        timeout = httpx.Timeout(settings.llm_connect_timeout_s, read=settings.llm_ttft_timeout_s)
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url, api_key=settings.llm_api_key,
            timeout=timeout, http_client=http_client, max_retries=0,
        )

    async def stream_text(self, messages: list[dict], *, max_tokens: int,
                          temperature: float) -> AsyncIterator[TextDelta]:
        s = self._settings
        stream = await self._create_stream(messages, max_tokens, temperature)
        async for chunk in stream:
            delta = TextDelta()
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                delta.text = chunk.choices[0].delta.content
            if chunk.choices:
                delta.finish_reason = chunk.choices[0].finish_reason
            if chunk.usage:
                delta.usage = chunk.usage.model_dump()
            yield delta

    async def _create_stream(self, messages, max_tokens, temperature):
        s = self._settings
        for attempt in (1, 2):
            try:
                return await self._client.chat.completions.create(
                    model=s.llm_model, messages=messages,
                    temperature=temperature, max_tokens=max_tokens,
                    stream=True, stream_options={"include_usage": True},
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError,
                    httpx.RemoteProtocolError, APIConnectionError,
                    APIStatusError) as e:
                if attempt == 2:
                    raise LLMConnectError(f"LLM connect failed: {e}") from e
        raise AssertionError("unreachable")

    async def complete_json(self, messages: list[dict], *, max_tokens: int,
                            temperature: float) -> JsonResult:
        s = self._settings
        resp = await self._client.chat.completions.create(
            model=s.llm_model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or ""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            raise JsonParseError(f"LLM returned non-JSON: {content[:80]!r}") from e
        if not isinstance(parsed, dict):
            raise JsonParseError(f"LLM returned non-object: {content[:80]!r}")
        return JsonResult(
            json=parsed,
            usage=resp.usage.model_dump() if resp.usage else None,
            finish_reason=resp.choices[0].finish_reason,
        )


def get_client(settings: Settings) -> LLMAdapter:
    # mock 在函数内导入：client ↔ mock 无模块级循环依赖
    from app.llm.mock import MockAdapter
    if not settings.llm_api_key:
        return MockAdapter(os.environ.get("MOCK_LLM_SCENARIO", "ok"))
    return OpenAIClient(settings)
