"""SceneDirector：把原型 + 候选概念目录 → 提案 ScenePlan。
persona/wordId 永不由本模块产生；只选 conceptId/npcId 与 setting（ASCII ≤24）。

MODE FEATURE: 新增 user_intent —— 用户在进场前用自然语言表达的需求/期望
（例如 "I want to practice ordering coffee" 或 "focus on words from my list"）。
Free Mode 下这就是唯一的方向信号；Goal-Oriented Mode 下它与 FSRS 选出的目标词并存，
用来影响 Director 在候选范围内怎么挑，而不是绕过候选范围（候选仍然是唯一合法来源，
proposals.py 的校验边界不变——user_intent 只影响"选哪个"，不产生新候选）。"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Protocol

from openai import APIStatusError

from app.learning.memory import format_world_summary
from app.llm.client import JsonParseError, LLMAdapter, LLMConnectError
from app.settings import Settings

_MAX_USER_INTENT_CHARS = 200

_DIRECTOR_SYSTEM = (
    "You are a scene director for an English-learning town. A scene template has slots; "
    "you choose what to place from the provided candidates. "
    "If a userIntent field is present, treat it as the learner's stated goal for this scene "
    "and prefer candidates that serve it — but you may ONLY choose from the given candidates, "
    "never invent new ones, even if userIntent asks for something not in the candidate lists. "
    "Reply with ONLY a JSON object of this exact shape: "
    '{"fills":[{"slotId":"...","conceptId":"..."}],"characters":[{"slotId":"...","npcId":"..."}],'
    '"setting":{"displayName":"...","time":"..."}}. '
    "Pick each fill conceptId ONLY from that slot's candidates. Pick each character npcId ONLY from that role's candidates. "
    "setting.displayName must be ≤24 ASCII characters. setting.time must be one of morning, afternoon, evening. "
    "No newlines, no URLs, no code."
)


def _sanitize_user_intent(user_intent: str | None) -> str | None:
    """裁剪 + 去换行；不做 ASCII 强制（自由文本，非 LLM 输出，proposals.py 的
    ASCII 校验只管 LLM 的 setting.displayName，这里不适用）。空/纯空白 → None。"""
    if not user_intent:
        return None
    cleaned = " ".join(user_intent.split())[:_MAX_USER_INTENT_CHARS]
    return cleaned or None


class SceneDirector(Protocol):
    async def propose(self, *, archetype_id: str, archetype: dict, catalog,
                      recent_scenes: list[str], world_summary: dict | None = None,
                      attempt: str = "enter", user_intent: str | None = None) -> dict: ...


class LlmSceneDirector:
    def __init__(self, client: LLMAdapter, settings: Settings, llm_log) -> None:
        self._client = client
        self._settings = settings
        self._llm_log = llm_log

    def _build_messages(self, archetype: dict, catalog, recent_scenes: list[str],
                        world_summary: dict | None = None,
                        user_intent: str | None = None) -> list[dict]:
        slots = []
        for s in archetype["propSlots"]:
            slots.append({"slotId": s["slotId"], "zone": s["zone"],
                          "candidates": [{"conceptId": c.concept_id, "name": c.name}
                                         for cat in s["categories"] for c in catalog.concepts_in(cat)]})
        npc_slots = []
        for s in archetype.get("npcSlots", []):
            npc_slots.append({"slotId": s["slotId"], "role": s["role"],
                              "candidates": [{"npcId": n.npc_id, "name": n.name} for n in catalog.npcs_in(s["role"])]})
        payload = {"archetypeId": archetype["archetypeId"],
                   "displayName": archetype["displayName"],
                   "propSlots": slots, "npcSlots": npc_slots,
                   "recentScenes": recent_scenes}
        block = format_world_summary(world_summary)
        if block:
            payload["worldSummary"] = block
        intent = _sanitize_user_intent(user_intent)
        if intent:
            payload["userIntent"] = intent
        return [
            {"role": "system", "content": _DIRECTOR_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    async def propose(self, *, archetype_id: str, archetype: dict, catalog,
                      recent_scenes: list[str], world_summary: dict | None = None,
                      attempt: str = "enter", user_intent: str | None = None) -> dict:
        t0 = time.perf_counter()
        messages = self._build_messages(archetype, catalog, recent_scenes, world_summary, user_intent)
        try:
            async with asyncio.timeout(self._settings.llm_total_timeout_director_s):
                res = await self._client.complete_json(
                    messages, max_tokens=self._settings.llm_max_tokens_director,
                    temperature=self._settings.llm_temperature_director,
                    read_timeout_s=self._settings.llm_total_timeout_director_s)
            self._llm_log.record(
                session_id="", generation_id="", role="scene_director",
                model=self._settings.llm_model, attempt=attempt,
                prompt_tokens=(res.usage or {}).get("prompt_tokens"),
                completion_tokens=(res.usage or {}).get("completion_tokens"),
                latency_ms=int((time.perf_counter() - t0) * 1000), ttft_ms=int((time.perf_counter() - t0) * 1000),
                finish_reason="stop", ok=True, fallback_reason="none")
            return res.json
        except TimeoutError:
            self._record_failure(t0, attempt, "timeout", "director timeout")
            raise
        except LLMConnectError as e:
            self._record_failure(t0, attempt, "connect", str(e))
            raise
        except APIStatusError as e:
            self._record_failure(t0, attempt, "connect", str(e))
            raise
        except JsonParseError as e:
            self._record_failure(t0, attempt, "invalid_json", str(e))
            raise

    def _record_failure(self, t0: float, attempt: str, reason: str, error: str) -> None:
        self._llm_log.record(session_id="", generation_id="", role="scene_director",
                             model=self._settings.llm_model, attempt=attempt,
                             latency_ms=int((time.perf_counter() - t0) * 1000),
                             finish_reason=None, fallback_reason=reason, ok=False, error=error)


def get_scene_director(settings: Settings, client: LLMAdapter, llm_log) -> SceneDirector:
    if settings.llm_api_key:
        return LlmSceneDirector(client, settings, llm_log)
    from app.llm.mock import MockSceneDirector
    import os
    return MockSceneDirector(os.environ.get("MOCK_SCENE_SCENARIO", "ok"))
