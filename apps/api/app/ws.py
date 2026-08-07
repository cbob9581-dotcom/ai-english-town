"""浏览器实时连接：音频二进制 + 控制 JSON。回合跑独立 asyncio 任务（可取消）；
playbackState + barge-in + spurious 守卫 + append-only 打断 + 双端过期丢弃（服务端侧）。"""
from __future__ import annotations

import asyncio
import base64
import json
import uuid

from fastapi import APIRouter, WebSocket

from app.arbitration import ArbitrationState
from app.scene_lifecycle import enter_scene, scene_maps
from app.llm.concepts import resolve_word_id  # noqa: F401
from app.settings import Settings
from app.voice_round import run_round

router = APIRouter()


class SessionState:
    def __init__(self, settings: Settings) -> None:
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
        try:
            async with state.semaphore:
                await run_round(session_id, utterance_id, payload, events,
                                app.state.asr_client, app.state.tts_client, send,
                                state.actor, state, budget_exceeded=budget_exceeded)
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
        entry = None
        if state.scene:
            entry = next(((e["semantics"]["wordId"], e["semantics"]["name"])
                          for e in state.scene.entities
                          if e["id"] == entity_id and e.get("semantics", {}).get("wordId")), None)
        if entry is None:
            await send({"type": "companion.reply", "turnId": f"comp_{uuid.uuid4().hex[:8]}",
                        "word": "", "scaffold": "", "degraded": True, "error": "unknown_entity"})
            return
        word_id, word = entry

        async def _run_tutor() -> None:
            try:
                try:
                    async with state.semaphore:
                        res = await app.state.tutor.reply(
                            session_id=session_id, generation_id=state.generation_id,
                            word_id=word_id, word=word)
                except Exception:  # noqa: BLE001 —— tutor 异常逃逸 → 降级 reply，绝不静默丢 companion.ask
                    await send({"type": "companion.reply", "turnId": f"comp_{uuid.uuid4().hex[:8]}",
                                "word": "", "scaffold": "", "degraded": True, "error": "tutor_failed"})
                    return
                turn_id = f"comp_{uuid.uuid4().hex[:8]}"
                await send({"type": "companion.reply", "turnId": turn_id,
                            "word": res.word, "scaffold": res.scaffold,
                            "degraded": res.degraded})
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
                elif t == "scene.request":
                    if state.scene is None:
                        continue
                    target = app.state.scenes.target_for(state.scene.archetype_id, ctrl.get("exitId"))
                    if target:
                        await enter_scene(app, events, state, session_id, send,
                                          target_archetype_id=target, source="exit")
                elif t == "scene.hint":
                    # Task 7 实现预取；本任务仅解析（保证协议字段不抛）
                    pass
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
