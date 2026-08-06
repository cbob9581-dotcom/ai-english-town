"""KokoroEngine 纯函数测试：16k 重采样（不加载真实模型）。"""
import numpy as np

from tts_worker.kokoro_engine import TARGET_RATE, _resample_pcm16


def test_resample_24k_to_16k_halves_length() -> None:
    pcm = np.zeros(24000, dtype=np.int16)  # 1s @24k
    out = _resample_pcm16(pcm, 24000, TARGET_RATE)
    assert len(out) == 16000


def test_resample_16k_is_passthrough() -> None:
    pcm = np.array([0, 100, 200, -100], dtype=np.int16)
    out = _resample_pcm16(pcm, TARGET_RATE, TARGET_RATE)
    assert out.tolist() == pcm.tolist()


def test_resample_preserves_sine_frequency() -> None:
    # 1kHz 正弦 @24k 重采样到 16k 后主导频率保持 ~1kHz（过零率 ≈ 2kHz/16kHz）
    rate = 24000
    n = rate
    t = np.arange(n) / rate
    pcm16 = (np.sin(2 * np.pi * 1000 * t) * 32767).astype(np.int16)
    out = _resample_pcm16(pcm16, rate, TARGET_RATE)
    signs = np.sign(out)
    zeros = int(np.sum(signs[1:] != signs[:-1]))
    assert abs(zeros / (len(out) / TARGET_RATE) - 2000) < 120
