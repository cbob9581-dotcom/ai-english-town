"""py-fsrs 5.x 包装层：FSRS-5 排期，确定性强制的薄封装。
py-fsrs 5.x 的 review_card 返回 (Card, ReviewLog)，且 Card 无 New 态
（只有 Learning/Review/Relearning）——'new' 由引擎维护（尚未 schedule）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

from fsrs import DEFAULT_PARAMETERS, Card, Rating, Scheduler, State

_STATE_TO_FSRS = {"learning": State.Learning, "review": State.Review, "relearning": State.Relearning}
_FSRS_TO_STATE = {"Learning": "learning", "Review": "review", "Relearning": "relearning"}


def guard_parameter_count() -> int:
    """FSRS-5 = 19 参数；升级踩线（FSRS-6 = 21）立即暴露。"""
    assert len(DEFAULT_PARAMETERS) == 19, f"expected FSRS-5 (19 params), got {len(DEFAULT_PARAMETERS)}"
    return len(DEFAULT_PARAMETERS)


def _scheduler() -> Scheduler:
    return Scheduler(desired_retention=0.9, enable_fuzzing=False,
                     learning_steps=(), relearning_steps=())


def to_fsrs_card(row: Mapping, *, card_id: int) -> Card:
    """mastery_states 行 → py-fsrs Card。state='new' 或 stability<=0（非新态但无效稳定性）
    → 全新 Card（默认 Learning/step 0）。guard 防 py-fsrs 对 0 稳定性卡 ZeroDivisionError
    （0**负幂）；走新卡路径而非硬塞 1.0，避免破坏复习节奏。"""
    if row["state"] == "new" or row.get("stability", 0.0) <= 0:
        return Card(card_id=card_id)
    due = datetime.fromisoformat(row["due"]) if row.get("due") else datetime.now(timezone.utc)
    last = datetime.fromisoformat(row["last_review"]) if row.get("last_review") else None
    step = 0 if row["state"] == "learning" else (1 if row["state"] == "relearning" else None)
    return Card(card_id=card_id, state=_STATE_TO_FSRS[row["state"]], step=step,
                stability=row["stability"], difficulty=row["difficulty"],
                due=due, last_review=last)


def from_fsrs_card(card: Card) -> dict:
    return {"state": _FSRS_TO_STATE[card.state.name],
            "due": card.due.isoformat(),
            "last_review": card.last_review.isoformat() if card.last_review else None,
            "stability": card.stability, "difficulty": card.difficulty}


def schedule(card: Card, rating: int, now: datetime) -> Card:
    """now 必须 tz-aware UTC（review_card 校验）。返回更新后的 Card。"""
    updated, _log = _scheduler().review_card(card, Rating(rating), now)
    return updated
