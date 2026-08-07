# 版本锁定（Blackwell / RTX 5060 Laptop）

实现阶段 1 时用 `scripts/startup-selfcheck.py` 实测并回填以下值。所有值必须与
`uv.lock` / 驱动实际安装一致，任何升级都需重跑自检。

> 标注「待模型下载后实测回填（deferred: model download required）」的项，依赖
> kokoro / faster-whisper 模型下载成功后才能实测，本机网络已多次下载失败，
> 按既定决策推迟实测，**不得臆造数值**。

## 环境实测值（2026-08-06）

| 组件 | 锁定版本 | 实测日期 |
|---|---|---|
| GPU | NVIDIA GeForce RTX 5060 Laptop GPU（8 GiB VRAM） | 2026-08-06 |
| NVIDIA 驱动 | 592.01 | 2026-08-06 |
| Python（uv 运行时） | 3.13.12 | 2026-08-06 |
| uv | 0.11.27 | 2026-08-06 |
| Node.js | v24.13.0 | 2026-08-06 |
| pnpm | 11.1.2 | 2026-08-06 |
| `kokoro-onnx`（uv.lock） | 0.5.0 | 2026-08-06 |
| `faster-whisper`（uv.lock） | 1.2.1 | 2026-08-06 |
| `ctranslate2`（uv.lock） | 4.8.1 | 2026-08-06 |
| `onnxruntime`（uv.lock） | 1.28.0 | 2026-08-06 |

## 待模型下载后实测回填（deferred: model download required）

| 组件 | 锁定版本 | 说明 |
|---|---|---|
| CUDA runtime | （实测 CUDA 12.8 可用性） | **2026-08-06 实测：不可用** —— asr selfcheck 报 `Library cublas64_12.dll is not found`。需安装 CUDA Toolkit / `nvidia-*` 包后重测（deferred） |
| cuDNN | （按 CUDA 12.8 配套） | 随 CUDA runtime 一并实测 |
| CTranslate2 / faster-whisper | （wheel 需支持 Blackwell） | wheel 与 Blackwell 兼容性待模型加载实测 |
| ASR 模型加载 | load_secs=4.48s, device=cuda | 2026-08-06 实测：distil-large-v3 已下载并加载成功（首次运行 300s 下载超时后二次运行命中缓存） |
| 3s 转写 | （待实测） | selfcheck `transcribe_ok`、`transcribe_secs` —— 当前被 cuBLAS 缺失阻断（deferred） |
| 一句 TTS | （待实测） | selfcheck `ok`、`load_secs`、`synthesize_secs`、`audio_bytes` —— kokoro 模型文件未下载（deferred） |
| 峰值显存 | （待实测） | selfcheck `peak_vram_mib`（模型加载后） |
| 端到端延迟 P50/P95 | （待实测） | `tests/latency/measure.py` 输出 `p95_ms < 1500`（热运行） |

> 2026-08-06 实测观察：`scripts/startup-selfcheck.py` 输出 `e2e_voice_ok: false`；tts 返回 error dict
> （voices-v1.0.bin 缺失）；asr 模型加载成功（device=cuda, load_secs=4.48s）但转写报 cuBLAS DLL 缺失。
> 完整门禁（`e2e_voice_ok: true`）需：① 下载 kokoro 模型文件；② 安装 CUDA 运行时（cuBLAS/cuDNN）。

启动自检项：GPU 名称 / CUDA 可用性 / ASR 模型加载 / 3s 转写 / 一句 TTS / 峰值显存 / 峰值耗时。

## 阶段 3 运行注意（沿用阶段 2 env quirks）
- vitest 需 `--maxWorkers=1`（16GB 机器）。
- `apps/web/node_modules/.bin/vite` 是 stale pnpm shim：web 构建用
  `node ../../node_modules/typescript/bin/tsc -b && node ../../node_modules/vite/bin/vite.js build`。
- tts-worker venv 缺 `babel.core`；ASR venv 缺 `cublas64_12.dll`（各自 selfcheck 已降级处理）。
- 无 `DEEPSEEK_API_KEY` → LLM 走 mock；`scripts/llm-smoke.py` golden 是手动测量步骤（不进 CI）。
- Scene Director 无 key 时同样走确定性 mock（`MOCK_SCENE_SCENARIO`）。
