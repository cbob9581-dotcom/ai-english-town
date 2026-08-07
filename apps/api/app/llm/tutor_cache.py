"""tutor_cache 表：word_id 主键，缓存 scaffold + 已合成音频路径。"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tutor_cache(
  word_id     TEXT PRIMARY KEY,
  scaffold    TEXT NOT NULL,
  model       TEXT NOT NULL,
  audio_path  TEXT,
  sample_rate INTEGER,
  created_at  TEXT NOT NULL
);
"""


class TutorCache:
    def __init__(self, connection: sqlite3.Connection, cache_dir: Path) -> None:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(_SCHEMA)
        connection.commit()
        self._connection = connection
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def audio_path(self, word_id: str) -> Path:
        return self._cache_dir / f"{word_id}.wav"

    def get(self, word_id: str) -> dict | None:
        row = self._connection.execute(
            "SELECT word_id, scaffold, model, audio_path, sample_rate, created_at FROM tutor_cache WHERE word_id = ?",
            (word_id,),
        ).fetchone()
        if row is None:
            return None
        return {"word_id": row[0], "scaffold": row[1], "model": row[2],
                "audio_path": row[3], "sample_rate": row[4], "created_at": row[5]}

    def put(self, word_id: str, scaffold: str, model: str, audio_path: str,
            sample_rate: int) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO tutor_cache(word_id, scaffold, model, audio_path, sample_rate, created_at)"
                " VALUES(?, ?, ?, ?, ?, ?)",
                (word_id, scaffold, model, audio_path, sample_rate,
                 datetime.now(timezone.utc).isoformat()),
            )
            self._connection.commit()
