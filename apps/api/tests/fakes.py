"""测试共享 fake：NpcActor / CompanionTutor 的 llm_log 注入。"""
from __future__ import annotations


class FakeLlmLog:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, **kw) -> int:
        self.rows.append(kw)
        return len(self.rows)

    def count_session_calls(self, session_id: str) -> int:
        return 0

    def recent(self, session_id: str, limit: int = 20) -> list[dict]:
        return list(self.rows[-limit:])
