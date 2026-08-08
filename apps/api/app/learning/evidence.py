"""证据分类 + 更新规则 + 日闸（纯函数 + store 更新，由引擎在单事务内调用）。"""
from __future__ import annotations

import hashlib
from datetime import datetime

from app.learning import fsrs as fsrs_mod
from app.learning.scores import WEIGHTS, normalize_asr_confidence, update_score
from app.llm.lexmatch import token_contains

MIN_CONFIDENCE = 0.6

# 日闸：当日最低有效评分代表当日。error=Again(1) 最低。
_RATING = {"spontaneous_production": 4, "prompted_production": 3,
           "repetition": 2, "action_understanding": 3, "error": 1}
# no_attempt/uncertain：ASR 噪声不惩罚——不更新分、不计尝试、不进排期。
# help 的 result 是 neutral：**要更新分**（负权重 -0.35）但无评分、不进排期。
_NON_SCORED = {"no_attempt", "uncertain"}


def classify_round(scene_words: dict[str, str], npc_text: str, user_text: str,
                   confidence: float, *, target_word_ids: set[str]) -> list[dict]:
    """scene_words: {word_id: lemma}（含选词补充进 target 的）。按 target_word_ids 迭代，
    保证选词补充但无场景实体的词也能判（lemma 缺则跳过）。返回证据草稿。"""
    conf = normalize_asr_confidence(confidence) if confidence < 0 else confidence
    drafts: list[dict] = []
    for wid in target_word_ids:
        lemma = scene_words.get(wid)
        if not lemma:
            continue
        in_npc = token_contains(npc_text, lemma)
        in_user = token_contains(user_text, lemma)
        if in_user and in_npc:
            source, pl = "prompted_production", 1
            result = "success" if conf >= MIN_CONFIDENCE else "uncertain"
        elif in_user:
            source, pl = "spontaneous_production", 0
            result = "success" if conf >= MIN_CONFIDENCE else "uncertain"
        elif in_npc:
            source, pl, result = "prompted_production", 1, "no_attempt"
        else:
            continue
        _, axis = WEIGHTS[source]
        drafts.append({"word_id": wid, "source": source, "prompt_level": pl,
                       "axis": axis, "result": result, "confidence": conf})
    return drafts


def _card_id(wid: str) -> int:
    return int.from_bytes(hashlib.sha1(wid.encode()).digest()[:4], "big")


def apply_evidence(store, user_id: str, ev: dict, *, now: datetime) -> None:
    """在调用方单事务内：add_evidence + 三维分（按 axis）+ 计数 + 进入条件/日闸排期。"""
    store.add_evidence(user_id, ev)
    source, result = ev["source"], ev["result"]
    weight, axis = WEIGHTS[source]
    wid = ev["word_id"]
    m = store.get_mastery(user_id, wid)
    if m is None:
        store.upsert_mastery(user_id, wid, updated_at=now.isoformat())
        m = store.get_mastery(user_id, wid)
    col = {"productive": "productive_score", "receptive": "receptive_score",
           "asr_confidence": "asr_confidence_score"}[axis]
    if result not in _NON_SCORED:
        store.upsert_mastery(user_id, wid, **{col: update_score(m[col], weight, ev["confidence"])},
                             updated_at=now.isoformat())

    store.upsert_mastery_counts(
        user_id, wid,
        attempts=1 if result in ("success", "error") else 0,
        success_count=1 if result == "success" else 0,
        scaffolded_success_count=1 if (result == "success" and ev["prompt_level"] > 0) else 0,
        help_count=1 if source == "help" else 0,
        exposure_count=1)

    m = store.get_mastery(user_id, wid)
    rating = _RATING.get(source)
    if rating is None or result in _NON_SCORED:
        return
    entered = m["state"] == "new" and m["scaffolded_success_count"] >= 1 and m["attempts"] >= 2
    if entered:
        # 首次进入：从 fresh card 排期 → 命中黄金值（fresh+Good → stability 3.173 / due +3d）
        _schedule(store, user_id, wid, fsrs_mod.to_fsrs_card(dict(m), card_id=_card_id(wid)), rating, now)
    else:
        _apply_daily_gate(store, user_id, wid, rating, now)


def _apply_daily_gate(store, user_id: str, wid: str, rating: int, now: datetime) -> None:
    m = store.get_mastery(user_id, wid)
    if m["state"] == "new":
        return  # 未满足进入条件，不排期
    today = now.date().isoformat()
    if m["last_scheduled_date"] != today:
        _schedule(store, user_id, wid, fsrs_mod.to_fsrs_card(dict(m), card_id=_card_id(wid)), rating, now, today)
    elif m["last_scheduled_rating"] and rating < m["last_scheduled_rating"]:
        _schedule(store, user_id, wid, fsrs_mod.to_fsrs_card(dict(m), card_id=_card_id(wid)), rating, now, today)


def _schedule(store, user_id: str, wid: str, card, rating: int, now: datetime,
              today: str | None = None) -> None:
    m = store.get_mastery(user_id, wid)
    updated = fsrs_mod.schedule(card, rating, now)
    fields = fsrs_mod.from_fsrs_card(updated)
    fields.update({"last_scheduled_date": today or now.date().isoformat(),
                   "last_scheduled_rating": rating,
                   "reps": m["reps"] + 1,
                   "lapses": m["lapses"] + (1 if rating == 1 else 0),
                   "updated_at": now.isoformat()})
    store.upsert_mastery(user_id, wid, **fields)
