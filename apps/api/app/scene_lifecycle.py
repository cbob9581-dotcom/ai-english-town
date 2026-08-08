"""per-session 场景状态机：进场 → 本地骨架 → （Task 6）Director 填充 → filled/degraded。
generationId 每次进场递增；所有消息先写库（events.append）再 send。"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from app.llm.concepts import resolve_word_id  # noqa: F401  （保留：后续 gesture 展开用）


@dataclass
class SceneSession:
    scene_id: str
    generation_id: str
    archetype_id: str
    revision: int = 1
    status: str = "skeleton"          # skeleton | filled | degraded
    setting: dict = field(default_factory=dict)
    background: dict = field(default_factory=dict)
    entities: list = field(default_factory=list)
    characters: list = field(default_factory=list)
    exits: list = field(default_factory=list)
    default_npc_id: str | None = None


def scene_maps(scene: SceneSession) -> tuple[dict, dict]:
    """scene → (scene_words: wordId→name, entity_by_word_id: wordId→entityId)。"""
    scene_words: dict[str, str] = {}
    entity_by_word_id: dict[str, str] = {}
    for e in scene.entities:
        wid = e.get("semantics", {}).get("wordId")
        if wid:
            scene_words[wid] = e["semantics"]["name"]
            entity_by_word_id[wid] = e["id"]
    return scene_words, entity_by_word_id


def recent_scenes(events, session_id: str, limit: int = 5) -> list[str]:
    return [e["payload"].get("archetypeId") for e in events.list_after(session_id, 0)
            if e["event_type"] == "scene.entered"][-limit:]


async def enter_scene(app, events, state, session_id, send, *,
                      target_archetype_id: str | None, source: str) -> None:
    """切入目标场景：取消旧回合/填充 → 编译骨架 → 广播 skeleton → 触发填充（Task 6 前无填充）。"""
    scenes = app.state.scenes
    target = target_archetype_id or scenes.load_town_map()["start"]
    await _cancel_work(app, events, state, session_id)

    state.scene_seq += 1
    scene_id = f"scene_{target}_{state.scene_seq}"
    generation_id = f"gen_{uuid.uuid4().hex[:8]}"
    skeleton = scenes.compile_skeleton(target, scene_id=scene_id, seed=scene_id,
                                       generation_id=generation_id)
    default_npc = scenes.default_npc_id(target)
    scene = SceneSession(scene_id=scene_id, generation_id=generation_id, archetype_id=target,
                         revision=1, status="skeleton", setting=skeleton["setting"],
                         background=skeleton["background"], entities=skeleton["entities"],
                         characters=skeleton["characters"], exits=skeleton["exits"],
                         default_npc_id=default_npc)
    state.scene = scene
    state.arbitration.reset(default_npc)
    scene_words, entity_by_word_id = scene_maps(scene)
    state.actor = app.state.scene_factory(scene_words, entity_by_word_id, npc_id=default_npc)

    events.append(session_id, "scene.entered", {
        "sceneId": scene_id, "archetypeId": target, "generationId": generation_id,
        "revision": 1, "source": source,
    })
    await send({
        "type": "scene.skeleton", "sceneId": scene_id, "generationId": generation_id,
        "archetypeId": target, "revision": 1, "status": "skeleton",
        "setting": skeleton["setting"], "background": skeleton["background"],
        "entities": skeleton["entities"], "characters": skeleton["characters"],
        "exits": skeleton["exits"],
    })
    # 阶段 3 全链路在 Task 6 接 Director；在此之前场景停留骨架（完整可玩）。
    # 预取命中在 Task 7 从这里切走。


async def _cancel_work(app, events, state, session_id) -> None:
    """取消旧回合/填充任务；清 active_turn_id/is_playing（防虚假 interrupted）；清 pending companion。
    不写 interrupted（转场不是"打断回合"语义）；未 commit 回合的证据由 run_round 的
    CancelledError 分支补写 partial turn（voice_round.py:80-87）。"""
    if state.round_task and not state.round_task.done():
        state.round_task.cancel()
        try:
            await state.round_task
        except asyncio.CancelledError:
            pass
    state.active_turn_id = None
    state.is_playing = False
    if state.fill_task and not state.fill_task.done():
        state.fill_task.cancel()
        try:
            await state.fill_task
        except asyncio.CancelledError:
            pass
        state.fill_task = None
    for task in list(state.pending_asks.values()):
        task.cancel()
    state.pending_asks.clear()
