# 英语小镇（English Town）阶段 4：学习引擎设计规格

> 主 spec：`docs/superpowers/specs/2026-08-05-english-town-design.md`（§12 学习引擎、§13 数据与接口）
> 上游：阶段 2/3 已交付 `event_store.py`（session_events WAL + 单写队列 + 幂等）、`lexmatch.py`、`concepts.py`、回合/仲裁/场景流。

## 1. 目标与验收

把「掌握度 + 可追溯证据 + 复习排期」落地为可运行系统：

- **两条词流汇入一套掌握度**：目标词流（词表导入 → 确定性处理 → learning_items）与偶遇词流（exposure 记录 → 达标升级）汇入 `mastery_states`。
- **每次尝试都有证据**：`evidence_events` 逐条记录（§12 证据结构 + direction 轴），`attemptId+turnId` 去重，先写库再对客户端确认（§13）。
- **FSRS 决定何时复习**：纯 Python FSRS-5 核心（固定官方参数，不含优化器），`fsrs_algorithm_version = "fsrs-5"`。
- **每场选词**：5–7 词（2–3 到期复习 + 2–3 新词 + 1 薄弱词），同场景标签。

**验收**：每个目标词可追溯证据（progress 页证据时间线可查）；断线补发不丢（session_events 回放 + event_id 幂等）。

## 2. 范围

**做**：迷你词典 + 导入管线、5 张新表、FSRS-5 核心、证据生成与更新规则、每场选词、偶遇词流、4 个 HTTP 端点、最小进度页。

**明确不做**：复习卡流 UI、FSRS 参数优化器、PostgreSQL、多用户（`users` 表仍预留）、云端词元数据补全。

## 3. 全局约束（继承主 spec + 本阶段新增）

- **本地优先**：学习引擎全本地运行，零云端依赖；迷你词典随仓库携带。
- **单写队列**：所有学习状态更新走单一写入锁（§13）；证据先写库再 ack。
- **幂等**：`event_id` 唯一，重复提交不重复计算（§13）。
- **确定性**：FSRS 固定参数集；导入管线纯函数（解析→去重→匹配→入库）；exposure 用服务端词法匹配。
- **纯 Python 依赖集**：不引入原生编译依赖；新依赖上限为纯 Python 包。
- **16GB / 严格串行**（开发环境约束，非运行时）：任何时刻单个测试进程；web 测试 `--maxWorkers=1`。
- 版本字段：`evidence_policy_version = "v1"`、`fsrs_algorithm_version = "fsrs-5"`（写入每条证据/掌握态）。

## 4. 数据模型（新增 5 表，与 event_store 同库 SQLite）

### 4.1 `word_lists`（目标词表头）

```sql
CREATE TABLE word_lists(
  list_id    TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  source     TEXT NOT NULL DEFAULT 'import',   -- import | spontaneous | default
  created_at TEXT NOT NULL
);
```

### 4.2 `learning_items`（词本体）

```sql
CREATE TABLE learning_items(
  word_id    TEXT PRIMARY KEY,        -- word_<lemma>_<pos>_<sense>
  lemma      TEXT NOT NULL,
  pos        TEXT,                    -- noun/verb/adj/...
  sense      TEXT,
  ipa        TEXT,
  cefr       TEXT,                    -- A1..B1（词典命中才有）
  scene_tags TEXT NOT NULL DEFAULT '[]',   -- JSON array，如 ["bakery","plaza"]
  source     TEXT NOT NULL,           -- quest | free
  list_id    TEXT REFERENCES word_lists(list_id),
  created_at TEXT NOT NULL
);
```

### 4.3 `mastery_states`（每词掌握态）

```sql
CREATE TABLE mastery_states(
  word_id           TEXT PRIMARY KEY REFERENCES learning_items(word_id),
  productive_score  REAL NOT NULL DEFAULT 0.0,
  receptive_score   REAL NOT NULL DEFAULT 0.0,
  pronunciation_score REAL NOT NULL DEFAULT 0.0,
  -- FSRS 六字段
  state             TEXT NOT NULL DEFAULT 'new',   -- new|learning|review|relearning
  due               TEXT NOT NULL,                 -- ISO date（FSRS 到期日）
  stability         REAL NOT NULL DEFAULT 0.0,
  difficulty        REAL NOT NULL DEFAULT 0.0,
  retrievability    REAL NOT NULL DEFAULT 0.0,
  reps              INTEGER NOT NULL DEFAULT 0,
  lapses            INTEGER NOT NULL DEFAULT 0,
  attempts          INTEGER NOT NULL DEFAULT 0,    -- 总尝试（FSRS 进入条件用）
  updated_at        TEXT NOT NULL
);
```

