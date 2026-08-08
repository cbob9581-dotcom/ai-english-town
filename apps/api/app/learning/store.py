"""learning 六表 + outbox 的 schema 与 CRUD。连接复用 event_store 的同一连接。
事务语义：add_evidence/upsert_* 不 commit（引擎单事务控制）；import_words/outbox_* 独立 commit。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

SCHEMA = """
CREATE TABLE IF NOT EXISTS word_lists(
  user_id    TEXT NOT NULL DEFAULT 'local',
  list_id    TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  source     TEXT NOT NULL DEFAULT 'import',
  created_at TEXT NOT NULL,
  UNIQUE(user_id, list_id)
);
CREATE TABLE IF NOT EXISTS learning_items(
  word_id      TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL DEFAULT 'local',
  lemma        TEXT NOT NULL,
  pos          TEXT,
  sense        TEXT,
  ipa          TEXT,
  cefr         TEXT,
  scene_tags   TEXT NOT NULL DEFAULT '[]',
  carrier      TEXT NOT NULL DEFAULT 'phrase',
  slot_categories TEXT NOT NULL DEFAULT '[]',
  source       TEXT NOT NULL,
  list_id      TEXT,
  created_at   TEXT NOT NULL,
  UNIQUE(user_id, lemma, pos, sense)
);
CREATE TABLE IF NOT EXISTS mastery_states(
  user_id             TEXT NOT NULL DEFAULT 'local',
  word_id             TEXT NOT NULL REFERENCES learning_items(word_id),
  productive_score    REAL NOT NULL DEFAULT 0.0,
  receptive_score     REAL NOT NULL DEFAULT 0.0,
  asr_confidence_score REAL NOT NULL DEFAULT 0.0,
  state               TEXT NOT NULL DEFAULT 'new',
  due                 TEXT,
  stability           REAL NOT NULL DEFAULT 0.0,
  difficulty          REAL NOT NULL DEFAULT 0.0,
  reps                INTEGER NOT NULL DEFAULT 0,
  lapses              INTEGER NOT NULL DEFAULT 0,
  last_review         TEXT,
  attempts            INTEGER NOT NULL DEFAULT 0,
  exposure_count      INTEGER NOT NULL DEFAULT 0,
  help_count          INTEGER NOT NULL DEFAULT 0,
  success_count       INTEGER NOT NULL DEFAULT 0,
  scaffolded_success_count INTEGER NOT NULL DEFAULT 0,
  last_scheduled_date TEXT,
  last_scheduled_rating INTEGER,
  fsrs_algorithm_version TEXT NOT NULL DEFAULT 'fsrs-5',
  updated_at          TEXT NOT NULL,
  PRIMARY KEY(user_id, word_id)
);
CREATE TABLE IF NOT EXISTS evidence_events(
  evidence_id           TEXT PRIMARY KEY,
  user_id               TEXT NOT NULL DEFAULT 'local',
  event_seq             INTEGER NOT NULL,
  session_id            TEXT NOT NULL,
  attempt_id            TEXT NOT NULL,
  turn_id               TEXT NOT NULL,
  objective_id          TEXT,
  word_id               TEXT NOT NULL REFERENCES learning_items(word_id),
  source                TEXT NOT NULL,
  prompt_level          INTEGER NOT NULL,
  axis                  TEXT NOT NULL,
  result                TEXT NOT NULL,
  confidence            REAL NOT NULL,
  evidence_policy_version TEXT NOT NULL DEFAULT 'v1',
  fsrs_algorithm_version  TEXT NOT NULL DEFAULT 'fsrs-5',
  created_at            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spontaneous_encounters(
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL DEFAULT 'local',
  session_id    TEXT NOT NULL,
  lemma         TEXT NOT NULL,
  pos           TEXT,
  turn_id       TEXT NOT NULL,
  encounter_no  INTEGER NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spontaneous_words(
  user_id      TEXT NOT NULL DEFAULT 'local',
  lemma        TEXT NOT NULL,
  pos          TEXT,
  first_seen_at  TEXT NOT NULL,
  last_seen_at   TEXT NOT NULL,
  encounter_count INTEGER NOT NULL DEFAULT 0,
  asked        INTEGER NOT NULL DEFAULT 0,
  promoted     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(user_id, lemma, pos)
);
CREATE TABLE IF NOT EXISTS evidence_outbox(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    TEXT NOT NULL DEFAULT 'local',
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


class LearningStore:
    def __init__(self, conn) -> None:
        self.conn = conn
        # Row 同时支持整数与键访问：event_store 的 tuple 索引（r[0]/r[1]）不受影响，
        # 学习层可用 m["state"]/m["due"]。FK ON 保证证据必须落在已导入词上（outbox 可测）。
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def import_words(self, user_id: str, list_id: str, name: str, items: list[dict]) -> tuple[int, int, int, int]:
        """独立事务（自行 commit）。word_id 缺省从 lemma_pos_sense 派生。"""
        created = items[0]["created_at"] if items else _utcnow()
        self.conn.execute(
            "INSERT OR IGNORE INTO word_lists(user_id, list_id, name, source, created_at) VALUES(?,?,?,?,?)",
            (user_id, list_id, name, "import", created))
        imported = known = missing = 0
        for it in items:
            lemma, pos, sense = it["lemma"], it["pos"], str(it.get("sense", "1"))
            word_id = it.get("word_id") or f"word_{lemma}_{pos}_{sense}"
            if self.conn.execute(
                    "SELECT 1 FROM learning_items WHERE user_id=? AND lemma=? AND pos=? AND sense=?",
                    (user_id, lemma, pos, sense)).fetchone():
                known += 1
                continue
            self.conn.execute(
                "INSERT OR IGNORE INTO learning_items(word_id, user_id, lemma, pos, sense, ipa, cefr, "
                "scene_tags, carrier, slot_categories, source, list_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (word_id, user_id, lemma, pos, sense, it.get("ipa"), it.get("cefr"),
                 it.get("scene_tags", "[]"), it.get("carrier", "phrase"),
                 it.get("slot_categories", "[]"), it.get("source", "quest"), list_id, created))
            if self.conn.execute("SELECT changes()").fetchone()[0]:
                imported += 1
            if not it.get("ipa") or not it.get("cefr"):
                missing += 1
        self.conn.commit()
        return imported, known, missing, len(items)

    def get_item(self, user_id: str, word_id: str):
        return self.conn.execute("SELECT * FROM learning_items WHERE user_id=? AND word_id=?",
                                 (user_id, word_id)).fetchone()

    def list_items_by_scene(self, user_id: str, archetype_id: str) -> list:
        """学习词按 scene_tag 命中场景（json_each）。mastery 缺失列 COALESCE：state→'new'、分→0.0。"""
        return self.conn.execute(
            "SELECT li.word_id, li.lemma, li.pos, li.carrier, li.slot_categories, li.scene_tags, "
            "       li.ipa, li.cefr, li.source, li.created_at, "
            "       COALESCE(ms.state, 'new') AS state, ms.due, "
            "       COALESCE(ms.productive_score, 0.0) AS productive_score, "
            "       COALESCE(ms.receptive_score, 0.0) AS receptive_score, "
            "       COALESCE(ms.asr_confidence_score, 0.0) AS asr_confidence_score "
            "FROM learning_items li "
            "LEFT JOIN mastery_states ms ON ms.user_id = li.user_id AND ms.word_id = li.word_id "
            "WHERE li.user_id=? AND EXISTS(SELECT 1 FROM json_each(li.scene_tags) WHERE json_each.value=?)",
            (user_id, archetype_id)).fetchall()

    def get_mastery(self, user_id: str, word_id: str):
        return self.conn.execute("SELECT * FROM mastery_states WHERE user_id=? AND word_id=?",
                                 (user_id, word_id)).fetchone()

    def get_mastery_for_scene(self, user_id: str, word_ids) -> dict:
        if not word_ids:
            return {}
        marks = ",".join("?" * len(word_ids))
        rows = self.conn.execute(
            f"SELECT * FROM mastery_states WHERE user_id=? AND word_id IN ({marks})",
            (user_id, *word_ids)).fetchall()
        return {r["word_id"]: r for r in rows}

    def upsert_mastery(self, user_id: str, word_id: str, **fields) -> None:
        """不 commit（引擎单事务内调用）。updated_at 缺省用当前 UTC。"""
        if "updated_at" not in fields:
            fields["updated_at"] = _utcnow()
        cols = list(fields)
        insert_cols = ["user_id", "word_id"] + cols
        placeholders = ", ".join("?" * len(insert_cols))
        sets = ", ".join(f"{c}=?" for c in cols)
        self.conn.execute(
            f"INSERT INTO mastery_states({', '.join(insert_cols)}) VALUES({placeholders}) "
            f"ON CONFLICT(user_id, word_id) DO UPDATE SET {sets}",
            [user_id, word_id] + [fields[c] for c in cols] + [fields[c] for c in cols])

    def upsert_mastery_counts(self, user_id: str, word_id: str, *, attempts: int = 0,
                              success_count: int = 0, scaffolded_success_count: int = 0,
                              help_count: int = 0, exposure_count: int = 0) -> None:
        """不 commit（引擎单事务内调用）。新行直接置值，已有行增量。"""
        self.conn.execute(
            "INSERT INTO mastery_states(user_id, word_id, attempts, success_count, "
            "scaffolded_success_count, help_count, exposure_count, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id, word_id) DO UPDATE SET "
            "attempts=mastery_states.attempts+excluded.attempts, "
            "success_count=mastery_states.success_count+excluded.success_count, "
            "scaffolded_success_count=mastery_states.scaffolded_success_count+excluded.scaffolded_success_count, "
            "help_count=mastery_states.help_count+excluded.help_count, "
            "exposure_count=mastery_states.exposure_count+excluded.exposure_count, "
            "updated_at=excluded.updated_at",
            (user_id, word_id, attempts, success_count, scaffolded_success_count,
             help_count, exposure_count, _utcnow()))

    def add_evidence(self, user_id: str, evidence: dict) -> None:
        """不 commit（引擎单事务内调用）。INSERT OR IGNORE 幂等；FK ON 校验词已导入。"""
        self.conn.execute(
            "INSERT OR IGNORE INTO evidence_events(evidence_id, user_id, event_seq, session_id, "
            "attempt_id, turn_id, objective_id, word_id, source, prompt_level, axis, result, "
            "confidence, evidence_policy_version, fsrs_algorithm_version, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (evidence["evidence_id"], user_id, evidence["event_seq"], evidence["session_id"],
             evidence["attempt_id"], evidence["turn_id"], evidence.get("objective_id"),
             evidence["word_id"], evidence["source"], evidence["prompt_level"],
             evidence["axis"], evidence["result"], evidence["confidence"],
             evidence["evidence_policy_version"], evidence["fsrs_algorithm_version"],
             evidence["created_at"]))

    def evidence_for_word(self, user_id: str, word_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_events WHERE user_id=? AND word_id=? ORDER BY created_at",
            (user_id, word_id)).fetchall()
        return [dict(r) for r in rows]

    def all_words(self, user_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT li.word_id, li.lemma, li.pos, li.ipa, li.cefr, li.scene_tags, li.carrier, li.source, "
            "       COALESCE(ms.state, 'new') AS state, ms.due, ms.stability, ms.difficulty, ms.reps, ms.lapses, "
            "       COALESCE(ms.productive_score, 0.0) AS productive_score, "
            "       COALESCE(ms.receptive_score, 0.0) AS receptive_score, "
            "       COALESCE(ms.asr_confidence_score, 0.0) AS asr_confidence_score "
            "FROM learning_items li "
            "LEFT JOIN mastery_states ms ON ms.user_id = li.user_id AND ms.word_id = li.word_id "
            "WHERE li.user_id=? ORDER BY li.created_at", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def outbox_push(self, user_id: str, payload_json: str) -> None:
        """独立事务（rollback 后入 outbox 必须落库）。"""
        self.conn.execute("INSERT INTO evidence_outbox(user_id, payload_json, created_at) VALUES(?,?,?)",
                          (user_id, payload_json, _utcnow()))
        self.conn.commit()

    def outbox_drain(self, user_id: str) -> list[str]:
        rows = self.conn.execute("SELECT id, payload_json FROM evidence_outbox WHERE user_id=? ORDER BY id",
                                 (user_id,)).fetchall()
        payloads = [r["payload_json"] for r in rows]
        if rows:
            self.conn.execute("DELETE FROM evidence_outbox WHERE user_id=?", (user_id,))
            self.conn.commit()
        return payloads

    def resolve_word_id_from_store(self, lemma: str, pos: str, sense: int = 1) -> str | None:
        """多 sense 取 created_at 最早（并列取 rowid 最小）。"""
        row = self.conn.execute(
            "SELECT word_id FROM learning_items WHERE user_id='local' AND lemma=? AND pos=? "
            "ORDER BY created_at, rowid LIMIT 1", (lemma, pos)).fetchone()
        return row["word_id"] if row else None
