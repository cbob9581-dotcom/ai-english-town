"""Kokoro ONNX，CPU 优先。阶段 1 默认音色 af_bella，允许按请求透传 voice。"""
from __future__ import annotations

import numpy as np
from kokoro_onnx import Kokoro  # 需安装 kokoro-onnx（见 Global Constraints 版本说明）

# 契约：POST /tts 返回 16kHz mono PCM。Kokoro 原生输出 24k → 统一重采样到 16k。
TARGET_RATE = 16000


class KokoroEngine:
    def __init__(self, voice: str = "af_bella") -> None:
        self.voice = voice
        self._kokoro: Kokoro | None = None

    def load(self) -> "KokoroEngine":
        self._kokoro = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")
        return self

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        if self._kokoro is None:
            raise RuntimeError("call load() first")
        samples, sr = self._kokoro.create(text, voice=voice or self.voice, speed=1.0)
        pcm16 = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
        if sr != TARGET_RATE:
            pcm16 = _resample_pcm16(pcm16, sr, TARGET_RATE)
            sr = TARGET_RATE
        return _wav_bytes(pcm16, sr)


def _resample_pcm16(pcm16: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """线性重采样 PCM16（与计划 asr_worker.selfcheck 的 24k→16k 算法一致）。"""
    pcm = pcm16.astype(np.float32)
    n = int(len(pcm) * to_rate / from_rate)
    out = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm)
    return np.clip(out, -32768, 32767).astype(np.int16)


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
