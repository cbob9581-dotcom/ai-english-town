"""实体/NPC 目录加载。entities.json 只存 conceptId+lemma+pos（不存 wordId）；
wordId 由 llm/concepts.py 服务端 resolve（阶段 4 接 learning_items）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Concept:
    concept_id: str
    name: str
    lemma: str
    pos: str
    visual_key: str


@dataclass(frozen=True)
class Npc:
    npc_id: str
    name: str
    persona: str
    emoji: str
    voice: str
    role: str = "vendor"


class Catalog:
    def __init__(self, entities_doc: dict, npcs_doc: dict) -> None:
        self._concepts: dict[str, Concept] = {}
        self._by_category: dict[str, list[Concept]] = {}
        for category, rows in entities_doc.items():
            for row in rows:
                c = Concept(concept_id=row["conceptId"], name=row["name"], lemma=row["lemma"],
                            pos=row["pos"], visual_key=row["visualKey"])
                self._concepts[c.concept_id] = c
                self._by_category.setdefault(category, []).append(c)
        self._npcs: dict[str, Npc] = {}
        self._by_role: dict[str, list[Npc]] = {}
        for role, rows in npcs_doc.items():
            for row in rows:
                n = Npc(npc_id=row["npcId"], name=row["name"], persona=row["persona"],
                        emoji=row["emoji"], voice=row["voice"], role=role)
                self._npcs[n.npc_id] = n
                self._by_role.setdefault(role, []).append(n)

    @classmethod
    def load(cls, asset_root: Path) -> "Catalog":
        entities = json.loads((asset_root / "catalog" / "entities.json").read_text(encoding="utf-8"))
        npcs = json.loads((asset_root / "catalog" / "npcs.json").read_text(encoding="utf-8"))
        return cls(entities, npcs)

    def concepts_in(self, category: str) -> list[Concept]:
        return list(self._by_category.get(category, []))

    def concept(self, concept_id: str) -> Concept | None:
        return self._concepts.get(concept_id)

    def concept_ids_in(self, categories: list[str]) -> list[str]:
        out: list[str] = []
        for cat in categories:
            out.extend(c.concept_id for c in self._by_category.get(cat, []))
        return out

    def npcs_in(self, role: str) -> list[Npc]:
        return list(self._by_role.get(role, []))

    def npc(self, npc_id: str) -> Npc | None:
        return self._npcs.get(npc_id)

    def all_visual_keys(self) -> set[str]:
        return {c.visual_key for c in self._concepts.values()}

    def all_npc_voices(self) -> list[str]:
        return sorted({n.voice for n in self._npcs.values()})
