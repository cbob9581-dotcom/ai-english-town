"""SceneDirector：把原型 + 候选概念目录 → 提案 ScenePlan。
persona/wordId 永不由本模块产生；只选 conceptId/npcId 与 setting（ASCII ≤24）。"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Protocol

from openai import APIStatusError

from app.learning.memory import format_world_summary
from app.llm.client import JsonParseError, LLMAdapter, LLMConnectError
from app.settings import Settings

_DIRECTOR_SYSTEM = (
    "You are a scene director for an English-learning town. A scene template has slots; "
    "you choose what to place from the provided candidates. "
    "Reply with ONLY a JSON object of this exact shape: "
    '{"fills":[{"slotId":"...","conceptId":"..."}],"characters":[{"slotId":"...","npcId":"..."}],'
    '"setting":{"displayName":"...","time":"..."}}. '
    "Pick each fill conceptId ONLY from that slot's candidates. Pick each character npcId ONLY from that role's candidates. "
    "setting.displayName must be ≤24 ASCII characters. setting.time must be one of morning, afternoon, evening. "
    "No newlines, no URLs, no code."
)


class SceneDirector(Protocol):
    async def propose(self, *, archetype_id: str, archetype: dict, catalog,
                      recent_scenes: list[str], world_summary: dict | None = None,
                      attempt: str = "enter") -> dict: ...


class LlmSceneDirector:
    def __init__(self, client: LLMAdapter, settings: Settings, llm_log) -> None:
        self._client = client
        self._settings = settings
        self._llm_log = llm_log

    def _build_messages(self, archetype: dict, catalog, recent_scenes: list[str],
                        world_summary: dict | None = None) -> list[dict]:
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
        return [
            {"role": "system", "content": _DIRECTOR_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    async def propose(self, *, archetype_id: str, archetype: dict, catalog,
                      recent_scenes: list[str], world_summary: dict | None = None,
                      attempt: str = "enter") -> dict:
        t0 = time.perf_counter()
        messages = self._build_messages(archetype, catalog, recent_scenes, world_summary)
        try:
            async with asyncio.timeout(self._settings.llm_total_timeout_director_s):
                res = await self._client.complete_json(
                    messages, max_tokens=self._settings.llm_max_tokens_director,
                    temperature=self._settings.llm_temperature_director)
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
