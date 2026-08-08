# 英语小镇（English Town）阶段 6：收尾——GOP 发音评测 + deferred minors + 性能回填 设计规格

> 主 spec：`docs/superpowers/specs/2026-08-05-english-town-design.md`（§11 发音评分边界 v2、§17 里程碑 5）
> 上游：阶段 5 已交付 `mastery_states` 的 `asr_confidence_score`/`asr_word_confidence_score` 双列、`word_confidence.py` 词级打分器、`_write_consent_audio` 授权音频落盘、`learning_items.ipa` 目标词 IPA、`settings.py` env 解析、asr-worker `word_timestamps_active` 门槛。
> 范围决策（AskUserQuestion 三问）：①「所有收尾」= GOP + deferred minors + 性能回填三块（云端图像继续不做）；② GOP 走**完整路线**（新增音素模型）；③ 模型边界= **维持完整 GOP，音素模型与 whisper 串行共用 GPU**（用户确认：8G 显存够用、不并发）。用户补充：「大多数情况又不会并发」。

## 1. 目标与验收

阶段 6 = 主 spec §17 里程碑 5 收尾，兑现 v2 承诺的**真正发音评测** + 清空阶段 5 全部 deferred minors + 回填 VERSION_LOCK 性能门禁。

- **子项目 A（GOP 音素级发音评测）**：兑现主 spec §11 v2「保存用户授权音频片段 + 强制对齐 + 音素级评分」。faster-whisper 的 `word.probability` 是词级后验、非发音质量（phase-5 §5.1 已诚实界定）；本阶段引入**音素级声学模型**做真正的 GOP（Goodness of Pronunciation）评分入证据轴。
- **子项目 B（deferred minors 批量修复）**：phase-5 ledger 全部 `[safe to defer]` 项逐条处置（修 or 显式不修+理由）。
- **子项目 C（性能指标回填验证）**：下载 kokoro 模型 + 装 CUDA runtime，回填 VERSION_LOCK 与延迟 P50/P95；**下载失败如实记录，不臆造数值**。

**验收**：
- A：目标词在授权音频上有音素级对齐与 GOP 分数（新证据轴 `pronunciation_score`）；模型缺失/加载失败/音频未授权时**自动回退词级置信度代理**，回合流与既有证据零回归；ProgressView 证据详情可看 GOP 分数。
- B：phase-5 ledger 每条 minor 有明确处置（fixed 或 not-fixed-with-reason），核心测试空转项（stability=0.0、interrupt cancel 时序）修复后有真实断言。
- C：`e2e_voice_ok: true`（若模型下载成功）；VERSION_LOCK 回填实测值；失败则保持 deferred 标注。

## 2. 范围

**做**：
- 子项目 A：torch + torchaudio 依赖（asr 侧独立 venv）、wav2vec2 音素模型加载与门槛开关、GOP 对齐器、GOP→证据轴接入、`pronunciation_score` 列迁移、ProgressView 标注、mock 驱动的全链路测试。
- 子项目 B：phase-5 ledger 全部 deferred minors 的代码/测试修复与"显式不修"裁决。
- 子项目 C：kokoro 模型下载、CUDA runtime 安装、`startup-selfcheck.py` 与 `tests/latency/measure.py` 实测回填。

**明确不做**：
- 云端图像实现（用户范围决定：continue defer；§11 缝保持）。
- embedding/向量记忆、多用户（仍单用户 `'local'`）、eSpeak G2P（GOP 期望音素**复用 dictionary IPA**，目标词均含；缺 IPA 词回退代理——保持 phase-5 决策）。
- 改变语音链路（GOP 是**离线评分**：只吃已授权落盘的音频 + 既有词级时间窗，不改变流式 ASR 实时路径）。

## 3. 全局约束（继承阶段 5 + 本阶段新增）

