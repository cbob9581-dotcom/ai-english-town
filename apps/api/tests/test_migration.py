import sqlite3
from app.learning.store import LearningStore

_OLD_MASTERY = """
CREATE TABLE mastery_states(
  user_id TEXT NOT NULL DEFAULT 'local',
  word_id TEXT NOT NULL REFERENCES learning_items(word_id),
  productive_score REAL NOT NULL DEFAULT 0.0,
  receptive_score REAL NOT NULL DEFAULT 0.0,
  asr_confidence_score REAL NOT NULL DEFAULT 0.0,
  state TEXT NOT NULL DEFAULT 'new', due TEXT,
  stability REAL NOT NULL DEFAULT 0.0, difficulty REAL NOT NULL DEFAULT 0.0,
  reps INTEGER NOT NULL DEFAULT 0, lapses INTEGER NOT NULL DEFAULT 0,
  last_review TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  exposure_count INTEGER NOT NULL DEFAULT 0, help_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0,
  scaffolded_success_count INTEGER NOT NULL DEFAULT 0,
  last_scheduled_date TEXT, last_scheduled_rating INTEGER,
  fsrs_algorithm_version TEXT NOT NULL DEFAULT 'fsrs-5',
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, word_id)
);
"""

def test_mastery_states_has_new_column_fresh():
    conn = sqlite3.connect(":memory:")
    st = LearningStore(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(mastery_states)")}
    assert "asr_word_confidence_score" in cols
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "memory_state" in tables

def test_migration_adds_column_and_memory_state_to_existing_db(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_MASTERY)          # 模拟 phase-4 旧库（无新列/新表）
    conn.commit(); conn.close()
    conn2 = sqlite3.connect(db)
    st = LearningStore(conn2)                  # 构造跑 _migrate()
    cols = {r[1] for r in conn2.execute("PRAGMA table_info(mastery_states)")}
    assert "asr_word_confidence_score" in cols
    tables = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "memory_state" in tables
    st._migrate()                              # 幂等：再跑不报错
    assert "asr_word_confidence_score" in {r[1] for r in conn2.execute("PRAGMA table_info(mastery_states)")}
