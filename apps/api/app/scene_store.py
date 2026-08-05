"""从本地资产编译场景，进程内缓存（Redis 前的开发态 LRU 由 dict 充当）。"""
import json
from functools import lru_cache
from pathlib import Path

from scene_compiler import compile_from_docs, template_scene_plan


class SceneStore:
    def __init__(self, asset_root: Path) -> None:
        self._archetypes_dir = asset_root / "archetypes"

    @lru_cache(maxsize=8)
    def _load_archetype(self, archetype_id: str) -> dict:
        return json.loads((self._archetypes_dir / f"{archetype_id}.json").read_text(encoding="utf-8"))

    def get_archetype(self, archetype_id: str) -> dict:
        return self._load_archetype(archetype_id)

    def list_archetype_ids(self) -> list[str]:
        return [p.stem for p in self._archetypes_dir.glob("*.json")]

    def get_compiled_scene(self, scene_id: str) -> dict:
        plan = template_scene_plan()
        if plan["sceneId"] != scene_id:
            raise KeyError(f"unknown scene: {scene_id}")
        return compile_from_docs(self.get_archetype(plan["archetypeId"]), plan)