- **本地优先 / 16GB 严格串行 / GPU 串行不并发**：任何时刻单个测试进程，web `--maxWorkers=1`；torch 音素模型与 whisper **同一 GPU 串行加载**（用户确认：8G 显存够、不并发）。新增依赖只进 **asr-worker venv**（或独立 scorer venv），不进 api。
- **降级优先**：GOP 任何失败（模型未下载/加载失败/音频未授权/对齐异常）→ 回退 phase-5 词级置信度代理，**绝不阻断回合**，现有证据路径零回归。
- **语义诚实**：`pronunciation_score` = 音素级 GOP 评测；与 `asr_confidence_score`（utterance 平均级代理）、`asr_word_confidence_score`（词级置信度代理）三列并存，ProgressView 明确区分"发音评测"与"ASR 置信度"。
- **幂等/确定性**：GOP 评分纯函数（音频段 + 期望音素序列 → 分数），时间敏感函数显式注入 `now`；证据侧沿用 phase-4 event_id 去重。
- **版本字段落表**：`pronunciation_gop_enabled = False`（默认关）、`pronunciation_gop_model = "facebook/wav2vec2-lv-60-espeak-cv-ft"`、`pronunciation_gop_min_word_ms = 120`（<120ms 词窗太短不评）进 `settings.py`。
- **state-audit 白名单**：`mastery_states` 加 `pronunciation_score` 列（加列不影响表级白名单，参照 phase-4 Task 13 / phase-5 先例）。
- **时间一律 ISO 8601 datetime（UTC）**。

## 4. 子项目 A：GOP 音素级发音评测

### 4.1 语义边界（诚实命名，承接 phase-5 §5.1）

| 列 | 含义 | 阶段 |
|---|---|---|
| `asr_confidence_score` | utterance 平均级 ASR 置信度代理（`exp(avg_logprob)`） | phase-4 |
| `asr_word_confidence_score` | 词级对齐 ASR 置信度代理（`word.probability`） | phase-5 |
| `pronunciation_score`（新） | **音素级 GOP 发音评测**（目标词各音素后验似然比） | phase-6 |

- 词级置信度 ≠ 发音质量（phase-5 §5.1）；GOP 是**音素级**真实发音评测。ProgressView 用词区分：`ASR confidence`（代理）vs `Pronunciation GOP`（评测）。
- 本子阶段验收：**对目标词在授权音频上做音素级强制对齐 + GOP 评分，入新证据轴；无音素模型/未授权音频时回退词级代理**。

### 4.2 音素后验模型（asr-worker venv，GPU 串行）

- **模型**：wav2vec2 音素 CTC 模型 `facebook/wav2vec2-lv-60-espeak-cv-ft`（~360MB，音素字符集为 espeak-ng 音素表）。经 torchaudio 的 `forced_align`（CTC 强制对齐，Viterbi 约束在期望音素序列上）得到每音素的时间区间 + 帧级后验。
- **依赖隔离**：torch + torchaudio 只进 **asr-worker venv**（已有 faster-whisper/ctranslate2）。16GB 严格串行：模型**惰性加载**（首次 GOP 评分时才 load），与 whisper **串行共享 GPU**，不并发（用户确认 8G 显存够）。
- **加载降级**：`load()` 失败（模型未下载/显存不足/CUDA 异常）→ 记录日志 + 标记 `gop_available=False` → 全链路回退词级代理。
- **asr-worker 启动自检**：自检检测 `pronunciation_gop_enabled` + 模型可加载性 → 报告 `gop_ok` 布尔；冒烟失败 → 一键回 phase-5 行为。

### 4.3 GOP 评分器（纯函数 + 惰性引擎）

新文件 **`services/asr-worker/asr_worker/pronunciation.py`**（§4.4 裁决：asr-worker 侧）：

```text
score_gop(audio_wav: bytes, expected_phonemes: list[str], aligner) -> dict | None
    # audio_wav: 目标词音频段（从授权落盘的整段 WAV 按词级时间窗切出，16kHz mono PCM16）
    # expected_phonemes: 目标词词典 IPA 转音素序列（learning_items.ipa）
    # → {"gop": 0.0..1.0, "phoneme_scores": {phone: 0.0..1.0}, "degraded": false}
```

- **GOP 公式**（经典）：对每个目标音素 p，`GOP(p) = log P(p | X) − max_{p'} log P(p' | X)`，其中 `P(p'|X)` 为 CTC 后验在 p 对齐帧区间的均值；`max_{p'}` 取该帧区间内所有音素的最大后验（含候选集合，防方言变体过度惩罚）。汇总为词级：`word_gop = mean(GOP(p))`，再用 `sigmoid` 或 min-max 归一到 `(0,1)`。
- **期望音素来源**：`learning_items.ipa`（目标词均含，phase-5 决策复用、不引 eSpeak G2P）。IPA 字符串 → 音素列表：按空格/音节边界切分（词典 IPA 已是词级音素串）。
- **音素集映射（关键）**：词典 IPA（如 `/loʊf/`，CMU 风格）与 wav2vec2 音素模型的字符集（`facebook/wav2vec2-lv-60-espeak-cv-ft` 用 espeak-ng 音素表）**不是同一套标注**。必须有显式映射层 `ipa → espeak-phoneme`（在 asr-worker `pronunciation.py` 内，如 `loʊ → l oʊ` 需查映射表/规则）。**实施第一件事**：加载模型后 dump 其 `alphabet`/词表，逐词典 IPA 符号核对覆盖率；缺映射的 IPA 符号 → 该词回退代理（不阻塞）。此映射表为可扩展 dict，随测试覆盖补充。
- **词窗**：复用 phase-5 `words` 词级时间戳的 `start/end` 切出目标词音频段；词窗 < `pronunciation_gop_min_word_ms` 或词未在 `words` 中命中 → 该词回退代理。
- **确定性**：纯函数；注入 `aligner`（生产 = torchaudio aligner；测试 = mock 返回固定后验）。

