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

## 已实测回填（phase-6 Task 13，2026-08-10 真机 CUDA + 真模型）

| 组件 | 锁定版本 | 实测结果 |
|---|---|---|
| CUDA runtime | `nvidia-cublas-cu12` 12.9.2.10 | **可用** —— asr selfcheck `cuda_ok:true`，cuBLAS 缺失已解除（uv 包 + PATH 提供 `cublas64_12.dll`） |
| cuDNN | `nvidia-cudnn-cu12` 9.24.0.43 | 随 CUDA runtime 一并可用，ASR CUDA 推理正常 |
| NV-RTC | `nvidia-cuda-nvrtc-cu12` 12.9.86 | cublas 依赖引入（uv.lock） |
| CTranslate2 / faster-whisper | ctranslate2 4.8.1 / faster-whisper 1.2.1 | wheel 与 Blackwell 兼容：distil-large-v3 在 CUDA float16 加载与推理正常 |
| ASR 模型加载 | distil-large-v3（CUDA float16） | `load_secs=5.37s`（自检进程冷加载），`device=cuda` |
| 3s 转写 | — | selfcheck `transcribe_ok:true`；`transcribe_secs=1.791s`（冷启动首次推理，3s 静音）；真实英文短句热转写 `0.52s` |
| 一句 TTS | kokoro-onnx 0.5.0（voice=af_bella） | `ok:true, load_secs=1.19s, synthesize_secs=2.461s, audio_bytes=51926` |
| 峰值显存 | — | `peak_vram_mib=2098`（distil-large-v3 CUDA 加载后，8 GiB 卡；nvidia-smi memory.used 一致） |
| 端到端延迟 P50/P95 | — | `p50_ms=474.9, p95_ms=490.3`（n=30 热运行，ASR CUDA + TTS 真模型；目标 `p95_ms < 1500` ✓） |

> 2026-08-06 实测观察：`scripts/startup-selfcheck.py` 输出 `e2e_voice_ok: false`；tts 返回 error dict
> （voices-v1.0.bin 缺失）；asr 模型加载成功（device=cuda, load_secs=4.48s）但转写报 cuBLAS DLL 缺失。
> 完整门禁（`e2e_voice_ok: true`）需：① 下载 kokoro 模型文件；② 安装 CUDA 运行时（cuBLAS/cuDNN）。

> 2026-08-09（phase-6 Task 1）重试记录：`uv add --project services/asr-worker nvidia-cublas-cu12 nvidia-cudnn-cu12`
> 下载**停滞**——uv 进程经本地代理 127.0.0.1:10801 连接空闲、uv 缓存 15 分钟零增长、
> `curl https://pypi.org/simple/` 挂死 160s+ 无响应。已终止安装并回退 pyproject.toml/uv.lock 半成品改动，
> **CUDA runtime 保持 deferred（cublas64_12.dll 仍缺失）**。phase-6 执行顺序改走降级路径：
> B 诊断类任务（纯 API 代码，无网络依赖）先做；Task 6 真机验证 word_timestamps 与 A 覆盖率门
> 受此阻断，届时如实记录 not-measurable，不臆造数字。网络恢复后重试。

> 2026-08-09（phase-6 Task 6）真机验证 word_timestamps：**not-measurable（deferred）**——
> ① 无真实英文音频：kokoro voices 模型未下载（VERSION_LOCK 已知）、仓库无任何 WAV 文件、
> 下载样本被网络阻断（PyPI/pypi.org 连通性再测 15s 超时）；② cuBLAS 缺失阻断 GPU 转写。
> 「3s 转写」「词级时间戳实测/词窗拦截比例」两项保持 deferred，无数字可回填，不臆造。
> A 覆盖率门（Task 8）同受网络阻断（需下载 wav2vec2 模型 dump alphabet）。

> 2026-08-10（phase-6 Task 13）回填记录：CUDA runtime（cublas 12.9.2.10 / cudnn 9.24.0.43 / nvrtc 12.9.86，
> uv 包 + PATH 提供 DLL）、kokoro 模型、distil-large-v3 均可用。`startup-selfcheck` 全链
> `e2e_voice_ok:true`（真实 TTS→ASR 往返，样例转写 "Hello, welcome to the bakery."）。
> 延迟由 `tests/latency/measure.py` 实测（已修 `audio_base64` 契约字段 bug，此前 422 导致误测），
> ASR 走 CUDA（nvidia-smi 确认驻留 GPU 2098 MiB）。`word_timestamps` 仍 not-measurable：
> 设计门槛是 `ENABLE_WORD_TIMESTAMPS` + `whisper-large-v3`，本机 ASR 用 distil-large-v3 → 预期 `word_timestamps:false`。
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
