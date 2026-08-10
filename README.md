# 英语小镇（English Town）

沉浸式英语学习网站，阶段 1 为**本地面包店语音闭环**：ASR（faster-whisper）→ 本地回复 → TTS（Kokoro），**不含任何 LLM / 云端模型调用**。用户浏览器打开面包店 → 说话 → 1.5s 内听到 Rosa 本地回复 + 字幕正确。

架构为 4 个本地进程（均为单进程 / `--workers 1`）：

| 端口 | 进程 | 角色 |
|---|---|---|
| 8001 | asr-worker | faster-whisper 转写，`POST /transcribe`（WS 流式留到阶段 2） |
| 8002 | tts-worker | Kokoro 合成，`POST /tts`（默认 CPU） |
| 8000 | api | FastAPI 编排：场景编译、WS 音频帧、`/api/scenes`、`session_events` |
| 5173 | web | Vite React 前端（dev proxy `/api` → 8000） |

## 启动（4 个终端，按顺序）

```bash
# 1) tts-worker（Kokoro 合成，默认 CPU）
cd services/tts-worker && uv run uvicorn tts_worker.server:app --port 8002 --workers 1
# 2) asr-worker（faster-whisper 转写，含 /transcribe 端点）
cd services/asr-worker && uv run uvicorn asr_worker.server:app --port 8001 --workers 1
# 3) api（FastAPI 编排）
cd apps/api && uv run uvicorn app.main:app --port 8000 --workers 1
# 4) web
cd apps/web && pnpm dev
```

打开 `http://localhost:5173`。注意：ASR 是 GPU 唯一高优先级任务；TTS 固定 CPU（阶段 1 不实现 GPU TTS）。

## 启动自检

`scripts/startup-selfcheck.py` 在每个 worker 自己的 uv 环境里跑 selfcheck，最后做一次真实「TTS 合成英文 → ASR 转写」端到端语音自检：

```bash
uv run python scripts/startup-selfcheck.py
```

输出 JSON：`gpu.*`、`tts.*`（`audio_bytes`/`ok`）、`asr.*`（`transcribe_ok`/`device`/`cuda_ok`）、`total_secs`、**`e2e_voice_ok`**（TTS 合成成功 且 ASR 转写非空，里程碑 1 门禁）。模型缺失时各 worker 返回 error dict（exit 0），`e2e_voice_ok: false`。

## 延迟测量

`tests/latency/measure.py` 测量服务端链路延迟（VAD 结束 → ASR final → 回复 → TTS 完成；不含浏览器采集与网络）：

```bash
# 需先启动 asr-worker(8001) 与 tts-worker(8002)
uv run python tests/latency/measure.py
```

输出 `{"p50_ms": ..., "p95_ms": ..., "n": 30}`（热运行）。里程碑 1 目标：`p95_ms < 1500`。

## 版本锁定

自检/延迟的实测值、GPU/CUDA/模型 wheel 兼容性等记录在 [`VERSION_LOCK.md`](./VERSION_LOCK.md)。**真实模型（kokoro / faster-whisper）下载完成后**才可回填模型加载、转写/合成耗时、峰值显存与延迟 P50/P95 —— 本机网络对模型下载不稳定的情况见该文档标注。

### 阶段 2：LLM（DeepSeek / OpenAI 兼容）

- 环境变量：`DEEPSEEK_API_KEY`（必填才走真模型；不填自动落 mock）、`LLM_BASE_URL`（默认 https://api.deepseek.com）、`LLM_MODEL`（默认 deepseek-chat；拒绝 deepseek-reasoner）。**密钥只经环境变量注入，切勿写进 settings.py（跟踪文件，提交即泄密）。** 启动 api 示例（PowerShell）：
  ```powershell
  $env:DEEPSEEK_API_KEY = "sk-..."
  $env:LLM_MODEL = "deepseek-v4-flash"   # 可选，按账号可用模型
  cd apps/api; python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
  ```
- 离线测试：`MOCK_LLM_SCENARIO=ok|timeout|connect_error|invalid_json|bad_word_id|missing_word|too_long|truncated|empty`（默认 ok）
- 测量：`uv run --project apps/api python scripts/llm-smoke.py`（真 key，20 次 → tests/fixtures/llm-golden/）
- 启动自检：`python scripts/startup-selfcheck.py`（含 LLM 探活）
- 成本护栏默认值：单 session 200 次 LLM 调用 / 并发 2 / tutor 同词 in-flight 合并
