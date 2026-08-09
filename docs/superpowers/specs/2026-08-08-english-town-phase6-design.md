# 英语小镇（English Town）阶段 6：收尾——GOP 发音评测 + deferred minors + 性能回填 设计规格

> 主 spec：`docs/superpowers/specs/2026-08-05-english-town-design.md`（§11 发音评分边界 v2、§17 里程碑 5）
> 上游：阶段 5 已交付 `mastery_states` 的 `asr_confidence_score`/`asr_word_confidence_score` 双列、`word_confidence.py` 词级打分器、`_write_consent_audio` 授权音频落盘、`learning_items.ipa` 目标词 IPA、`settings.py` env 解析、asr-worker `word_timestamps_active` 门槛。
> 范围决策（AskUserQuestion 三问）：①「所有收尾」= GOP + deferred minors + 性能回填三块（云端图像继续不做）；② GOP 走**完整路线**（新增音素模型）；③ 模型边界= **维持完整 GOP**。用户确认：8G 显存够用、不并发、「大多数情况又不会并发」。**评审修订（点 6）**：音素模型**默认 CPU 推理**（规避 ct2/torch CUDA 冲突），GPU 为可选优化开关，走 GPU 才与 whisper 串行。
>
> 设计评审已整合（11 条裁决 + 代码证据见 `2026-08-08-english-town-phase6-review-response.md` @ 29637d9）。本文件已按评审修订：IPA trie 分词器 + 覆盖率门（§4.3）、先算后写（§3/§4.5）、GOP 公式去自身 + blank 排除 + 英语分母（§4.3）、词窗 pad（§4.3/§4.6）、执行顺序 C 先于 A（§3/§6）、CPU 默认（§3/§4.2）、FSRS guard + 收窄 except（§5.1）、可空列 + 弃 degraded 枚举（§4.5/§7）、ProgressView 只留 GOP（§1/§4.7）。

## 1. 目标与验收

阶段 6 = 主 spec §17 里程碑 5 收尾，兑现 v2 承诺的**真正发音评测** + 清空阶段 5 全部 deferred minors + 回填 VERSION_LOCK 性能门禁。

- **子项目 A（GOP 音素级发音评测）**：兑现主 spec §11 v2「保存用户授权音频片段 + 强制对齐 + 音素级评分」。faster-whisper 的 `word.probability` 是词级后验、非发音质量（phase-5 §5.1 已诚实界定）；本阶段引入**音素级声学模型**做真正的 GOP（Goodness of Pronunciation）评分入证据轴。
- **子项目 B（deferred minors 批量修复）**：phase-5 ledger 全部 `[safe to defer]` 项逐条处置（修 or 显式不修+理由）。
- **子项目 C（性能指标回填验证）**：下载 kokoro 模型 + 装 CUDA runtime，回填 VERSION_LOCK 与延迟 P50/P95；**下载失败如实记录，不臆造数值**。

**验收**：
- A：目标词在授权音频上有音素级对齐与 GOP 分数（新证据轴 `pronunciation_score`）；模型缺失/加载失败/音频未授权时**自动回退词级置信度代理**，回合流与既有证据零回归；ProgressView **只展示 GOP 发音评测**、ASR 代理列降级到详情/dev。**前置门**：`scripts/ipa-coverage.py` 覆盖率 ≥80% 才实施 A（<80% partial、<50% defer——phase-6 只交 B+C，A 转 phase-7）。
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

