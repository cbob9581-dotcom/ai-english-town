"""启动自检：加载 Kokoro 模型 → 合成一句。模型加载/合成失败返回 error dict，绝不 crash（Task 10 解析 stdout JSON）。"""
from __future__ import annotations

import time

from tts_worker.kokoro_engine import KokoroEngine


def run() -> dict:
    import base64

    start = time.perf_counter()
    try:
        engine = KokoroEngine().load()
        load_secs = time.perf_counter() - start
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "load_secs": round(time.perf_counter() - start, 2), "synthesize_secs": -1.0, "audio_bytes": 0, "audio_base64": "", "voice": "af_bella", "error": str(e)}
    t0 = time.perf_counter()
    try:
        audio = engine.synthesize("Hello welcome to the bakery.")
        synth_secs = time.perf_counter() - t0
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "load_secs": load_secs, "synthesize_secs": -1.0, "audio_bytes": 0, "audio_base64": "", "voice": engine.voice, "error": str(e)}
    # audio_base64 供 startup-selfcheck 转成 WAV 喂给 ASR 做真实英文语音自检
    return {"ok": True, "load_secs": round(load_secs, 2), "synthesize_secs": round(synth_secs, 3), "audio_bytes": len(audio), "audio_base64": base64.b64encode(audio).decode(), "voice": engine.voice}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), ensure_ascii=False))