### 4.4 运行位置裁决

GOP 评分需要 torch 音素模型 + 原始音频。两个候选，**选 asr-worker**：

- **asr-worker（选）**：音素模型与 whisper 同进程串行共享 GPU（8G 显存、不并发，用户确认）；音频经 WS 已在 asr-worker 侧流过；新增 `POST /pronounce` 端点（或复用 `/transcribe` 响应扩展 `gop` 字段）返回目标词 GOP。
- api 侧（弃）：api 无 torch，需把音频传回 api 再加载第二个模型——多一跳网络 + api venv 膨胀，违背"新增依赖只进 asr-worker"。

接口：asr-worker `POST /pronounce {wav_b64, expected_phonemes}` → `{"gop": 0.0..1.0, "phoneme_scores": {...}, "degraded": bool}`。api 的 `run_round` 在 `replied=True` 且授权落盘后，对每个目标词（复用 `classify_round` 集合）调 `/pronounce`（词窗切段），得分为证据。

### 4.5 证据接入

- **新列**：`mastery_states.pronunciation_score REAL NOT NULL DEFAULT 0.0`（`_migrate` 加列，参照 `asr_word_confidence_score` 模式）。
- **新证据源/轴**：`WEIGHTS["pronunciation_gop"] = (0.5, "pronunciation_gop")`；`apply_evidence` 增加独立分支（参照 `word_production` 先例 evidence.py:59-69）：
  - 只更新 `pronunciation_score` 轴分（`update_score`），**不计数、不进排期**（GOP 是评测补充，不扰动复习节奏）；
  - 证据 `axis="pronunciation_gop"`、`result="success"|"uncertain"|"degraded"`、`confidence=word_gop`。
- **降级**：无音素模型/未授权音频/词窗过短/对齐异常 → 该词不产生 `pronunciation_gop` 证据，词级代理照旧（`asr_word_confidence_score` 路径不变）。两套并存，互不覆盖。
- **触发时机（单事务，无异步）**：`run_round` 成功返回（`replied=True`）后，api 侧在**现有 `record_round` 调用（ws.py `_run_round`，同一连接同一事务）内**追加 GOP 证据——`LearningEngine.record_round` 增加可选的 GOP 评分步骤：对每个 target 词（词窗已切好），若 `gop_available` 且音频已授权，调用 asr-worker `/pronounce` 得 `word_gop`，在同一事务内 `add_evidence(axis="pronunciation_gop")` + 更新 `pronunciation_score` 列。`/pronounce` 调用失败/超时 → 该词降级跳过，**record_round 主体不受影响**（GOP 是加分项，失败不入账、不阻断回合）。`_handle_entity_click`/`_handle_companion_ask` 路径不变（不评 GOP）。

### 4.6 settings 新增（env 解析模式同 phase-5）

```python
pronunciation_gop_enabled: bool = False          # 默认关
pronunciation_gop_model: str = "facebook/wav2vec2-lv-60-espeak-cv-ft"
pronunciation_gop_min_word_ms: int = 120
pronunciation_gop_min_conf: float = 0.6          # GOP 分数进入 success 的阈值
```

### 4.7 测试

- asr-worker：`score_gop` 纯函数单测（mock aligner 返回固定后验 → 断言 GOP 公式输出）；`POST /pronounce` 集成（合法请求 / 缺 phonemes / aligner 异常 → degraded）。
- api：GOP 证据写入 `pronunciation_score` 列 + 独立分支不计数不进排期（参照 `test_word_confidence.py` 线程化端到端模式，正 stability seed）；模型缺失 → 无 `pronunciation_gop` 证据且词级代理不受影响；`_migrate` 加列幂等。
- ProgressView：`pronunciation_gop` 轴证据行显示 GOP 分数 + `发音评测` 标注，与 `ASR confidence` 区分。