- **本地优先 / 16GB 严格串行 / 依赖隔离**：任何时刻单个测试进程，web `--maxWorkers=1`；音素模型**默认 CPU**（`pronunciation_gop_device="cpu"`，可装 CPU-only torch wheel，规避与 ctranslate2 的 CUDA/cuDNN 版本冲突）；若走 GPU 则与 whisper **串行不并发**。新增依赖只进 **asr-worker venv**，不进 api。
- **先算后写（事务边界）**：GOP 的 HTTP+GPU 推理**必须在 `events.write_lock` 外**（engine.py:42 `record_evidence` 全程持锁，锁内推理会阻塞全应用证据写入）；锁内只做短事务 SQL 写入。
- **执行顺序**：`C.step2（CUDA runtime）→ B 诊断类 → 真机验证 word_timestamps → A 覆盖率门 → A 实施 → C.step1/3/4`（真机地基未验证前不实施 A，见 §6）。
- **降级优先**：GOP 任何失败（模型未下载/加载失败/音频未授权/对齐异常）→ 回退 phase-5 词级置信度代理，**绝不阻断回合**，现有证据路径零回归。
- **语义诚实**：`pronunciation_score` = 音素级 GOP 评测；与 `asr_confidence_score`（utterance 平均级代理）、`asr_word_confidence_score`（词级置信度代理）三列并存，ProgressView 明确区分"发音评测"与"ASR 置信度"。
- **幂等/确定性**：GOP 评分纯函数（音频段 + 期望音素序列 → 分数），时间敏感函数显式注入 `now`；证据侧沿用 phase-4 event_id 去重。
- **版本字段落表**：`pronunciation_gop_enabled = False`（默认关）、`pronunciation_gop_model = "facebook/wav2vec2-lv-60-espeak-cv-ft"`、`pronunciation_gop_device = "cpu"`（默认 CPU）、`pronunciation_gop_min_word_ms = 120`（<120ms 词窗太短不评）、`pronunciation_gop_word_pad_ms = 100`（词窗双侧 pad）、`pronunciation_gop_min_conf = None`（阈值先量后定）进 `settings.py`。
- **state-audit 白名单**：`mastery_states` 加 `pronunciation_score` 列（加列不影响表级白名单，参照 phase-4 Task 13 / phase-5 先例）。
- **时间一律 ISO 8601 datetime（UTC）**。

## 4. 子项目 A：GOP 音素级发音评测

### 4.1 语义边界（诚实命名，承接 phase-5 §5.1）

| 列 | 含义 | 阶段 |
|---|---|---|
| `asr_confidence_score` | utterance 平均级 ASR 置信度代理（`exp(avg_logprob)`） | phase-4 |
| `asr_word_confidence_score` | 词级对齐 ASR 置信度代理（`word.probability`） | phase-5 |
| `pronunciation_score`（新） | **音素级 GOP 发音评测**（目标词各音素后验似然比） | phase-6 |

- 词级置信度 ≠ 发音质量（phase-5 §5.1）；GOP 是**音素级**真实发音评测。ProgressView **用户可见只展示 `Pronunciation GOP`**（评测）；`ASR confidence`/词级代理列降级到证据详情/dev 面板。
- 本子阶段验收：**对目标词在授权音频上做音素级强制对齐 + GOP 评分，入新证据轴；无音素模型/未授权音频时回退词级代理**。

### 4.2 音素后验模型（asr-worker venv，CPU 默认）

- **模型**：wav2vec2 音素 CTC 模型 `facebook/wav2vec2-lv-60-espeak-cv-ft`（~360MB，音素字符集为 espeak-ng 音素表）。经 torchaudio 的 `forced_align`（CTC 强制对齐，Viterbi 约束在期望音素序列上）得到每音素的时间区间 + 帧级后验。**动手前必须 dump 核对**：torchaudio `forced_align` 当前签名/返回结构、模型实际 alphabet/词表（评审响应「未决验证项」；发生在 C.step2 之后、A 覆盖率门步骤，不阻塞规划）。
- **依赖隔离 + 设备**：torch + torchaudio 只进 **asr-worker venv**（已有 faster-whisper/ctranslate2）。16GB 严格串行：模型**惰性加载**（首次 GOP 评分时才 load）。**默认 CPU**（`pronunciation_gop_device="cpu"`，装 CPU-only torch wheel）；GPU 为可选优化开关，走 GPU 时与 whisper **串行不并发**。
- **加载降级**：`load()` 失败（模型未下载/加载异常）→ 记录日志 + 标记 `gop_available=False` → 全链路回退词级代理。
- **asr-worker 启动自检**：自检检测 `pronunciation_gop_enabled` + 模型可加载性 → 报告 **`gop_ok` 三态**：`ok`（可评分）/ `degraded`（模型在但评分不可用）/ `unavailable`（模型缺失）；非 ok → 一键回 phase-5 行为。

