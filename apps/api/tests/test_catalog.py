import json
from pathlib import Path

from app.catalog import Catalog

ROOT = Path(__file__).resolve().parents[3]


def _catalog() -> Catalog:
    return Catalog.load(ROOT / "assets")


def test_load_categories_and_concepts() -> None:
    c = _catalog()
    assert c.concept("concept.food.loaf") is not None
    assert {x.concept_id for x in c.concepts_in("food")} == {"concept.food.loaf", "concept.food.apple"}
    assert c.concept_ids_in(["food", "paper"]) == ["concept.food.loaf", "concept.food.apple", "concept.paper.receipt"]


def test_load_npcs_and_roles() -> None:
    c = _catalog()
    rosa = c.npc("npc_rosa")
    assert rosa is not None and rosa.role == "vendor"
    assert c.npcs_in("greeter")[0].npc_id == "npc_tom"


def test_every_catalog_visual_key_exists_in_icon_map() -> None:
    """构建期规则：entities.json 的 visualKey 全集 ⊆ icon-map.json。缺映射 = 构建失败。"""
    c = _catalog()
    icons = json.loads((ROOT / "assets" / "icons" / "icon-map.json").read_text(encoding="utf-8"))
    missing = c.all_visual_keys() - set(icons)
    assert missing == set(), f"catalog 里缺图标映射的 visualKey: {missing}"


def test_all_npc_role_visual_keys_in_icon_map() -> None:
    """NPC 渲染键（npc.<role>）也必须有图标。"""
    c = _catalog()
    icons = json.loads((ROOT / "assets" / "icons" / "icon-map.json").read_text(encoding="utf-8"))
    needed = {f"npc.{role}" for role in {"vendor", "greeter", "guard", "conductor", "barista", "librarian"}}
    assert needed <= set(icons)
