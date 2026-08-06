"""阶段 1 本地模板 NPC（Rosa）。也是 spec 降级阶梯的"本地模板短句"实现。"""
from __future__ import annotations

import re

from app.reply_templates import FALLBACK_LINES

_PATTERNS: list[tuple[re.Pattern[str], str, list[str]]] = [
    (re.compile(r"\b(hi|hello|hey)\b", re.I), "Hello! Welcome to the bakery. Can I help you?", []),
    (re.compile(r"\b(loaf|bread)\b", re.I), "A loaf! Great choice. Would you like the whole loaf or just a slice?", ["word_loaf_n_1"]),
    (re.compile(r"\b(slice)\b", re.I), "A slice? Of course. Here you are.", []),
    (re.compile(r"\b(price|cost|how much)\b", re.I), "The loaf is three dollars. Would you like one?", []),
    (re.compile(r"\b(thank)\w*\b", re.I), "You're welcome! Come back anytime.", []),
    (re.compile(r"\b(bye|goodbye)\b", re.I), "Goodbye! See you soon.", []),
]

_FALLBACK = {"speech": FALLBACK_LINES[0], "gesture": {"type": "shake"}, "candidateWordIds": []}


def reply(utterance: str) -> dict:
    for pattern, text, candidate_word_ids in _PATTERNS:
        if pattern.search(utterance):
            return {"speech": text, "gesture": {"type": "nod"}, "candidateWordIds": candidate_word_ids}
    return _FALLBACK
