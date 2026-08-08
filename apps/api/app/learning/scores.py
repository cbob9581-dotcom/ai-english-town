"""三维分更新：一次证据只更新其 axis 对应的一维。
公式（评审第 1 点）：delta = weight * confidence；
  delta>=0 → min(1, s + alpha*delta*(1-s))；delta<0 → max(0, s + alpha*delta*s)。
alpha 随 evidence_policy_version 版本化（settings.score_alpha）。"""
from __future__ import annotations

import math

WEIGHTS: dict[str, tuple[float, str]] = {
    "spontaneous_production": (1.0, "productive"),
    "prompted_production": (0.65, "productive"),
    "repetition": (0.4, "asr_confidence"),
    "word_production": (0.4, "asr_word_confidence"),
    "action_understanding": (0.55, "receptive"),
    "help": (-0.35, "productive"),
    "error": (-0.5, "productive"),
}


def update_score(score: float, weight: float, confidence: float, *, alpha: float = 0.35) -> float:
    delta = weight * confidence
    if delta >= 0:
        return min(1.0, score + alpha * delta * (1.0 - score))
    return max(0.0, score + alpha * delta * score)


def normalize_asr_confidence(avg_logprob: float) -> float:
    """ASR confidence 是 Whisper avg_logprob（负数，越高越好），归一化到 (0,1)。"""
    return math.exp(max(-40.0, avg_logprob))
