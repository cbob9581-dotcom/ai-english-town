"""faster-whisper 非原生流式 → 滚动窗口伪流式 + segment commit。
核心：稳定前缀合并 + 连续一致才算 stable + 只对变化发 partial + final 覆盖。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UtteranceState:
    utterance_id: str
    stable_text: str = ""
    final_text: str = ""
    revision: int = 0


def merge_windows(previous: str, current: str) -> str:
    """返回 previous/current 共有的稳定前缀（按 token）。"""
    prev_tokens = previous.split()
    cur_tokens = current.split()
    n = 0
    for a, b in zip(prev_tokens, cur_tokens):
        if a == b:
            n += 1
        else:
            break
    return " ".join(cur_tokens[:n])


class RollingTranscriber:
    """stable 语义：相邻两次转写的公共前缀（"连续两次一致"的 token）。
    只对 stable 变化发 partial，final 覆盖 partial。"""
    def __init__(self, transcribe_fn, window_s: float = 6.0, check_ms: float = 300.0) -> None:
        self.transcribe_fn = transcribe_fn
        self.window_s = window_s
        self.check_ms = check_ms
        self.last_check_ms = -1.0
        self.last_text = ""
        self.emitted_stable = ""

    def _reset(self) -> None:
        self.last_check_ms = -1.0
        self.last_text = ""
        self.emitted_stable = ""

    def feed(self, utterance: UtteranceState, samples: object, sample_rate: int, now_ms: float) -> list[dict]:
        if now_ms - self.last_check_ms < self.check_ms:
            return []
        self.last_check_ms = now_ms
        window = samples[-int(self.window_s * sample_rate):]
        text = self.transcribe_fn(window, final=False)
        stable = merge_windows(self.last_text, text)
        self.last_text = text
        if stable and stable != self.emitted_stable:
            self.emitted_stable = stable
            utterance.stable_text = stable
            utterance.revision += 1
            return [{"type": "partial", "utteranceId": utterance.utterance_id, "stableText": stable, "revision": utterance.revision}]
        return []

    def finalize(self, utterance: UtteranceState, samples: object, sample_rate: int) -> dict:
        result = self.transcribe_fn(samples, final=True)
        text = result if isinstance(result, str) else result.get("text", "")
        utterance.final_text = text
        segments = result.get("segments", []) if isinstance(result, dict) else []
        lang = result.get("language", "en") if isinstance(result, dict) else "en"
        conf = float(result.get("avg_logprob", -0.5)) if isinstance(result, dict) else -0.5
        self._reset()
        return {
            "type": "final", "utteranceId": utterance.utterance_id,
            "finalText": text, "segments": segments, "language": lang, "confidence": conf,
        }
