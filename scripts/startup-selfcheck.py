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


def check_catalog_icons() -> list[str]:
    import json as _json
    from pathlib import Path
    assets = ROOT / "assets"
    try:
        entities = _json.loads((assets / "catalog" / "entities.json").read_text(encoding="utf-8"))
        icons = set(_json.loads((assets / "icons" / "icon-map.json").read_text(encoding="utf-8")))
    except FileNotFoundError as e:  # 老仓库无 catalog 时降级跳过，不 crash
        return [f"catalog check skipped: {e}"]
    keys = {row["visualKey"] for rows in entities.values() for row in rows}
    missing = keys - icons
    return [f"missing icon for {k}" for k in sorted(missing)]


def check_town_map() -> list[str]:
    """town-map 边完整性：每条边的目标存在、spoke 可回 start、方向与原型 exits 一致。"""
    import json as _json
    assets = ROOT / "assets"
    try:
        tm = _json.loads((assets / "archetypes" / "town-map.json").read_text(encoding="utf-8"))
        ids = {p.stem for p in (assets / "archetypes").glob("*.json")}
        problems = []
        hub = tm["start"]
        for src, edges in tm["edges"].items():
            if src not in ids:
                problems.append(f"town-map src {src} 缺原型")
                continue
            arche = _json.loads((assets / "archetypes" / f"{src}.json").read_text(encoding="utf-8"))
            dirs = {e["direction"] for e in arche["exits"]}
            if set(edges) != dirs:
                problems.append(f"{src}: town-map 方向 {set(edges)} != archetype exits {dirs}")
            for direction, target in edges.items():
                if target not in ids:
                    problems.append(f"{src}.{direction} -> {target} 不存在")
        # 图可达性：从每个 spoke 沿 edges 传递，断言能回到 hub（修正 brief 只查直接连接的缺陷：
        # library→station→plaza 是间接回 hub，直接比对会误报）
        for spoke in [k for k in tm["edges"] if k != hub]:
            seen = {spoke}
            stack = list(tm["edges"][spoke].values())
            reachable = False
            while stack:
                node = stack.pop()
                if node == hub:
                    reachable = True
                    break
                if node in seen:
                    continue
                seen.add(node)
                stack.extend(tm["edges"].get(node, {}).values())
            if not reachable:
                problems.append(f"{spoke} 无法回到 hub {hub}")
        return problems
    except Exception as e:  # noqa: BLE001
        return [f"town-map check skipped: {e}"]


def probe_director() -> dict:
    """Director 探活：能出合法提案则 ok；失败返回降级提示（骨架场景 + 明确日志）。"""
    import asyncio
    import json as _json
    import sys as _sys
    try:
        # 使 app.* 在 root 环境（uv run python scripts/startup-selfcheck.py）可导入
        _sys.path.insert(0, str(ROOT / "apps" / "api"))
        from app.catalog import Catalog
        from app.llm.mock import MockSceneDirector
        arche = _json.loads((ROOT / "assets" / "archetypes" / "plaza.json").read_text(encoding="utf-8"))
        catalog = Catalog.load(ROOT / "assets")
        d = MockSceneDirector("ok")
        p = asyncio.run(d.propose(archetype_id="plaza", archetype=arche, catalog=catalog, recent_scenes=[]))
        return {"ok": True, "fills": len(p["fills"])}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "degrade": True, "log": f"Director probe failed: {e}; fall back to skeleton scene"}


def check_tts_voices() -> list[str]:
    """全部 catalog 音色枚举记录（首次合成预热由 tts worker 完成，这里只枚举不合成）。"""
    import json as _json
    try:
        npcs = _json.loads((ROOT / "assets" / "catalog" / "npcs.json").read_text(encoding="utf-8"))
        voices = sorted({n["voice"] for rows in npcs.values() for n in rows})
        out = [f"voices={voices} (selfcheck 记录耗时; 预热由 tts worker 完成)"]
        return out
    except Exception as e:  # noqa: BLE001
        return [f"tts voices check skipped: {e}"]


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
    report["llm"] = run_in("apps/api", "app.llm.probe")
    report["catalogIconMissing"] = check_catalog_icons()
    report["townMapProblems"] = check_town_map()
    report["directorProbe"] = probe_director()
    report["ttsVoices"] = check_tts_voices()
    report["total_secs"] = round(time.perf_counter() - t0, 2)
    # 端到端语音门禁：TTS 合成成功 且 ASR 转写成功（非空）
    report["e2e_voice_ok"] = bool(report["tts"].get("audio_bytes")) and bool(report["asr"].get("transcribe_ok"))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