### 4.4 `evidence_events`（证据明细；session_events 之外的物化表）

```sql
CREATE TABLE evidence_events(
  evidence_id   TEXT PRIMARY KEY,
  event_seq     INTEGER NOT NULL,      -- 对应 session_events.sequence
  session_id    TEXT NOT NULL,
  attempt_id    TEXT NOT NULL,
  turn_id       TEXT NOT NULL,
  objective_id  TEXT NOT NULL,         -- obj_<archetypeId>_<wordId>
  word_id       TEXT NOT NULL,
  source        TEXT NOT NULL,         -- spontaneous_production|prompted_production|repetition|action_understanding|help|error
  prompt_level  INTEGER NOT NULL,
  axis          TEXT NOT NULL,         -- productive|receptive|pronunciation
  result        TEXT NOT NULL,         -- success|fail|uncertain|neutral
  confidence    REAL NOT NULL,
  created_at    TEXT NOT NULL
);
```

### 4.5 `spontaneous_encounters`（偶遇流原始记录）

```sql
CREATE TABLE spontaneous_encounters(
  id            TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL,
  lemma         TEXT NOT NULL,
  pos           TEXT,
  turn_id       TEXT NOT NULL,
  encounter_no  INTEGER NOT NULL,      -- 该词在本会话累计出现次数
  asked         INTEGER NOT NULL DEFAULT 0,  -- 是否被主动问过
  promoted      INTEGER NOT NULL DEFAULT 0,  -- 是否已提升为目标词
  created_at    TEXT NOT NULL
);
```

**`resolve_word_id` 接缝替换**（`apps/api/app/llm/concepts.py`）：先查 `learning_items`（lemma+pos+sense 命中用其 `word_id`），未命中回退现有确定性派生。保留函数签名不变。

## 5. 迷你词典 `assets/wordbook/dictionary.json`

```json
{
  "version": 1,
  "words": [
    { "lemma": "loaf", "pos": "noun", "senses": ["一条面包"],
      "ipa": "/loʊf/", "cefr": "A2", "sceneTags": ["bakery"] },
    { "lemma": "order", "pos": "verb", "senses": ["点（餐）", "订购"],
      "ipa": "/ˈɔːrdər/", "cefr": "A2", "sceneTags": ["bakery", "cafe"] }
  ]
}
```

- 覆盖 6 场景原型（bakery/cafe/park/plaza/library/station）的目标词，A1–B1，约 120–200 词。
- 词条与阶段 3 资产（entities/catalog/concepts/icon-map）的词形保持一致；词形不一致处以后者为准并补进词典。
- 加载：`Dictionary.load(asset_root)`（类比 `Catalog.load`），未命中场景标签的词允许出现但选词不选它。

## 6. 导入管线（`learning/wordbook.py`）

`POST /api/word-lists/import` 请求体 `{ "name"?: string, "words": string[] }`，管线：

1. **解析/规范化**：trim、小写、去空。
2. **去重**：同一请求内 + 与既有 `learning_items` 按 `lemma+pos` 去重。
3. **词典匹配**：`lemma+pos` 精确命中 → 补齐 ipa/cefr/sense/sceneTags；未命中 → 元数据留空。
4. **入库**：创建 `word_lists`（缺 name 则用时间戳默认名）+ 批量 insert `learning_items`（source=quest）。
5. **返回** `{ "imported": n, "known": k, "missingMetadata": m, "total": t }`。

## 7. FSRS-5 核心（`learning/fsrs.py`，纯 Python）

- 记忆状态：`new / learning / review / relearning`（New → Learning → Review；间隔忘 → Relearning）。
- 评分：`1=Again 2=Hard 3=Good 4=Easy`。
- 使用 **FSRS-5 官方默认参数向量 w（17 项）与 DSR 模型公式**（difficulty/stability/retrievability 更新 + 间隔公式），取自 open-spaced-repetition 参考实现；**不运行参数优化器**。保留默认 retention 0.9。
- 接口：

```python
@dataclass
class FsrsItem:
    state: str              # new|learning|review|relearning
    due: date
    stability: float
    difficulty: float
    retrievability: float
    reps: int
    lapses: int

def fsrs_schedule(item: FsrsItem | None, rating: int,
                  now: date, retention: float = 0.9) -> FsrsItem: ...
```

- 纯函数、确定性；**单测对照官方参考实现生成的固定样例**（固定 rating 序列 → 断言 due/interval/stability/difficulty 具体值），fixture 从参考实现一次性生成后入库为常量。

### 证据源 → FSRS 评分映射

