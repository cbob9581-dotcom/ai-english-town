"""ScenePlan 服务端缓存：预取 Director 提案（非骨架——骨架本地无条件快）。
键 = archetypeId（阶段 3 无 world memory，revision 恒 0），TTL 过期即失效。"""
from __future__ import annotations

import time


class ScenePrefetchCache:
    def __init__(self, *, ttl_s: float = 60.0, maxsize: int = 32) -> None:
        self._ttl_s = ttl_s
        self._maxsize = maxsize
        self._items: dict[str, tuple[float, dict]] = {}

    def get(self, archetype_id: str) -> dict | None:
        item = self._items.get(archetype_id)
        if item is None:
            return None
        expires_at, proposal = item
        if time.time() > expires_at:
            self._items.pop(archetype_id, None)
            return None
        return proposal

    def put(self, archetype_id: str, proposal: dict) -> None:
        if len(self._items) >= self._maxsize and archetype_id not in self._items:
            oldest = min(self._items, key=lambda k: self._items[k][0])
            self._items.pop(oldest, None)
        self._items[archetype_id] = (time.time() + self._ttl_s, proposal)

    def invalidate(self, archetype_id: str) -> None:
        """丢弃某 archetype 的缓存提案。缓存提案无法应用时、回退 Director 前必须调用（防递归）。"""
        self._items.pop(archetype_id, None)
