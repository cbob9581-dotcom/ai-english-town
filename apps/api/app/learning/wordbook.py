"""导入管线：校验 → 规范化 → 去重 → 词典匹配 → 入库（source=quest）。
pos 来源规则：条目 "lemma/pos" 显式 pos；未指定取词典该 lemma 首词条，多 sense 全导入。"""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from app.learning.dictionary import Dictionary
from app.learning.store import LearningStore

MAX_WORDS = 500
MAX_WORD_LEN = 64
_WORD_RE = re.compile(r"^[a-z][a-z'/\- ]*[a-z]$")
import json


class ImportValidationError(ValueError):
    pass


def validate_words(words: list[str]) -> list[str]:
    if len(words) > MAX_WORDS:
        raise ImportValidationError(f"too many words: {len(words)} > {MAX_WORDS}")
    out: list[str] = []
    for raw in words:
        w = raw.strip().lower()
        if not w:
            continue
        if len(w) > MAX_WORD_LEN or not _WORD_RE.match(w):
            raise ImportValidationError(f"invalid word: {raw!r}")
        out.append(w)
    return out


def _word_id(lemma: str, pos: str, sense: int) -> str:
    return f"word_{lemma}_{pos}_{sense}"


def run_import(store: LearningStore, dictionary: Dictionary, user_id: str,
               words: list[str], name: str | None = None, *, now: datetime) -> dict:
    cleaned = validate_words(words)
    created = now.isoformat()
    items: list[dict] = []
    for entry in cleaned:
        pos_spec = None
        lemma = entry
        if "/" in entry:
            lemma, pos_spec = entry.rsplit("/", 1)
        entries = dictionary.get(lemma, pos_spec) if pos_spec else dictionary.by_lemma(lemma)
        if not entries:
            pos = pos_spec or "n"
            items.append({"word_id": _word_id(lemma, pos, 1), "lemma": lemma, "pos": pos,
                          "sense": "1", "ipa": None, "cefr": None,
                          "scene_tags": json.dumps([]), "carrier": "phrase",
                          "slot_categories": json.dumps([]), "source": "quest",
                          "created_at": created})
            continue
        for w in entries:                      # 多 sense 全导入
            items.append({"word_id": _word_id(w.lemma, w.pos, 1), "lemma": w.lemma, "pos": w.pos,
                          "sense": "1", "ipa": w.ipa, "cefr": w.cefr,
                          "scene_tags": json.dumps(w.scene_tags), "carrier": w.carrier,
                          "slot_categories": json.dumps(w.slot_categories), "source": "quest",
                          "created_at": created})
    list_id = name and f"list_{uuid.uuid4().hex[:8]}"
    imported, known, missing, total = store.import_words(user_id, list_id, name or created, items)
    return {"imported": imported, "known": known, "missingMetadata": missing, "total": total}
