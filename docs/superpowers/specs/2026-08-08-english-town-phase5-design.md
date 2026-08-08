# 英语小镇（English Town）阶段 5：WorldMemory + 词级对齐评分 设计规格

> 主 spec：`docs/superpowers/specs/2026-08-05-english-town-design.md`（§2 决策基线 2 伴学者、§10 世界摘要、§12 学习引擎发音轴、§17 里程碑 5）
> 上游：阶段 4 已交付 `learning/store.py`（mastery_states/evidence_events/spontaneous_words 等）、`event_store.py`（session_events WAL + 幂等 + internal 列）、`learning/evidence.py`、`llm/lexmatch.py`、`llm/scene_director.py`、`services/asr-worker`（faster-whisper）。
> 本规格吸收用户设计评审 7 点反馈（4 阻塞 + 3 重要 + 建议级）：revision 实质变化语义、词级置信度≠发音评分、记忆快照事件化、word_timestamps 代价与模型门槛、PCM 落盘时机、WorldSummary prompt 模板、云端图像最小预留缝。

## 1. 目标与验收

阶段 5 = 主 spec 里程碑 5 中**本轮可实现**的两部分 + 一处预留缝：

- **子阶段 A（WorldMemory 结构化记忆抽取与增强）**：从 `session_events` + 学习引擎表抽取长期记忆 → 结构化 `WorldSummary` → 喂给 Scene Director / Companion Tutor / NPC Actor；`worldMemoryRevision` 接入 scene_prefetch 缓存键（既有占位，注释"revision 恒 0"）。
- **子阶段 B（词级对齐 ASR 置信度升级）**：把 phase-4 的 utterance 平均级 `asr_confidence` 代理升级为**词级对齐**结果（faster-whisper `word_timestamps`），逐词可视化在证据详情里。**语义诚实：这是 ASR 词级置信度，不是发音评分**（词后验 ≠ 发音质量，见 §9）；真正的发音评测（GOP/音素级）留后续阶段。
- **子阶段 C（云端图像）**：本轮**不实现**（无云服务凭据），设计文档留最小适配缝（§11），不写实现代码。

**验收**：
- A：任意 session 事件序列可重放重建 WorldSummary（`world_summary.snapshot` 事件）；`worldMemoryRevision` 只在摘要实质性变化时推进（一轮多证据最多 +1）；场景/词/求助信息进入 Director/Tutor/NPC 的 prompt。
- B：目标词在证据详情中可逐词看到时间戳窗口与词级置信度（`word.probability`）；无 `words`（模型不满足门槛/静音）时自动回退 utterance 平均级代理，回合流零回归。
- C：无实现代码；文档记录缝的形状。

## 2. 范围

**做**：
- 子阶段 A：`memory_state` 单例表 + 抽取器（内联事件钩子）+ `world_summary.snapshot` 事件 + WorldSummary prompt 模板 + scene_prefetch 键扩展 + memory_smoke 脚本。
- 子阶段 B：asr-worker `word_timestamps`（门槛开关 + 自检降级）+ 授权音频落盘 + 词级打分器入证据轴 + 证据详情逐词可视化 + ProgressView 标注更新。

**明确不做**：
- 发音评测（GOP / 音素级后验似然比）——phase-6 或专门子阶段；本轮只做词级对齐置信度。
- 云端图像实现、embedding/向量记忆、多用户（仍单用户 `'local'`）、eSpeak G2P 依赖（目标词期望比对默认只用 dictionary IPA，缺 IPA 词回退代理）。

## 3. 全局约束（继承阶段 4 + 本阶段新增）

- **本地优先 / 16GB 严格串行**：全本地、零新模型依赖（`word_timestamps` 是 faster-whisper 现成能力）；任何时刻单个测试进程，web `--maxWorkers=1`。
- **单事务**：记忆抽取与证据记录在同一连接同一事务提交；抽取失败 catch + 日志、revision 不推进，不阻断主流程。
- **幂等**：`world_summary.snapshot` 事件按 `event_id` 幂等（复用 event_store append_in_tx）；证据侧沿用 phase-4 event_id 去重。
- **确定性**：记忆抽取纯函数（输入事件序列 → 摘要）；一切时间敏感函数显式注入 `now: datetime`。
- **时间一律 ISO 8601 datetime（UTC）**：`memory_state.updated_at` / snapshot 事件时间均 tz-aware UTC ISO。
- **版本字段落表**：`memory_policy_version = "v1"`、`pronunciation_policy_version = "v1"`、`enable_word_timestamps = False`（默认关）、`word_timestamp_min_model = "whisper-large-v3"`、`pronunciation_audio_consent = False`（默认关）进 `settings.py`。
- **state-audit 白名单**新增 `memory_state` 表；`mastery_states` 加 `asr_word_confidence_score` 列（加列不影响表级白名单断言，白名单更新必须在实施本阶段时同步，参照 phase-4 Task 13 先例）。
- **语义诚实**：任何面向用户/证据轴的"发音"字样必须区分「ASR 置信度」与「发音评测」；词级结果是前者。

