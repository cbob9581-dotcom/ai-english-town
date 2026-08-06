"""Kokoro ONNX，CPU 优先。阶段 1 固定音色（如 af_bella）。"""
from __future__ import annotations

import numpy as np
from kokoro_onnx import Kokoro  # 需安装 kokoro-onnx（见 Global Constraints 版本说明）


class KokoroEngine:
    def __init__(self, voice: str = "af_bella") -> None:
        self.voice = voice
        self._kokoro: Kokoro | None = None

    def load(self) -> "KokoroEngine":
        self._kokoro = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")
        return self

    def synthesize(self, text: str) -> bytes:
        assert self._kokoro is not None, "call load() first"
        samples, sr = self._kokoro.create(text, voice=self.voice, speed=1.0)
        pcm16 = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
        return _wav_bytes(pcm16, sr)


def _wav_bytes(pcm16: np.ndarray, sample_rate: int) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()
