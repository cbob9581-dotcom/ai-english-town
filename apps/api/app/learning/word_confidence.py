"""词级对齐打分器：faster-whisper word_timestamps → 目标词词级置信度。
语义诚实（spec §5.1）：词后验 = 解码器信心，非发音评测。"""
from __future__ import annotations

from app.llm.lexmatch import token_contains

MISRECOGNIZED_SCORE = 0.15   # user 说了目标词但 ASR 未在 words 中识别到


def score_word_confidence(words: list[dict] | None, scene_words: dict[str, str],
                          user_text: str) -> dict[str, float]:
    """对 scene_words 中用户实际产出（user_text 命中）的目标词打分。
    命中 words → word.probability；说了但不在 words → MISRECOGNIZED_SCORE；未说 → 不计。"""
    if not words:
        return {}
    word_by_text = {}
    for w in words:
        t = (w.get("word") or "").strip().lower()
        if t and t not in word_by_text:
            word_by_text[t] = w.get("probability", 0.0)
    out: dict[str, float] = {}
    for word_id, lemma in scene_words.items():
        if not token_contains(user_text, lemma):
            continue                        # 用户未产出该词 → 无词级信号
        prob = word_by_text.get(lemma.lower())
        out[word_id] = float(prob) if prob is not None else MISRECOGNIZED_SCORE
    return out