## 4. 子阶段 A：WorldMemory 结构化抽取

### 4.1 数据源（全本地，无新模型）

- `session_events`：`scene.enter`、`dialogue.turn`、`help`（companion.ask 记 help 证据）、`evidence`（internal）、spontaneous 相关事件。
- 学习引擎表：`mastery_states`（词掌握态/分数/到期）、`evidence_events`（每词证据 + 轴）、`spontaneous_words`（求助/偶遇词）。

### 4.2 记忆条目（结构化）

抽取器输出 `WorldSummary`，含四组字段（全部可 JSON 序列化）：

```json
{
  "memoryPolicyVersion": "v1",
  "scenes": { "plaza": {"count": 3, "lastAt": "2026-08-08T12:00:00Z"},
              "bakery": {"count": 2, "lastAt": "2026-08-08T13:00:00Z"} },
  "wordMastery": { "known": 8, "learning": 5, "reviewDue": 3,
                   "weakWords": [ {"wordId": "word_shelf_n_1", "lemma": "shelf"} ] },
  "userProfile": { "productiveAvg": 0.72, "receptiveAvg": 0.81,
                   "asrWordConfAvg": 0.65, "helpCount": 4,
                   "commonErrorWords": [ {"wordId": "word_jar_n_1", "lemma": "jar"} ] },
  "askedWords": [ {"wordId": "word_loaf_n_1", "lemma": "loaf", "count": 2} ],
  "updatedAt": "2026-08-08T14:00:00Z"
}
```

- **场景足迹**：去过的 archetypeId + 次数 + 最近时间（升级现状 `recent_scenes` limit 5）。
- **词汇掌握摘要**：mastery_states 聚合 —— known/learning/review 计数、今日到期数、薄弱词 top N（`productive_score + receptive_score` 最低，asr 轴不参与）。
- **用户表现档案**：productive/receptive/asrWordConf 均值、help 次数、常见错误词（`error` 证据聚簇）。
- **求助历史**：用户主动问过的词（spontaneous_words / help 证据）。

### 4.3 存储：单例表 + 快照事件（revision 只在实质变化时推进）

```sql
CREATE TABLE memory_state(
  user_id           TEXT NOT NULL DEFAULT 'local',
  world_summary_json TEXT NOT NULL,            -- 单例行，内嵌 memoryPolicyVersion
  revision          INTEGER NOT NULL DEFAULT 0,
  updated_at        TEXT NOT NULL,             -- tz-aware UTC ISO
  PRIMARY KEY(user_id)
);
```

**单例一行**（`user_id='local'`）。revision 单调递增，**只在摘要发生实质性变化时 +1**：

```text
should_touch =
   新场景进入足迹                      # scene.enter 且该 archetype 首次/再次进入改变 lastAt
   or 新词进入 known/learning/review   # evidence 使某词状态进入这三者之一（原 last_known_word_ids 无此词）
   or 新增求助词                       # 求助词不在 last_helped_set

if should_touch:
  UPDATE memory_state SET world_summary_json=?, revision = revision + 1, updated_at=?
  events.append_in_tx(conn, "world_summary.snapshot", {summary, revision}, event_id=..., internal=True)
```

- **进程内只读快照缓存**：`last_known_word_ids`、`last_helped_set` 初始化时从库加载，更新后同步刷新。读侧零成本；revision 严格只在摘要变化时推进（一轮多证据最多 +1，scene_prefetch 缓存不误失效）。
- **`world_summary.snapshot` 事件**：每次 revision 推进追加一条 internal 事件（payload 含 `summary` + `revision`），重放可完整重建历史摘要；当前读侧仍 O(1) 直读单例行。**不建第二张聚合表**。JSON 内显式写死 `memoryPolicyVersion`，防止聚合逻辑变更后旧摘要无法识别。
- 抽取是**纯函数**（输入事件序列 → 摘要），增量更新逻辑与重放重建共用同一函数。

