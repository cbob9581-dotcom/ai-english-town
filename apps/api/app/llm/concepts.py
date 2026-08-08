"""conceptId → wordId 的服务端 resolve 接缝（阶段 4 接 learning_items）。
缓存 + 注入式 lookup：concepts 不直接依赖 learning.store（避免循环 import）。
"""
from __future__ import annotations

from typing import Callable

_lookup: Callable[[str, str, int], str | None] | None = None
_cache: dict[tuple[str, str, int], str] = {}


def configure_word_resolver(lookup: Callable[[str, str, int], str | None]) -> None:
    """注入学习引擎的 resolve（查 learning_items，多 sense 取最早创建）。"""
    global _lookup
    _lookup = lookup


def invalidate_word_id_cache() -> None:
    _cache.clear()


def resolve_word_id(lemma: str, pos: str, *, sense: int = 1) -> str:
    """先缓存 → 注入 lookup（learning_items）→ 确定性回退。签名不变。"""
    key = (lemma, pos, sense)
    if key in _cache:
        return _cache[key]
    resolved = _lookup(lemma, pos, sense) if _lookup else None
    if resolved is None:
        resolved = f"word_{lemma}_{pos}_{sense}"
    _cache[key] = resolved
    return resolved
