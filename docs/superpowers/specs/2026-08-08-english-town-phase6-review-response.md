# Phase-6 设计评审核实响应（11 条，逐条对真实代码）

> 基线：`docs/superpowers/specs/2026-08-08-english-town-phase6-design.md`（已提交 @ ac91b57）
> 目的：客观核实用户在评审中提出的 11 条论断，逐条给出 采纳 / 部分采纳 / 拒绝 + 代码证据。
> 本文件是明日整合修订的**待办清单**（每条含修订指引）。设计文档正文**未改动**，待用户审阅本响应后统一整合。

## 裁决一览

| # | 评审论断 | 裁决 | 代码证据 |
|---|---|---|---|
| 1 | IPA 空格切分必失败；映射是 A 存亡前提 | ✅ 采纳 | dictionary.json 实测 `/loʊf/`、`/ˈɔːrdər/` |
| 2 | 单事务内 HTTP+GPU 锁死全应用 | ✅ 采纳 | engine.py:42 `events.write_lock` 包全程 |
| 3 | GOP 分母含自身全塌缩；blank 稀释；min_conf 拍脑袋 | ✅ 采纳 | 文档 §4.3 line 73/103 |
| 4 | whisper 词窗精度不足 | ✅ 采纳 | 文档 §4.3 line 76 |
| 5 | C 必须先于 A | ✅ 采纳 | VERSION_LOCK：cublas64_12.dll 缺失 |
| 6 | wav2vec2 默认跑 CPU | ✅ 采纳 | 依赖冲突风险（ct2 vs torch） |
| 7 | stability=0.0 被吞；guard 走新卡路径；selfcheck 传 ASR_MODEL；due 比较 | ✅ 部分采纳 | engine.py:63 / fsrs.py:26 / selfcheck.py:28 / memory.py:129 |
| 8 | pronunciation_score 应允许 NULL；薄弱词只用 p+r | ✅ 采纳 | scheduler.py:29 已只用 p+r（维持）；line 90 DEFAULT 0.0 改可空 |
| 9 | result="degraded" 新枚举矛盾；改不写证据 | ✅ 采纳 | evidence.py:18 `_NON_SCORED`；文档 line 93 vs line 169 |
| 10 | 多语言分母削弱判别力 | ✅ 采纳 | 文档 §4.3 line 73 分母全音素集 |
| 11 | ProgressView 只留 GOP | ✅ 采纳 | ProgressView.tsx:170-172 现显示词级 chip |

## 阻塞级（1-5）

### 1. IPA 切分规则与文档例子矛盾 —— ✅ 采纳（阻塞）

**核实**：`assets/wordbook/dictionary.json` 实测 `total words: 4`：
`loaf → '/loʊf/'`、`bread → '/bred/'`、`order → '/ˈɔːrdər/'`、`buy → '/baɪ/'`。
**真实格式：带斜杠包裹、无空格、含重音符（ˈ）、长音符（ː）、r-colored（ɔːr）**。
文档 §4.3「按空格/音节边界切分」必然产生单元素 `['/loʊf/']` 或空，无法映射到音素序列。

**修订指引**：
- IPA → 音素序列用 **trie 最长匹配分词器**（词典 IPA 符号表 → 词表），逐符号核对；斜杠/重音/长音/连字符为元字符需剥离或跳过。
- 映射层（词典 IPA ↔ wav2vec2 espeak-ng 音素字符集）从「实施第一件事」**提升为独立前置门任务**：
  - `scripts/ipa-coverage.py`：加载模型 dump 其 `alphabet`/词表 → 对 dictionary.json 全部目标词 IPA 逐符号算覆盖率。
  - 门：覆盖率 <80% → A **partial**（缺映射词回退代理）；<50% → A **defer**（phase-6 只交 B+C，A 转 phase-7，见建议·abort path）。
  - 覆盖率脚本产物（符号表/映射表）进 fixtures，供确定性测试复用（建议级）。

### 2. GOP 的 HTTP+GPU 在单写事务内会锁死 —— ✅ 采纳（阻塞）