### 4.4 接口

- `MemoryStore`（读）：`get_world_summary(user_id) -> dict | None`、`get_revision(user_id) -> int`。
- 抽取器（写）：`apply_memory_updates(conn, events, user_id, scene_enter=None, evidence=None, ask=None, *, now)` —— 在现有事件钩子内联调用，与证据记录同事务。
- `WorldSummary` → prompt（§4.5 最小模板基线）。
- `scene_prefetch.get/put` 键由 `archetype_id` 扩展为 `(archetype_id, world_memory_revision)`；revision 变化 → 键变化 → 自然失效。`invalidate` 语义不变。

### 4.5 WorldSummary → LLM prompt 最小模板（基线，写死进实现）

```text
User memory summary:
  Visited scenes: plaza × 3, bakery × 2
  Known words: 8, learning: 5, review due: 3
  Top weak words: shelf, jar, cream
  Words user explicitly asked about: loaf, checkout
```

- Scene Director：`_build_messages(archetype, catalog, recent_scenes, world_summary)` 将上述模板注入；`recent_scenes` 仍保留（Director 上下文最小集），`world_summary` 为新增块。
- Companion Tutor：注入 `askedWords`（用户问过的词）与 `userProfile.helpCount`（求助历史 → 渐进提示强度）。
- NPC Actor：注入 `scenes`（去过场景）+ `wordMastery.weakWords`（情境设计用薄弱词）。具体注入位置在 plan 阶段由各 task 落实，模板以上文为基线、不得偏离字段语义。

### 4.6 降级

- 抽取失败 → catch + 日志，revision 不推进，不阻断主流程（与 evidence 失败同款）。
- 无记忆 → 空摘要（等价现状：Director 无 world_summary 块）。
- snapshot 事件写失败 → 记录日志（摘要仍落单例行，读侧不受影响；重放重建仅在有 snapshot 事件的时间段内可用）。

### 4.7 测试

- 抽取器纯函数单测：给定事件序列 → 断言 WorldSummary 各字段值。
- **revision 语义测试**：一轮多证据（help + prompted_production + click）→ 断言 revision 只 +1；新场景进入 → +1；仅场景计数变但 known words 不变 → revision 不变（见 §10 缓存命中测试）。
- snapshot 事件重放重建测试：事件序列 → 重建摘要 == 单例行。
- scene_prefetch 键失效/命中双向单测（§10）。
- `memory_smoke.py`：喂回放真实 `session_events` 样例 → 输出 WorldSummary 字段 + 最终 revision，同时实测「一轮多证据 revision 不暴涨」。

## 5. 子阶段 B：词级对齐 ASR 置信度升级

### 5.1 语义边界（诚实命名）

**词后验概率不是发音评分**。ASR 的 `word.probability` 反映解码器在语言模型先验与声学输入间权衡后"识别出该词"的信心，与用户发音质量不直接相关（场景上下文中的词，发音不准也可能高分）。因此：

- 证据轴名：**`asr_word_confidence_score`**（不是 `pronunciation_score`）。
- ProgressView 标注：**"Word-level ASR confidence (experimental, from aligned timestamps)"**——保留"实验性"字样，语义为词级对齐置信度，非发音评测。
- 本子阶段验收：**从 utterance 平均级升级为词级对齐，且对齐结果可逐词逐句可视化在证据详情里**。真实发音评分（GOP / 音素级后验似然比）留 phase-6 或专门子阶段。

### 5.2 ASR 词级时间戳（asr-worker）

- `WhisperEngine.transcribe` 支持 `word_timestamps` 开关（faster-whisper 原生参数），开启时 final 返回 `words: [{word, start, end, probability}]`（word 级时间戳）。
- **门槛与代价控制**：
  - `enable_word_timestamps: bool = False`（settings 默认关）——开启约增 20–50% 转写耗时，仅显式开启。
  - `word_timestamp_min_model: str = "whisper-large-v3"` —— 仅非 distil 的全尺寸模型启用；`distil-large-v3`/`small.en` 不满足门槛。
  - **asr-worker 启动自检**：自检阶段检测模型名 + 开启状态，模型不满足 `word_timestamp_min_model` 时自动关闭词级输出（回退 utterance 级）。冒烟发现 distil 时间戳乱跳/速度崩 → 一键回 phase-4 行为，回合流与证据流零回归。