### 4.3 GOP 评分器（纯函数 + 惰性引擎）

新文件 **`services/asr-worker/asr_worker/pronunciation.py`**（§4.4 裁决：asr-worker 侧）：

```text
score_gop(audio_wav: bytes, expected_phonemes: list[str], aligner, *, device="cpu") -> dict | None
    # audio_wav: 目标词音频段（从授权落盘的整段 WAV 按词级时间窗切出，16kHz mono PCM16）
    # expected_phonemes: 目标词词典 IPA 转音素序列（learning_items.ipa，经 IPA 分词器）
    # → {"gop": 0.0..1.0, "phoneme_scores": {phone: 0.0..1.0}, "degraded": false}
```

- **GOP 公式（修正）**：对每个目标音素 p，`margin(p) = log P(p | X_p) − max_{p'≠p} log P(p' | X_p)`，其中 `P(p'|X_p)` 为 CTC 后验在 p 对齐帧区间的均值，**分母排除自身**（含 p'=p 会令正确音素 margin=0，全塌缩）。**排除 CTC blank 帧**（强制对齐输出含 blank token，直接均值会稀释 `P(p|X)`）。**分母限英语音素子集**（espeak-ng 词表为多语言，掺入他语言结构性异类音素会削弱判别力；英语子集由下方覆盖率脚本的映射表副产品直接给出）。汇总为词级：`word_gop = mean(margin(p))`，归一化（sigmoid/min-max）**先量后定**——用 kokoro 合成 golden 音频测分布后定，不预置拍脑袋参数。
- **期望音素来源 + IPA 分词器**：`learning_items.ipa`（目标词均含，phase-5 决策复用、不引 eSpeak G2P）。**IPA 字符串 → 音素列表用 trie 最长匹配分词器**（按词典 IPA 符号表逐符号匹配），非空格切分——实测 dictionary.json IPA 为 `/loʊf/`、`/ˈɔːrdər/`（带斜杠、无空格、含重音符/长音符），空格切分必然失败。斜杠包裹/重音/长音为元字符，分词器剥离或跳过。
- **音素集映射 = A 前置门（存亡前提）**：词典 IPA（CMU 风格）与 wav2vec2 模型的 espeak-ng 音素字符集**不是同一套标注**。`scripts/ipa-coverage.py`：加载模型 dump 其 `alphabet`/词表 → 对 dictionary.json 全部目标词 IPA 逐符号算覆盖率 → **门**：<80% → A partial（缺映射词回退代理）；<50% → A **defer**（phase-6 只交 B+C，A 转 phase-7）。覆盖率/映射表产物 → 进 fixtures 供确定性测试复用。缺映射的 IPA 符号 → 该词回退代理（不阻塞）。映射表为可扩展 dict，随测试覆盖补充。
- **词窗（精度修正）**：复用 phase-5 `words` 词级时间戳的 `start/end`；whisper 词级时间戳来自 cross-attention、误差几十~百 ms → **双侧 pad `pronunciation_gop_word_pad_ms`（默认 100ms）** 并 **clamp 到整段音频边界**；词窗 < `pronunciation_gop_min_word_ms` 或词未命中 `words` → 该词回退代理；真机（C.step2 后）**记录词窗拦截比例**为验收数据。
- **确定性**：纯函数；注入 `aligner`（生产 = torchaudio aligner；测试 = mock 返回固定后验）。**测试断言分级**：mock → 精确值；真模型 → 区间（如 GOP ∈ (0.5, 1.0)），不写死浮点。

#### 4.3 附注 — A 前置门真机裁决（Task 8，2026-08-09）

真机跑门 `uv run --project services/asr-worker python scripts/ipa-coverage.py --device cpu`（网络恢复后）：

