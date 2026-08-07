from pathlib import Path

from app.scene_store import SceneStore

ROOT = Path(__file__).resolve().parents[3]


def _store() -> SceneStore:
    return SceneStore(ROOT / "assets")


def test_skeleton_deterministic_on_seed() -> None:
    store = _store()
    a = store.compile_skeleton("plaza", scene_id="s1", seed="s1", generation_id="g1")
    b = store.compile_skeleton("plaza", scene_id="s1", seed="s1", generation_id="g1")
    assert a == b  # 同输入（archetypeId, seed）→ 完全一致


def test_skeleton_fully_playable() -> None:
    store = _store()
    s = store.compile_skeleton("plaza", scene_id="s1", seed="s1", generation_id="g1")
    assert any(e["component"] == "npc" for e in s["entities"])       # 有默认 NPC
    assert any(e["component"] == "companion" for e in s["entities"])  # 有伴学者
    assert len(s["exits"]) == 4                                        # 四向出口
    assert all(0 <= e["layout"]["x"] <= 1000 and 0 <= e["layout"]["y"] <= 1000 for e in s["entities"])
    assert len(s["entities"]) <= 40


def test_skel_vs_filled_diff_replaces_slots_and_setting() -> None:
    store = _store()
    skel = store.compile_skeleton("bakery", scene_id="s1", seed="s1", generation_id="g1")
    proposal = {
        "fills": [{"slotId": "counter.main", "conceptId": "concept.food.apple"}],
        "characters": [{"slotId": "vendor", "npcId": "npc_rosa"}],
        "setting": {"displayName": "Rosewood Bakery", "time": "morning"},
    }
    filled = store.compile_filled("bakery", scene_id="s1", seed="s1",
                                  generation_id="g1", proposal=proposal)
    ops = store.diff_scenes(skel, filled)
    assert any(o["path"] == "/setting" for o in ops)
    # counter.main 被替换为 apple（wordId 变化）；其余槽位保持骨架默认
    replaced = [o for o in ops if o["op"] == "replace" and "/entities/" in o["path"]]
    assert any("counter.main" in o["path"] and o["entity"]["semantics"]["wordId"] == "word_apple_n_1"
               for o in replaced)


def test_filled_setting_preserves_ascii_display_name() -> None:
    store = _store()
    filled = store.compile_filled("bakery", scene_id="s1", seed="s1", generation_id="g1", proposal={
        "fills": [], "characters": [],
        "setting": {"displayName": "Rosewood Bakery", "time": "morning"},
    })
    assert filled["setting"]["displayName"] == "Rosewood Bakery"
    assert filled["setting"]["displayName"].isascii()