## 5. 子项目 B：deferred minors 批量修复

phase-5 ledger 全部 minor 处置清单（每任务一条线，修 or 显式不修）：

### 5.1 测试正确性（修——空转/假绿问题）

| ledger 条目 | 处置 |
|---|---|
| Task 7/3：`test_memory_hooks.test_one_round` seed `stability=0.0` → FSRS ZeroDivisionError 被吞 → 空转通过 | **修**：seed `stability=3.0` + `last_review`（真实日闸执行），或 `to_fsrs_card` 加 `if stability<=0: stability=1.0` guard（二选一，实施时定） |
| Task 8：interrupt 测试 `cancel()` 落在 coroutine 进入 try **之前** → 不测 mid-stream 取消 | **修**：actor 先 yield 一个 delta 再阻塞，cancel 在首次 yield 后 |
| Task 3：三证据共享 `evidence_id="ev_x"` → INSERT OR IGNORE 丢 2 行（断言未受影响但非真实） | **修**：改唯一 evidence_id |
| Task 8：ask-hook 测试缺负向对照（省略 `events=` 应抑制钩子） | **修**：加负向断言 |

### 5.2 代码卫生（修）

- `_write_consent_audio` 吞错无日志（M3）→ 加 `print(..., flush=True)`；JSON 写于 WAV 后可能留孤儿 WAV → 先写 JSON 后写 WAV。
- `scene_prefetch.py:1-2` 过时 docstring（"revision 恒 0"）→ 更新为 `(archetype_id, revision)` 键。
- `memory_smoke.py` 错误调用文档（`uv run --project apps/api python -m ...` 是坏的）→ 修 docstring；`arch=None` 行无 guard → `if arch is None: continue`；`max(bumps, default=1)` 空库返回 1 → 语义 0。
- Task 5：`memory_smoke.py:31` 耦合 MemoryCache 内部 → 保留 + 注释补强（诊断脚本可接受）。
- Task 2：`state != 'new'` 过滤同时过滤 SUM(help_count) 下计 → 拆分 SUM 或加意图注释（实施时定，倾向注释 + 不改变行为）。

### 5.3 显式不修（记录理由）

| ledger 条目 | 理由 |
|---|---|
| Task 2：`due <= now.isoformat()` 字符串比较、`Z` 后缀边界 | 生产 today 一致（`+00:00`），不混 `Z`；注释说明即可 |
| Task 2：`build_world_summary` 的 `events` 参数未用 | brief 强制接口，保留 |
| Task 4：`_seed_memory` 返回 revision 但调用方丢弃 | 测试 helper 死返回，无害；drop 返回值（低优先，可修） |
| Task 4：plan brief 自身缺陷（tutor 片段 `json.dumps(user)` 与 frozen 断言矛盾） | 已用 ensure_ascii=False 正确实现；改 brief 留待 future，不在本阶段碰 plan 文件 |
| Task 6：selfcheck 不带 ASR_MODEL 加载 → `word_timestamps:False` 误报 | 可修（selfcheck 传 `model=os.environ.get("ASR_MODEL")`）；低优先，实施时看 |
| Task 6：`ENGINE.word_timestamps_enabled` 动态属性 | 无类型隐患，改 dataclass 字段属增强，YAGNI |
| Task 7：Test 2 两 run 跨 UTC 午夜 flake | 天文概率，固有设计 |
| Task 8：`test_audio_consent.py` 未用 import json/Path | lint 噪音，顺手删 |
| Task 9：`it`/`test` 混用、fallback 值未直接断言 | 风格 + 已覆盖崩溃场景；可顺手统一 |
| Task 9：ProgressView 标签括号（visible vs title） | title 已传达实验性/非发音评测，语义诚实满足；保留 |
| Final M1：userProfile AVG 排除 `state='new'` | 意图性设计（profile 描述复习中学习者），注释说明 |
| Final M2：词级 chip 与 generic `置信度` 双渲染 | 可修（`axis==='asr_word_confidence'` 时抑制 generic）；实施时顺手 |
| Final M4：`format_world_summary` 不暴露 userProfile | plan 逐字要求，保留 |
| memory_smoke 重放 bump revision | 重放固有语义，docstring 已警示勿跑生产 DB |
| Task 8：JSON 孤儿 WAV（见 5.2 已归修） | – |
| Final 建议：snapshot 重放幂等测试 | 可加（snapshots 是 internal，apply_memory_updates 忽略）；实施时顺手 |

