"""session_events 持久化 + SQLite WAL + 单一写入队列（锁）。
学习证据等事件先写库再对外确认，断线可补发。"""
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_events(
  sequence    INTEGER NOT NULL,
  event_id    TEXT NOT NULL UNIQUE,
  session_id  TEXT NOT NULL,
  event_type  TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  PRIMARY KEY(session_id, sequence)
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(db_path), check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.executescript(_SCHEMA)
        self.connection.commit()
        self._lock = threading.Lock()

    def append(self, session_id: str, event_type: str, payload: dict, event_id: str | None = None) -> int:
        """返回该事件的 sequence。event_id 相同则幂等（返回既有 sequence）。"""
        with self._lock:
            row = self.connection.execute(
                "SELECT sequence FROM session_events WHERE event_id = ?", (event_id,)
            ).fetchone() if event_id else None
            if row is not None:
                return int(row[0])
            seq_row = self.connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            seq = int(seq_row[0])
            self.connection.execute(
                "INSERT OR IGNORE INTO session_events(sequence, event_id, session_id, event_type, payload_json, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (seq, event_id or str(uuid.uuid4()), session_id, event_type, json.dumps(payload, ensure_ascii=False), _utcnow()),
            )
            self.connection.commit()
            return seq

    def list_after(self, session_id: str, seq: int) -> list[dict]:
        rows = self.connection.execute(
            "SELECT sequence, event_type, payload_json FROM session_events WHERE session_id = ? AND sequence > ? ORDER BY sequence",
            (session_id, seq),
        ).fetchall()
        return [{"sequence": int(r[0]), "event_type": r[1], "payload": json.loads(r[2])} for r in rows]
