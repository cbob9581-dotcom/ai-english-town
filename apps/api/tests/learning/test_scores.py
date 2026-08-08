import math

import pytest

from app.learning.scores import WEIGHTS, normalize_asr_confidence, update_score


def test_positive_delta_bounded_upper() -> None:
    assert update_score(0.0, 1.0, 1.0) == 0.35                       # 0 + .35*1*(1-0)
    assert update_score(0.9, 1.0, 1.0) == pytest.approx(0.9 + 0.35 * 0.1)
    assert update_score(1.0, 1.0, 1.0) == 1.0                        # 封顶


def test_negative_delta_bounded_lower() -> None:
    assert update_score(0.5, -0.35, 1.0) == pytest.approx(0.5 - 0.35 * 0.35 * 0.5)
    assert update_score(0.0, -0.35, 1.0) == 0.0                      # 保底


def test_confidence_scales_delta() -> None:
    lo = update_score(0.0, 1.0, 0.5)   # delta = 0.5
    hi = update_score(0.0, 1.0, 1.0)   # delta = 1.0
    assert 0.0 < lo < hi < 1.0


def test_weights_match_spec() -> None:
    assert WEIGHTS["spontaneous_production"] == (1.0, "productive")
    assert WEIGHTS["prompted_production"] == (0.65, "productive")
    assert WEIGHTS["repetition"] == (0.4, "asr_confidence")
    assert WEIGHTS["action_understanding"] == (0.55, "receptive")
    assert WEIGHTS["help"] == (-0.35, "productive")
    assert WEIGHTS["error"] == (-0.5, "productive")


def test_normalize_asr_confidence() -> None:
    assert normalize_asr_confidence(-0.3) == pytest.approx(math.exp(-0.3))
    assert 0.0 < normalize_asr_confidence(-0.01) < 1.0
    assert normalize_asr_confidence(-100.0) == pytest.approx(0.0, abs=1e-9)
