"""偶遇词流：明细行（可追溯）+ 聚合表（API/提升）+ 手动提升。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from app.learning.store import LearningStore


def _wid(lemma: str, pos: str, sense: int = 1) -> str:
    return f"word_{lemma}_{pos}_{sense}"


def record_exposure(store: LearningStore, user_id: str, session_id: str,
                    lemma: str, pos: str, turn_id: str, *, now: datetime) -> None:
    created = now.isoformat()
    store.conn.execute(
        "INSERT INTO spontaneous_encounters(id, user_id, session_id, lemma, pos, turn_id, encounter_no, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (f"enc_{uuid.uuid4().hex[:12]}", user_id, session_id, lemma, pos, turn_id,
         store.conn.execute("SELECT COUNT(*)+1 FROM spontaneous_encounters WHERE user_id=? AND lemma=? AND pos=?",
                            (user_id, lemma, pos)).fetchone()[0], created))
    store.conn.execute(
        "INSERT INTO spontaneous_words(user_id, lemma, pos, first_seen_at, last_seen_at, encounter_count) "
        "VALUES(?,?,?,?,?,1) "
        "ON CONFLICT(user_id, lemma, pos) DO UPDATE SET last_seen_at=excluded.last_seen_at, "
        "encounter_count=encounter_count+1",
        (user_id, lemma, pos, created, created))
    store.conn.commit()


def record_ask(store: LearningStore, user_id: str, session_id: str,
               lemma: str, pos: str, turn_id: str, *, now: datetime) -> None:
    record_exposure(store, user_id, session_id, lemma, pos, turn_id, now=now)
    store.conn.execute(
        "UPDATE spontaneous_words SET asked=1 WHERE user_id=? AND lemma=? AND pos=?",
        (user_id, lemma, pos))
    store.conn.commit()


def promote(store: LearningStore, user_id: str, lemmas: list[str], *, now: datetime) -> int:
    created = now.isoformat()
    n = 0
    for lemma in lemmas:
        agg = store.conn.execute(
            "SELECT pos FROM spontaneous_words WHERE user_id=? AND lemma=?", (user_id, lemma)).fetchone()
        pos = agg[0] if agg else "n"
        wid = _wid(lemma, pos)
        store.conn.execute(
            "INSERT OR IGNORE INTO learning_items(word_id, user_id, lemma, pos, sense, scene_tags, carrier, "
            "slot_categories, source, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (wid, user_id, lemma, pos, "1", json.dumps([]), "phrase", json.dumps([]), "free", created))
        store.conn.execute(
            "INSERT OR IGNORE INTO mastery_states(user_id, word_id, updated_at) VALUES(?,?,?)",
            (user_id, wid, created))
        cur = store.conn.execute("SELECT changes()").fetchone()[0]
        if cur:
            n += 1
        store.conn.execute(
            "UPDATE spontaneous_words SET promoted=1 WHERE user_id=? AND lemma=? AND pos=?",
            (user_id, lemma, pos))
    store.conn.commit()
    return n


def list_spontaneous(store: LearningStore, user_id: str) -> list[dict]:
    rows = store.conn.execute(
        "SELECT lemma, pos, first_seen_at, last_seen_at, encounter_count, asked, promoted "
        "FROM spontaneous_words WHERE user_id=? ORDER BY last_seen_at DESC", (user_id,)).fetchall()
    return [{"lemma": r[0], "pos": r[1], "firstSeenAt": r[2], "lastSeenAt": r[3],
             "encounterCount": r[4], "asked": bool(r[5]), "promoted": bool(r[6])} for r in rows]
