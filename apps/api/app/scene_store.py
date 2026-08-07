"""从本地资产编译场景：town-map 邻接表 + catalog 概念目录 + 确定性骨架 + 提案展开。
进程内缓存（Redis 前的开发态 LRU 由 dict/lru_cache 充当）。"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from scene_compiler import compile_from_docs, compile_scene, template_scene_plan
from scene_schema.models import Archetype, ScenePlan
from scene_schema.validate import validate_scene_plan

from app.catalog import Catalog
from app.llm.concepts import resolve_word_id


def _stable_index(key: str, n: int) -> int:
    return hashlib.sha1(key.encode("utf-8")).digest()[0] % n


class SceneStore:
    def __init__(self, asset_root: Path) -> None:
        self._root = asset_root
        self._archetypes_dir = asset_root / "archetypes"
        self._catalog = Catalog.load(asset_root)

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    @lru_cache(maxsize=8)
    def _load_archetype(self, archetype_id: str) -> dict:
        return json.loads((self._archetypes_dir / f"{archetype_id}.json").read_text(encoding="utf-8"))

    def get_archetype(self, archetype_id: str) -> dict:
        return self._load_archetype(archetype_id)

    def list_archetype_ids(self) -> list[str]:
        # town-map.json 也住在 archetypes 目录，排除掉（否则会被当成可玩原型）
        return [p.stem for p in self._archetypes_dir.glob("*.json") if p.stem != "town-map"]

    @lru_cache(maxsize=1)
    def load_town_map(self) -> dict:
        return json.loads((self._archetypes_dir / "town-map.json").read_text(encoding="utf-8"))

    def target_for(self, archetype_id: str, exit_id: str) -> str | None:
        return self.load_town_map().get("edges", {}).get(archetype_id, {}).get(exit_id)

    def default_npc_id(self, archetype_id: str) -> str | None:
        arche = self.get_archetype(archetype_id)
        for slot in arche.get("npcSlots", []):
            npcs = self._catalog.npcs_in(slot["role"])
            if npcs:
                return npcs[_stable_index(f"{slot['slotId']}:default", len(npcs))].npc_id
        return None

    def get_compiled_scene(self, scene_id: str) -> dict:
        plan = template_scene_plan()
        if plan["sceneId"] != scene_id:
            raise KeyError(f"unknown scene: {scene_id}")
        return compile_from_docs(self.get_archetype(plan["archetypeId"]), plan)

    # --- 阶段 3：骨架 / 展开 / diff ---

    def _default_concept(self, archetype: dict, slot: dict, seed: str):
        for category in slot["categories"]:
            concepts = self._catalog.concepts_in(category)
            if concepts:
                return concepts[_stable_index(f"{slot['slotId']}:{seed}", len(concepts))]
        return None

    def _entity_from_concept(self, concept, slot_id: str) -> dict:
        # layout 由 compile_scene 覆盖；这里给 schema 合法占位
        return {
            "id": f"{slot_id}-1",
            "component": "prop",
            "layout": {"x": 0, "y": 0, "w": 40, "h": 40, "anchor": "bottom"},
            "appearance": {"visualKey": concept.visual_key},
            "semantics": {"name": concept.name,
                          "wordId": resolve_word_id(concept.lemma, concept.pos),
                          "conceptId": concept.concept_id},
            "interactions": ["focus", "ask"],
        }

    def _character_entries(self, archetype: dict, characters: list[dict]) -> list[dict]:
        role_of = {s["slotId"]: s["role"] for s in archetype.get("npcSlots", [])}
        out: list[dict] = []
        for ch in characters:
            npc = self._catalog.npc(ch["npcId"])
            if npc is None:
                continue
            role = role_of.get(ch["slotId"], "vendor")
            out.append({"slotId": ch["slotId"], "npcId": npc.npc_id, "name": npc.name,
                        "emoji": npc.emoji, "voice": npc.voice, "visualKey": f"npc.{role}"})
        return out

    def _plan(self, archetype_id: str, *, scene_id: str, generation_id: str,
              setting: dict, fills: list[dict], characters: list[dict]) -> dict:
        return {"schemaVersion": "1.0", "sceneId": scene_id, "generationId": generation_id,
                "revision": 1, "mode": "free", "archetypeId": archetype_id, "setting": setting,
                "fills": fills, "characters": characters, "objectives": [], "exits": []}

    def _finalize(self, archetype: dict, scene: dict) -> dict:
        scene["exits"] = []
        edges = self.load_town_map().get("edges", {}).get(archetype["archetypeId"], {})
        for i, es in enumerate(archetype["exits"]):
            direction = es["direction"]
            target = edges.get(direction)
            for e in scene["entities"]:
                if e["id"] == f"door-{i + 1}":
                    e["semantics"]["exitId"] = direction
                    if target:
                        e["semantics"]["targetArchetypeId"] = target
            if target:
                scene["exits"].append({"id": direction, "targetArchetypeId": target})
        return scene

    def compile_skeleton(self, archetype_id: str, *, scene_id: str, seed: str,
                         generation_id: str) -> dict:
        """本地确定性默认填充 → 完整可玩骨架。输入仅 (archetypeId, seed)；seed 派生自 sceneId。"""
        arche = self.get_archetype(archetype_id)
        fills: list[dict] = []
        for slot in arche["propSlots"]:
            concept = self._default_concept(arche, slot, seed)
            if concept is None:
                continue
            fills.append({"slotId": slot["slotId"], "entity": self._entity_from_concept(concept, slot["slotId"])})
        characters: list[dict] = []
        for slot in arche.get("npcSlots", []):
            npcs = self._catalog.npcs_in(slot["role"])
            if npcs:
                npc = npcs[_stable_index(f"{slot['slotId']}:{seed}", len(npcs))]
                characters.append({"slotId": slot["slotId"], "npcId": npc.npc_id})
        setting = {"displayName": arche["displayName"], "time": "day"}
        plan = self._plan(archetype_id, scene_id=scene_id, generation_id=generation_id,
                          setting=setting, fills=fills, characters=characters)
        validate_scene_plan(plan)
        plan["characters"] = self._character_entries(arche, plan["characters"])
        scene = compile_scene(Archetype.model_validate(arche),
                              ScenePlan.model_validate(self._plan(archetype_id, scene_id=scene_id,
                                                                  generation_id=generation_id, setting=setting,
                                                                  fills=fills, characters=plan["characters"])))
        return self._finalize(arche, scene)

    def compile_filled(self, archetype_id: str, *, scene_id: str, seed: str,
                       generation_id: str, proposal: dict) -> dict:
        """Director 提案（validate_proposal 已清洗）→ 概念展开 → 编译为丰富场景。"""
        arche = self.get_archetype(archetype_id)
        fills = []
        for f in proposal["fills"]:
            concept = self._catalog.concept(f["conceptId"])
            if concept is None:
                continue
            fills.append({"slotId": f["slotId"], "entity": self._entity_from_concept(concept, f["slotId"])})
        characters = self._character_entries(arche, proposal["characters"])
        plan = self._plan(archetype_id, scene_id=scene_id, generation_id=generation_id,
                          setting=proposal["setting"], fills=fills, characters=characters)
        validate_scene_plan(plan)
        scene = compile_scene(Archetype.model_validate(arche), ScenePlan.model_validate(plan))
        return self._finalize(arche, scene)

    def diff_scenes(self, skeleton: dict, filled: dict) -> list[dict]:
        """骨架 vs 丰富 → 白名单 patch ops（/entities/<id> 与 /setting）。"""
        skel = {e["id"]: e for e in skeleton["entities"]}
        fill = {e["id"]: e for e in filled["entities"]}
        ops: list[dict] = []
        for eid, e in fill.items():
            if eid not in skel:
                ops.append({"op": "add", "path": f"/entities/{eid}", "entity": e})
            elif e != skel[eid]:
                ops.append({"op": "replace", "path": f"/entities/{eid}", "entity": e})
        for eid in skel:
            if eid not in fill:
                ops.append({"op": "remove", "path": f"/entities/{eid}"})
        if filled["setting"] != skeleton["setting"]:
            ops.append({"op": "replace", "path": "/setting", "value": filled["setting"]})
        return ops
