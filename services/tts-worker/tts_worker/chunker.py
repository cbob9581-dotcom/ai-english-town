"""按句子边界切块；首块控制在 8–20 词，避免按单词切块破坏韵律。"""
from __future__ import annotations

import re

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def chunk_sentences(text: str, first_max: int = 20, first_min: int = 8) -> list[str]:
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    if not sentences:
        return []
    first = sentences[0]
    i = 1
    while len(first.split()) < first_min and i < len(sentences):
        first = first + " " + sentences[i]
        i += 1
    rest = sentences[i:]
    if not rest:
        return [first]
    tail = " ".join(rest)
    # 首块保留尾部空格，保证 "".join(chunks) 不丢失块间单词分隔（不丢词）
    return [first + " "] + chunk_sentences(tail, first_max=first_max, first_min=1)
