"""启动自检：GPU → CUDA → ASR 模型 → 真实英文转写 → TTS 一句 → 峰值显存/耗时。
每个 worker 在自己项目的 uv 环境里跑 selfcheck（避免根环境缺依赖）。
最后做一次真实"TTS 合成英文 → ASR 转写"端到端语音自检。"""
from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def gpu_info() -> dict:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used,temperature.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if not out:
            return {"present": False}
        parts = out.split(",")
        return {"present": True, "name": parts[0].strip(), "driver": parts[1].strip(), "vram_total_gib": parts[2].strip(), "vram_used_gib": parts[3].strip(), "temp_c": parts[4].strip()}
    except Exception as e:  # noqa: BLE001
        return {"present": False, "error": str(e)}


def run_in(project: str, module: str, *args: str) -> dict:
    # cwd 设为项目目录，确保 python -m 能在该环境里找到本项目的包
    cmd = ["uv", "run", "--project", str(ROOT / project), "-m", module, *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(ROOT / project))
    except subprocess.TimeoutExpired:
        # 模型缺失且自动下载卡住时（如 faster-whisper 在本机网络挂起），
        # 不 crash，按 error dict 降级（与两个 worker selfcheck 的"绝不 crash"约定一致）。
        return {"error": f"selfcheck timed out after 300s: {module}"}
    if proc.returncode != 0:
        return {"error": proc.stderr.strip()[-500:]}
    return json.loads(proc.stdout)


def _wav_bytes_from_base64(b64: str) -> bytes:
    """把 tts selfcheck 返回的合成音频（WAV bytes base64）落盘供 ASR 转写。
    ASR 侧负责重采样（见 asr_worker.selfcheck.run 的 16k 重采样逻辑）。"""
    return base64.b64decode(b64)


def main() -> None:
    report: dict = {"gpu": gpu_info()}
    t0 = time.perf_counter()
    report["tts"] = run_in("services/tts-worker", "tts_worker.selfcheck")
    wav_path = ROOT / ".selfcheck-hello.wav"
    if report["tts"].get("audio_base64"):
        wav_path.write_bytes(_wav_bytes_from_base64(report["tts"]["audio_base64"]))
        report["asr"] = run_in("services/asr-worker", "asr_worker.selfcheck", str(wav_path))
        wav_path.unlink(missing_ok=True)
    else:
        report["asr"] = run_in("services/asr-worker", "asr_worker.selfcheck")
    report["total_secs"] = round(time.perf_counter() - t0, 2)
    # 端到端语音门禁：TTS 合成成功 且 ASR 转写成功（非空）
    report["e2e_voice_ok"] = bool(report["tts"].get("audio_bytes")) and bool(report["asr"].get("transcribe_ok"))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
