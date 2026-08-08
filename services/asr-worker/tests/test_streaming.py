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


def test_finalize_returns_words_when_enabled():
    class FakeEngine:
        def transcribe(self, audio, final=False, **kwargs):
            return {"text": "a loaf", "segments": [{"start": 0.1, "end": 0.9, "text": "a loaf"}],
                    "language": "en", "avg_logprob": -0.2,
                    "words": [{"word": "a", "start": 0.1, "end": 0.3, "probability": 0.9},
                              {"word": "loaf", "start": 0.4, "end": 0.9, "probability": 0.95}]}
    from asr_worker.streaming import RollingTranscriber, UtteranceState
    rt = RollingTranscriber(FakeEngine().transcribe, word_timestamps=True)
    out = rt.finalize(UtteranceState("u1"), object(), 16000)
    assert out["finalText"] == "a loaf"
    assert out["words"][1]["word"] == "loaf"
    assert abs(out["words"][1]["probability"] - 0.95) < 1e-6

def test_finalize_words_absent_when_disabled():
    class FakeEngine:
        def transcribe(self, audio, final=False, **kwargs):
            return {"text": "hi", "segments": [], "language": "en", "avg_logprob": -0.3}
    from asr_worker.streaming import RollingTranscriber, UtteranceState
    out = RollingTranscriber(FakeEngine().transcribe, word_timestamps=False).finalize(UtteranceState("u1"), object(), 16000)
    assert "words" not in out        # 未开启 → 无 words 键（API 侧据此回退）

def test_finalize_words_empty_on_silence():
    class FakeEngine:
        def transcribe(self, audio, final=False, **kwargs):
            return {"text": "", "segments": [], "language": "en", "avg_logprob": -1.0, "words": []}
    from asr_worker.streaming import RollingTranscriber, UtteranceState
    out = RollingTranscriber(FakeEngine().transcribe, word_timestamps=True).finalize(UtteranceState("u1"), object(), 16000)
    assert out["words"] == []        # 静音 → 空数组（API 侧按"空→回退"处理，不按 None）

def test_word_timestamps_gate_by_model_floor():
    from asr_worker.whisper_engine import word_timestamps_active
    class E:
        model_name = "distil-large-v3"
    class E2:
        model_name = "whisper-large-v3"
    assert word_timestamps_active(E(), True, "whisper-large-v3") is False   # 低于门槛
    assert word_timestamps_active(E2(), True, "whisper-large-v3") is True
    assert word_timestamps_active(E2(), False, "whisper-large-v3") is False  # env 关
