"""用户上传词表 → learning_items（Goal-Oriented Mode 的目标词来源）。
遵循 encounters.py promote() 的直连 sqlite3 写入模式，保持 schema 与写入方式一致。
source="goal_list" 供 scheduler.pick() 在 mode="goal" 时优先挑选（见 scheduler.py
_split_goal_first）——用户自己设定的目标词会比场景里随机出现的到期/新词更早被选中。"""
from __future__ import annotations

import json
from datetime import datetime

from app.learning.store import LearningStore


def _wid(lemma: str, pos: str, sense: int = 1) -> str:
    return f"word_{lemma}_{pos}_{sense}"


def import_wordlist(store: LearningStore, user_id: str, entries: list[dict], *, now: datetime) -> int:
    """entries 形如：
        [{"lemma": "croissant", "pos": "n", "sceneTags": ["bakery"],
          "carrier": "object", "slotCategories": ["pastry"]}, ...]
    - pos 缺省 "n"；carrier 缺省 "phrase"（非具体实体词，不受场景槽位可行性过滤限制，
      任何场景都能被选中——见 scheduler.pick() 的 feasible 过滤：carrier != "object" 直接可行）。
    - carrier="object" 时必须提供匹配某个场景 propSlot 分类的 slotCategories，
      否则该词在所有场景里都不可行（永远选不中）。
    - 已存在的 word_id（同 lemma+pos+sense）→ INSERT OR IGNORE 跳过，不覆盖已有 mastery 进度，
      支持重复导入同一份词表（幂等）。

    返回本次新插入的词数。"""
    created = now.isoformat()
    n = 0
    for entry in entries:
        lemma = entry.get("lemma", "").strip().lower()
        if not lemma:
            continue
        pos = entry.get("pos", "n")
        sense = entry.get("sense", 1)
        wid = _wid(lemma, pos, sense)
        store.conn.execute(
            "INSERT OR IGNORE INTO learning_items(word_id, user_id, lemma, pos, sense, scene_tags, "
            "carrier, slot_categories, source, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (wid, user_id, lemma, pos, str(sense),
             json.dumps(entry.get("sceneTags", [])),
             entry.get("carrier", "phrase"),
             json.dumps(entry.get("slotCategories", [])),
             "goal_list", created))
        store.conn.execute(
            "INSERT OR IGNORE INTO mastery_states(user_id, word_id, updated_at) VALUES(?,?,?)",
            (user_id, wid, created))
        if store.conn.execute("SELECT changes()").fetchone()[0]:
            n += 1
    store.conn.commit()
    return n


def list_wordlist(store: LearningStore, user_id: str) -> list[dict]:
    """列出该用户所有 goal_list 来源的词，供前端展示"我的目标词表"用。"""
    rows = store.conn.execute(
        "SELECT word_id, lemma, pos, scene_tags, carrier, slot_categories, created_at "
        "FROM learning_items WHERE user_id=? AND source='goal_list' ORDER BY created_at DESC",
        (user_id,)).fetchall()
    return [{"wordId": r[0], "lemma": r[1], "pos": r[2],
             "sceneTags": json.loads(r[3]), "carrier": r[4],
             "slotCategories": json.loads(r[5]), "createdAt": r[6]} for r in rows]
