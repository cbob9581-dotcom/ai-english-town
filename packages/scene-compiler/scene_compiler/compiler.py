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
    if direction == "up":
        pos = {"x": 500, "y": 60}
    elif direction == "down":
        pos = {"x": 500, "y": 830}
    else:
        pos = {"x": 920 if direction == "right" else 60, "y": 520}
    return {
        "id": f"door-{index}",
        "component": "door",
        "layout": {**pos, "w": 90, "h": 120 if direction in ("up", "down") else 200, "anchor": "bottom"},
        "appearance": {"visualKey": "door.wooden"},
        "semantics": {"name": "door"},
        "interactions": ["pick"],
    }


def compile_scene(archetype: Archetype, plan: ScenePlan) -> dict:
    """archetype/plan 已通过 Pydantic 校验（由调用方保证或经 compile_from_docs）。"""
    zone_of_slot = {s["slotId"]: s["zone"] for s in archetype.propSlots}
    zones = archetype.zones
    entities: list[dict] = []
    counter: dict[str, int] = {}   # 按 slotId 计数（骨架与 filled 同槽位 id 一致 → diff 可对齐）
    for fill in plan.fills:
        zone_name = zone_of_slot.get(fill["slotId"])
        if not zone_name or zone_name not in zones:
            continue
        counter[fill["slotId"]] = counter.get(fill["slotId"], 0) + 1
        entity = dict(fill["entity"])
        entity["id"] = fill["entity"].get("id") or f"{fill['slotId']}-{counter[fill['slotId']]}"
        entity["layout"] = _zone_center(zones[zone_name].model_dump(), counter[fill["slotId"]] - 1)
        entities.append(entity)

    npc_slot_of = {s["slotId"]: s for s in archetype.npcSlots}
    for ch in plan.characters:
        slot = npc_slot_of.get(ch["slotId"])
        if not slot or slot["zone"] not in zones:
            continue
        entities.append({
            "id": f"npc-{ch['slotId']}",
            "component": "npc",
            "layout": _zone_center(zones[slot["zone"]].model_dump(), 0),
            "appearance": {"visualKey": ch.get("visualKey", f"npc.{slot.get('role', 'vendor')}")},
            "semantics": {"name": ch.get("name", ch.get("npcId", ch["slotId"])), "npcId": ch["npcId"]},
            "interactions": ["focus", "ask"],
        })

    entities.append(dict(COMPANION_ENTITY))
    for i, exit_spec in enumerate(archetype.exits):
        entities.append(_door_entity(i + 1, exit_spec["direction"]))

    return {
        "archetypeId": archetype.archetypeId,
        "sceneId": plan.sceneId,
        "generationId": plan.generationId,
        "setting": plan.setting,
        "background": archetype.background.model_dump(),
        "entities": entities,
        "characters": [c for c in plan.characters],
    }


def compile_from_docs(archetype_doc: dict, plan_doc: dict) -> dict:
    validate_archetype(archetype_doc)
    validate_scene_plan(plan_doc)
    return compile_scene(Archetype.model_validate(archetype_doc), ScenePlan.model_validate(plan_doc))
