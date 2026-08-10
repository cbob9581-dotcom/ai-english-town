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
                            temperature: float,
                            read_timeout_s: float | None = None) -> JsonResult: ...

    async def aclose(self) -> None: ...


class OpenAIClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        if settings.llm_model.startswith("deepseek-reasoner"):
            raise ValueError("deepseek-reasoner 不支持 JSON Output；请配置 llm_model=deepseek-chat")
        self._settings = settings
        # connect=建立连接；read=TTFT 守卫——仅对流式（stream_text）按块成立。
        # 非流式（complete_json）整个响应体一次读完，read=ttft(2.0) 对真实 LLM 必然超时
        # （完整 JSON 响应实测 ~3-6s）→ 调用方把角色总预算作为 read 超时传入（read_timeout_s，
        # 见 complete_json/_json_create）；真正的墙钟预算仍由调用方 asyncio.timeout 强制。
        # httpx 0.28 已移除 total 子超时。以 httpx 默认 5.0 为底，仅覆盖 connect/read——
        # write/pool 保持默认，不继承 connect(1.5s)。
        timeout = httpx.Timeout(
            5.0,
            connect=settings.llm_connect_timeout_s,
            read=settings.llm_ttft_timeout_s,
        )
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url, api_key=settings.llm_api_key,
            timeout=timeout, http_client=http_client, max_retries=0,
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def stream_text(self, messages: list[dict], *, max_tokens: int,
                          temperature: float) -> AsyncIterator[TextDelta]:
        stream = await self._retry_connect(self._stream_create, messages, max_tokens, temperature)
        async for chunk in stream:
            delta = TextDelta()
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                delta.text = chunk.choices[0].delta.content
            if chunk.choices:
                delta.finish_reason = chunk.choices[0].finish_reason
            if chunk.usage:
                delta.usage = chunk.usage.model_dump()
            yield delta

    async def _stream_create(self, messages, max_tokens, temperature):
        s = self._settings
        return await self._client.chat.completions.create(
            model=s.llm_model, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
            stream=True, stream_options={"include_usage": True},
        )

    async def complete_json(self, messages: list[dict], *, max_tokens: int,
                            temperature: float,
                            read_timeout_s: float | None = None) -> JsonResult:
        """非流式 JSON 完成。read_timeout_s：非流式响应体一次读完，read 超时须覆盖整段 body
        （默认 ttft 2.0s 只适合流式按块守卫，真实完整 JSON ~3-6s 必然超时）；
        调用方把角色总预算传入，None → 用客户端默认（仅测试/流式路径）。"""
        resp = await self._retry_connect(self._json_create, messages, max_tokens, temperature,
                                         read_timeout_s)
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

    async def _json_create(self, messages, max_tokens, temperature, read_timeout_s=None):
        s = self._settings
        kwargs: dict = dict(
            model=s.llm_model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, response_format={"type": "json_object"},
        )
        if read_timeout_s is not None:
            # 非流式响应体是一次性读：read 超时须覆盖整段 body（流式的 TTFT 按块守卫不适用）。
            kwargs["timeout"] = httpx.Timeout(
                5.0, connect=s.llm_connect_timeout_s, read=read_timeout_s)
        return await self._client.chat.completions.create(**kwargs)

    async def _retry_connect(self, call, messages, max_tokens, temperature, read_timeout_s=None):
        """连接错误 / 瞬时 5xx 重试一次后抛 LLMConnectError；4xx 业务失败不重试、原样上抛。"""
        for attempt in (1, 2):
            try:
                if read_timeout_s is None:  # 流式调用无 read_timeout_s（按块守卫即可）
                    return await call(messages, max_tokens, temperature)
                return await call(messages, max_tokens, temperature, read_timeout_s)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError,
                    httpx.RemoteProtocolError, APIConnectionError) as e:
                if attempt == 2:
                    raise LLMConnectError(f"LLM connect failed: {e}") from e
            except APIStatusError as e:
                if e.status_code < 500:
                    raise  # 业务失败（401/403/404/422/429 等）不重试，作为 API 状态错误原样上抛
                if attempt == 2:
                    raise LLMConnectError(f"LLM connect failed: {e}") from e
        raise AssertionError("unreachable")


def get_client(settings: Settings) -> LLMAdapter:
    # mock 在函数内导入：client ↔ mock 无模块级循环依赖
    from app.llm.mock import MockAdapter
    if not settings.llm_api_key:
        return MockAdapter(os.environ.get("MOCK_LLM_SCENARIO", "ok"))
    return OpenAIClient(settings)
