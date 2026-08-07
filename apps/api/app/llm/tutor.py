"""Companion Tutor：点击实体 → {word, scaffold} 带读提案（JSON mode，不在硬实时路径）+ 缓存。"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from app.llm.client import JsonParseError, LLMAdapter, LLMConnectError
from app.llm.proposals import ProposalError, validate_tutor
from app.llm.tutor_cache import TutorCache
from app.settings import Settings

TUTOR_SYSTEM_PROMPT = (
    "You help an A1-A2 English learner understand one word. "
    "Reply with ONLY a JSON object: {\"word\": <the given word>, \"scaffold\": <one simple English sentence>}. "
    "The scaffold must contain the given word and be under 120 characters. No newlines, no URLs, no code."
)


@dataclass
class TutorResult:
    word: str
    scaffold: str
    audio_base64: str | None
    sample_rate: int | None
    from_cache: bool
    degraded: bool


class CompanionTutor:
    def __init__(self, client: LLMAdapter, settings: Settings, llm_log,
                 cache: TutorCache, tts_client: Callable[[str], Awaitable[dict]]) -> None:
        self._client = client
        self._settings = settings
        self._llm_log = llm_log
        self._cache = cache
        self._tts_client = tts_client

    def _record(self, *, session_id, generation_id, reason, ok, error=None,
                latency_ms=None, ttft_ms=None, tokens=None) -> None:
        self._llm_log.record(
            session_id=session_id, generation_id=generation_id, role="companion_tutor",
            model=self._settings.llm_model,
            prompt_tokens=(tokens or {}).get("prompt_tokens"),
            completion_tokens=(tokens or {}).get("completion_tokens"),
            latency_ms=latency_ms, ttft_ms=ttft_ms,
            finish_reason="stop" if ok else None,
            fallback_reason=reason, attempt=1, ok=ok, error=error,
        )

    async def _synthesize_word(self, word: str) -> tuple[str, int]:
        tts = await self._tts_client(word)
        return tts["audioBase64"], int(tts["sampleRate"])

    async def reply(self, *, session_id: str, generation_id: str,
                    word_id: str, word: str) -> TutorResult:
        cached = self._cache.get(word_id)
        if cached is not None and cached.get("audio_path"):
            try:
                audio = base64.b64encode(Path(cached["audio_path"]).read_bytes()).decode()
                return TutorResult(word=word, scaffold=cached["scaffold"],
                                   audio_base64=audio, sample_rate=cached.get("sample_rate"),
                                   from_cache=True, degraded=False)
            except OSError:
                pass  # 缓存文件丢失 → 走重新合成

        t0 = time.perf_counter()
        reason = "none"
        error: str | None = None
        scaffold = ""
        try:
            messages = [
                {"role": "system", "content": TUTOR_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"word": word})},
            ]
            async with asyncio.timeout(self._settings.llm_total_timeout_tutor_s):
                res = await self._client.complete_json(
                    messages, max_tokens=self._settings.llm_max_tokens_tutor,
                    temperature=self._settings.llm_temperature_tutor)
            validate_tutor(res.json.get("word", ""), res.json.get("scaffold", ""),
                           expected_word=word, max_scaffold_chars=self._settings.llm_max_scaffold_chars)
            scaffold = res.json["scaffold"]
            # LLM+scaffold 已成功：llm_calls 记 ok=True（latency 覆盖 LLM 调用段）。
            # 下方 TTS / 音频写盘 / 缓存写行的失败不得逃出 reply()——降级为无音频
            # （scaffold 保留，degraded=True），带读不因 TTS 失败整条失败。
            self._record(session_id=session_id, generation_id=generation_id, reason="none",
                         ok=True, latency_ms=int((time.perf_counter() - t0) * 1000),
                         ttft_ms=int((time.perf_counter() - t0) * 1000), tokens=res.usage)
            try:
                audio_b64, sample_rate = await self._synthesize_word(word)
                audio_path = str(self._cache.audio_path(word_id))
                Path(audio_path).write_bytes(base64.b64decode(audio_b64))
                self._cache.put(word_id, scaffold, self._settings.llm_model, audio_path, sample_rate)
            except Exception:  # noqa: BLE001 —— TTS/写盘/缓存失败：返回无音频降级结果
                return TutorResult(word=word, scaffold=scaffold, audio_base64=None,
                                   sample_rate=None, from_cache=False, degraded=True)
            return TutorResult(word=word, scaffold=scaffold, audio_base64=audio_b64,
                               sample_rate=sample_rate, from_cache=False, degraded=False)
        except TimeoutError:
            reason, error = "timeout", "tutor total timeout"
        except LLMConnectError as e:
            reason, error = "connect", str(e)
        except JsonParseError as e:
            reason, error = "invalid_json", str(e)
        except ProposalError as e:
            reason, error = "schema_reject", str(e)

        # 降级：只读单词（scaffold 缺省），仍 TTS；TTS 失败则无音频
        self._record(session_id=session_id, generation_id=generation_id, reason=reason,
                     ok=False, error=error, latency_ms=int((time.perf_counter() - t0) * 1000))
        try:
            audio_b64, sample_rate = await self._synthesize_word(word)
        except Exception:  # noqa: BLE001 —— 带读不能因 TTS 失败整条失败
            return TutorResult(word=word, scaffold="", audio_base64=None, sample_rate=None,
                               from_cache=False, degraded=True)
        return TutorResult(word=word, scaffold="", audio_base64=audio_b64,
                           sample_rate=sample_rate, from_cache=False, degraded=True)
