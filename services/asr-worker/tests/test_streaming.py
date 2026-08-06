import numpy as np

from asr_worker.streaming import RollingTranscriber, UtteranceState, merge_windows


def test_merge_windows_keeps_common_prefix() -> None:
    assert merge_windows("I would like a loaf", "I would like a bagel") == "I would like a"


def test_partial_follows_stable_prefix_growth() -> None:
    # 语义：stable = 相邻两次转写的公共前缀（即"连续两次一致"的 token）
    texts = iter(["I would", "I would like", "I would like a loaf"])
    tr = RollingTranscriber(lambda w, **kw: next(texts), window_s=2.0, check_ms=300)
    u = UtteranceState("u1")
    samples = np.zeros(32000, dtype=np.float32)  # 2s @16k
    events: list[dict] = []
    for t in (300, 600, 900):
        events += tr.feed(u, samples, 16000, now_ms=t)
    partials = [e for e in events if e["type"] == "partial"]
    assert [p["stableText"] for p in partials] == ["I would", "I would like"]
    assert partials[-1]["revision"] == 2


def test_final_replaces_and_increments() -> None:
    tr = RollingTranscriber(lambda w, **kw: "I would like a loaf", window_s=2.0, check_ms=300)
    u = UtteranceState("u1")
    samples = np.zeros(32000, dtype=np.float32)
    tr.feed(u, samples, 16000, now_ms=300)
    tr.feed(u, samples, 16000, now_ms=600)
    final = tr.finalize(u, samples, 16000)
    assert final["type"] == "final"
    assert final["finalText"] == "I would like a loaf"


def test_no_repeated_partial_for_unchanged_stable() -> None:
    tr = RollingTranscriber(lambda w, **kw: "hi there", window_s=2.0, check_ms=300)
    u = UtteranceState("u1")
    samples = np.zeros(32000, dtype=np.float32)
    events: list[dict] = []
    for t in (300, 600, 900):
        events += tr.feed(u, samples, 16000, now_ms=t)
    stable_msgs = [e for e in events if e["type"] == "partial"]
    assert len(stable_msgs) == 1  # 同一个 stableText 只发一次