- **裁决：`ok`** —— `total=4 / coverable=4 / partial=0 / ratio=1.0 / verdict="ok" / unmapped=[] / unaligned=[]`。**A 继续（Task 9-12）**。
- **`dump_vocab` 适配**：transformers 5.14.1 的 `Wav2Vec2PhonemeCTCTokenizer` 实例化强制要求 `phonemizer`（本机未装，且本门只需 alphabet 不需文本→音素）；改为经 `huggingface_hub` 直接读模型 `vocab.json`，等价于 `processor.tokenizer.get_vocab()`，返回契约与异常语义不变。
- **英语音素子集修正（重要）**：模型 `wav2vec2-lv-60-espeak-cv-ft` 是 **60 语言多语言词表**（388 符号，含他语言音素如 ʂ/ɴ/β、数字/声调 `i1`/`a4`、标点 `t[`/`u"`）。`build_mapping_from_vocab` 原文 `| set(vocab)` 会把全词表漏入 `ENGLISH_ESPEAK_SYMBOLS`（GOP margin **分母**，被污染则他语言音素可成为 max 竞争者 → 全部 GOP 失真）。修正：**英语子集 = 已映射的词典英语音素值域** `{mapping[s] for s in mapping}`（43 符号，全部 identity——模型 alphabet 用 Unicode IPA，词典符号同形，`_ESPEAK_ASCII_REFERENCE` 未触发）。真机 dump 后人工核对 43 符号全为英语音素、无数字/声调/标点。
- **映射产物**：`IPA_TO_ESPEAK`（43 项 identity）+ `ENGLISH_ESPEAK_SYMBOLS`（43 英语音素）已回填 `asr_worker/ipa.py`，fixtures 落 `assets/wordbook/ipa-symbols.json`。`ɝ` 未映射（不在模型词表也无 ASCII 参照；当前 4 词未含，将来含 ɝ 的词回退代理或人工补映射）。
- **CLI 返回码契约**：`0` = ok/partial，`1` = defer，`2` = model_unavailable。

### 4.4 运行位置裁决

GOP 评分需要 torch 音素模型 + 原始音频。两个候选，**选 asr-worker**：

- **asr-worker（选）**：音素模型与 whisper 同进程（**默认 CPU**，`pronunciation_gop_device="cpu"`；若开 GPU 则串行不并发）；音频经 WS 已在 asr-worker 侧流过；新增 `POST /pronounce` 端点（或复用 `/transcribe` 响应扩展 `gop` 字段）返回目标词 GOP。
- api 侧（弃）：api 无 torch，需把音频传回 api 再加载第二个模型——多一跳网络 + api venv 膨胀，违背"新增依赖只进 asr-worker"。

接口：asr-worker `POST /pronounce {wav_b64, expected_phonemes, device?}` → `{"gop": 0.0..1.0, "phoneme_scores": {...}, "degraded": bool}`。api 的 `run_round` 在 `replied=True` 且授权落盘后、`record_round` **之前**（锁外，§4.5 先算后写），对每个目标词（复用 `classify_round` 集合）调 `/pronounce`（词窗切段 + pad），得分作为**纯数据**传入证据写入。

### 4.5 证据接入（先算后写）

- **新列**：`mastery_states.pronunciation_score REAL`（**可空，无默认**；NULL = 未评测，避免与"评测得 0 分"混淆）。`_migrate` 加列时**不含** `NOT NULL DEFAULT 0.0`（区别于 `asr_word_confidence_score` 模式）。
- **新证据源/轴**：`WEIGHTS["pronunciation_gop"] = (0.5, "pronunciation_gop")`；`apply_evidence` 增加独立分支（参照 `word_production` 先例 evidence.py:59-69）：
  - 只更新 `pronunciation_score` 轴分（`update_score`），**不计数、不进排期、不参与选词**（薄弱词排序维持 productive+receptive，scheduler.py:29 既有行为）；
  - 证据 `axis="pronunciation_gop"`、`result="success"|"uncertain"`、`confidence=word_gop`。**不引入 `degraded` 枚举**——降级是系统条件非学习者观测（见下）。