**核实**：`engine.py:42-69` `record_evidence` 全程持有 `with self.events.write_lock:`，内部是 append + `apply_evidence`（含 FSRS 排期）+ `apply_memory_updates` + commit。**该锁是全局单写锁（sequence 分配与提交同锁）**。
文档 §4.5「在现有 record_round 调用（同一连接同一事务）内追加 GOP 证据」若含同步 `/pronounce`（HTTP 往返 + asr-worker GPU 推理，数十~数百 ms），**锁内阻塞全应用所有证据写入**——比评审说的"锁死应用"更严重：不仅是该回合，任何并发证据（help、receptive、completion）全部排队。

**修订指引**：改「**先算后写**」：
- `run_round` 成功路径（`replied=True` + 授权落盘后、`record_round` **之前**）对目标词逐个调 `/pronounce` 得 `word_gop` dict（**锁外**，无锁）。
- 得分结果作为**纯数据**传入证据写入路径；`record_evidence`/`apply_evidence` 锁内只做短事务 SQL 写入（append + 更新列 + commit）。
- `/pronounce` 失败/超时 → 该词降级跳过，**不产生 GOP 证据**（见点 9），不影响 record_round 主体。

### 3. GOP 公式分母含自身 → 正确发音全塌缩到 0 —— ✅ 采纳（阻塞）

**核实**：文档 §4.3 line 73 `GOP(p) = log P(p|X) − max_{p'} log P(p'|X)`，`max_{p'}` 取**所有**音素。若 p 就是最高后验（正确发音），`max` 含自身 → `GOP(p)=0`。**每个正确音素都记 0，判别力归零**。公式缺陷成立。

**修订指引**：
- 分母排除自身：`margin(p) = log P(p|X) − max_{p'≠p} log P(p'|X)`。
- **CTC blank 帧排除**：wav2vec2 强制对齐的帧级后验含 blank 标签，直接均值会稀释 `P(p|X)`。只对**非 blank、且属于期望音素对齐区间**的帧求均值（torchaudio forced_align 输出 transition 边界过滤 blank token）。
- **min_conf 先量后定**：`pronunciation_gop_min_conf=0.6`（文档 §4.6）是拍脑袋。先用 kokoro 合成 golden 音频（已知词）跑 GOP 分布 → 依据分布定 success/uncertain 阈值；阈值**先设 None**（只入证据不判 success/uncertain），量完再定。
- 归一化/阈值策略在 golden 分布出来前不固化。

### 4. whisper 词级时间戳精度不足 —— ✅ 采纳（阻塞）

**核实**：文档 §4.3 line 76 复用 phase-5 `words` 的 `start/end` 切词窗。faster-whisper 词级时间戳来自 **cross-attention 权重**，误差几十~上百 ms，且无强制对齐约束；直接按原窗切段会**切偏或切短**目标词音频。

**修订指引**：
- 词窗双侧 pad **~100ms**（可配置），并 clamp 到整段音频边界（防越界）。
- 词窗门槛（`word_timestamp_min_model="whisper-large-v3"`）重新审视：真机（C.step2 装 CUDA 后）实测该模型词级时间戳在 kokoro 合成语音上的对齐质量；**记录词窗拦截比例**（被 `min_word_ms`/未命中 `words` 拦截的词占比）作为验收数据，不臆造。

### 5. C 必须先于 A（真机地基未验证）—— ✅ 采纳（阻塞）

**核实**：`VERSION_LOCK.md` 记录 **CUDA runtime 不可用（cublas64_12.dll 缺失）** → faster-whisper 在这台机器上**从未真实转写过** → `word_timestamps`（phase-5 新功能）**从未真机验证过**。A 的整个地基（词级时间窗 → 切段 → 对齐）都依赖词级时间戳，地基未验证就建 A 是空中楼阁。

**修订指引**（执行顺序约束，写进设计 §6 与计划）：
```
C.step2（装 CUDA runtime）→ B 诊断类（stability/interrupt/selfcheck-ASR_MODEL 等）
→ 真机验证 word_timestamps（C 解锁后冒烟，记录拦截比例）
→ A 覆盖率门（scripts/ipa-coverage.py）
→ A 实施
→ C.step1（kokoro）/step3/step4（VERSION_LOCK 回填）
```
- 装 CUDA 时**记录 DLL 版本快照**（cublas/cudnn 版本），与 ctranslate2 需求对照，防 torch 与 ctranslate2 的 CUDA 运行时版本冲突；wav2vec2 默认 CPU（点 6）进一步降低冲突面。

## 重要级（6-11）

### 6. wav2vec2 建议跑 CPU —— ✅ 采纳

