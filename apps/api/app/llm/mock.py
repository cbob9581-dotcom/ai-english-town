"""确定性 mock LLM。离线测试 + 故障注入（MOCK_LLM_SCENARIO）。
scenario 枚举：ok | timeout | connect_error | status_error | invalid_json
             | bad_word_id | missing_word | too_long | truncated | empty
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import httpx
from openai import APIStatusError

from app.llm.client import JsonParseError, JsonResult, LLMConnectError, TextDelta

SCENARIOS = frozenset({
    "ok", "timeout", "connect_error", "status_error", "invalid_json",
    "bad_word_id", "missing_word", "too_long", "truncated", "empty",
})


def _make_status_error() -> APIStatusError:
    """构造一个 4xx APIStatusError（与真实客户端 _retry_connect 原样上抛的形状一致）。"""
    return APIStatusError(
        "mock 4xx",
        response=httpx.Response(401, request=httpx.Request("POST", "http://mock")),
        body=None,
    )


class MockAdapter:
    def __init__(self, scenario: str = "ok", stream_text_override: str | None = None) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown mock scenario: {scenario}")
        self.scenario = scenario
        self.stream_text_override = stream_text_override

    async def aclose(self) -> None:
        return None

    async def stream_text(self, messages: list[dict], *, max_tokens: int,
                          temperature: float) -> AsyncIterator[TextDelta]:
        if self.scenario == "timeout":
            await asyncio.sleep(60)  # 外层 total timeout 会取消它（Task 5 测降级）
            return
        if self.scenario == "connect_error":
            raise LLMConnectError("mock connect error")
        if self.scenario == "status_error":
            raise _make_status_error()
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
                            temperature: float,
                            read_timeout_s: float | None = None) -> JsonResult:
        if self.scenario == "timeout":
            await asyncio.sleep(60)
        if self.scenario == "connect_error":
            raise LLMConnectError("mock connect error")
        if self.scenario == "status_error":
            raise _make_status_error()
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


MOCK_SCENE_SCENARIO = frozenset({
    "ok", "timeout", "connect_error", "invalid_json", "slot_mismatch",
    "unknown_word", "too_many_entities", "partial_fills", "duplicate_slot",
})


class MockSceneDirector:
    """确定性 mock：无 key 时的 SceneDirector。故障注入见 MOCK_SCENE_SCENARIO。"""

    def __init__(self, scenario: str = "ok") -> None:
        if scenario not in MOCK_SCENE_SCENARIO:
            raise ValueError(f"unknown mock scene scenario: {scenario}")
        self.scenario = scenario

    async def propose(self, *, archetype_id: str, archetype: dict, catalog,
                      recent_scenes: list[str], world_summary: dict | None = None,
                      attempt: str = "enter", user_intent: str | None = None) -> dict:
        # MODE FEATURE：接收但忽略 user_intent —— mock 不按意图填充，只做确定性故障注入。
        if self.scenario == "timeout":
            await asyncio.sleep(60)                       # 外层 director timeout 取消它
        if self.scenario == "connect_error":
            raise LLMConnectError("mock scene connect error")
        if self.scenario == "invalid_json":
            raise JsonParseError("mock scene invalid json")
        slots = archetype["propSlots"]
        npc_slots = archetype.get("npcSlots", [])
        first_concepts = {cat: catalog.concepts_in(cat)[0].concept_id
                          for s in slots for cat in s["categories"] if catalog.concepts_in(cat)}

        def _fill(slot_id: str) -> dict:
            s = next(x for x in slots if x["slotId"] == slot_id)
            cat = next(c for c in s["categories"] if c in first_concepts)
            return {"slotId": slot_id, "conceptId": first_concepts[cat]}

        if self.scenario == "slot_mismatch":
            return {"fills": [{"slotId": "no.such.slot", "conceptId": "x"}],
                    "characters": [], "setting": {"displayName": "Broken", "time": "morning"}}
        if self.scenario == "unknown_word":
            fills = [_fill(slots[0]["slotId"])] + [{"slotId": slots[-1]["slotId"], "conceptId": "concept.ghost"}]
            return {"fills": fills, "characters": [], "setting": {"displayName": "Ghost", "time": "morning"}}
        if self.scenario == "duplicate_slot":
            return {"fills": [_fill(slots[0]["slotId"]), _fill(slots[0]["slotId"])],
                    "characters": [], "setting": {"displayName": "Dup", "time": "morning"}}
        if self.scenario == "too_many_entities":
            return {"fills": [_fill(slots[0]["slotId"]) for _ in range(45)],
                    "characters": [], "setting": {"displayName": "Many", "time": "morning"}}
        if self.scenario == "partial_fills":
            fills = [_fill(slots[0]["slotId"])] if slots else []
            return {"fills": fills, "characters": [], "setting": {"displayName": "Partial", "time": "morning"}}

        fills = [_fill(s["slotId"]) for s in slots if any(c in first_concepts for c in s["categories"])]
        chars = [{"slotId": s["slotId"], "npcId": catalog.npcs_in(s["role"])[0].npc_id} for s in npc_slots]
        return {"fills": fills, "characters": chars,
                "setting": {"displayName": f"{archetype_id.title()} Scene", "time": "morning"}}