- **触发时机（先算后写）**：`run_round` 成功返回（`replied=True`）后、`record_round` **之前**（`events.write_lock` **外**），api 对每个 target 词（词窗已切好）调 asr-worker `/pronounce` 得 `word_gop`（§4.4）；得分 dict 收集完毕后，`record_round` 锁内只做**短事务 SQL 写入**（追加 `pronunciation_gop` 证据 + 更新 `pronunciation_score` 列）。**绝不在锁内做 HTTP+GPU 推理**（engine.py:42 `record_evidence` 全程持 `events.write_lock`，锁内推理会阻塞全应用证据写入）。
- **降级（不写证据，记运行侧日志）**：无音素模型/未授权音频/词窗过短/对齐异常 → 该词**不产生** `pronunciation_gop` 证据行，词级代理照旧（`asr_word_confidence_score` 路径不变）；降级计数记 **asr-worker/api 运行侧日志**（可观测，不污染证据流）。两套并存，互不覆盖。`/pronounce` 失败/超时同样降级跳过，**record_round 主体不受影响**（GOP 是加分项，失败不入账、不阻断回合）。
- `_handle_entity_click`/`_handle_companion_ask` 路径不变（不评 GOP）。

### 4.6 settings 新增（env 解析模式同 phase-5）

```python
pronunciation_gop_enabled: bool = False          # 默认关
pronunciation_gop_model: str = "facebook/wav2vec2-lv-60-espeak-cv-ft"
pronunciation_gop_device: str = "cpu"            # 默认 CPU，规避 ct2/torch CUDA 冲突
pronunciation_gop_min_word_ms: int = 120         # 词窗最短时长，过短不评
pronunciation_gop_word_pad_ms: int = 100         # 词窗双侧 pad，补偿 whisper 时间戳误差
pronunciation_gop_min_conf: float | None = None  # 阈值先 None；kokoro golden 分布量后再定
```

### 4.7 测试

- **覆盖率门**：`scripts/ipa-coverage.py` 单测（mock alphabet → 覆盖率计算正确；<50% 触发 defer 裁决）。
- asr-worker：`score_gop` 纯函数单测（mock aligner 返回固定后验 → 断言 margin 公式、**分母排除自身、blank 帧排除**；mock 断言**精确值**）；`POST /pronounce` 集成（合法请求 / 缺 phonemes / aligner 异常 → degraded 响应）；真模型断言**区间**（不写死浮点）。
- api：GOP 证据写入**可空** `pronunciation_score` 列 + 独立分支不计数不进排期（参照 `test_word_confidence.py` 线程化端到端模式，正 stability seed）；模型缺失 → 无 `pronunciation_gop` 证据且词级代理不受影响；`_migrate` 加列幂等；**先算后写时序**：断言 `/pronounce` 调用发生在锁外。
- ProgressView：只显示 `pronunciation_gop` 轴（发音评测）；ASR 代理列降级到详情/dev。
- **golden 排序验证**（validation 非门禁）：kokoro 合成已知词 golden 音频 → 断言正确词 GOP > 改读音词 GOP。

## 5. 子项目 B：deferred minors 批量修复

phase-5 ledger 全部 minor 处置清单（每任务一条线，修 or 显式不修）：

### 5.1 测试正确性（修——空转/假绿问题）

| ledger 条目 | 处置 |
|---|---|
| Task 7/3：`test_memory_hooks.test_one_round` seed `stability=0.0` → FSRS ZeroDivisionError 被吞 → 空转通过 | **修（两者都做）**：`to_fsrs_card` 加 guard **`stability<=0 → 走新卡路径`**（fsrs.py:26-36，非硬塞 1.0，硬塞破坏复习节奏）+ seed `stability=3.0` + `last_review`（真实日闸执行）；`record_evidence` 宽泛 except（engine.py:63）**收窄 + 补日志**（吞异常无日志 + outbox 无界重试是生产健壮性缺陷，评审响应点 7） |
| Task 8：interrupt 测试 `cancel()` 落在 coroutine 进入 try **之前** → 不测 mid-stream 取消 | **修**：actor 先 yield 一个 delta 再阻塞，cancel 在首次 yield 后 |
| Task 3：三证据共享 `evidence_id="ev_x"` → INSERT OR IGNORE 丢 2 行（断言未受影响但非真实） | **修**：改唯一 evidence_id |
| Task 8：ask-hook 测试缺负向对照（省略 `events=` 应抑制钩子） | **修**：加负向断言 |

