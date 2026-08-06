"""启动自检：GPU/CUDA/模型加载/3 秒转写/峰值显存与耗时。"""
from __future__ import annotations

import time

import numpy as np

from asr_worker.whisper_engine import WhisperEngine


def _nvidia_gpu() -> str:
    try:
        import subprocess
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True)
        return out.stdout.strip().splitlines()[0] if out.stdout.strip() else "unknown"
    except Exception:
        return "n/a"


def run(wav_path: str | None = None) -> dict:
    """自检：加载模型 → 转写。wav_path 给定时转写真实英文录音并断言非空；
    否则转写 3s 静音（仅验"不崩"）。启动编排（Task 10）会先 TTS 合成再传入 wav_path。"""
    import wave

    start = time.perf_counter()
    try:
        engine = WhisperEngine.load("auto")
        load_secs = time.perf_counter() - start
    except Exception as e:  # noqa: BLE001
        load_secs = time.perf_counter() - start
        return {"gpu_name": _nvidia_gpu(), "cuda_ok": False, "load_secs": load_secs, "peak_vram_mib": -1, "transcribe_ok": False, "transcribe_secs": -1.0, "device": "n/a", "error": str(e)}
    if wav_path:
        with wave.open(wav_path, "rb") as w:
            assert w.getnchannels() == 1 and w.getsampwidth() == 2, "selfcheck wav 必须 mono 16bit"
            sr = w.getframerate()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        if sr != 16000:  # Kokoro 默认可能输出 24k → 线性重采样到 16k
            n = int(len(pcm) * 16000 / sr)
            audio = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm).astype(np.float32)
        else:
            audio = pcm
    else:
        audio = np.zeros(16000 * 3, dtype=np.float32)
    t0 = time.perf_counter()
    try:
        result = engine.transcribe(audio)
        transcribe_ok = bool(result["text"].strip())
        transcribe_secs = time.perf_counter() - t0
    except Exception as e:  # noqa: BLE001
        return {"gpu_name": _nvidia_gpu(), "cuda_ok": engine.device == "cuda", "load_secs": load_secs, "peak_vram_mib": -1, "transcribe_ok": False, "transcribe_secs": -1.0, "device": engine.device, "error": str(e)}
    return {"gpu_name": _nvidia_gpu(), "cuda_ok": engine.device == "cuda", "load_secs": round(load_secs, 2), "peak_vram_mib": _peak_vram(), "transcribe_ok": transcribe_ok, "transcribe_secs": round(transcribe_secs, 3), "device": engine.device, "sample": result["text"]}


def _peak_vram() -> int:
    try:
        import subprocess
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return -1


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(run(*sys.argv[1:])))
