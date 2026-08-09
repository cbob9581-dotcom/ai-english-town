"""memory_smoke：回放 session_events 的 scene.entered → WorldSummary + 最终 revision + 增长频率。
实测「一轮多证据/连续进场 revision 不暴涨」。
用法: cd apps/api && uv run -m app.learning.memory_smoke <db_path>"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.event_store import EventStore
from app.learning.memory import MemoryStore, build_world_summary
from app.learning.store import LearningStore


def main(db_path: Path) -> dict:
    events = EventStore(db_path)
    conn = events.connection
    store = LearningStore(conn)
    mem = MemoryStore(conn)
    now = datetime.now(timezone.utc)
    revisions: list[int] = []
    seen: set[str] = set()
    rows = conn.execute(
        "SELECT payload_json FROM session_events WHERE event_type='scene.entered' ORDER BY sequence").fetchall()
    for r in rows:
        arch = json.loads(r[0]).get("archetypeId")
        if arch is None:
            continue
        # 重放语义：apply 内部 _store_summary→cache.refresh 会让 visited_archetypes
        # 一次看到库内全部场景（build_world_summary 全量重建），使首个 touch 后所有场景
        # 都被视为"已访问"，revision 卡死在 1。这里按回放进度覆盖 visited，使每个新场景
        # 恰好 +1、重复进场 +0 —— 正是 smoke 要实测的「连续进场 revision 不暴涨」。
        mem.cache.visited_archetypes = set(seen)
        with events.write_lock:
            mem.apply_memory_updates(conn, events, "local", scene_enter=arch, now=now)
            conn.commit()
        seen.add(arch)
        revisions.append(mem.get_revision("local"))
    bumps = [b - a for a, b in zip([0] + revisions[:-1], revisions)]
    return {
        "revision": mem.get_revision("local"),
        "summary": mem.get_world_summary("local") or build_world_summary(events, conn, "local", now=now),
        "revisionHistory": revisions,
        "revisionGrowth": {"touches": len([b for b in bumps if b > 0]),
                           "maxStep": max(bumps, default=0)},
    }


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("english_town.db")
    print(json.dumps(main(path), ensure_ascii=False, indent=2))
