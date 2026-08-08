from datetime import datetime, timezone, timedelta

import pytest
from fsrs import Card, Rating

from app.learning.fsrs import from_fsrs_card, guard_parameter_count, schedule, to_fsrs_card


def test_fsrs5_has_19_parameters() -> None:
    assert guard_parameter_count() == 19


def test_fresh_good_schedule_golden() -> None:
    now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    card = Card(card_id=1)
    updated = schedule(card, Rating.Good, now)
    assert updated.state.name == "Review"          # learning_steps=() → 直进 Review
    assert updated.last_review == now
    assert (updated.due - now).days == 3
    assert updated.stability == pytest.approx(3.173, abs=1e-3)
    assert updated.difficulty == pytest.approx(5.282434422319005, abs=1e-9)


def test_second_good_extends_interval() -> None:
    now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    card = Card(card_id=1)
    s1 = schedule(card, Rating.Good, now)
    s2 = schedule(s1, Rating.Good, now + timedelta(days=7))
    assert (s2.due - (now + timedelta(days=7))).days == 19
    assert s2.stability == pytest.approx(18.858155152579183, abs=1e-9)


def test_again_on_fresh_card_short_interval() -> None:
    now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    updated = schedule(Card(card_id=3), Rating.Again, now)
    assert (updated.due - now).days == 1
    assert updated.stability == pytest.approx(0.40255, abs=1e-5)


def test_roundtrip_to_from_fsrs_card() -> None:
    row = {"state": "review", "stability": 3.173, "difficulty": 5.28,
           "due": "2026-08-11T12:00:00+00:00", "last_review": "2026-08-08T12:00:00+00:00"}
    card = to_fsrs_card(row, card_id=7)
    assert card.state.name == "Review"
    out = from_fsrs_card(card)
    assert out["state"] == "review"
    assert out["due"] == row["due"]
    assert out["stability"] == pytest.approx(3.173)


def test_new_state_maps_to_fresh_card() -> None:
    row = {"state": "new", "stability": 0.0, "difficulty": 0.0, "due": None, "last_review": None}
    card = to_fsrs_card(row, card_id=5)
    assert card.state.name == "Learning"   # Card() 默认 Learning/step 0
