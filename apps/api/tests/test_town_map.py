import json
from pathlib import Path

from app.scene_store import SceneStore

ROOT = Path(__file__).resolve().parents[3]


def _store() -> SceneStore:
    return SceneStore(ROOT / "assets")


def test_town_map_edges_all_resolve_and_return() -> None:
    """每条边的目标存在；spoke 可回 hub；无悬挂出口；出口方向与该原型 exits 一致。"""
    store = _store()
    tm = store.load_town_map()
    assert tm["start"] == "plaza"
    archetype_ids = set(store.list_archetype_ids())
    for src, edges in tm["edges"].items():
        assert src in archetype_ids
        arche = store.get_archetype(src)
        dirs = {e["direction"] for e in arche["exits"]}
        assert set(edges) == dirs, f"{src}: town-map 方向必须与 archetype.exits 完全一致"
        for direction, target in edges.items():
            assert target in archetype_ids, f"{src}.{direction} -> {target} 不存在"
            assert store.target_for(src, direction) == target


def test_hub_is_start_and_every_spoke_returns() -> None:
    """每个地点都能（直接或经中转）回到 hub；town-map 无单向死胡同。
    library 是 station 的叶子（唯一出口 left→station），回 hub 需经 station 中转。"""
    tm = _store().load_town_map()
    hub = tm["start"]
    for src, edges in tm["edges"].items():
        if src == hub:
            continue
        seen = {src}
        stack = list(edges.values())
        while stack:
            node = stack.pop()
            if node == hub:
                break
            if node in seen:
                continue
            seen.add(node)
            stack.extend(tm["edges"].get(node, {}).values())
        else:
            raise AssertionError(f"{src} 无法回到 hub {hub}")
