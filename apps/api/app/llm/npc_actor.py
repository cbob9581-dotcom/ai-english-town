"""NPC Actor：prompt 构造（trusted 系统人格 + untrusted 结构化字段）→ 流式文本
→ 按句切分 → 校验 → delta/commit/metadata；超时/失败降级 scripted_npc。"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable, Iterator

from app.llm.chunker import SentenceChunker
from app.llm.client import LLMAdapter, LLMConnectError
from app.llm.lexmatch import derive_candidate_word_ids
from app.llm.proposals import ProposalError, validate_speech
from app.settings import Settings

SYSTEM_PROMPT = (
    "You are Rosa, a friendly vendor in a small English bakery. "
    "Reply in short, simple English sentences suitable for an A1-A2 English learner. "
    "Stay in character at the bakery. Never mention that you are an AI. "
    "Use only plain English text with basic punctuation: no newlines, no URLs, no code."
)


class _BudgetExceeded(Exception):
    pass


def build_history(events, session_id: str, limit: int = 10, max_chars: int = 1200) -> list[dict]:
    """投影 dialogue.turn + dialogue.turn.interrupted → 成对历史（从旧到新）。"""
    all_events = events.list_after(session_id, 0)
    turns = [e["payload"] for e in all_events if e["event_type"] == "dialogue.turn"]
    interrupted = {e["payload"]["turnId"]: e["payload"]
                   for e in all_events if e["event_type"] == "dialogue.turn.interrupted"}
    recent = turns[-limit:]
    pairs: list[list[dict]] = []
    total = 0
    for t in reversed(recent):  # 从新到旧累积，超预算丢旧的；最新一对无条件保留
        learner = {"speaker": "learner", "text": t["userText"]}
        rosa: dict = {"speaker": "rosa", "text": t["npcText"]}
        intr = interrupted.get(t["turnId"])
        if intr:
            rosa["interrupted_after_ms"] = int(intr.get("playedMs", 0))
        add = len(json.dumps([learner, rosa], ensure_ascii=False))
        if total and total + add > max_chars:
            continue  # 这条（更旧）超预算 → 丢弃，保留更新的
        pairs.append([learner, rosa])
        total += add
    return [m for pair in reversed(pairs) for m in pair]


class NpcActor:
    def __init__(self, client: LLMAdapter, settings: Settings, llm_log,
                 allowed_words: dict[str, str], fallback: Callable[[str], str]) -> None:
        self._client = client
        self._settings = settings
        self._llm_log = llm_log
        self._allowed_words = dict(allowed_words)
        self._fallback = fallback

    def _scene_hint(self) -> str:
        return "Items nearby: " + ", ".join(self._allowed_words.values())

    def _build_messages(self, user_text: str, recent_turns: list[dict]) -> list[dict]:
        user_payload = {
            "transcript": user_text,
            "recent_turns": recent_turns,
            "scene": self._scene_hint(),
        }
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]

    def _record(self, *, session_id, generation_id, turn_id, utterance_id,
                latency_ms, ttft_ms, reason, ok, error=None, tokens=None) -> None:
        self._llm_log.record(
            session_id=session_id, generation_id=generation_id, turn_id=turn_id,
            utterance_id=utterance_id, role="npc_actor", model=self._settings.llm_model,
            prompt_tokens=(tokens or {}).get("prompt_tokens"),
            completion_tokens=(tokens or {}).get("completion_tokens"),
            latency_ms=latency_ms, ttft_ms=ttft_ms,
            finish_reason="stop" if ok else None,
            fallback_reason=reason, attempt=2 if reason == "connect" else 1,
            ok=ok, error=error,
        )

    def _degrade(self, *, generation_id: str, turn_id: str, user_text: str,
                 reason: str, error: str | None = None) -> Iterator[dict]:
        text = self._fallback(user_text)
        yield {"type": "npc.speech.delta", "generationId": generation_id, "turnId": turn_id, "text": text}
        yield {"type": "npc.speech.commit", "generationId": generation_id, "turnId": turn_id, "text": text}
        yield {"type": "npc.turn.metadata", "generationId": generation_id, "turnId": turn_id,
               "candidateWordIds": derive_candidate_word_ids(text, self._allowed_words)}

    async def stream_reply(self, *, session_id: str, generation_id: str, turn_id: str,
                           utterance_id: str, user_text: str, recent_turns: list[dict],
                           budget_exceeded: bool = False) -> AsyncIterator[dict]:
        t0 = time.perf_counter()
        ttft_ms: int | None = None
        tokens: dict | None = None
        reason = "none"
        error: str | None = None
        chunker = SentenceChunker()
        emitted_chars = 0
        sentences: list[str] = []
        messages = self._build_messages(user_text, recent_turns)

        try:
            if budget_exceeded:
                raise _BudgetExceeded()
            async with asyncio.timeout(self._settings.llm_total_timeout_npc_s):
                sent_any = False
                async for delta in self._client.stream_text(
                        messages, max_tokens=self._settings.llm_max_tokens_npc,
                        temperature=self._settings.llm_temperature_npc):
                    if ttft_ms is None and delta.text:
                        ttft_ms = int((time.perf_counter() - t0) * 1000)
                    if delta.usage:
                        tokens = delta.usage
                    if delta.finish_reason == "length":
                        reason = "length_truncated"
                        break
                    for sentence in chunker.feed(delta.text):
                        validate_speech(sentence, self._settings.llm_max_speech_chars)
                        if emitted_chars + len(sentence) > self._settings.llm_max_speech_chars:
                            raise ProposalError("cumulative speech over limit")
                        sentences.append(sentence)
                        emitted_chars += len(sentence) + 1
                        sent_any = True
                        yield {"type": "npc.speech.delta", "generationId": generation_id,
                               "turnId": turn_id, "text": sentence}
                if reason == "none":
                    # 只有正常流才 finalize 余量；length_truncated 直接整轮降级，不读半句
                    remainder = chunker.finalize()
                    if remainder:
                        validate_speech(remainder, self._settings.llm_max_speech_chars)
                        if emitted_chars + len(remainder) > self._settings.llm_max_speech_chars:
                            raise ProposalError("cumulative speech over limit")
                        sentences.append(remainder)
                        sent_any = True
                        yield {"type": "npc.speech.delta", "generationId": generation_id,
                               "turnId": turn_id, "text": remainder}
                if not sent_any:
                    raise ProposalError("empty speech")
            if reason == "none":
                full = " ".join(sentences).strip()
                latency_ms = int((time.perf_counter() - t0) * 1000)
                self._record(session_id=session_id, generation_id=generation_id, turn_id=turn_id,
                             utterance_id=utterance_id, latency_ms=latency_ms, ttft_ms=ttft_ms,
                             reason="none", ok=True, tokens=tokens)
                yield {"type": "npc.speech.commit", "generationId": generation_id, "turnId": turn_id, "text": full}
                yield {"type": "npc.turn.metadata", "generationId": generation_id, "turnId": turn_id,
                       "candidateWordIds": derive_candidate_word_ids(full, self._allowed_words)}
        except TimeoutError:
            reason = "timeout"
            error = "total timeout"
        except LLMConnectError as e:
            reason = "connect"
            error = str(e)
        except ProposalError as e:
            reason = "schema_reject"
            error = str(e)
        except _BudgetExceeded:
            reason = "budget"
            error = "session call cap exceeded"

        if reason != "none":
            self._record(session_id=session_id, generation_id=generation_id, turn_id=turn_id,
                         utterance_id=utterance_id, latency_ms=int((time.perf_counter() - t0) * 1000),
                         ttft_ms=ttft_ms, reason=reason, ok=False, error=error)
            for m in self._degrade(generation_id=generation_id, turn_id=turn_id,
                                   user_text=user_text, reason=reason, error=error):
                yield m
