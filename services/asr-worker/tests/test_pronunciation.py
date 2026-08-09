import math

import numpy as np
import pytest

from asr_worker.pronunciation import AlignResult, _target_segments, expected_phonemes_from_ipa, score_gop


class _MockAligner:
    """固定后验 + 固定音素区间：断言 margin 公式、分母排除自身、blank 排除，mock 断言精确值。"""
    def __init__(self, posteriors, segments, symbol_by_col):
        self._posteriors = posteriors
        self._segments = segments
        self._symbol_by_col = symbol_by_col

    def align(self, audio, expected_phonemes):
        return AlignResult(self._posteriors, self._segments, self._symbol_by_col)


def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def test_target_segments_skip_blank_frames():
    path = [0, 0, 1, 1, 1, 1]            # 0=blank；2..5=token1（l）共 4 帧
    segs = _target_segments(path, [1], blank=0, id_to_phone={1: "l"})
    assert segs == [("l", 2, 6)]         # 半开区间 [i0, i1)：blank 帧不进入音素区间


def test_score_gop_margin_excludes_self_and_blank():
    # 列：0=l, 1=o, 2=f, 3=x（同属英语子集，竞争者）, 4=blank（非英语子集，应排除）
    symbol_by_col = {0: "l", 1: "o", 2: "f", 3: "x", 4: "<pad>"}
    posteriors = np.array([
        [0.8, 0.05, 0.05, 0.05, 0.0],    # l 帧：P(l)=0.8
        [0.05, 0.7, 0.10, 0.10, 0.05],   # o 帧：P(o)=0.7
        [0.05, 0.10, 0.70, 0.10, 0.05],  # f 帧：P(f)=0.7
    ], dtype=np.float32)
    segments = [("l", 0, 1), ("o", 1, 2), ("f", 2, 3)]
    aligner = _MockAligner(posteriors, segments, symbol_by_col)
    english = frozenset({"l", "o", "f", "x"})
    result = score_gop(b"\x00\x00" * 1600, ["l", "o", "f"], aligner,
                       ipa_to_espeak={"l": "l", "oʊ": "o", "f": "f"}, english_symbols=english)
    assert result is not None and result["degraded"] is False
    # l: log(0.8) − max(log 0.05, log 0.05, log 0.05) = log(16) → sigmoid
    assert result["phoneme_scores"]["l"] == pytest.approx(_sigmoid(math.log(16)), rel=1e-4)
    # o: log(0.7) − max(log 0.05, log 0.10, log 0.10)=log(0.7)−log(0.10)=log(7)
    assert result["phoneme_scores"]["o"] == pytest.approx(_sigmoid(math.log(7)), rel=1e-4)
    # gop = mean of 3 phone scores
    assert result["gop"] == pytest.approx(
        sum(result["phoneme_scores"].values()) / 3, rel=1e-6)


def test_expected_phonemes_from_ipa_unmapped_returns_none():
    assert expected_phonemes_from_ipa("/loʊf/", ipa_to_espeak={"l": "l", "oʊ": "o"}) is None   # f 缺映射
    assert expected_phonemes_from_ipa("/loʊf/", ipa_to_espeak={"l": "l", "oʊ": "o", "f": "f"}) == ["l", "o", "f"]


def test_score_gop_empty_phonemes_degrades():
    assert score_gop(b"\x00\x00" * 16, [], _MockAligner(np.zeros((1, 3), np.float32), [], {0: "l"})) is None
