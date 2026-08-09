"""浏览器实时连接：音频二进制 + 控制 JSON。回合跑独立 asyncio 任务（可取消）；
playbackState + barge-in + spurious 守卫 + append-only 打断 + 双端过期丢弃（服务端侧）。"""
from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket

from app.arbitration import ArbitrationState
from app.learning.encounters import record_ask
from app.scene_lifecycle import enter_scene, fill_scene, rebuild_from_events, scene_maps
from app.settings import Settings
from app.voice_round import run_round

router = APIRouter()


class SessionState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._fallback_generation = f"gen_{uuid.uuid4().hex[:8]}"
        self.scene = None                    # SceneSession | None（Task 4）
        self.scene_seq = 0
        self.fill_task = None
        self.arbitration = ArbitrationState()
        self.actor = None                    # Task 4：每场景重建（persona 动态）
        self.round_task: asyncio.Task | None = None
        self.active_turn_id: str | None = None
        self.is_playing = False
        self.played_ms = 0
        self.utterance_id: str | None = None
        self.frames: list[bytes] = []
        self.target_word_ids: set[str] = set()
        self.scene_words: dict[str, str] = {}   # 选词合并后的 wordId→name
        self.audio_start_armed = False
        self.barge_in_armed = False
        self.pending_asks: dict[str, asyncio.Task] = {}
        self.spurious_guards: set[asyncio.Task] = set()
        self.semaphore = asyncio.Semaphore(settings.llm_concurrency_limit)
        self.spurious_window_s = 0.5
        self._turn_seq = 0

    @property
    def generation_id(self) -> str:
        return self.scene.generation_id if self.scene else self._fallback_generation

    def new_turn_id(self) -> str:
        self._turn_seq += 1
        return f"turn_{uuid.uuid4().hex[:8]}_{self._turn_seq}"