**核实**：asr-worker venv 已有 faster-whisper/ctranslate2（绑 CUDA 运行时）。再装 torch 默认带 CUDA 依赖，两套 CUDA/cuDNN 版本同进程加载存在冲突风险；16GB 内存 + 8G 显存机器上同时驻留两个 GPU 模型也紧张。
**修订指引**：`pronunciation_gop_device = "cpu"` 默认（settings 新增）；可装 CPU-only torch wheel（`--index-url https://download.pytorch.org/whl/cpu`）；GPU 作为可选优化开关。GOP 单次评分毫秒~百毫秒级，CPU 足够。

### 7. stability=0.0 是被吞的生产 bug（非测试 minor）—— ✅ 部分采纳（重要）

**核实**（三分）：
- **吞异常无日志 + outbox 无界重试 = 真实生产健壮性缺陷**：`engine.py:63` `except Exception:` 捕获**任何**异常 → rollback → 入 outbox，**无一行日志**。`drain_outbox`（engine.py:71-80）先 DELETE outbox 行再重放；若证据管线持续抛错，会 **滚出→入 outbox→再滚出** 静默循环，证据永不落库且无人知晓。这是真实 bug，不限于测试。✅ 采纳
- **ZeroDivisionError 触发面**：`to_fsrs_card`（fsrs.py:26-36）对 `state != 'new'` 的行直接传 `stability=row["stability"]`，无 guard；测试 seed（state='learning', stability=0.0）已证明触发 py-fsrs ZeroDivisionError。**窄义上 phase-5 triage 说"生产不自然出现"是对的**——当前生产路径（首入→fresh card→正 stability；日闸→读库内正 stability）不会产生非 new 态 + 0.0 stability。但：① guard 是一行防御，phase-6 新增 GOP 证据路径扩大异常面后更值得加；② 无法穷举未来路径/手工改库。✅ 采纳 guard（**≤0 → 走新卡路径**，非硬塞 1.0 —— 硬塞会破坏复习节奏）。
- **两点翻成修**（评审明确）：
  - **selfcheck 传 ASR_MODEL**：`asr_worker/selfcheck.py:28` 硬编码 `WhisperEngine.load("auto")`，而服务器按 `ASR_MODEL` 加载；C.step2 要靠 selfcheck 真机验证 word_timestamps，若 selfcheck 用 "auto"（≠ 生产模型），报告可能与服务器行为不符。改：`model=os.environ.get("ASR_MODEL") or "auto"`。**必须 C 前修**。
  - **due Z 后缀比较**：`memory.py:129` `r["due"] <= now.isoformat()` 字符串比较；若库内 due 带 `Z` 后缀，字典序上 `Z` > `+00:00`，精确边界处错判。改：解析为 datetime 再比较（或统一规范化）。✅ 翻成修
- **测试**：`test_memory_hooks` seed `stability=3.0` + `last_review`（真实日闸执行，非空转）。

### 8. pronunciation_score 应允许 NULL；薄弱词只用 productive+receptive —— ✅ 采纳

**核实**：
- 文档 §4.5 line 90 `REAL NOT NULL DEFAULT 0.0`：0.0 会把「未评测」与「评测得 0 分」混为一谈。`_migrate`（store.py:129-135）现有加列模式是 `NOT NULL DEFAULT 0.0`，但 GOP 列应**可空**（NULL = 未评测），这是新语义，不沿用 word_confidence 的默认值模式。
- 薄弱词排序：**实测 scheduler.py:29 已只用 `productive_score + receptive_score`**，asr 两轴本就不参与（有测试 `test_weak_excludes_asr_confidence_axis` 锁定）；memory.py:131-134 的 weakWords 同口径。**当前行为已符合评审**——维持即可，GOP 轴不得加入排序/选词/排期。

**修订指引**：列 `pronunciation_score REAL`（可空，无默认）；文档明确「GOP 不参与选词/排期，仅轴分展示」；薄弱词排序维持 `p+r`（写死约束，防回归）。

### 9. result="degraded" 是新枚举值且 §4.5/§7 矛盾 —— ✅ 采纳

