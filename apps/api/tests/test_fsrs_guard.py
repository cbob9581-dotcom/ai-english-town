from datetime import datetime, timezone

from app.learning.fsrs import schedule, to_fsrs_card


def test_stability_zero_routes_to_new_card_path():
    # 非 new 态 + stability=0.0：guard 走新卡路径（Learning/step 0），不得触发 py-fsrs ZeroDivisionError
    row = {"state": "learning", "stability": 0.0, "difficulty": 5.0,
           "due": "2026-08-08T00:00:00Z", "last_review": None}
    card = to_fsrs_card(row, card_id=42)
    assert card.state.name == "Learning"
    # 新卡路径判别特征：stability/difficulty 为 None（pre-fix 透传 0.0/5.0，此处应失败）
    assert card.stability is None
    assert card.difficulty is None
    # 直接打排期：0 稳定性卡不得触发 ZeroDivisionError（0**负幂）——本 task 核心防护
    updated = schedule(card, 3, datetime(2026, 8, 8, 1, 0, tzinfo=timezone.utc))
    assert updated.stability > 0


def test_positive_stability_kept():
    row = {"state": "review", "stability": 3.0, "difficulty": 5.0,
           "due": "2026-08-08T00:00:00Z", "last_review": "2026-08-07T00:00:00Z"}
    card = to_fsrs_card(row, card_id=42)
    assert card.state.name == "Review"
    assert card.stability == 3.0
