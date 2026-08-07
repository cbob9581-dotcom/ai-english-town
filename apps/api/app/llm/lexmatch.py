"""服务端词法匹配：commit.text → candidateWordIds（lemma 屈折归一）。
这是 spec §7 的最终形态——不信任 LLM 自报 exposure，从全文派生。"""
from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z]+")

# 标准 f→v 变复数名词集（loaf→loaves 等）。非此集的词不适用 f/fe→ves 规则，
# 避免 belief→believes、safe→saves、roof→rooves 等假阳性。
_F_V_SET = {"loaf", "leaf", "wolf", "thief", "shelf", "wife", "life", "knife"}


def _variants(lemma: str) -> set[str]:
    v = {lemma, lemma + "s", lemma + "es"}
    # y→ies 仅当前一字母为辅音（cry→cries），排除 day→daies、boy→boies 等
    if lemma.endswith("y") and len(lemma) > 1 and lemma[-2] not in "aeiou":
        v.add(lemma[:-1] + "ies")
    if lemma in _F_V_SET:
        if lemma.endswith("fe") and len(lemma) > 2:
            v.add(lemma[:-2] + "ves")
        if lemma.endswith("f") and len(lemma) > 1:
            v.add(lemma[:-1] + "ves")
    return v


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def token_contains(text: str, lemma: str) -> bool:
    forms = _variants(lemma)
    return any(tok in forms for tok in _tokens(text))


def derive_candidate_word_ids(text: str, allowed: dict[str, str]) -> list[str]:
    """allowed: {wordId: lemma}，返回出现过的 wordId（按 allowed 插入序）。"""
    return [word_id for word_id, lemma in allowed.items() if token_contains(text, lemma)]
