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

    def synthesize_pcm(self, text: str, voice: str | None = None) -> tuple[bytes, int]:
        """返回原始 16kHz mono PCM16 字节（不含 WAV 头）+ 采样率。
        FIX: 拆出这个方法是为了让多段文本（chunk_sentences 产出多句）的调用方
        先拼接原始 PCM、最后只包一次 WAV 头 —— 旧代码在每段上各自调用 synthesize()
        （每段都是完整 WAV），再把多个完整 WAV 文件的字节直接拼在一起，产生的不是
        一个合法 WAV（后面几段的 RIFF/fmt 头会被当成音频数据本身），浏览器端
        decodeAudioData() 对此要么解码失败（静默丢弃，用户听不到任何声音），
        要么只播放第一段就结束。"""
        if self._kokoro is None:
            raise RuntimeError("call load() first")
        samples, sr = self._kokoro.create(text, voice=voice or self.voice, speed=1.0)
        pcm16 = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
        if sr != TARGET_RATE:
            pcm16 = _resample_pcm16(pcm16, sr, TARGET_RATE)
            sr = TARGET_RATE
        return pcm16.tobytes(), sr

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """单段便捷方法：返回完整 WAV 字节（含头）。仅用于单次调用场景（如测试、
        单句 tutor 回复）。多段文本请改用 synthesize_pcm() 拼接 PCM 后调用
        wav_bytes() 统一包一次头 —— 参见 server.py 的 /tts 端点。"""
        pcm, sr = self.synthesize_pcm(text, voice)
        return wav_bytes(pcm, sr)


def _resample_pcm16(pcm16: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """线性重采样 PCM16（与计划 asr_worker.selfcheck 的 24k→16k 算法一致）。"""
    pcm = pcm16.astype(np.float32)
    n = int(len(pcm) * to_rate / from_rate)
    out = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm)
    return np.clip(out, -32768, 32767).astype(np.int16)


def wav_bytes(pcm16_bytes: bytes, sample_rate: int) -> bytes:
    """把原始 PCM16 字节包一层 WAV 头。公开这个函数（原来是模块内 _wav_bytes，
    只接受 ndarray）是为了让 server.py 能在拼接完所有 chunk 的原始 PCM 之后，
    只调用一次，而不是每个 chunk 各包一次头。"""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16_bytes)
    return buf.getvalue()