@router.websocket("/ws/sessions/{session_id}")
async def ws_session(ws: WebSocket) -> None:
    await ws.accept()
    app = ws.app
    events = app.state.events
    settings = app.state.settings
    session_id = ws.path_params["session_id"]
    sessions: dict[str, SessionState] = app.state.sessions
    state = sessions.get(session_id) or SessionState(settings)
    sessions[session_id] = state

    async def send(payload: object) -> None:
        if isinstance(payload, bytes):
            await ws.send_bytes(payload)
        else:
            await ws.send_json(payload)

    if state.scene is None:
        replayed = rebuild_from_events(app, events, state, session_id, send)
        if not replayed:
            await enter_scene(app, events, state, session_id, send,
                              target_archetype_id=None, source="connect")

    async def _spurious_guard() -> None:
        try:
            await asyncio.sleep(state.spurious_window_s)
            if state.audio_start_armed:
                # 无任何帧到达 → 忽略这次 audio.start（不取消回合、不记打断）
                state.audio_start_armed = False
                state.barge_in_armed = False
        finally:
            state.spurious_guards.discard(asyncio.current_task())

    async def _cancel_and_interrupt() -> None:
        state.is_playing = False
        task = state.round_task
        turn_id = state.active_turn_id
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if turn_id:
            events.append(session_id, "dialogue.turn.interrupted", {
                "generationId": state.generation_id, "turnId": turn_id,
                "playedMs": state.played_ms,
            })
        state.round_task = None
        state.active_turn_id = None

    async def _run_round(payload: bytes, utterance_id: str) -> None:
        budget_exceeded = app.state.llm_log.count_session_calls(session_id) >= settings.llm_session_call_cap
        mem = getattr(app.state, "memory", None)
        world_summary = mem.get_world_summary("local") if mem else None
        try:
            async with state.semaphore:
                result = await run_round(session_id, utterance_id, payload, events,
                                         app.state.asr_client, app.state.tts_client, send,
                                         state.actor, state, budget_exceeded=budget_exceeded,
                                         world_summary=world_summary)
                learning = getattr(app.state, "learning", None)
                if learning and result.get("replied") and state.scene is not None:
                    from app.learning.gop_client import maybe_score_round_gop, post_json
                    # 先算后写：锁外调 /pronounce 得 word_gop（纯数据）；失败 → None 降级，不影响回合与证据。
                    try:
                        gop_scores = await maybe_score_round_gop(
                            store=learning.store, session_id=session_id, utterance_id=utterance_id,
                            settings=settings, scene_words=state.scene_words,
                            words=result.get("words"), target_word_ids=state.target_word_ids,
                            http_post=post_json)
                    except Exception:  # noqa: BLE001 —— GOP 预计算失败降级，回合与证据不受影响
                        gop_scores = None
                    try:
                        learning.record_round(
                            session_id, state.scene_words,
                            result.get("npcText", ""), result.get("finalText", ""),
                            result.get("confidence", -0.5), turn_id=result["turnId"],
                            target_word_ids=state.target_word_ids,
                            words=result.get("words"), gop_scores=gop_scores)
                    except Exception:  # noqa: BLE001 —— 学习证据失败不杀回合
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 回合失败不杀连接
            # 非取消失败后必须清掉 active_turn_id：否则后续 playback.interrupted 会给
            # 这个从未 commit 的回合补写一条虚假的 dialogue.turn.interrupted。
            turn_id = state.active_turn_id
            state.active_turn_id = None
            await send({"type": "round.error", "turnId": turn_id, "error": str(e)})
        finally:
            state.round_task = None

    async def _handle_companion_ask(entity_id: str) -> None:
        # genId 必须在 ask 时刻捕获（ask 所在场景），而非 reply 发射时刻：
        # 若 ask→reply 之间发生转场，发射时 state.generation_id 已是新场景的 genId，
        # 前端会误收为新场景回复；ask 时刻的旧 genId 正确触发前端跨场景丢弃。
        gen_id = state.generation_id
        entry = None
        if state.scene:
            entry = next(((e["semantics"]["wordId"], e["semantics"]["name"])
                          for e in state.scene.entities
                          if e["id"] == entity_id and e.get("semantics", {}).get("wordId")), None)
        if entry is None:
            await send({"type": "companion.reply", "turnId": f"comp_{uuid.uuid4().hex[:8]}",
                        "word": "", "scaffold": "", "degraded": True, "error": "unknown_entity",
                        "generationId": gen_id})
            return
        word_id, word = entry
        learning = getattr(app.state, "learning", None)
        if learning:
            now = datetime.now(timezone.utc)
            try:
                parts = word_id.split("_")          # word_{lemma}_{pos}_{sense}
                lemma, pos = parts[1], parts[2]
                if word_id in state.target_word_ids:
                    learning.record_evidence(
                        session_id,
                        {"evidence_id": f"ev_{uuid.uuid4().hex[:12]}", "event_seq": 0,
                         "session_id": session_id, "attempt_id": f"help_{word_id}",
                         "turn_id": f"comp_{uuid.uuid4().hex[:8]}", "objective_id": None,
                         "word_id": word_id, "source": "help", "prompt_level": 0,
                         "axis": "productive", "result": "neutral", "confidence": 1.0,
                         "evidence_policy_version": app.state.settings.evidence_policy_version,
                         "fsrs_algorithm_version": app.state.settings.fsrs_algorithm_version,
                         "created_at": now.isoformat()},
                        event_id=f"ev_help_{word_id}_{now.date().isoformat()}")
                else:
                    record_ask(learning.store, "local", session_id, lemma, pos,
                               f"comp_{uuid.uuid4().hex[:8]}", now=now, events=learning.events)
            except Exception:  # noqa: BLE001 —— 求助证据失败不影响 tutor
                pass

        async def _run_tutor() -> None:
            try:
                try:
                    async with state.semaphore:
                        res = await app.state.tutor.reply(
                            session_id=session_id, generation_id=state.generation_id,
                            word_id=word_id, word=word)
                except Exception:  # noqa: BLE001 —— tutor 异常逃逸 → 降级 reply，绝不静默丢 companion.ask
                    await send({"type": "companion.reply", "turnId": f"comp_{uuid.uuid4().hex[:8]}",
                                "word": "", "scaffold": "", "degraded": True, "error": "tutor_failed",
                                "generationId": gen_id})
                    return
                turn_id = f"comp_{uuid.uuid4().hex[:8]}"
                await send({"type": "companion.reply", "turnId": turn_id,
                            "word": res.word, "scaffold": res.scaffold,
                            "degraded": res.degraded, "generationId": gen_id})
                if res.audio_base64 is not None:
                    state.is_playing = True
                    await send({"type": "tts.audio.start", "generationId": state.generation_id,
                                "turnId": turn_id, "chunkId": "c1",
                                "sampleRate": res.sample_rate})
                    await send(base64.b64decode(res.audio_base64))
                    await send({"type": "tts.audio.end", "generationId": state.generation_id,
                                "turnId": turn_id, "chunkId": "c1"})
                    state.is_playing = False
            finally:
                state.pending_asks.pop(word_id, None)

        if word_id in state.pending_asks:
            return  # in-flight 合并：连点同一实体不放大调用
        state.pending_asks[word_id] = asyncio.create_task(_run_tutor())

    async def _handle_npc_focus(ctrl: dict) -> None:
        scene = state.scene
        if scene is None:
            return
        if ctrl.get("sceneId") != scene.scene_id or ctrl.get("generationId") != scene.generation_id:
            return  # 过期 focus 丢弃
        npc_id = ctrl.get("characterId")
        npcs = {c["npcId"] for c in scene.characters}
        if npc_id not in npcs:
            return
        speaker = state.arbitration.set_focus(npc_id, "user_click")
        scene_words, entity_by_word_id = scene_maps(scene)
        state.actor = app.state.scene_factory(state.scene_words, entity_by_word_id, npc_id=npc_id)
        await send({"type": "scene.focus", "sceneId": scene.scene_id, "generationId": scene.generation_id,
                    "activeSpeaker": speaker, "focusSource": "user_click",
                    "focusExpiresAt": state.arbitration.focus_expires_ms})

    async def _handle_entity_click(entity_id: str) -> None:
        if state.scene is None:
            return
        entry = next(((e["semantics"]["wordId"], e["semantics"]["name"])
                      for e in state.scene.entities
                      if e["id"] == entity_id and e.get("semantics", {}).get("wordId")), None)
        if entry is None:
            return
        word_id, _word = entry
        learning = getattr(app.state, "learning", None)
        if not learning or word_id not in state.target_word_ids:
            return
        now = datetime.now(timezone.utc)
        try:
            learning.record_evidence(
                session_id,
                {"evidence_id": f"ev_{uuid.uuid4().hex[:12]}", "event_seq": 0,
                 "session_id": session_id, "attempt_id": f"click_{word_id}",
                 "turn_id": f"click_{uuid.uuid4().hex[:8]}", "objective_id": None,
                 "word_id": word_id, "source": "action_understanding", "prompt_level": 1,
                 "axis": "receptive", "result": "success", "confidence": 1.0,
                 "evidence_policy_version": app.state.settings.evidence_policy_version,
                 "fsrs_algorithm_version": app.state.settings.fsrs_algorithm_version,
                 "created_at": now.isoformat()},
                event_id=f"ev_click_{word_id}_{now.date().isoformat()}")
        except Exception:  # noqa: BLE001 —— 点击证据失败不杀连接
            pass

    async def _prefetch_for(app, state, session_id: str, archetype_id: str) -> None:
        """预算允许时后台预取目标 archetype 的提案并缓存。"""
        settings = app.state.settings
        cap = settings.llm_session_call_cap
        if app.state.llm_log.count_session_calls(session_id) >= cap * settings.scene_prefetch_budget_ratio:
            return
        mem = getattr(app.state, "memory", None)
        revision = mem.get_revision("local") if mem else 0
        world_summary = mem.get_world_summary("local") if mem else None
        if app.state.prefetch.get(archetype_id, revision) is not None:
            return
        try:
            async with state.semaphore:
                async with asyncio.timeout(settings.llm_total_timeout_director_s):
                    proposal = await app.state.director.propose(
                        archetype_id=archetype_id,
                        archetype=app.state.scenes.get_archetype(archetype_id),
                        catalog=app.state.catalog,
                        recent_scenes=[], world_summary=world_summary, attempt="prefetch")
            app.state.prefetch.put(archetype_id, revision, proposal)
        except Exception:  # noqa: BLE001 —— 预取失败（含超时）静默（下次正常进场再 Director）
            return

    def _maybe_spawn_prefetch(app, state, session_id: str, archetype_id: str) -> None:
        task = asyncio.create_task(_prefetch_for(app, state, session_id, archetype_id))
        state.spurious_guards.add(task)
        task.add_done_callback(state.spurious_guards.discard)

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                if state.round_task and not state.round_task.done():
                    state.round_task.cancel()
                if state.fill_task and not state.fill_task.done():
                    state.fill_task.cancel()
                return
            if msg.get("text"):
                ctrl = json.loads(msg["text"])
                t = ctrl["type"]
                if t == "audio.start":
                    state.utterance_id = ctrl.get("utteranceId")
                    state.frames = []
                    state.audio_start_armed = True
                    state.barge_in_armed = state.is_playing or (
                        state.round_task is not None and not state.round_task.done())
                    guard = asyncio.create_task(_spurious_guard())
                    state.spurious_guards.add(guard)
                elif t == "audio.end":
                    if state.utterance_id is not None and state.frames:
                        payload = b"".join(state.frames)
                        state.round_task = asyncio.create_task(_run_round(payload, state.utterance_id))
                    state.utterance_id = None
                    state.audio_start_armed = False
                    state.barge_in_armed = False
                elif t == "playback.interrupted":
                    await _cancel_and_interrupt()
                elif t == "companion.ask":
                    await _handle_companion_ask(ctrl.get("entityId"))
                elif t == "entity.click":
                    await _handle_entity_click(ctrl.get("entityId"))
                elif t == "scene.request":
                    if state.scene is None:
                        continue
                    target = app.state.scenes.target_for(state.scene.archetype_id, ctrl.get("exitId"))
                    if target:
                        await enter_scene(app, events, state, session_id, send,
                                          target_archetype_id=target, source="exit")
                        _maybe_spawn_prefetch(app, state, session_id,
                                              app.state.scenes.load_town_map()["start"])
                elif t == "scene.hint":
                    if state.scene is None:
                        continue
                    target = app.state.scenes.target_for(state.scene.archetype_id, ctrl.get("exitId"))
                    if target:
                        _maybe_spawn_prefetch(app, state, session_id, target)
                elif t == "npc.focus":
                    await _handle_npc_focus(ctrl)
            else:
                raw = msg.get("bytes")
                if raw:
                    if state.audio_start_armed:
                        state.audio_start_armed = False
                        if state.barge_in_armed:
                            await _cancel_and_interrupt()
                        state.barge_in_armed = False
                    if state.utterance_id is not None:
                        state.frames.append(raw)
    finally:
        # 清理 spurious 守卫任务：正常断开、ws_session 被取消或异常退出都不留 pending task
        for guard in list(state.spurious_guards):
            guard.cancel()
            try:
                await guard
            except asyncio.CancelledError:
                pass
            state.spurious_guards.discard(guard)
