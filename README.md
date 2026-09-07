# English Town

An immersive English-learning website. Phase 1 is a **local bakery voice loop**: ASR (faster-whisper) → local reply → TTS (Kokoro), with **no LLM / cloud model calls**. The learner opens the bakery in the browser → speaks → hears Rosa's local reply within 1.5s, with correct captions.

The architecture runs as 4 local processes (each single-process / `--workers 1`):

| Port | Process | Role |
|---|---|---|
| 8001 | asr-worker | faster-whisper transcription, `POST /transcribe` (streaming WS left for Phase 2) |
| 8002 | tts-worker | Kokoro synthesis, `POST /tts` (CPU by default) |
| 8000 | api | FastAPI orchestration: scene compilation, WS audio frames, `/api/scenes`, `session_events` |
| 5173 | web | Vite React frontend (dev proxy `/api` → 8000) |

## Startup (4 terminals, in order)

```bash
# 1) tts-worker (Kokoro synthesis, CPU by default)
cd services/tts-worker && uv run uvicorn tts_worker.server:app --port 8002 --workers 1
# 2) asr-worker (faster-whisper transcription, includes the /transcribe endpoint)
cd services/asr-worker && uv run uvicorn asr_worker.server:app --port 8001 --workers 1
# 3) api (FastAPI orchestration)
cd apps/api && uv run uvicorn app.main:app --port 8000 --workers 1
# 4) web
cd apps/web && pnpm dev
```

Open `http://localhost:5173`. Note: ASR is the only high-priority GPU task; TTS stays on CPU (Phase 1 does not implement GPU TTS).

## Startup self-check

`scripts/startup-selfcheck.py` runs the self-check inside each worker's own uv environment and finally performs a real end-to-end voice check: TTS synthesises English → ASR transcribes it.

```bash
uv run python scripts/startup-selfcheck.py
```

Output JSON: `gpu.*`, `tts.*` (`audio_bytes`/`ok`), `asr.*` (`transcribe_ok`/`device`/`cuda_ok`), `total_secs`, **`e2e_voice_ok`** (TTS synthesis succeeded **and** ASR transcription is non-empty; the milestone-1 gate). When models are missing, each worker returns an error dict (exit 0) and `e2e_voice_ok: false`.

## Latency measurement

`tests/latency/measure.py` measures the server-side pipeline latency (VAD end → ASR final → reply → TTS complete; browser capture and network excluded):

```bash
# Requires asr-worker(8001) and tts-worker(8002) to be running first
uv run python tests/latency/measure.py
```

Outputs `{"p50_ms": ..., "p95_ms": ..., "n": 30}` (warm run). Milestone-1 target: `p95_ms < 1500`.

## Version locking

Measured values from the self-check / latency runs, GPU/CUDA/model-wheel compatibility, etc. are recorded in [`VERSION_LOCK.md`](./VERSION_LOCK.md). Model-load and transcribe/synthesise timings, peak VRAM, and P50/P95 latency can only be back-filled **after the real models (kokoro / faster-whisper) have been downloaded** — see that file for notes about this machine's unstable model-download network.

### Phase 2: LLM (DeepSeek / OpenAI-compatible)

- Environment variables: `DEEPSEEK_API_KEY` (required to use the real model; falls back to a mock when unset), `LLM_BASE_URL` (default https://api.deepseek.com), `LLM_MODEL` (default deepseek-chat; deepseek-reasoner is rejected). **Keys must only be injected via environment variables — never write them into settings.py (a tracked file; committing one leaks the secret).** Example of starting the api (PowerShell):
  ```powershell
  $env:DEEPSEEK_API_KEY = "sk-..."
  $env:LLM_MODEL = "deepseek-v4-flash"   # optional; use a model available to your account
  cd apps/api; python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
  ```
- Offline testing: `MOCK_LLM_SCENARIO=ok|timeout|connect_error|invalid_json|bad_word_id|missing_word|too_long|truncated|empty` (default: ok)
- Measurement: `uv run --project apps/api python scripts/llm-smoke.py` (real key, 20 runs → tests/fixtures/llm-golden/)
- Startup self-check: `python scripts/startup-selfcheck.py` (includes an LLM probe)
- Cost-guard defaults: 200 LLM calls per session / concurrency 2 / in-flight merge for repeated tutor words
