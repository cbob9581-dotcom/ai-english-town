"""WhisperEngine.transcribe 的真实路径测试（fake faster-whisper model）。
此前仅 streaming 侧用 FakeEngine（dict 契约）测过；这里直接测引擎对
faster-whisper Segment/TranscriptionInfo 的读取——回归 1.2.1 的
`TranscriptionInfo` 无 `avg_logprob` 属性（它在 Segment 上）。"""
import numpy as np

from asr_worker.whisper_engine import WhisperEngine


class _Word:
    def __init__(self, word: str, start: float, end: float, probability: float):
        self.word = word
        self.start = start
        self.end = end
        self.probability = probability


class _Seg:
    def __init__(self, start: float, end: float, text: str, avg_logprob: float, words=None):
        self.start = start
        self.end = end
        self.text = text
        self.avg_logprob = avg_logprob
        self.words = words or []


class _Info:
    language = "en"


class _FakeModel:
    def __init__(self, segments: list[_Seg]):
        self._segments = segments

    def transcribe(self, audio, **kwargs):
        return iter(self._segments), _Info()


def test_transcribe_avg_logprob_duration_weighted() -> None:
    eng = WhisperEngine(_FakeModel([
        _Seg(0.0, 1.0, "hello", -0.2),
        _Seg(1.0, 3.0, " world", -0.4),
    ]), "cuda")
    out = eng.transcribe(np.zeros(16000, dtype=np.float32))
    # 时长加权：(-0.2*1 + -0.4*2) / 3 = -1.0/3
    assert abs(out["avg_logprob"] - (-1.0 / 3.0)) < 1e-9
    assert out["text"] == "hello world"
    assert out["language"] == "en"


def test_transcribe_avg_logprob_default_on_no_segments() -> None:
    eng = WhisperEngine(_FakeModel([]), "cuda")
    out = eng.transcribe(np.zeros(16000, dtype=np.float32))
    assert out["avg_logprob"] == -0.5  # 静音无 segment → 消费方默认值


def test_transcribe_word_timestamps_enabled() -> None:
    eng = WhisperEngine(_FakeModel([
        _Seg(0.0, 1.0, "a loaf", -0.2, words=[
            _Word("a", 0.1, 0.3, 0.9),
            _Word("loaf", 0.4, 0.9, 0.95),
        ]),
    ]), "cuda")
    out = eng.transcribe(np.zeros(16000, dtype=np.float32), word_timestamps=True)
    assert [w["word"] for w in out["words"]] == ["a", "loaf"]
    assert abs(out["words"][1]["probability"] - 0.95) < 1e-9


def test_transcribe_no_words_key_when_disabled() -> None:
    eng = WhisperEngine(_FakeModel([_Seg(0.0, 1.0, "hi", -0.3)]), "cuda")
    out = eng.transcribe(np.zeros(16000, dtype=np.float32))
    assert "words" not in out