- **协议向后兼容**：final 消息 `words` 字段缺失、`None`、或**空数组**（faster-whisper 静音输入可能返回 `[]` 而非 `None`）→ 三种情况都映射回 utterance 平均级代理（`exp(avg_logprob)`），不假设空数组语义。

### 5.3 授权音频落盘（时机明确）

- **写入时机**：`run_round` 内，**仅当回合完全成功（`replied=True`）** 且 `settings.pronunciation_audio_consent` 开启时，将本话轮 PCM16（`run_round` 参数已持有）编码 WAV 写盘：`data/pronunciation-audio/{session_id}/{utterance_id}.wav` + 同名字典元数据（utterance_id/turn_id/finalText/sampleRate）。
- **打断/取消/错误一律不写盘**：`CancelledError`（`playback.interrupted` 路径）与异常路径丢弃累积缓冲，避免存下半句截断音频（省磁盘 + 省排查噪音）。
- **授权默认关**：未授权 → 不落盘，但词级打分仍可在内存帧上进行（帧本就被 ASR 使用）。
- 写盘失败 → 日志，不影响评分与回合。

### 5.4 词级打分器（入证据轴）

对每回合每目标词（复用 `classify_round` 产出的目标词集合）：

```text
words 有目标词且 lexmatch 命中该词：
    score = word.probability                  # 词级置信度
words 存在但目标词缺失 / 被误识别：
    score = 0.0    # 目标词缺失（用户没说/ASR 未检出）
    score = 0.15   # 被识别为另一词（词级不匹配，探测到存在但非该词）
无 words（模型门槛/静音/未开启）或目标词缺 IPA：
    回退 exp(avg_logprob) 代理（phase-4 行为）
归一化到 (0,1)，复用 fsrs_min_confidence=0.6
```

- 期望比对默认只用 dictionary IPA（目标词均含）；**不引 eSpeak G2P**（本轮不加新依赖）。
- 证据详情新增字段：目标词的时间戳窗口（`start`/`end`）、词级置信度（`wordProbability`）、检测来源（lexmatch）。进度页词表列表按词展开证据时间线时可逐词看到 "你说的第 0.5–1.2s 处的词：loaf（prob=0.92）"。
- **分数列决策（冻结）**：新增 `asr_word_confidence_score` 列，与既有 `asr_confidence_score`（utterance 平均级代理）**并存**——词级开启时写新列，回退时仍写旧列；迁移只加列不改旧数据，零破坏。证据轴字段 `axis` 在词级开启时记 `"asr_word_confidence"`（映射到新列），回退时记 `"asr_confidence"`（映射到旧列）。

### 5.5 降级

- words 缺失/空/解析失败 → 回退代理，不阻断回合（§5.2 三态全覆盖）。
- 音频写盘失败/未授权 → 跳过落盘，评分照常。
- 词级打分器异常 → 回退代理，证据照记（词级分留空/标记 degraded）。

### 5.6 测试

- asr-worker：mock engine 返回带 words/不带 words 的 final；`[]` 与 `None` 两分支映射回代理。
- 打分器单测：命中高分 / 缺失低分 / 回退三分支。
- ws 集成：授权开→写盘且元数据正确；打断→不写盘；未授权→不写盘但评分照常。
- ProgressView 标注与逐词可视化测试（更新既有标注断言）。

## 6. 数据流

```
A: scene.enter / evidence(含 help/click) / 求助
     → [单事务]
        event_store.append_in_tx(事件)
        apply_evidence(...)                       # 既有 phase-4 路径
        apply_memory_updates(...) → should_touch?  # 新
             → UPDATE memory_state SET ..., revision=revision+1
             → append world_summary.snapshot (internal, event_id 幂等)
        COMMIT
     → worldMemoryRevision 变化 → scene_prefetch 键 (archetypeId, rev) 自然失效

B: 用户话轮 → ASR final(含 words 若门槛开启) → run_round
     → replied=True 且授权 → WAV 落盘
     → classify_round 目标词 × words → 词级打分器 → record_evidence(axis=asr_word_confidence)
     → 证据详情含逐词窗口
```