**核实**：
- 现有 result 取值域实测：`success` / `uncertain` / `no_attempt`（classify_round，evidence.py:35-40）、`neutral`（help，ws.py:168）；`error` 为 `_RATING` 保留值（evidence.py:15）当前无产出路径。`_NON_SCORED = {no_attempt, uncertain}`（evidence.py:18）。**`degraded` 是全新枚举值**。
- 文档自相矛盾：§4.5 line 93 引入 `result="success"|"uncertain"|"degraded"`，§7 line 169 又说「不进证据 **或** 记 result='degraded'」——两处冲突。

**修订指引**：选「**不写证据**」。降级（模型缺失/未授权/词窗过短/对齐异常）是**系统条件**，不是学习者观测 → 不产生 `pronunciation_gop` 证据行；降级计数记**运行侧日志**（asr-worker/api 侧，可观测但不可见于证据流）。证据语义保持「只写观测结果」。

### 10. 多语言音素分母削弱判别力 —— ✅ 采纳

**核实**：`wav2vec2-lv-60-espeak-cv-ft` 字符集是 **espeak-ng 全语言音素表**。文档 §4.3「分母取该帧区间内所有音素」含**其他语言**的结构性异类音素 → 分母里掺入与 p 无竞争关系的音素，margin 被无意义拉大/扭曲，判别力下降。
**修订指引**：分母限制到**英语音素子集**（由覆盖率脚本 `scripts/ipa-coverage.py` 的映射表副产品直接给出英语音素集）。`max_{p'≠p}` 只在英语子集内取。

### 11. ProgressView 收敛：只留 Pronunciation GOP —— ✅ 采纳

**核实**：`ProgressView.tsx:170-172` 现显示 `Word-level ASR confidence` chip（`asrWordConfidence ?? asrConfidence`），`197` 行显示 `词级 · 0.88` chip。GOP 上线后三类分数（utterance 级 / 词级代理 / GOP）若全展示用户侧信息过载。

**修订指引**：用户可见只保留 **Pronunciation GOP**（发音评测）；`asr_confidence_score`/`asr_word_confidence_score` 两代理列降级到证据详情 / dev 面板。此收敛同时自然消解 phase-5 M2（词级 chip 与 generic 置信度双渲染）。

## 建议级（采纳为设计细节）

- **kokoro golden 排序断言**：用 kokoro TTS 合成已知词的 golden 音频，断言 GOP 对已知正确词的排序（正确词 GOP > 改读音词 GOP），作为 validation（非门禁）。
- **确定性声明收紧**：测试对 mock aligner 断言**精确值**；对真模型断言**区间**（如 GOP ∈ (0.5, 1.0)），不写死浮点。
- **音频保留策略**：授权 WAV 明确保留/清理策略（尺寸、时长上限），进设计不臆造。
- **gop_ok 三态**：`ok` / `degraded`（模型有但评分不可用） / `unavailable`（模型缺失）——替代文档 §4.2 的布尔。
- **覆盖率/延迟进 fixtures**：`ipa-coverage.py` 产物（符号映射表）与延迟实测值进 fixtures，供确定性测试与文档引用。
- **A 的 abort path**：覆盖率门不过 → phase-6 只交 B+C，A 转 phase-7（明确写进验收条件，防「设计已批准就必须做完」的沉没成本）。

## 未决验证项（动手前必须自己 dump 核对）

评审声明基于记忆、需自行验证的部分：
- **torchaudio `forced_align` 当前 API**：签名/返回结构/blank token 处理方式（设计 §4.2/4.3 引用）。**动手前**在目标 venv 装对应版本后 dump 核对（`torchaudio.functional.forced_align` 现签名 vs 设计假设）。
- **wav2vec2-lv-60-espeak-cv-ft 实际 alphabet/词表**：覆盖率门脚本第一件事就是 dump 它，不臆造符号集。
- 以上验证都发生在 **C.step2 之后、A 覆盖率门** 步骤，不阻塞本阶段规划。

## 执行顺序总约束（汇总）

```
C.step2（CUDA runtime + DLL 快照）→ B 诊断类（stability guard / interrupt / selfcheck-ASR_MODEL / due 比较）
→ 真机验证 word_timestamps（记录拦截比例）
→ A 覆盖率门（ipa-coverage.py：<80% partial / <50% defer A→phase-7）
→ A 实施（先算后写 / margin 去自身 / blank 排除 / 英语分母 / CPU 默认 / 词窗 pad）
→ C.step1/3/4（kokoro + VERSION_LOCK 回填 + P50/P95）
```
