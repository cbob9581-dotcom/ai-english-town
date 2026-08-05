"""槽位编译器：把 ScenePlan 的 fills 落到原型 zone 的 0..1000 逻辑坐标。
布局全部由本模块计算 —— 填槽者 / 未来 LLM 都不产生像素位置。"""
from scene_schema.models import Archetype, ScenePlan
from scene_schema.validate import validate_archetype, validate_scene_plan

COMPANION_ENTITY = {
    "id": "companion-1",
    "component": "companion",
    "layout": {"x": 60, "y": 760, "w": 90, "h": 90, "anchor": "bottom"},
    "appearance": {"visualKey": "companion.fox"},
    "semantics": {"name": "companion"},
    "interactions": ["ask"],
}


def _zone_center(zone: dict, slot_index: int) -> dict:
    x0, x1 = zone["x"]
    y0, y1 = zone["y"]
    # 同槽位多个物品按 index 在 zone 内错开，避免完全重叠
    col = slot_index % 3
    x = x0 + int((x1 - x0) * (0.25 + 0.25 * col))
    y = y0 + int((y1 - y0) * 0.5)
    w = max(40, (x1 - x0) // 5)
    h = max(40, (y1 - y0) // 4)
    return {"x": x, "y": y, "w": w, "h": h, "anchor": zone.get("anchor", "bottom")}


def _door_entity(index: int, direction: str) -> dict:
    x = 920 if direction == "right" else 60
    return {
        "id": f"door-{index}",
        "component": "door",
        "layout": {"x": x, "y": 520, "w": 90, "h": 200, "anchor": "bottom"},
        "appearance": {"visualKey": "door.wooden"},
        "semantics": {"name": "door"},
        "interactions": ["pick"],
    }


def compile_scene(archetype: Archetype, plan: ScenePlan) -> dict:
    """archetype/plan 已通过 Pydantic 校验（由调用方保证或经 compile_from_docs）。"""
    zone_of_slot = {s["slotId"]: s["zone"] for s in archetype.propSlots}
    zones = archetype.zones
    entities: list[dict] = []
    counter: dict[str, int] = {}
    for fill in plan.fills:
        zone_name = zone_of_slot.get(fill["slotId"])
        if not zone_name or zone_name not in zones:
            continue
        counter[zone_name] = counter.get(zone_name, 0) + 1
        entity = dict(fill["entity"])
        entity["id"] = fill["entity"].get("id") or f"{fill['slotId']}-{counter[zone_name]}"
        entity["layout"] = _zone_center(zones[zone_name].model_dump(), counter[zone_name] - 1)
        entities.append(entity)

    entities.append(dict(COMPANION_ENTITY))
    for i, exit_spec in enumerate(archetype.exits):
        entities.append(_door_entity(i + 1, exit_spec["direction"]))

    return {
        "archetypeId": archetype.archetypeId,
        "sceneId": plan.sceneId,
        "generationId": plan.generationId,
        "setting": plan.setting,
        "entities": entities,
        "characters": [c for c in plan.characters],
    }


def compile_from_docs(archetype_doc: dict, plan_doc: dict) -> dict:
    validate_archetype(archetype_doc)
    validate_scene_plan(plan_doc)
    return compile_scene(Archetype.model_validate(archetype_doc), ScenePlan.model_validate(plan_doc))
