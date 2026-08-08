"""迷你词典加载。词条对齐阶段 3 资产（catalog entities pos 短格式；sceneTags 命中场景）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DICT_PATH = "wordbook/dictionary.json"


@dataclass(frozen=True)
class WordEntry:
    lemma: str
    pos: str
    senses: list[str] = field(default_factory=list)
    ipa: str | None = None
    cefr: str | None = None
    scene_tags: list[str] = field(default_factory=list)
    carrier: str = "phrase"                     # object|action|phrase（主 spec §3:34）
    slot_categories: list[str] = field(default_factory=list)


class Dictionary:
    def __init__(self, entries: list[WordEntry]) -> None:
        self._all = list(entries)
        self._by_key: dict[tuple[str, str | None], list[WordEntry]] = {}
        for e in entries:
            self._by_key.setdefault((e.lemma, e.pos), []).append(e)
            self._by_key.setdefault((e.lemma, None), []).append(e)

    @classmethod
    def load(cls, asset_root: Path) -> "Dictionary":
        raw = json.loads((asset_root / DEFAULT_DICT_PATH).read_text(encoding="utf-8"))
        entries = [WordEntry(lemma=w["lemma"], pos=w["pos"], senses=w.get("senses", []),
                             ipa=w.get("ipa"), cefr=w.get("cefr"),
                             scene_tags=w.get("sceneTags", []),
                             carrier=w.get("carrier", "phrase"),
                             slot_categories=w.get("slotCategories", []))
                   for w in raw["words"]]
        return cls(entries)

    def get(self, lemma: str, pos: str | None = None) -> list[WordEntry]:
        return self._by_key.get((lemma, pos), [])

    def by_lemma(self, lemma: str) -> list[WordEntry]:
        return self._by_key.get((lemma, None), [])

    def all(self) -> list[WordEntry]:
        return list(self._all)
