"""每场选词：carrier 可行性 → 到期（due<=now datetime）→ 新词 → 薄弱（排除 asr 轴）→ 确定性 seed。
seed = sha1(f"scene-select:{archetype_id}:{now.date().isoformat()}")（sceneId 每次进场变化，用 archetype+日期作稳定键）。"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime


def scene_prop_slot_categories(archetype: dict) -> set[str]:
    return {c for slot in archetype.get("propSlots", []) for c in slot.get("categories", [])}


def _seed_int(key: str) -> int:
    return int.from_bytes(hashlib.sha1(key.encode()).digest()[:4], "big")


def pick(words: list[dict], *, archetype_id: str, now: datetime,
         slot_categories: set[str], limit: int = 7) -> list[str]:
    feasible = [w for w in words
                if w["carrier"] != "object"
                or (set(json.loads(w["slot_categories"])) & slot_categories)]
    due = [w for w in feasible if w["state"] != "new" and w["due"] and w["due"] <= now.isoformat()]
    new = [w for w in feasible if w["state"] == "new"]
    weak = [w for w in feasible
            if not (w["state"] != "new" and w["due"] and w["due"] <= now.isoformat())
            and w not in new]
    weak.sort(key=lambda w: w["productive_score"] + w["receptive_score"])

    seed = _seed_int(f"scene-select:{archetype_id}:{now.date().isoformat()}")
    rng = random.Random(seed)
    rng.shuffle(due); rng.shuffle(new)
    # weak 不做 shuffle —— 必须先按 p+r 升序排好，weak[0] 才是真正最薄弱词（见 test_weak_excludes_asr_confidence_axis）

    chosen: list[str] = []
    chosen += [w["word_id"] for w in due[:3]]
    chosen += [w["word_id"] for w in new[:3]]
    if len(chosen) < limit and weak:
        chosen.append(weak[0]["word_id"])
    for w in feasible:
        if len(chosen) >= limit:
            break
        if w["word_id"] not in chosen:
            chosen.append(w["word_id"])
    return chosen[:limit]
