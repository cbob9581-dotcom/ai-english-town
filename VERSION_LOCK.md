# Version lock (Blackwell / RTX 5060 Laptop)

The following values were measured with `scripts/startup-selfcheck.py` while building Phase 1. All values must match the actual `uv.lock` / driver installation; any upgrade requires re-running the self-check.

> Items marked "to be measured and back-filled after the models are downloaded (deferred: model download required)" can only be measured once the kokoro / faster-whisper models are downloaded. Downloads have repeatedly failed on this machine's network, so per the agreed decision the measurement is deferred — **no fabricated numbers**.

## Environment measurements (2026-08-06)

| Component | Locked version | Measured on |
|---|---|---|
| GPU | NVIDIA GeForce RTX 5060 Laptop GPU (8 GiB VRAM) | 2026-08-06 |
| NVIDIA driver | 592.01 | 2026-08-06 |
| Python (uv runtime) | 3.13.12 | 2026-08-06 |
| uv | 0.11.27 | 2026-08-06 |
| Node.js | v24.13.0 | 2026-08-06 |
| pnpm | 11.1.2 | 2026-08-06 |
| `kokoro-onnx` (uv.lock) | 0.5.0 | 2026-08-06 |
| `faster-whisper` (uv.lock) | 1.2.1 | 2026-08-06 |
| `ctranslate2` (uv.lock) | 4.8.1 | 2026-08-06 |
| `onnxruntime` (uv.lock) | 1.28.0 | 2026-08-06 |

## Back-filled after real-machine measurement (phase-6 Task 13, 2026-08-10 real CUDA + real models)

| Component | Locked version | Measured result |
|---|---|---|
| CUDA runtime | `nvidia-cublas-cu12` 12.9.2.10 | **Working** — asr selfcheck `cuda_ok:true`; the missing cuBLAS was resolved (uv package + PATH provides `cublas64_12.dll`) |
| cuDNN | `nvidia-cudnn-cu12` 9.24.0.43 | Available together with the CUDA runtime; ASR CUDA inference works |
| NV-RTC | `nvidia-cuda-nvrtc-cu12` 12.9.86 | Pulled in by the cublas dependency (uv.lock) |
| CTranslate2 / faster-whisper | ctranslate2 4.8.1 / faster-whisper 1.2.1 | Wheel compatible with Blackwell: distil-large-v3 loads and infers correctly on CUDA float16 |
| ASR model load | distil-large-v3 (CUDA float16) | `load_secs=5.37s` (cold load in the self-check process), `device=cuda` |
| 3s transcription | — | selfcheck `transcribe_ok:true`; `transcribe_secs=1.791s` (cold-start first inference, 3s silence); hot transcription of a real short English sentence `0.52s` |
| One-sentence TTS | kokoro-onnx 0.5.0 (voice=af_bella) | `ok:true, load_secs=1.19s, synthesize_secs=2.461s, audio_bytes=51926` |
| Peak VRAM | — | `peak_vram_mib=2098` (after distil-large-v3 CUDA load, on the 8 GiB card; matches nvidia-smi memory.used) |
| End-to-end latency P50/P95 | — | `p50_ms=474.9, p95_ms=490.3` (n=30 warm run, ASR CUDA + real TTS models; target `p95_ms < 1500` ✓) |

> 2026-08-06 measured observation: `scripts/startup-selfcheck.py` reported `e2e_voice_ok: false`; tts returned an error dict
> (voices-v1.0.bin missing); the asr model loaded (device=cuda, load_secs=4.48s) but transcription failed with a missing cuBLAS DLL.
> The full gate (`e2e_voice_ok: true`) requires: ① downloading the kokoro model files; ② installing the CUDA runtime (cuBLAS/cuDNN).

> 2026-08-09 (phase-6 Task 1) retry log: `uv add --project services/asr-worker nvidia-cublas-cu12 nvidia-cudnn-cu12`
> download **stalled** — the uv process sat idle on the local proxy 127.0.0.1:10801, the uv cache grew by zero over 15 minutes,
> and `curl https://pypi.org/simple/` hung for 160s+ with no response. The install was aborted and the half-finished pyproject.toml/uv.lock changes were reverted.
> **CUDA runtime stays deferred (cublas64_12.dll still missing)**. The phase-6 execution order switched to the degraded path:
> the B diagnostic tasks (pure API code, no network dependency) ran first; Task 6 (real-machine word_timestamps verification) and the A coverage gate
> were blocked by this and will honestly be recorded as not-measurable — no fabricated numbers. Retry once the network recovers.

> 2026-08-09 (phase-6 Task 6) real-machine word_timestamps verification: **not-measurable (deferred)** —
> ① no real English audio: the kokoro voices model is not downloaded (known in VERSION_LOCK), the repo contains no WAV files,
> and downloading samples is blocked by the network (PyPI/pypi.org reachability re-tested with a 15s timeout); ② missing cuBLAS blocks GPU transcription.
> "3s transcription" and "word-level timestamps measurement / word-window interception ratio" stay deferred — no numbers to back-fill, none fabricated.
> The A coverage gate (Task 8) is likewise blocked by the network (it needs to download the wav2vec2 model alphabet dump).

> 2026-08-10 (phase-6 Task 13) back-fill log: the CUDA runtime (cublas 12.9.2.10 / cudnn 9.24.0.43 / nvrtc 12.9.86,
> DLLs provided via uv packages + PATH), the kokoro model, and distil-large-v3 are all available. `startup-selfcheck` passes the full chain with
> `e2e_voice_ok:true` (real TTS→ASR round trip; sample transcript "Hello, welcome to the bakery.").
> Latency was measured with `tests/latency/measure.py` (a contract-field bug in `audio_base64` was fixed; a previous 422 had caused a false reading),
> ASR runs on CUDA (nvidia-smi confirms 2098 MiB resident on GPU). `word_timestamps` is still not-measurable:
> the design gate is `ENABLE_WORD_TIMESTAMPS` + `whisper-large-v3`, and this machine's ASR uses distil-large-v3 → `word_timestamps:false` is expected.
> 顺带修 faster-whisper 1.2.1 的 `TranscriptionInfo.avg_logprob` 兼容（改 Segment 时长加权聚合，
> TDD 4 测试守护，asr-worker 全绿）。

启动自检项：GPU 名称 / CUDA 可用性 / ASR 模型加载 / 3s 转写 / 一句 TTS / 峰值显存 / 峰值耗时。

## 阶段 3 运行注意（沿用阶段 2 env quirks）
- vitest 需 `--maxWorkers=1`（16GB 机器）。
- `apps/web/node_modules/.bin/vite` 是 stale pnpm shim：web 构建用
  `node ../../node_modules/typescript/bin/tsc -b && node ../../node_modules/vite/bin/vite.js build`。
- tts-worker venv 缺 `babel.core`；ASR venv 缺 `cublas64_12.dll`（各自 selfcheck 已降级处理）。
- 无 `DEEPSEEK_API_KEY` → LLM 走 mock；`scripts/llm-smoke.py` golden 是手动测量步骤（不进 CI）。
- Scene Director 无 key 时同样走确定性 mock（`MOCK_SCENE_SCENARIO`）。