### 5.4 环境清理

- phase-3/4 遗留**损坏 ACL 空壳目录**（memory 记录 2 个）：`git worktree` 残留或 `.superpowers` 空壳——实施时定位，**提权删除**（Remove-Item -Force / icacls reset），不删内容数据。

## 6. 子项目 C：性能指标回填验证

- **步骤 1 下载 kokoro 模型**：`kokoro-onnx` voices 文件（`voices-v1.0.bin`）——VERSION_LOCK 记录"模型未下载"。下载成功后 tts selfcheck `ok`。
- **步骤 2 装 CUDA runtime**：cuBLAS/cuDNN（`cublas64_12.dll` 缺失阻断 asr 转写）。装后 asr selfcheck `transcribe_ok`。
- **步骤 3 回填 VERSION_LOCK**：`startup-selfcheck.py` 实测 `load_secs`/`transcribe_secs`/`synthesize_secs`/`peak_vram_mib` → 回填到 VERSION_LOCK 各 deferred 行。
- **步骤 4 延迟 P50/P95**：`tests/latency/measure.py`（需 asr-worker + tts-worker 启动）→ 回填 `p50_ms/p95_ms`。
- **失败语义**：任一下载/安装失败 → 保持 deferred 标注 + 记录失败原因；**绝不臆造数值**（VERSION_LOCK 既定决策）。此子项目**纯环境操作无代码**，验收为 VERSION_LOCK 回填状态变更。

## 7. 失败与容错

- GOP 模型缺失/加载失败/显存不足 → `gop_available=False`，全部目标词回退词级代理，回合与证据零影响（§4.2/§4.5）。
- `/pronounce` 超时/异常 → 该词降级（`degraded`），不进证据或记 `result="degraded"`，不阻断回合。
- 授权音频缺失（未授权/未落盘）→ GOP 无音频可评，静默跳过该词（词级代理照常）。
- deferred minors 修复每个子任务独立 commit，回归跑全量（api + web + asr-worker，串行）。

## 8. 配置项（settings.py 新增，汇总）

| 字段 | 默认 | env | 说明 |
|---|---|---|---|
| `pronunciation_gop_enabled` | `False` | `PRONUNCIATION_GOP_ENABLED` | GOP 评分总开关（默认关） |
| `pronunciation_gop_model` | `facebook/wav2vec2-lv-60-espeak-cv-ft` | `PRONUNCIATION_GOP_MODEL` | wav2vec2 音素模型 |
| `pronunciation_gop_min_word_ms` | `120` | `PRONUNCIATION_GOP_MIN_WORD_MS` | 词窗最短时长，过短不评 |
| `pronunciation_gop_min_conf` | `0.6` | `PRONUNCIATION_GOP_MIN_CONF` | GOP 分数 ≥ 此值记 success |

## 9. 与主 spec / 阶段 4-5 的关系

- 主 spec §11 v2「强制对齐 + 音素级评分」在本阶段兑现为 GOP；阶段 5 的词级置信度**保持为独立代理列**，与 GOP 三列并存（§4.1）。
- 复用阶段 5 的全部决策：授权落盘时机、词级时间窗、词典 IPA、门槛开关模式、降级链、16GB 串行。
- `scene_prefetch`/WorldMemory（阶段 5）不受影响；GOP 证据只写 `pronunciation_score` 轴，不改变 `_RATING`/排期。

## 10. 测试策略（阶段 6）

- 子项目 A：asr-worker `score_gop` 纯函数 + `/pronounce` 集成 + api 证据接入端到端（mock aligner，正 stability seed 非空转）+ ProgressView GOP 标注测试。
- 子项目 B：每个修复自带针对性测试（空转测试转真实断言）。
- 子项目 C：无代码测试；验收 = VERSION_LOCK 回填状态 + 实测输出记录。
- 全量回归：api + web + asr-worker 串行全绿（16GB 严格串行）。

## 11. 交付物

- 子项目 A：asr-worker `pronunciation.py`（score_gop + /pronounce）+ 模型门槛开关 + `pronunciation_score` 列迁移 + `apply_evidence` 独立分支 + ProgressView 标注 + 全量单测。
- 子项目 B：ledger 全部 minor 的 fixed 或 not-fixed 裁决 + 对应测试。
- 子项目 C：VERSION_LOCK 回填（成功）或 deferred 保持 + 失败记录。
- 端到端：三套测试全绿；`e2e_voice_ok` 视模型下载结果。
