"""每场选词：carrier 可行性 → 到期（due<=now datetime）→ 新词 → 薄弱（排除 asr 轴）→ 确定性 seed。
seed = sha1(f"scene-select:{archetype_id}:{now.date().isoformat()}")（sceneId 每次进场变化，用 archetype+日期作稳定键）。

MODE FEATURE:
- mode="goal"（默认，等同旧行为）：due[:3] + new[:3] + weak[0] + 补齐，其中 due/new 优先取
  source="quest" 的词（用户上传词表导入的目标词，见 store.import_words 的默认 source），
  耗尽后再取其余来源，兑现 "goal-oriented mode 优先推进用户自己设定的目标"。
- mode="free"：大幅降低 FSRS 排期的存在感——只留 1 个到期复习位（不挤占场景，
  维持轻量间隔重复），不强制填新词/薄弱词。场景大部分内容应由 Free Mode 下的
  user_intent（见 scene_director.py）和随手问 companion 的 spontaneous encounter
  流程（encounters.py）驱动，而不是被 FSRS 主导。

⚠️ FIX：words 行来自 store.list_items_by_scene()，行对象是 sqlite3.Row（不是 dict），
没有 .get() 方法，且用字符串 key 取不存在的列时抛 IndexError（不是 KeyError）。
_source_of() 用 try/except 同时兼容 Row 与测试里手搭的 plain dict。"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime

GOAL_SOURCE = "quest"  # 对齐 store.import_words() 的默认导入来源


def scene_prop_slot_categories(archetype: dict) -> set[str]:
    return {c for slot in archetype.get("propSlots", []) for c in slot.get("categories", [])}


def _seed_int(key: str) -> int:
    return int.from_bytes(hashlib.sha1(key.encode()).digest()[:4], "big")


def _source_of(w) -> str | None:
    """兼容 sqlite3.Row（无 .get()，缺列抛 IndexError）与测试用的 plain dict（缺列抛 KeyError）。"""
    try:
        return w["source"]
    except (KeyError, IndexError):
        return None


def _split_goal_first(rows: list) -> tuple[list, list]:
    """把 source="quest"（用户上传词表）的行排到前面（保持各自内部原有顺序），
    用于 goal 模式下优先耗尽用户自己上传的目标词。"""
    goal = [w for w in rows if _source_of(w) == GOAL_SOURCE]
    rest = [w for w in rows if _source_of(w) != GOAL_SOURCE]
    return goal, rest


def pick(words: list[dict], *, archetype_id: str, now: datetime,
         slot_categories: set[str], limit: int = 7,
         mode: str = "goal") -> list[str]:
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

    if mode == "free":
        # 轻触：只保留 1 个到期复习位，不强推新词/薄弱词，把场景内容让位给
        # user_intent 与 spontaneous encounter 流程。
        chosen = [w["word_id"] for w in due[:1]]
        return chosen[:limit]

    # mode == "goal"（默认）：due/new 各自把 source="quest" 排到前面优先耗尽
    due_goal, due_rest = _split_goal_first(due)
    new_goal, new_rest = _split_goal_first(new)
    due_ordered = due_goal + due_rest
    new_ordered = new_goal + new_rest

    chosen: list[str] = []
    chosen += [w["word_id"] for w in due_ordered[:3]]
    chosen += [w["word_id"] for w in new_ordered[:3]]
    if len(chosen) < limit and weak:
        chosen.append(weak[0]["word_id"])
    for w in feasible:
        if len(chosen) >= limit:
            break
        if w["word_id"] not in chosen:
            chosen.append(w["word_id"])
    return chosen[:limit]
