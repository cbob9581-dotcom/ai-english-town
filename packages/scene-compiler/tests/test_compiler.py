import json
from pathlib import Path

from scene_compiler.compiler import compile_from_docs
from scene_compiler.template_scene import template_scene_plan

ROOT = Path(__file__).resolve().parents[3]
ARCHETYPE = json.loads((ROOT / "assets" / "archetypes" / "bakery.json").read_text(encoding="utf-8"))


def test_compiled_scene_contains_fills_companion_and_door() -> None:
    compiled = compile_from_docs(ARCHETYPE, template_scene_plan())
    ids = [e["id"] for e in compiled["entities"]]
    # 3 fills + 1 companion + 2 doors
    assert any("loaf" in i or "counter" in i for i in ids)
    assert any(e["component"] == "companion" for e in compiled["entities"])
    assert any(e["component"] == "door" for e in compiled["entities"])
    assert len(compiled["entities"]) <= 40


def test_all_layouts_within_logical_canvas() -> None:
    compiled = compile_from_docs(ARCHETYPE, template_scene_plan())
    for e in compiled["entities"]:
        lay = e["layout"]
        assert 0 <= lay["x"] <= 1000 and 0 <= lay["y"] <= 1000
        assert 0 <= lay["w"] <= 1000 and 0 <= lay["h"] <= 1000


def test_fill_placed_inside_its_slot_zone() -> None:
    compiled = compile_from_docs(ARCHETYPE, template_scene_plan())
    counter_zone = ARCHETYPE["zones"]["counter"]
    for e in compiled["entities"]:
        if e["semantics"].get("wordId") == "word_loaf_n_1":
            assert counter_zone["x"][0] <= e["layout"]["x"] <= counter_zone["x"][1]
            assert counter_zone["y"][0] <= e["layout"]["y"] <= counter_zone["y"][1]
            return
    raise AssertionError("loaf fill not found")