| 证据 source | rating | 进入排期的前提 |
|---|---|---|
| spontaneous_production（conf≥0.6） | 4 Easy | 满足 §7.1 |
| prompted_production（conf≥0.6） | 3 Good | 满足 §7.1 |
| repetition（conf≥0.6） | 2 Hard | 满足 §7.1 |
| action_understanding（conf≥0.6） | 3 Good | 满足 §7.1 |
| error | 1 Again | — |
| help | 不直接评分 | — |

### 7.1 进入 FSRS 排期条件（§12）

某词在**非 promptLevel 0** 下成功 ≥ 1 次 **且** 总尝试 ≥ 2 后，进入排期（state 从 new → learning）。

## 8. 证据模型与更新规则（`learning/evidence.py`）

证据结构（§12 基线 + axis）：

```json
{
  "evidenceId": "ev_<uuid>", "attemptId": "attempt_<uuid>", "turnId": "turn_...",
  "objectiveId": "obj_<archetypeId>_<wordId>", "itemId": "word_loaf_n_1",
  "source": "prompted_production", "promptLevel": 1,
  "result": "success", "confidence": 0.86,
  "axis": "productive"
}
```

评分权重与轴（§12 表，v1）：

| 证据 | 权重 | axis |
|---|---|---|
| 自发正确产出 spontaneous_production | +1.0 | productive |
| 提示后产出 prompted_production | +0.65 | productive |
| 复述 repetition | +0.4 | pronunciation（ASR 置信度代理） |
| 正确动作理解 action_understanding | +0.55 | receptive |
| 主动求助 help | −0.35 | productive |
| 错误使用 error | −0.5 | productive |

**更新规则（全部落地，§12 逐条）**：

1. **去重**：同一 `attemptId + turnId` 重复命中只记一次（INSERT OR IGNORE）。
2. **低置信度**：`confidence < 0.6` 标 `uncertain`，不判 fail、不计成功；评分权重打 0，可选提示用户重说。
3. **错后重试**：错误后立即重试算一次新尝试（保留原负证据，重试结果独立入账）。
4. **求助后成功**：求助负证据与产出正证据**都记**，且该词教学目标自动下调一档（state 归为需要复习而非新授——FSRS 进入条件按非 promptLevel 0 成功计）。
5. **exposure 计数**：只认服务端 `lexmatch`，同一场景同一词只计一次 exposure（不重复入账）。

**记录顺序**：`engine.record_evidence()` 单一写锁内：`event_store.append("evidence", payload, event_id)` → 幂等返回 sequence → 写 `evidence_events` → 按轴更新 `mastery_states` 三维分 → 满足进入条件则 `fsrs_schedule` 更新排期字段 → COMMIT。→ 返回 sequence 供 ack。

## 9. 挂钩点（与既有回合流集成）

- **回合完成**（`apps/api/app/ws.py::_run_round`，`run_round` 返回后）：用最终 ASR 文本 + 本轮上下文生成产出类证据。上下文判定（**判定可执行规则**）：
  - 该词 ∈ 本轮 `scene_words` **且**本轮 NPC 台词经服务端 `lexmatch` 命中该词（=本轮教过）→ `prompted_production`（promptLevel 1）
  - 该词 ∈ `scene_words` 但 NPC 台词未命中、用户主动说出 → `spontaneous_production`（promptLevel 0）
  - 带读/复述模式 → `repetition`（promptLevel 2）
  - 本轮提示过该词但最终 ASR 落空 → `error`（promptLevel 1）
- **实体点击**（前端 `onEntityClick` → 既有事件流）：点中实体对应的 catalog 概念词 == 目标词 → `action_understanding`。
- **求助**（`_handle_companion_ask`）：记录 `help` 负证据；若该词随后产出成功，产出证据照记并触发规则 4。
- **每场选词**：`learning/scheduler.py` 产出的 `scene_words` 喂入既有 `scene_factory(scene_words, …)` 接缝（`main.py`），并记录到会话态供证据归因（objectiveId 用 `obj_<archetypeId>_<wordId>`）。

## 10. 每场选词（`learning/scheduler.py`）

输入：场景 archetypeId + 该场景 `learning_items`（scene_tags 含该场景）+ `mastery_states`。输出：≤7 个 word_id（同场景标签约束）：

1. **2–3 到期复习**：`mastery_states.state != new` 且 `due <= today`。
2. **2–3 新词**：未排期（state=new）的目标词，优先词典补齐元数据的。
3. **1 薄弱词**：非到期词中三维分和最低。
4. 不足则从剩余词补齐；无词则返回空（NPC 走无目标词模式）。

## 11. 偶遇词流（`learning/encounters.py`）

`spontaneous_encounters` 记录规则（§12）：

- 用户**主动问过一次**（companion.ask 的词）→ 立即记录 `asked=1`。
- 自然对话中**连续两轮出现**（lexmatch 命中，非 NPC 教过的目标词）→ 记录。
- **仅 NPC 台词**出现 → 只记 exposure 计数，**不**创建 learning_item。