### 5.2 代码卫生（修）

- `_write_consent_audio` 吞错无日志（M3）→ 加 `print(..., flush=True)`；JSON 写于 WAV 后可能留孤儿 WAV → 先写 JSON 后写 WAV。
- `scene_prefetch.py:1-2` 过时 docstring（"revision 恒 0"）→ 更新为 `(archetype_id, revision)` 键。
- `memory_smoke.py` 错误调用文档（`uv run --project apps/api python -m ...` 是坏的）→ 修 docstring；`arch=None` 行无 guard → `if arch is None: continue`；`max(bumps, default=1)` 空库返回 1 → 语义 0。
- Task 5：`memory_smoke.py:31` 耦合 MemoryCache 内部 → 保留 + 注释补强（诊断脚本可接受）。
- Task 2：`state != 'new'` 过滤同时过滤 SUM(help_count) 下计 → 拆分 SUM 或加意图注释（实施时定，倾向注释 + 不改变行为）。

### 5.3 其余 ledger 条目处置（含翻修为修，记录理由）

| ledger 条目 | 理由 |
|---|---|
| Task 2：`due <= now.isoformat()` 字符串比较、`Z` 后缀边界 | **修**：解析为 datetime 再比较（memory.py:129）——`Z` 后缀字典序 > `+00:00`，精确边界处错判 |
| Task 2：`build_world_summary` 的 `events` 参数未用 | brief 强制接口，保留 |
| Task 4：`_seed_memory` 返回 revision 但调用方丢弃 | 测试 helper 死返回，无害；drop 返回值（低优先，可修） |
| Task 4：plan brief 自身缺陷（tutor 片段 `json.dumps(user)` 与 frozen 断言矛盾） | 已用 ensure_ascii=False 正确实现；改 brief 留待 future，不在本阶段碰 plan 文件 |
| Task 6：selfcheck 不带 ASR_MODEL 加载（selfcheck.py:28 硬编码 `"auto"`）→ 报告与服务器行为不符 | **修（必须 C 前）**：selfcheck 传 `model=os.environ.get("ASR_MODEL") or "auto"`——C.step2 要靠 selfcheck 真机验证 word_timestamps |
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

> **执行顺序（评审阻塞 5）**：C 的 **step2（CUDA runtime）必须先于 A**——`cublas64_12.dll` 缺失导致 faster-whisper 真机从未转写过，word_timestamps 从未真机验证，A 的地基未验证。总顺序：`C.step2 → B 诊断类 → 真机验证 word_timestamps → A 覆盖率门 → A 实施 → C.step1/3/4`。装 CUDA 时**记录 DLL 版本快照**（cublas/cudnn 版本，与 ctranslate2 需求对照，防 torch/ct2 CUDA 版本冲突）。

- **步骤 1（后期）下载 kokoro 模型**：`kokoro-onnx` voices 文件（`voices-v1.0.bin`）——VERSION_LOCK 记录"模型未下载"。下载成功后 tts selfcheck `ok`。
- **步骤 2（最先）装 CUDA runtime**：cuBLAS/cuDNN（`cublas64_12.dll` 缺失阻断 asr 转写）。装后 asr selfcheck `transcribe_ok` + **真机验证 word_timestamps**（含 `selfcheck` 传 ASR_MODEL 修复，§5.3），记录词窗拦截比例。
- **步骤 3（后期）回填 VERSION_LOCK**：`startup-selfcheck.py` 实测 `load_secs`/`transcribe_secs`/`synthesize_secs`/`peak_vram_mib` → 回填到 VERSION_LOCK 各 deferred 行。
- **步骤 4（后期）延迟 P50/P95**：`tests/latency/measure.py`（需 asr-worker + tts-worker 启动）→ 回填 `p50_ms/p95_ms`。
- **失败语义**：任一下载/安装失败 → 保持 deferred 标注 + 记录失败原因；**绝不臆造数值**（VERSION_LOCK 既定决策）。此子项目**纯环境操作无代码**，验收为 VERSION_LOCK 回填状态变更。

