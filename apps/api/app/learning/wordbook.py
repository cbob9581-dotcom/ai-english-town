"""导入管线：校验 → 规范化 → 去重 → 词典匹配 → 入库（source=quest）。
pos 来源规则：条目 "lemma/pos" 显式 pos；未指定取词典该 lemma 首词条，多 sense 全导入。

RICH IMPORT (new): 简化格式（words: ["loaf", "bread/n"]）对词典里没有的词只能落
carrier="phrase"/scene_tags=[]/slot_categories=[]——这类词永远无法作为场景里的实体道具
被选中（scheduler.pick() 的 feasible 过滤：carrier=="object" 才检查 slot_categories 交集，
其余 carrier 值一律直接可行，但也就意味着它们不会被 Director 当成"某类场景专属物件"）。
entries 参数让调用方显式提供 carrier/sceneTags/slotCategories：
  - 词典命中该 lemma → 仍以词典的 ipa/cefr/carrier/sceneTags/slotCategories 为准，
    entries 里显式给的字段覆盖词典对应字段（allow 用户明确指定优先于词典默认）。
  - 词典未命中 → 完全使用 entries 提供的字段，缺省回落到 phrase/[]/[]（与简化格式一致）。
  - entries 里每条对应恰好一个 sense（用户已经在描述"这一个词"），不做简化格式那种
    "未指定 pos 时词典多 sense 全导入"的展开。"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

from app.learning.dictionary import Dictionary
from app.learning.store import LearningStore

MAX_WORDS = 500
MAX_WORD_LEN = 64
_WORD_RE = re.compile(r"^[a-z][a-z'/\- ]*[a-z]$")


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


def validate_entries(entries: list[dict]) -> list[dict]:
    """校验富格式条目。每条至少要有 lemma；pos/carrier/sceneTags/slotCategories 均可选。"""
    if len(entries) > MAX_WORDS:
        raise ImportValidationError(f"too many entries: {len(entries)} > {MAX_WORDS}")
    out: list[dict] = []
    for raw in entries:
        lemma = str(raw.get("lemma", "")).strip().lower()
        if not lemma:
            raise ImportValidationError(f"entry missing lemma: {raw!r}")
        if len(lemma) > MAX_WORD_LEN or not _WORD_RE.match(lemma):
            raise ImportValidationError(f"invalid entry lemma: {lemma!r}")
        pos = raw.get("pos")
        if pos is not None and not isinstance(pos, str):
            raise ImportValidationError(f"pos must be a string for {lemma!r}")
        carrier = raw.get("carrier")
        if carrier is not None and not isinstance(carrier, str):
            raise ImportValidationError(f"carrier must be a string for {lemma!r}")
        scene_tags = raw.get("sceneTags")
        if scene_tags is not None and not (isinstance(scene_tags, list)
                                           and all(isinstance(t, str) for t in scene_tags)):
            raise ImportValidationError(f"sceneTags must be a list of strings for {lemma!r}")
        slot_categories = raw.get("slotCategories")
        if slot_categories is not None and not (isinstance(slot_categories, list)
                                                and all(isinstance(c, str) for c in slot_categories)):
            raise ImportValidationError(f"slotCategories must be a list of strings for {lemma!r}")
        out.append({"lemma": lemma, "pos": pos, "carrier": carrier,
                    "sceneTags": scene_tags, "slotCategories": slot_categories})
    return out


def _word_id(lemma: str, pos: str, sense: int) -> str:
    return f"word_{lemma}_{pos}_{sense}"


def _items_from_words(dictionary: Dictionary, words: list[str], created: str) -> list[dict]:
    """原有简化格式路径，行为完全不变。"""
    items: list[dict] = []
    for entry in words:
        pos_spec = None
        lemma = entry
        if "/" in entry:
            lemma, pos_spec = entry.rsplit("/", 1)
        dict_entries = dictionary.get(lemma, pos_spec) if pos_spec else dictionary.by_lemma(lemma)
        if not dict_entries:
            pos = pos_spec or "n"
            items.append({"word_id": _word_id(lemma, pos, 1), "lemma": lemma, "pos": pos,
                          "sense": "1", "ipa": None, "cefr": None,
                          "scene_tags": json.dumps([]), "carrier": "phrase",
                          "slot_categories": json.dumps([]), "source": "quest",
                          "created_at": created})
            continue
        for w in dict_entries:                  # 多 sense 全导入
            items.append({"word_id": _word_id(w.lemma, w.pos, 1), "lemma": w.lemma, "pos": w.pos,
                          "sense": "1", "ipa": w.ipa, "cefr": w.cefr,
                          "scene_tags": json.dumps(w.scene_tags), "carrier": w.carrier,
                          "slot_categories": json.dumps(w.slot_categories), "source": "quest",
                          "created_at": created})
    return items


def _items_from_entries(dictionary: Dictionary, entries: list[dict], created: str) -> list[dict]:
    """富格式路径：单 sense；显式字段覆盖词典对应字段，词典未命中则用显式字段或默认值。"""
    items: list[dict] = []
    for e in validate_entries(entries):
        lemma, pos_spec = e["lemma"], e["pos"]
        dict_entries = dictionary.get(lemma, pos_spec) if pos_spec else dictionary.by_lemma(lemma)
        if dict_entries:
            w = dict_entries[0]
            pos = w.pos
            ipa, cefr = w.ipa, w.cefr
            scene_tags = e["sceneTags"] if e["sceneTags"] is not None else w.scene_tags
            carrier = e["carrier"] if e["carrier"] is not None else w.carrier
            slot_categories = e["slotCategories"] if e["slotCategories"] is not None else w.slot_categories
        else:
            pos = pos_spec or "n"
            ipa = cefr = None
            scene_tags = e["sceneTags"] if e["sceneTags"] is not None else []
            carrier = e["carrier"] if e["carrier"] is not None else "phrase"
            slot_categories = e["slotCategories"] if e["slotCategories"] is not None else []
        items.append({"word_id": _word_id(lemma, pos, 1), "lemma": lemma, "pos": pos,
                      "sense": "1", "ipa": ipa, "cefr": cefr,
                      "scene_tags": json.dumps(scene_tags), "carrier": carrier,
                      "slot_categories": json.dumps(slot_categories), "source": "quest",
                      "created_at": created})
    return items


def run_import(store: LearningStore, dictionary: Dictionary, user_id: str,
               words: list[str], name: str | None = None, *,
               entries: list[dict] | None = None, now: datetime) -> dict:
    """words: 原有简化格式，行为不变。entries: 新增富格式（可选，默认 None/空）。
    两者可同时提供，合并进同一次 store.import_words() 调用（同一个 list_id，counts 合计）。"""
    entries = entries or []
    if len(words) + len(entries) > MAX_WORDS:
        raise ImportValidationError(f"too many words: {len(words) + len(entries)} > {MAX_WORDS}")
    cleaned = validate_words(words)
    created = now.isoformat()
    items = _items_from_words(dictionary, cleaned, created)
    items += _items_from_entries(dictionary, entries, created)
    list_id = name and f"list_{uuid.uuid4().hex[:8]}"
    imported, known, missing, total = store.import_words(user_id, list_id, name or created, items)
    return {"imported": imported, "known": known, "missingMetadata": missing, "total": total}
