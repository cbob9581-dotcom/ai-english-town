"""gesture 产出与校验：point 指物 + 情绪手势（wave/nod/shake）。
entityId 若给出必须 ∈ 当前场景实体，否则只拒 gesture、其余 metadata 照常。"""
from __future__ import annotations

import re

GESTURE_TYPES = frozenset({"point", "wave", "nod", "shake"})
_WAVE_WORDS = ("hello", "hi", "welcome", "bye", "goodbye")
_NOD_WORDS = ("yes", "yep", "right", "correct", "sure")
_SHAKE_WORDS = ("no", "nope", "sorry", "cannot", "can't", "not", "never")


def derive_gesture(text: str, entity_by_word_id: dict[str, str]) -> dict | None:
    """由已产出文本派生手势。优先级：point（提到场景词）> wave > nod > shake。"""
    words = set(re.findall(r"[a-z']+", text.lower()))
    for word_id, entity_id in entity_by_word_id.items():
        lemma = word_id.split("_")[1] if word_id.startswith("word_") and len(word_id.split("_")) >= 2 else None
        if lemma and lemma in words:
            return {"type": "point", "entityId": entity_id}
    if words & set(_WAVE_WORDS):
        return {"type": "wave"}
    if words & set(_NOD_WORDS):
        return {"type": "nod"}
    if words & set(_SHAKE_WORDS):
        return {"type": "shake"}
    return None


def validate_gesture(gesture: dict | None, entity_ids: set[str]) -> dict | None:
    """只拒 gesture；非法 → None（metadata 其余字段照常）。"""
    if not isinstance(gesture, dict):
        return None
    gtype = gesture.get("type")
    if gtype not in GESTURE_TYPES:
        return None
    if gtype == "point":
        return gesture if gesture.get("entityId") in entity_ids else None
    if gesture.get("entityId") is not None:
        return None
    return gesture
