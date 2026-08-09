from app.learning.fsrs import to_fsrs_card


def test_stability_zero_routes_to_new_card_path():
    # 非 new 态 + stability=0.0：guard 走新卡路径（Learning/step 0），不得触发 py-fsrs ZeroDivisionError
    row = {"state": "learning", "stability": 0.0, "difficulty": 5.0,
           "due": "2026-08-08T00:00:00Z", "last_review": None}
    card = to_fsrs_card(row, card_id=42)
    assert card.state.name == "Learning"


def test_positive_stability_kept():
    row = {"state": "review", "stability": 3.0, "difficulty": 5.0,
           "due": "2026-08-08T00:00:00Z", "last_review": "2026-08-07T00:00:00Z"}
    card = to_fsrs_card(row, card_id=42)
    assert card.state.name == "Review"
    assert card.stability == 3.0
