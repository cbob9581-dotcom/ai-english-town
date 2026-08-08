"""per-session 场景状态机：进场 → 本地骨架 → （Task 6）Director 填充 → filled/degraded。
generationId 每次进场递增；所有消息先写库（events.append）再 send。"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.llm.client import APIStatusError, JsonParseError, LLMConnectError
from app.llm.proposals import ProposalError, validate_proposal


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
    state.target_word_ids = set()
    state.scene_words = scene_words
    learning = getattr(app.state, "learning", None)
    if learning:
        try:
            chosen = learning.pick_scene_words(target, scenes.get_archetype(target),
                                               datetime.now(timezone.utc))
            state.target_word_ids = set(chosen)
            state.scene_words = {**scene_words, **chosen}   # 选词补充无场景实体的词
        except Exception:  # noqa: BLE001 —— 选词失败退化为无目标词（不杀进场）
            pass
    state.actor = app.state.scene_factory(state.scene_words, entity_by_word_id, npc_id=default_npc)

    events.append(session_id, "scene.entered", {
        "sceneId": scene_id, "archetypeId": target, "generationId": generation_id,
        "revision": 1, "source": source,
    })
    mem = getattr(app.state, "memory", None)
    if mem is not None:
        try:
            with events.write_lock:
                mem.apply_memory_updates(events.connection, events, "local",
                                         scene_enter=target, now=datetime.now(timezone.utc))
                events.connection.commit()
        except Exception:  # noqa: BLE001 —— 记忆失败不杀进场
            pass
    await send({
        "type": "scene.skeleton", "sceneId": scene_id, "generationId": generation_id,
        "archetypeId": target, "revision": 1, "status": "skeleton",
        "setting": skeleton["setting"], "background": skeleton["background"],
        "entities": skeleton["entities"], "characters": skeleton["characters"],
        "exits": skeleton["exits"],
    })
    state.fill_task = asyncio.create_task(
        fill_scene(app, events, state, session_id, send, scene_id=scene_id,
                   seed=scene_id, generation_id=generation_id, skeleton=skeleton))


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


_FALLBACK_REASON_UNKNOWN = "unknown_error"


async def fill_scene(app, events, state, session_id, send, *,
                     scene_id: str, seed: str, generation_id: str, skeleton: dict) -> None:
    """Director 填充任务：提案 → 校验 → 展开 → diff → scene.patch；失败 → degraded。"""
    scenes = app.state.scenes
    archetype_id = state.scene.archetype_id
    try:
        calls = app.state.llm_log.count_session_calls(session_id)
        if calls >= app.state.settings.llm_session_call_cap:
            await _degrade(app, events, state, session_id, send, scene_id, generation_id, "budget")
            return
        cached = app.state.prefetch.get(archetype_id)
        if cached is not None:
            await _apply_proposal(app, events, state, session_id, send, scene_id=scene_id,
                                  seed=seed, generation_id=generation_id, skeleton=skeleton,
                                  proposal=cached)
            return
        async with state.semaphore:
            # Director 总超时（含 Mock timeout 场景）：由下方 except TimeoutError 接 → _degrade
            async with asyncio.timeout(app.state.settings.llm_total_timeout_director_s):
                proposal = await app.state.director.propose(
                    archetype_id=archetype_id,
                    archetype=scenes.get_archetype(archetype_id),
                    catalog=app.state.catalog,
                    recent_scenes=recent_scenes(events, session_id),
                    attempt="enter")
        cleaned, _warnings = validate_proposal(proposal, scenes.get_archetype(archetype_id), app.state.catalog)
        app.state.prefetch.put(archetype_id, cleaned)      # 供下次访问（进入即命中）
        filled = scenes.compile_filled(archetype_id, scene_id=scene_id, seed=seed,
                                       generation_id=generation_id, proposal=cleaned)
        ops = scenes.diff_scenes(skeleton, filled)
        if state.scene is None or state.scene.scene_id != scene_id:
            return  # 填充期间已转场 → 丢弃
        state.scene.status = "filled"
        state.scene.setting = filled["setting"]
        state.scene.entities = filled["entities"]
        state.scene.characters = filled["characters"]
        state.scene.exits = filled["exits"]
        state.scene.revision += 1
        patch_id = f"patch_{uuid.uuid4().hex[:8]}"
        events.append(session_id, "scene.patch", {
            "sceneId": scene_id, "generationId": generation_id,
            "baseRevision": state.scene.revision - 1, "patchId": patch_id, "ops": ops,
        })
        if ops:
            await send({"type": "scene.patch", "sceneId": scene_id, "generationId": generation_id,
                        "baseRevision": state.scene.revision - 1, "patchId": patch_id, "ops": ops})
    except TimeoutError:
        await _degrade(app, events, state, session_id, send, scene_id, generation_id, "timeout")
    except (LLMConnectError, APIStatusError):
        await _degrade(app, events, state, session_id, send, scene_id, generation_id, "connect")
    except JsonParseError:
        await _degrade(app, events, state, session_id, send, scene_id, generation_id, "invalid_json")
    except ProposalError:
        await _degrade(app, events, state, session_id, send, scene_id, generation_id, "schema_reject")
    except Exception:  # noqa: BLE001 —— 填充失败不杀连接，骨架停留
        await _degrade(app, events, state, session_id, send, scene_id, generation_id, _FALLBACK_REASON_UNKNOWN)


async def _degrade(app, events, state, session_id, send, scene_id, generation_id, reason: str) -> None:
    if state.scene is None or state.scene.scene_id != scene_id:
        return
    state.scene.status = "degraded"
    events.append(session_id, "scene.degraded", {
        "sceneId": scene_id, "generationId": generation_id, "reason": reason, "fallbackReason": reason,
    })
    await send({"type": "scene.degraded", "sceneId": scene_id,
                "generationId": generation_id, "reason": reason, "fallbackReason": reason})


async def _apply_proposal(app, events, state, session_id, send, *,
                          scene_id, seed, generation_id, skeleton, proposal) -> None:
    """预取命中的提案直接应用（无 Director 调用、无 llm_calls 记录）。"""
    scenes = app.state.scenes
    archetype_id = state.scene.archetype_id
    try:
        cleaned, _warnings = validate_proposal(proposal, scenes.get_archetype(archetype_id), app.state.catalog)
        filled = scenes.compile_filled(archetype_id, scene_id=scene_id, seed=seed,
                                       generation_id=generation_id, proposal=cleaned)
        ops = scenes.diff_scenes(skeleton, filled)
        if state.scene is None or state.scene.scene_id != scene_id:
            return
        state.scene.status = "filled"
        state.scene.setting = filled["setting"]
        state.scene.entities = filled["entities"]
        state.scene.characters = filled["characters"]
        state.scene.exits = filled["exits"]
        state.scene.revision += 1
        patch_id = f"patch_{uuid.uuid4().hex[:8]}"
        events.append(session_id, "scene.patch", {
            "sceneId": scene_id, "generationId": generation_id,
            "baseRevision": state.scene.revision - 1, "patchId": patch_id, "ops": ops,
        })
        if ops:
            await send({"type": "scene.patch", "sceneId": scene_id, "generationId": generation_id,
                        "baseRevision": state.scene.revision - 1, "patchId": patch_id, "ops": ops})
    except (ProposalError, Exception):  # noqa: BLE001 —— 缓存提案异常时回退到 Director 填充
        # 先失效缓存条目：否则 fill_scene 重读同一坏提案 → _apply_proposal → 无限递归
        app.state.prefetch.invalidate(archetype_id)
        await fill_scene(app, events, state, session_id, send, scene_id=scene_id,
                         seed=seed, generation_id=generation_id, skeleton=skeleton)


def rebuild_from_events(app, events, state, session_id, send) -> bool:
    """事件投影重建场景。成功 → 组好 state.scene + actor + 发送 skeleton/patch/focus，返回 True。"""
    evs = events.list_after(session_id, 0)
    entered = [e["payload"] for e in evs if e["event_type"] == "scene.entered"]
    if not entered:
        return False
    last = entered[-1]
    patches = [e["payload"] for e in evs if e["event_type"] == "scene.patch"
               and e["payload"].get("sceneId") == last["sceneId"]]

    scenes = app.state.scenes
    skeleton = scenes.compile_skeleton(last["archetypeId"], scene_id=last["sceneId"],
                                       seed=last["sceneId"], generation_id=last["generationId"])
    ops: list[dict] = []
    for p in patches:
        ops.extend(p.get("ops", []))
    entities, setting = apply_ops(skeleton["entities"], skeleton["setting"], ops)
    scene = SceneSession(scene_id=last["sceneId"], generation_id=last["generationId"],
                         archetype_id=last["archetypeId"], revision=len(patches) + 1,
                         status="filled" if patches else "skeleton",
                         setting=setting, background=skeleton["background"],
                         entities=entities, characters=skeleton["characters"],
                         exits=skeleton["exits"], default_npc_id=scenes.default_npc_id(last["archetypeId"]))
    state.scene = scene
    state.scene_seq = int(last["sceneId"].rsplit("_", 1)[-1])
    state.arbitration.reset(scene.default_npc_id)
    scene_words, entity_by_word_id = scene_maps(scene)
    state.scene_words = scene_words
    state.actor = app.state.scene_factory(scene_words, entity_by_word_id, npc_id=scene.default_npc_id)

    async def _send_sync() -> None:
        await send({"type": "scene.skeleton", "sceneId": scene.scene_id, "generationId": scene.generation_id,
                    "archetypeId": scene.archetype_id, "revision": scene.revision, "status": scene.status,
                    "setting": scene.setting, "background": scene.background,
                    "entities": scene.entities, "characters": scene.characters, "exits": scene.exits})
        if ops:
            await send({"type": "scene.patch", "sceneId": scene.scene_id, "generationId": scene.generation_id,
                        "baseRevision": len(patches), "patchId": "replay", "ops": ops})
        await send({"type": "scene.focus", "sceneId": scene.scene_id, "generationId": scene.generation_id,
                    "activeSpeaker": state.arbitration.active_speaker,
                    "focusSource": "reconnect", "focusExpiresAt": None})
    # ws 层保证 send 可用（连接刚建立）；同步包装
    asyncio.get_running_loop().create_task(_send_sync())
    return True


def apply_ops(entities: list[dict], setting: dict, ops: list[dict]) -> tuple[list[dict], dict]:
    """白名单 patch ops 应用（/entities/<id> 与 /setting）。重放与前端共用语义。"""
    out: dict[str, dict] = {e["id"]: e for e in entities}
    for op in ops:
        path = op.get("path", "")
        if path.startswith("/entities/"):
            eid = path[len("/entities/"):]
            if op.get("op") in ("add", "replace") and "entity" in op:
                out[eid] = op["entity"]
            elif op.get("op") == "remove":
                out.pop(eid, None)
        elif path == "/setting" and op.get("op") == "replace":
            setting = op.get("value", setting)
    return list(out.values()), setting
