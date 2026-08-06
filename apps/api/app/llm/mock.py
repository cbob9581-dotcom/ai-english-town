"""确定性 mock LLM。离线测试 + 故障注入（MOCK_LLM_SCENARIO）。
scenario 枚举：ok | timeout | connect_error | invalid_json | bad_word_id
             | missing_word | too_long | truncated | empty
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from app.llm.client import JsonParseError, JsonResult, LLMConnectError, TextDelta

SCENARIOS = frozenset({
    "ok", "timeout", "connect_error", "invalid_json", "bad_word_id",
    "missing_word", "too_long", "truncated", "empty",
})


class MockAdapter:
    def __init__(self, scenario: str = "ok", stream_text_override: str | None = None) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown mock scenario: {scenario}")
        self.scenario = scenario
        self.stream_text_override = stream_text_override

    async def stream_text(self, messages: list[dict], *, max_tokens: int,
                          temperature: float) -> AsyncIterator[TextDelta]:
        if self.scenario == "timeout":
            await asyncio.sleep(60)  # 外层 total timeout 会取消它（Task 5 测降级）
            return
        if self.scenario == "connect_error":
            raise LLMConnectError("mock connect error")
        if self.scenario == "too_long":
            # 单个超长 token：chunker 不断词，validate_speech 判超长 → 降级
            yield TextDelta(text="A" * 250)
            return
        if self.scenario == "truncated":
            yield TextDelta(text="Hello! Welcome to the bakery. ")
            yield TextDelta(text="Would you like a", finish_reason="length")
            return
        if self.scenario == "empty":
            return  # 无任何 delta、无 finish_reason
        # 逐词吐流：句子切分交给 chunker（纯函数单测已覆盖），mock 不重复实现断句
        text = self.stream_text_override or "Hello! Welcome to the bakery. Can I help you? "
        for word in text.split():
            yield TextDelta(text=word + " ")
        yield TextDelta(finish_reason="stop", usage={"prompt_tokens": 40, "completion_tokens": 9})

    async def complete_json(self, messages: list[dict], *, max_tokens: int,
                            temperature: float) -> JsonResult:
        if self.scenario == "timeout":
            await asyncio.sleep(60)
        if self.scenario == "connect_error":
            raise LLMConnectError("mock connect error")
        if self.scenario == "invalid_json":
            raise JsonParseError("mock invalid json")
        word = self._word_from_messages(messages)
        if self.scenario == "bad_word_id":
            return JsonResult(json={"word": "wrongword", "scaffold": "A wrong word."},
                              usage=None, finish_reason="stop")
        if self.scenario == "missing_word":
            return JsonResult(json={"word": word, "scaffold": "A baked good you can buy."},
                              usage=None, finish_reason="stop")
        if self.scenario == "too_long":
            return JsonResult(json={"word": word, "scaffold": "word " * 40},
                              usage=None, finish_reason="stop")
        return JsonResult(json={"word": word, "scaffold": f"A {word} is a thing you can see here. Say it: {word}."},
                          usage={"prompt_tokens": 3, "completion_tokens": 6}, finish_reason="stop")

    def _word_from_messages(self, messages: list[dict]) -> str:
        for m in messages:
            if m.get("role") == "user":
                try:
                    return json.loads(m["content"]).get("word", "loaf")
                except (json.JSONDecodeError, TypeError, AttributeError):
                    continue
        return "loaf"
