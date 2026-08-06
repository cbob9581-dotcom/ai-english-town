"""llm_calls 表：LLM 调用记账（含降级原因枚举、attempt）。与 session_events 同一 SQLite 连接。"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

FALLBACK_REASONS = frozenset({
    "timeout", "connect", "invalid_json", "schema_reject",
    "length_truncated", "no_key", "budget", "none",
})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls(
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id        TEXT NOT NULL,
  generation_id     TEXT,
  turn_id           TEXT,
  utterance_id      TEXT,
  role              TEXT NOT NULL,
  model             TEXT NOT NULL,
  prompt_tokens     INTEGER,
  completion_tokens INTEGER,
  latency_ms        INTEGER,
  ttft_ms           INTEGER,
  finish_reason     TEXT,
  fallback_reason   TEXT,
  attempt           INTEGER,
  ok                INTEGER NOT NULL,
  error             TEXT,
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_session ON llm_calls(session_id, created_at);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class LlmLog:
    def __init__(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(_SCHEMA)
        connection.commit()
        self._connection = connection
        self._lock = threading.Lock()

    def record(self, *, session_id: str, role: str, model: str,
               generation_id: str | None = None, turn_id: str | None = None,
               utterance_id: str | None = None, prompt_tokens: int | None = None,
               completion_tokens: int | None = None, latency_ms: int | None = None,
               ttft_ms: int | None = None, finish_reason: str | None = None,
               fallback_reason: str = "none", attempt: int = 1,
               ok: bool = True, error: str | None = None) -> int:
        if fallback_reason not in FALLBACK_REASONS:
            raise ValueError(f"unknown fallback_reason: {fallback_reason}")
        with self._lock:
            cur = self._connection.execute(
                "INSERT INTO llm_calls(session_id, generation_id, turn_id, utterance_id, role, model,"
                " prompt_tokens, completion_tokens, latency_ms, ttft_ms, finish_reason, fallback_reason,"
                " attempt, ok, error, created_at)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, generation_id, turn_id, utterance_id, role, model,
                 prompt_tokens, completion_tokens, latency_ms, ttft_ms, finish_reason,
                 fallback_reason, attempt, 1 if ok else 0, error, _utcnow()),
            )
            self._connection.commit()
            return int(cur.lastrowid)

    def count_session_calls(self, session_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE session_id = ?", (session_id,)
        ).fetchone()
        return int(row[0])

    def recent(self, session_id: str, limit: int = 20) -> list[dict]:
        rows = self._connection.execute(
            "SELECT * FROM llm_calls WHERE session_id = ? ORDER BY id LIMIT ?",
            (session_id, limit),
        ).fetchall()
        cols = [d[0] for d in self._connection.execute("SELECT * FROM llm_calls LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]