## 7. 失败与容错

- 抽取失败：catch + 日志，revision 不推进，不阻断（A）。
- snapshot 写失败：日志，读侧 O(1) 不受影响（A）。
- words 三态缺失：回退 utterance 平均级代理，零回归（B）。
- 音频落盘失败/未授权/打断：不写盘，评分照常（B）。
- state-audit：白名单新增 `memory_state` 表（实施时同步更新断言）。

## 8. 配置项（settings.py 新增）

```text
memory_policy_version       = "v1"
pronunciation_policy_version = "v1"
enable_word_timestamps      = False        # 默认关；开则 asr-worker 输出 words
word_timestamp_min_model    = "whisper-large-v3"
pronunciation_audio_consent = False        # 默认关；开才落盘 WAV
```

全部进 `_ENV_FIELDS`（env 可覆盖），对齐阶段 2–4 既有模式。

## 9. 与主 spec / 阶段 4 的关系

- 主 spec §2 决策 2（伴学者轻量记忆）升级为结构化记忆抽取；§10 Scene Director「世界摘要」输入从 `recent_scenes` 升级为 `WorldSummary`；§17 里程碑 5 的"发音评分（强制对齐）"在本阶段落地为**词级对齐 ASR 置信度**（诚实语义），真正的发音评测（GOP）明确推迟。
- 阶段 4 的 `asr_confidence` 代理与「实验性 · ASR 置信度代理，非发音评测」标注：本阶段把"utterance 平均"升级为"词级对齐"，标注更新为 `Word-level ASR confidence (experimental, from aligned timestamps)`，仍是置信度而非评测。
- `scene_prefetch` 注释"阶段 3 无 world memory，revision 恒 0"的预留位在本阶段兑现。

## 10. 测试策略（阶段 5）

- **记忆抽取**：纯函数单测（事件序列 → 摘要字段）；revision 语义（一轮多证据 ≤ +1；场景变化但词不变 → 不变）。
- **scene_prefetch 缓存**：双向单测——(a) revision 触发变化 → 断言缓存未命中（重查 Director）；(b) 场景足迹更新但 known words 完全没变 → 断言缓存仍命中。
- **snapshot 重放**：事件序列重放重建摘要 == 单例行。
- **asr-worker**：words 三态（缺失/`None`/`[]`）→ 回退代理。
- **打分器**：命中/缺失/回退三分支；阈值复用。
- **音频落盘**：授权/未授权/打断三路径。
- **memory_smoke.py**：回放真实 `session_events` 样例 → 输出摘要字段 + 最终 revision；实测一轮多证据 revision 增长频率（确认不暴涨）。
- **全量回归**：api / web / scene-schema / scene-compiler / tsc 全绿（沿用阶段 4 基线：api 209 / web 49 / schema 3 / compiler 5 / tsc 0）。

## 11. 子阶段 C：云端图像预留缝（本轮不实现）

- **缝的形状（只写进文档，不写代码）**：前端渲染层 `visualKey → ImageSource` 处加一个条件分支 `ENABLE_CLOUD_IMAGES ? /api/assets/images/{visualKey} : visualKeyToEmoji(visualKey)`；后端仅一个空 stub 路由 `/api/assets/images/{visualKey}`（当前恒 404 或 501）。
- **不做**：抽象图像接口、后端代理、provider 适配层。避免"不实现"引入不必要的代码与依赖。
- 待有云凭据时单独立 phase 实施（届时补 env 驱动 provider + 缓存 + 成本预算，模式参照阶段 2 的 OpenAI 兼容 LLM 适配：env 驱动、空 key → mock/emoji 兜底）。

## 12. 交付物

- 子阶段 A：`memory_state` 迁移 + `MemoryStore` + 抽取器 + snapshot 事件 + WorldSummary prompt 模板（Scene Director / Tutor / NPC 注入）+ scene_prefetch 键扩展 + `memory_smoke.py` + 全量单测。
- 子阶段 B：asr-worker word_timestamps 开关 + 自检降级 + 授权音频落盘 + 词级打分器 + 证据详情逐词可视化 + ProgressView 标注更新 + 全量单测。
- 子阶段 C：仅文档（§11），无代码。
- 端到端：`memory_smoke.py` 可跑、全量回归绿、state-audit 白名单含 `memory_state`。