## 7. 失败与容错

- GOP 模型缺失/加载失败 → `gop_available=False`（`gop_ok="unavailable"`），全部目标词回退词级代理，回合与证据零影响（§4.2/§4.5）。
- `/pronounce` 超时/异常 → 该词**不产生** `pronunciation_gop` 证据（**不写 `result="degraded"`**——降级是系统条件非学习者观测），降级计数记运行侧日志；不阻断回合。
- 授权音频缺失（未授权/未落盘）→ GOP 无音频可评，静默跳过该词（词级代理照常）。
- deferred minors 修复每个子任务独立 commit，回归跑全量（api + web + asr-worker，串行）。

## 8. 配置项（settings.py 新增，汇总）

| 字段 | 默认 | env | 说明 |
|---|---|---|---|
| `pronunciation_gop_enabled` | `False` | `PRONUNCIATION_GOP_ENABLED` | GOP 评分总开关（默认关） |
| `pronunciation_gop_model` | `facebook/wav2vec2-lv-60-espeak-cv-ft` | `PRONUNCIATION_GOP_MODEL` | wav2vec2 音素模型 |
| `pronunciation_gop_device` | `cpu` | `PRONUNCIATION_GOP_DEVICE` | 推理设备；默认 CPU（规避 ct2/torch CUDA 冲突） |
| `pronunciation_gop_min_word_ms` | `120` | `PRONUNCIATION_GOP_MIN_WORD_MS` | 词窗最短时长，过短不评 |
| `pronunciation_gop_word_pad_ms` | `100` | `PRONUNCIATION_GOP_WORD_PAD_MS` | 词窗双侧 pad，补偿 whisper 时间戳误差 |
| `pronunciation_gop_min_conf` | `None` | `PRONUNCIATION_GOP_MIN_CONF` | success/uncertain 阈值；kokoro golden 分布量后再定 |

## 9. 与主 spec / 阶段 4-5 的关系

- 主 spec §11 v2「强制对齐 + 音素级评分」在本阶段兑现为 GOP；阶段 5 的词级置信度**保持为独立代理列**，与 GOP 三列并存（§4.1）。
- 复用阶段 5 的全部决策：授权落盘时机、词级时间窗、词典 IPA、门槛开关模式、降级链、16GB 串行。
- `scene_prefetch`/WorldMemory（阶段 5）不受影响；GOP 证据只写 `pronunciation_score` 轴，不改变 `_RATING`/排期。

## 10. 测试策略（阶段 6）

- 子项目 A：**覆盖率门**（ipa-coverage.py）→ asr-worker `score_gop` 纯函数（mock 精确值断言）+ `/pronounce` 集成 + api 证据接入端到端（**先算后写时序断言**，正 stability seed 非空转）+ ProgressView 只显示 GOP + kokoro golden 排序验证（validation）。
- 子项目 B：每个修复自带针对性测试（空转测试转真实断言；FSRS guard + 收窄 except 补日志）。
- 子项目 C：无代码测试；验收 = VERSION_LOCK 回填状态 + 实测输出记录。
- 全量回归：api + web + asr-worker 串行全绿（16GB 严格串行）。

## 11. 交付物

- 子项目 A：`scripts/ipa-coverage.py` 覆盖率门（含 IPA trie 分词器 + 映射表）+ asr-worker `pronunciation.py`（score_gop + /pronounce）+ 模型门槛开关 + **可空** `pronunciation_score` 列迁移 + `apply_evidence` **先算后写**独立分支 + ProgressView 只显示 GOP + 全量单测 + kokoro golden 排序验证。
- 子项目 B：ledger 全部 minor 的 fixed 或 not-fixed 裁决 + 对应测试。
- 子项目 C：VERSION_LOCK 回填（成功）或 deferred 保持 + 失败记录。
- 端到端：三套测试全绿；`e2e_voice_ok` 视模型下载结果。