记录是**自动**的；从偶遇提升为目标词（创建 `learning_items(source=free)` + 初始化 `mastery_states`）走 `POST /api/word-lists/spontaneous/import`（**手动**，见 §12）。

## 12. HTTP 接口（挂到 `main.py`）

```text
POST /api/word-lists/import
  body  { "name"?: string, "words": string[] }
  resp  { "imported": int, "known": int, "missingMetadata": int, "total": int }

GET  /api/word-lists/spontaneous
  resp  { "items": [{ "id": str, "word": str, "pos": str|null, "turnId": str,
                      "encounterNo": int, "asked": bool, "promoted": bool }] }

POST /api/word-lists/spontaneous/import
  body  { "encounterIds": string[] }   # spontaneous_encounters.id
  resp  { "promoted": int }

GET  /api/progress/summary
  resp  { "totals": { "quest": int, "free": int, "dueToday": int },
          "words": [ { "wordId": str, "lemma": str, "pos": str|null, "ipa": str|null,
                       "cefr": str|null, "sceneTags": string[], "source": str,
                       "scores": { "productive": number, "receptive": number, "pronunciation": number },
                       "fsrs": { "state": str, "due": str|null, "reps": int, "lapses": int },
                       "evidenceCount": int, "lastEvidenceAt": str|null } ] }
```

全部端点读侧不加锁（SQLite WAL 并发读），写侧走引擎单一写锁。错误统一 `{ "detail": str }`。

## 13. 前端最小进度页（`/progress`）

- 路由：`window.location.hash === '#/progress'`（与 `#/dev/archetypes` 同款，App.tsx 顶层分流）。
- 新组件 `ProgressView.tsx`：`fetch('/api/progress/summary')` → 渲染三区：
  - 概览卡：目标词/偶遇词/今日到期数；
  - 词表列表：每词 lemma + IPA + CEFR + 三维分（条形/数字）+ FSRS due + 证据数；
  - 证据时间线（按词展开）：source/result/confidence/时间。
- 复用现有 header 布局与样式（index.css 已有令牌）；不加复习交互。
- 偶遇词「提升」动作：列表里放按钮 → `POST /api/word-lists/spontaneous/import` → 刷新。

## 14. 数据流与容错（§13 兑现）

```
回合完成/点击/求助 → 收集信号 → engine.record_evidence(evidence)
  → [单一写锁]
     event_store.append("evidence", payload, event_id)   ← 幂等，返回 sequence
     insert evidence_events
     按轴更新 mastery_states 三维分
     满足进入条件 → fsrs_schedule 更新排期
     COMMIT
  → ack（含 sequence）发回客户端
```

- **断线补发**：`session_events` 既有回放机制，`evidence` 是其中一种 event_type；重放时 event_id 幂等，不重复入账。
- **音频帧不补发**，重连取消当前 utterance（既有行为不变）。
- SQLite WAL + busy_timeout 5000 + 单一写锁（既有 event_store 配置，LearningStore 复用同一锁/同一库）。
- **失败降级**：证据记录失败不阻断回合主流程（catch 后日志 + ack 带错误标记，学习状态下次补）。

## 15. 测试策略

- **fsrs.py**：官方参考样例单测（固定 rating 序列 → 具体 due/interval/stability/difficulty）；进入条件单测。
- **evidence.py**：更新规则逐条（去重 / 低置信 uncertain / 错后重试 / 求助后成功下调 / 权重与轴）。
- **wordbook.py**：导入管线（解析/去重/词典命中/未命中留空/已知词不重复）。
- **scheduler.py**：选词（due/新/薄弱比例、场景标签约束、不足补齐、空表）。
- **encounters.py**：三条偶遇规则 + 提升流程。
- **引擎集成**：ws harness 产出一轮 → 证据落库 → 三维分更新 → FSRS 排期 → `session_events` 可回放且幂等。
- **API**：4 个端点（导入幂等、spontaneous 列表/提升、progress summary 结构）。
- **前端**：ProgressView 渲染 + summary fetch + 提升按钮（vitest --maxWorkers=1）；tsc。
- **全量回归**：`uv run pytest`（apps/api + scene-schema + scene-compiler）+ web vitest + tsc。

## 16. 依赖与版本

- 后端：仅标准库 + 既有依赖（无新增第三方包；FSRS 自实现）。Python 版本沿用 workspace（3.13）。
- 前端：无新依赖（hash 路由 + fetch）。
- 版本锁定：`evidence_policy_version = "v1"`、`fsrs_algorithm_version = "fsrs-5"` 常量进 `settings.py`。
