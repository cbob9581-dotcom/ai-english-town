# 英语小镇（English Town）阶段 4：学习引擎设计规格（修订版 v2）

> 主 spec：`docs/superpowers/specs/2026-08-05-english-town-design.md`（§3 目标词类型、§12 学习引擎、§13 数据与接口）
> 上游：阶段 2/3 已交付 `event_store.py`（session_events WAL + 幂等）、`lexmatch.py`、`concepts.py`、回合/仲裁/场景流。
> 本修订版吸收评审 17 点 + 建议：FSRS 改用 py-fsrs（钉 FSRS-5）、三维分更新公式、日闸、datetime 语义、no_attempt 判空、原子事务、user_id/版本字段落表、计数列、carrier 可行性、偶遇流分层、outbox、确定性 seed 等。

## 1. 目标与验收

把「掌握度 + 可追溯证据 + 复习排期」落地为可运行系统：

- **两条词流汇入一套掌握度**：目标词流（词表导入 → 确定性处理 → learning_items）与偶遇词流（exposure 记录 → 达标升级）汇入 `mastery_states`。
- **每次尝试都有证据**：`evidence_events` 逐条记录（§12 证据结构 + axis 轴），`attemptId+turnId` 去重，先写库再对客户端确认（§13）。
- **FSRS 决定何时复习**：依赖 **py-fsrs**（纯 Python），锁定实现 **FSRS-5**（参数向量 w 长度 19）的发行版；`fsrs_algorithm_version = "fsrs-5"`；**不运行参数优化器**。
- **每场选词**：5–7 词（2–3 到期复习 + 2–3 新词 + 1 薄弱词），同场景标签，且满足 carrier 可行性。

**验收**：每个目标词可追溯证据（progress 页证据时间线可查）；断线补发不丢（session_events 回放 + event_id 幂等 + evidence_outbox 兜底）。

## 2. 范围

**做**：迷你词典（含 carrier/slotCategories）+ 导入管线、6 张业务表 + 1 张可靠性表（outbox）、py-fsrs 包装层、证据生成与更新规则、每场选词、偶遇词流（明细 + 聚合两层）、4 个 HTTP 端点 + 证据明细端点、最小进度页。

**明确不做**：复习卡流 UI、FSRS 参数优化器、PostgreSQL、多用户（`user_id` 列已落表，仅预留单用户 `'local'`）、云端词元数据补全。

## 3. 全局约束（继承主 spec + 本阶段新增）

- **本地优先**：学习引擎全本地运行，零云端依赖；迷你词典随仓库携带。
- **单一写入事务**：一次 `record_evidence` 内，session_events（evidence 事件）+ evidence_events + mastery_states 更新在**同一连接同一事务**提交（`event_store.append_in_tx`），杜绝「证据已写、掌握态未写」的窗口。
- **幂等**：`event_id` 唯一，重复提交不重复计算（§13）。
- **确定性**：FSRS 固定参数集；导入管线纯函数；选词用 **sceneId 派生的确定性 seed**；一切时间敏感函数显式注入 `now: datetime`（测试可注入固定值）。
- **时间一律 ISO 8601 datetime（UTC，`YYYY-MM-DDTHH:MM:SSZ`）**：`due` / `last_review` 均为 datetime，不做 date 粒度——date 会让同日多次调度状态丢失。
- **ASR 噪声不惩罚用户**：目标词未检出 / 置信度低于阈值 → `no_attempt` / `uncertain`，权重 0，不进 FSRS；**`error` 仅由 companion 明确纠错路径产生**。
- **日闸**：同一词每个自然日 FSRS 最多调度一次；当日代表评分 = 当日所有有效评分中的**最低值**（保守）。
- **纯 Python 依赖集**：不引入原生编译依赖；新依赖上限为纯 Python 包。
- **16GB / 严格串行**（开发环境约束，非运行时）：任何时刻单个测试进程；web 测试 `--maxWorkers=1`。
- 版本字段落表：`evidence_policy_version = "v1"`、`fsrs_algorithm_version = "fsrs-5"`、`score_alpha = 0.35` 常量进 `settings.py`，并写入每条证据/掌握态（见 §4）。
- 实施顺序约束（来自评审）：先写纯函数（fsrs 包装 / update_score / resolve_word_id 缓存 / carrier 过滤 / 选词），再写迁移与集成（evidence / engine / ws 挂钩），最后 API 与前端；迁移在数据模型冻结（本 spec 定稿）后才写。

## 4. 数据模型（与 event_store 同库 SQLite；新增 6 业务表 + 1 可靠性表）

全部业务表带 `user_id TEXT NOT NULL DEFAULT 'local'`（多用户预留；v1 恒为 `'local'`）。

### 4.1 `word_lists`（目标词表头）

```sql
CREATE TABLE word_lists(
  user_id    TEXT NOT NULL DEFAULT 'local',
  list_id    TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  source     TEXT NOT NULL DEFAULT 'import',   -- import | spontaneous | default
  created_at TEXT NOT NULL,                    -- ISO datetime
  UNIQUE(user_id, list_id)
);
```

### 4.2 `learning_items`（词本体）

```sql
CREATE TABLE learning_items(
  word_id      TEXT PRIMARY KEY,        -- word_<lemma>_<pos>_<sense>
  user_id      TEXT NOT NULL DEFAULT 'local',
  lemma        TEXT NOT NULL,
  pos          TEXT,                    -- 短格式 n/v/adj/...（与 catalog/concepts 一致，勿用 noun/verb）
  sense        TEXT,
  ipa          TEXT,
  cefr         TEXT,                    -- A1..B1（词典命中才有）
  scene_tags   TEXT NOT NULL DEFAULT '[]',   -- JSON array，如 ["bakery","plaza"]
  carrier      TEXT NOT NULL DEFAULT 'phrase', -- object|action|phrase（§3 主 spec 支持类型；从词典快照）
  slot_categories TEXT NOT NULL DEFAULT '[]',  -- JSON array；carrier=object 时映射场景 propSlot 类别
  source       TEXT NOT NULL,           -- quest | free
  list_id      TEXT REFERENCES word_lists(list_id),
  created_at   TEXT NOT NULL,
  UNIQUE(user_id, lemma, pos, sense)
);
```

### 4.3 `mastery_states`（每词掌握态；PK 含 user_id）

```sql
CREATE TABLE mastery_states(
  user_id             TEXT NOT NULL DEFAULT 'local',
  word_id             TEXT NOT NULL REFERENCES learning_items(word_id),
  productive_score    REAL NOT NULL DEFAULT 0.0,
  receptive_score     REAL NOT NULL DEFAULT 0.0,
  asr_confidence_score REAL NOT NULL DEFAULT 0.0,  -- 旧名 pronunciation；轴 = asr_confidence
  -- FSRS（py-fsrs Card 持久化字段）
  state               TEXT NOT NULL DEFAULT 'new',    -- new|learning|review|relearning
  due                 TEXT,                           -- ISO datetime（FSRS 到期时刻）；NULL=尚未排期
  stability           REAL NOT NULL DEFAULT 0.0,
  difficulty          REAL NOT NULL DEFAULT 0.0,
  reps                INTEGER NOT NULL DEFAULT 0,
  lapses              INTEGER NOT NULL DEFAULT 0,
  last_review         TEXT,                           -- ISO datetime
  -- 计数与日闸
  attempts            INTEGER NOT NULL DEFAULT 0,     -- 有判定尝试数（success + error）
  exposure_count      INTEGER NOT NULL DEFAULT 0,     -- 该词被服务端 lexmatch 命中次数
  help_count          INTEGER NOT NULL DEFAULT 0,
  success_count       INTEGER NOT NULL DEFAULT 0,
  scaffolded_success_count INTEGER NOT NULL DEFAULT 0, -- promptLevel>0 的成功数（FSRS 进入条件用）
  last_scheduled_date TEXT,                           -- YYYY-MM-DD；日闸
  last_scheduled_rating INTEGER,                      -- 当日已应用的评分（1..4）
  fsrs_algorithm_version TEXT NOT NULL DEFAULT 'fsrs-5',
  updated_at          TEXT NOT NULL,                  -- ISO datetime
  PRIMARY KEY(user_id, word_id)
);
```

> **retrievability 不持久化**：py-fsrs 的 `Card.retrievability` 是派生值（由 stability + 距今间隔即时计算），不落列。

### 4.4 `evidence_events`（证据明细；session_events 之外的物化表）

```sql
CREATE TABLE evidence_events(
  evidence_id           TEXT PRIMARY KEY,
  user_id               TEXT NOT NULL DEFAULT 'local',
  event_seq             INTEGER NOT NULL,      -- 对应 session_events.sequence（同一事务写入）
  session_id            TEXT NOT NULL,
  attempt_id            TEXT NOT NULL,
  turn_id               TEXT NOT NULL,
  objective_id          TEXT,                  -- obj_<archetypeId>_<wordId>；可空（§11 建议）
  word_id               TEXT NOT NULL,
  source                TEXT NOT NULL,         -- spontaneous_production|prompted_production|repetition|action_understanding|help|error
  prompt_level          INTEGER NOT NULL,
  axis                  TEXT NOT NULL,         -- productive|receptive|asr_confidence
  result                TEXT NOT NULL,         -- success|error|no_attempt|uncertain|neutral
  confidence            REAL NOT NULL,         -- ASR 置信度；确定性证据（help/error/action）恒 1.0
  evidence_policy_version TEXT NOT NULL DEFAULT 'v1',
  fsrs_algorithm_version  TEXT NOT NULL DEFAULT 'fsrs-5',
  created_at            TEXT NOT NULL          -- ISO datetime
);
```

### 4.5 偶遇流：`spontaneous_encounters`（明细行，可追溯）+ `spontaneous_words`（聚合汇总）

```sql
CREATE TABLE spontaneous_encounters(   -- 每次命中一行，追源用
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL DEFAULT 'local',
  session_id    TEXT NOT NULL,
  lemma         TEXT NOT NULL,
  pos           TEXT,
  turn_id       TEXT NOT NULL,
  encounter_no  INTEGER NOT NULL,      -- 该词本会话累计出现次数
  created_at    TEXT NOT NULL
);

CREATE TABLE spontaneous_words(        -- 按 (user_id, lemma, pos) 聚合，驱动 API 与提升
  user_id      TEXT NOT NULL DEFAULT 'local',
  lemma        TEXT NOT NULL,
  pos          TEXT,
  first_seen_at  TEXT NOT NULL,        -- ISO datetime
  last_seen_at   TEXT NOT NULL,
  encounter_count INTEGER NOT NULL DEFAULT 0,
  asked        INTEGER NOT NULL DEFAULT 0,   -- 是否被主动问过
  promoted     INTEGER NOT NULL DEFAULT 0,   -- 是否已提升为目标词
  PRIMARY KEY(user_id, lemma, pos)
);
```

### 4.6 `evidence_outbox`（可靠性表：证据事务失败时的待补队列）

```sql
CREATE TABLE evidence_outbox(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    TEXT NOT NULL DEFAULT 'local',
  payload_json TEXT NOT NULL,          -- 完整 evidence payload（含 event_id）
  created_at TEXT NOT NULL
);
```

### 4.7 `session_events` 变更（阶段 2 表，加一列）

```sql
ALTER TABLE session_events ADD COLUMN internal INTEGER NOT NULL DEFAULT 0;
-- internal=1：不推送给前端的内部事件（evidence 等）；重放时跳过
```

`event_store.append()` 增加 `internal: bool = False` 参数（默认 0，既有调用不受影响）。

### 4.8 `resolve_word_id` 接缝替换（`apps/api/app/llm/concepts.py`）

- 签名不变：`resolve_word_id(lemma, pos, *, sense=1) -> str`。
- 行为：先查**进程内缓存** `{(lemma, pos, sense): word_id}`，miss 再查 `learning_items`（命中用其 `word_id`），仍 miss 回退现有确定性派生。
- **冲突规则**：同一 `lemma+pos` 多条 sense → 取**最早创建**者（`created_at` 最小，并列取 rowid 最小）进缓存。
- 缓存失效：导入 / 偶遇提升后调用 `invalidate_word_id_cache()`（模块级清空）。

## 5. 迷你词典 `assets/wordbook/dictionary.json`

```json
{
  "version": 1,
  "words": [
    { "lemma": "loaf", "pos": "n", "senses": ["一条面包"],
      "ipa": "/loʊf/", "cefr": "A2", "sceneTags": ["bakery"],
      "carrier": "object", "slotCategories": ["food"] },
    { "lemma": "order", "pos": "v", "senses": ["点（餐）", "订购"],
      "ipa": "/ˈɔːrdər/", "cefr": "A2", "sceneTags": ["bakery", "cafe"],
      "carrier": "phrase", "slotCategories": [] }
  ]
}
```

- 覆盖 6 场景原型（bakery/cafe/park/plaza/library/station）的目标词，A1–B1，约 120–200 词。
- **carrier ∈ {object, action, phrase}**（对齐主 spec §3:34「目标词类型先支持 object / action / phrase」）。
  - `object`：词须映射到某实体，`slotCategories` 必须命中场景原型至少一个 propSlot 类别（**可行性过滤**，见 §10）；
  - `action` / `phrase`：无需 propSlot，作为 NPC 引导用户说出的目标词。
- 词条与阶段 3 资产（entities/catalog/concepts/icon-map）的词形保持一致；词形不一致处以后者为准并补进词典。
- 加载：`Dictionary.load(asset_root)`（类比 `Catalog.load`）；未命中场景标签或 carrier 可行性不满足的词，允许出现但选词不选它。
- 导入 `learning_items` 时把 `carrier` / `slot_categories` 一并快照（选词不依赖词典热加载）。

## 6. 导入管线（`learning/wordbook.py`）

`POST /api/word-lists/import` 请求体 `{ "name"?: string, "words": string[] }`。

**输入约束（评审建议）**：
- `words` 长度 ≤ 500；单个词 ≤ 64 字符；仅允许 `[a-zA-Z]`、连字符 `-`、撇号 `'`、空格（其余整请求 422 `{ "detail": str }`）。
- `pos` 来源规则：条目支持 `"loaf/n"` 语法（显式短格式 pos，与 catalog/concepts 一致）；未指定时取词典中该 lemma 的第一个词条，**多义词全部导入**（每个 sense 一条 learning_item）。

管线：

1. **解析/规范化**：trim、小写、去空。
2. **去重**：同一请求内 + 与既有 `learning_items` 按 `(user_id, lemma, pos, sense)` 去重。
3. **词典匹配**：`lemma+pos` 精确命中 → 补齐 ipa/cefr/sense/sceneTags/carrier/slotCategories；未命中 → 元数据留空、carrier 默认 `phrase`。
4. **入库**：创建 `word_lists`（缺 name 则用时间戳默认名）+ 批量 insert `learning_items`（source=quest）。
5. **缓存失效**：调用 `invalidate_word_id_cache()`。
6. **返回** `{ "imported": n, "known": k, "missingMetadata": m, "total": t }`。

## 7. FSRS 排期（`learning/fsrs.py`，py-fsrs 包装层）

- **依赖**：PyPI 发行名 `fsrs`（py-fsrs，纯 Python，仅运行时依赖 `typing-extensions`）。**锁定 `fsrs>=5,<6`**：5.1.3 经核实即 FSRS-5（`Scheduler` docstring「19 model weights」，参数向量 w 长度 19，w[0..18]）；6.x 是 FSRS-6（21 项）故上界 `<6`。
- **守卫测试**（启动即断言，防止升级踩线）：`len(默认参数向量) == 19`；否则启动失败并提示回退 py-fsrs 版本。
- 记忆状态 `new / learning / review / relearning`；评分 `1=Again 2=Hard 3=Good 4=Easy`；保留默认 retention 0.9（进 `settings.py`，`fsrs_retention`）。
- 包装层接口（**按 py-fsrs 5.1.3 真实 API**，已核源码；5.x 的 `review_card` 直接返回更新后的 Card，**不是** 2.x 的 `SchedulingCards`）：

```python
# learning/fsrs.py
from fsrs import Card, Rating, Scheduler
from datetime import datetime

def _scheduler() -> Scheduler:
    # enable_fuzzing=False 保证确定性；learning_steps/relearning_steps 置空 → 首次评分
    # 直接进 Review（按 interval 排期），与日闸（每天至多一次）一致，不做 1min/10min 学习步。
    return Scheduler(desired_retention=0.9, enable_fuzzing=False,
                     learning_steps=(), relearning_steps=())

def to_fsrs_card(row: Mapping) -> Card:
    """mastery_states 行 → py-fsrs Card。state='new' → 全新 Card()（内部 Learning/step 0）；
    否则按 state/stability/difficulty/due/last_review 恢复（step 由 state 推导）。
    card_id 传显式稳定值（如 hash(word_id)）——避免 Card() 默认构造里的 time.sleep(0.001)。"""

def from_fsrs_card(card: Card) -> dict:
    """Card → mastery_states 列。state 映射：Learning→learning, Review→review,
    Relearning→relearning（'new' 由引擎维护：尚未 schedule 时）。"""

def schedule(card: Card | None, rating: int, now: datetime) -> Card:
    """updated, _log = _scheduler().review_card(card or to_fsrs_card(new_row), Rating(rating), now)
    now 必须是 tz-aware UTC（review_card 校验）；返回更新后的 Card。纯函数、确定性。"""
```

- `due` / `last_review` 均为 **ISO datetime**；`schedule` 的 `now: datetime` 由调用方注入（WS 会话或测试的时钟），须 tz-aware UTC。
- `reps`/`lapses` **不由 py-fsrs Card 维护**（5.x Card 无此字段）：`mastery_states.reps/lapses` 由引擎自记（reps = 已调度次数；lapses = 在 Review 态评 Again 的次数）。
- 单测：固定 `now` + 固定 rating 序列 → 断言 `due/stability/difficulty` 具体值（`enable_fuzzing=False` 保证可复现），并断言与 py-fsrs 5.1.3 输出一致。

### 证据源 → FSRS 评分映射（有效评分）

| 证据 source | rating | 备注 |
|---|---|---|
| spontaneous_production（result=success） | 4 Easy | |
| prompted_production（result=success） | 3 Good | |
| repetition（result=success） | 2 Hard | |
| action_understanding（result=success） | 3 Good | 接受性检索，按成功记 |
| error（companion 明确纠错） | 1 Again | |
| no_attempt / uncertain / neutral（help） | 无 | 不进 FSRS、不评日闸 |

### 7.1 进入 FSRS 排期条件（主 spec §12）

某词 **`scaffolded_success_count >= 1` 且 `attempts >= 2`** 后进入排期（state: new → learning）。`attempts` = result ∈ {success, error} 的计数；`no_attempt/uncertain/neutral` 不计尝试。

### 7.2 日闸（评审第 5 点）

- 同一 `(user_id, word_id)` **每个自然日最多调度一次**（`last_scheduled_date` 比对，自然日 = 注入 `now` 的 UTC 日期）。
- **当日代表评分 = 该日所有有效评分中的最低值**（保守），增量实现：当日已调度后，若新证据评分 < `last_scheduled_rating` → 用更低评分**重排**并更新 `last_scheduled_rating`；若 ≥ → 跳过 FSRS，仅更新三维分与计数。
- 日闸不影响三维分 / 计数更新（那些每证据都记）。

## 8. 证据模型与更新规则（`learning/evidence.py`）

证据结构：

```json
{
  "evidenceId": "ev_<uuid>", "attemptId": "attempt_<uuid>", "turnId": "turn_...",
  "objectiveId": "obj_<archetypeId>_<wordId> | null",
  "wordId": "word_loaf_n_1",
  "source": "prompted_production", "promptLevel": 1,
  "axis": "productive",
  "result": "success",
  "confidence": 0.86
}
```

- `result`：`success | error | no_attempt | uncertain | neutral`。
  - `no_attempt`：目标词**未在 ASR 文本中检出**（ASR 落空）→ 权重 0、不进 FSRS、不更新分、不计尝试；
  - `uncertain`：检出但 `confidence < 0.6` → 同上（不判成败、不计尝试），可选提示用户重说；
  - `error`：**仅** companion 明确纠错路径（见 §9）；
  - `neutral`：无成败判定（help 求助）。
- 确定性证据（help / error / action_understanding）`confidence = 1.0`；产出类证据 `confidence = ASR 置信度`。

评分权重与轴（§12 表，`evidence_policy_version="v1"`）：

| 证据 | 权重 | axis | 说明 |
|---|---|---|---|
| 自发正确产出 spontaneous_production | +1.0 | productive | |
| 提示后产出 prompted_production | +0.65 | productive | |
| 复述 repetition | +0.4 | **asr_confidence** | 发音轴=ASR 置信度代理 |
| 正确动作理解 action_understanding | +0.55 | receptive | |
| 主动求助 help | −0.35 | productive | result=neutral |
| 明确纠错 error | −0.5 | productive | result=error，rating 1 |

**三维分更新公式（评审第 1 点，逐条落地）**：

```python
ALPHA = settings.score_alpha  # 0.35；随 evidence_policy_version 版本化，调参不回溯历史

def update_score(score: float, weight: float, confidence: float) -> float:
    """一次证据只更新其 axis 对应的一维。delta = weight * confidence。"""
    delta = weight * confidence
    if delta >= 0:
        return min(1.0, score + ALPHA * delta * (1.0 - score))
    return max(0.0, score + ALPHA * delta * score)
```

**更新规则（全部落地，主 spec §12 逐条 + 评审修正）**：

1. **去重**：同一 `attemptId + turnId` 重复命中只记一次（INSERT OR IGNORE）。
2. **低置信度 / 未检出**：`confidence < 0.6` → `uncertain`；目标词未检出 → `no_attempt`。二者权重 0、不进 FSRS（**不再判 error**——评审第 4 点，避免 ASR 落空反向破坏排期）。
3. **错后重试**：error 后立即重试算一次新尝试（保留原负证据，重试结果独立入账）。
4. **求助后成功**：求助负证据与产出正证据**都记**（help_count、成功证据照记）；FSRS 进入条件按 `scaffolded_success_count` 计，求助不直接下调排期（评审第 5 点改由日闸保守化承载「教学目标自动下调」）。
5. **exposure 计数**：只认服务端 `lexmatch`，同一场景同一词每轮最多计一次 exposure（`exposure_count += 1`；主 spec 未定义阈值，v1 不设）。
6. **日闸**：见 §7.2。

**记录顺序（单一事务）**：`engine.record_evidence()` 在引擎单写锁内、用**引擎自持连接**：
`event_store.append_in_tx(conn, "evidence", payload, event_id, internal=True)` → 返回 sequence → 写 `evidence_events` → 按轴 `update_score` 更新三维分 → 更新计数 → 有效评分则按 §7.2 日闸 `schedule` → 一次 **COMMIT**；失败 **ROLLBACK** 后把 payload 写 `evidence_outbox`（尽力而为）→ 返回 `None`（ack 带错误标记）。成功返回 sequence 供 ack。

## 9. 挂钩点（与既有回合流集成）

- **回合完成**（`apps/api/app/ws.py::_run_round`，`run_round` 返回后）：用最终 ASR 文本 + 本轮上下文生成产出类证据。判定（可执行规则）：
  - 该词 ∈ 本轮 `scene_words` **且**本轮 NPC 台词经服务端 `lexmatch` 命中该词（=本轮教过）→ `prompted_production`（promptLevel 1）；
  - 该词 ∈ `scene_words` 但 NPC 台词未命中、用户主动说出 → `spontaneous_production`（promptLevel 0）；
  - 带读/复述模式 → `repetition`（promptLevel 2）；
  - **目标词 ∈ `scene_words` 但最终 ASR 未检出 → `no_attempt`**（不判 error）；
  - 本轮 **companion 明确纠错**该词（若回合流存在纠错信号则钩之；v1 无该信号则该 source 不产生）→ `error`。
- **实体点击**（前端 `onEntityClick` → 既有事件流）：点中实体对应的 catalog 概念词 == 目标词 → `action_understanding`。
- **求助**（`_handle_companion_ask`）：记录 `help` 负证据（neutral）；若该词随后产出成功，产出证据照记并触发规则 4。
- **每场选词**：`learning/scheduler.py` 产出的 `scene_words` 喂入既有 `scene_factory(scene_words, …)` 接缝（`main.py`），并记录到会话态供证据归因（`objectiveId = obj_<archetypeId>_<wordId>`）。
- **resolve_word_id**：所有归因走 §4.8 接缝（缓存 + learning_items 优先），不再裸派生。

## 10. 每场选词（`learning/scheduler.py`）

输入：场景 archetypeId + 该场景 `learning_items`（scene_tags 含该场景）+ `mastery_states` + 注入的 `now: datetime`。输出：≤7 个 word_id（同场景标签约束）：

1. **可行性过滤**：`carrier != object` 或 `word.slot_categories ∩ scene.propSlot 类别 ≠ ∅`（§5）；不过滤的词本轮不入选。
2. **2–3 到期复习**：`state != new` 且 `due <= now`（**datetime 比较**）。
3. **2–3 新词**：未排期（state=new）的目标词，优先词典补齐元数据的。
4. **1 薄弱词**：非到期词中 **`productive_score + receptive_score` 和最低**的 1 词（**asr_confidence 轴不参与薄弱词选择**——评审第 12 点，它只是实验性代理）。
5. 候选若超过名额，用**确定性 seed** 排序取前 N：`seed = sha1(f"scene-select:{archetypeId}:{now.date().isoformat()}")`（**sceneId 派生**思路；因 sceneId 每次进场变化，改用「archetypeId + 日期」作稳定键——与阶段 3 `_stable_index` 稳定哈希一致）。同一天同场景每次进入词不变，跨天轮转，测试注入固定 now 后完全确定。
6. 不足则从剩余词补齐；无词则返回空（NPC 走无目标词模式）。

## 11. 偶遇词流（`learning/encounters.py`）

- **明细行**（`spontaneous_encounters`）：服务端 `lexmatch` 命中非目标词时逐次记一行（含 `encounter_no`，会话内累加）。
- **聚合表**（`spontaneous_words`）：每次命中 upsert 聚合（`encounter_count`+1、`first/last_seen_at` 更新）。
- 触发记录（主 spec §12）：
  - 用户**主动问过一次**（companion.ask 的词）→ `asked=1`；
  - 自然对话中**连续两轮出现**（lexmatch 命中，非 NPC 教过的目标词）→ 记录。**「连续两轮」状态存会话内存**（`{lemma: last_turn_seen}`），重连时重置，不落库；
  - **仅 NPC 台词**出现 → 只记 exposure 计数，**不**创建 learning_item。
- 从偶遇提升为目标词（创建 `learning_items(source=free)` + 初始化 `mastery_states`）走 `POST /api/word-lists/spontaneous/import`（**手动**，见 §12），入参为 **`lemmas`**（评审第 13 点），完成后 `invalidate_word_id_cache()`。

## 12. HTTP 接口（挂到 `main.py`）

```text
POST /api/word-lists/import
  body  { "name"?: string, "words": string[] }        # words ≤500、每词 ≤64、字符集约束（§6）
  resp  { "imported": int, "known": int, "missingMetadata": int, "total": int }
  err   422 { "detail": str }   # 超限/非法字符

GET  /api/word-lists/spontaneous
  resp  { "items": [{ "lemma": str, "pos": str|null, "firstSeenAt": str,
                      "lastSeenAt": str, "encounterCount": int,
                      "asked": bool, "promoted": bool }] }

POST /api/word-lists/spontaneous/import
  body  { "lemmas": string[] }                        # 评审第 13 点：按 lemma 提升（非 encounter id）
  resp  { "promoted": int, "unknown": int }

GET  /api/progress/summary?page=1&page_size=50
  resp  { "strategy": { "evidencePolicyVersion": "v1", "fsrsAlgorithmVersion": "fsrs-5" },
          "totals": { "quest": int, "free": int, "dueToday": int },   # dueToday = state!=new 且 due<=now
          "page": int, "pageSize": int, "totalWords": int,
          "words": [ { "wordId": str, "lemma": str, "pos": str|null, "ipa": str|null,
                       "cefr": str|null, "sceneTags": string[], "source": str,
                       "carrier": str, "scores": { "productive": number, "receptive": number,
                                                   "asrConfidence": number },
                       "fsrs": { "state": str, "due": str|null, "reps": int, "lapses": int },
                       "evidenceCount": int, "lastEvidenceAt": str|null } ] }

GET  /api/progress/words/{wordId}/evidence            # 评审建议：证据时间线独立端点，summary 只回计数
  resp  { "items": [ { "evidenceId": str, "source": str, "result": str,
                       "axis": str, "confidence": number, "promptLevel": int,
                       "objectiveId": str|null, "createdAt": str } ] }
```

- 全部端点读侧不加锁（SQLite WAL 并发读），写侧走引擎单一写锁。错误统一 `{ "detail": str }`。

## 13. 前端最小进度页（`/progress`）

- 路由：`window.location.hash === '#/progress'`（与 `#/dev/archetypes` 同款，App.tsx 顶层分流）。
- 新组件 `ProgressView.tsx`：`fetch('/api/progress/summary')` → 渲染四区：
  - **策略角标**：`策略 v1 · FSRS v5`（来自 summary.strategy，评审建议）；
  - 概览卡：目标词/偶遇词/今日到期数；
  - 词表列表：每词 lemma + IPA + CEFR + 三维分（**asr_confidence 轴标注「实验性 · ASR 置信度代理，非发音评测」**，评审第 12 点）+ FSRS due + 证据数；分页（`page`/`pageSize`）；
  - 证据时间线（按词展开）：点词 → `GET /api/progress/words/{wordId}/evidence` → source/result/confidence/时间。
- 复用现有 header 布局与样式（index.css 已有令牌）；不加复习交互。
- 偶遇词「提升」动作：列表里放按钮 → `POST /api/word-lists/spontaneous/import`（body `{"lemmas": [...]}`）→ 刷新。

## 14. 数据流与容错（§13 兑现）

```
回合完成/点击/求助 → 收集信号 → engine.record_evidence(evidence)
  → [引擎单写锁 + 引擎自持连接，单一事务]
     event_store.append_in_tx(conn, "evidence", payload, event_id, internal=True)   ← 幂等，返回 sequence
     insert evidence_events
     按轴 update_score 更新三维分
     更新计数（attempts/success_count/...）
     有效评分 → 日闸（§7.2）→ schedule（inject now）
     COMMIT
  → ack（含 sequence）发回客户端
  失败 → ROLLBACK → payload 写 evidence_outbox（尽力而为）→ ack 带错误标记
```

- **启动补账**：API lifespan 启动时读取 `evidence_outbox`，逐条走同一 `record_evidence` 路径重放（event_id 幂等，不重复入账），成功后清出队；日志记录条数。
- **evidence 事件不推送前端**：session_events 新增 `internal` 列，evidence 置 1；重连回放过滤 `internal=1`（评审第 15 点）。
- **断线补发**：`session_events` 既有回放机制，`evidence` 是其中一种 event_type（internal）；重放时 event_id 幂等，不重复入账。
- **音频帧不补发**，重连取消当前 utterance（既有行为不变）。
- SQLite WAL + busy_timeout 5000 + 引擎单写锁（既有 event_store 配置，LearningStore 复用同一库）。
- **失败降级**：证据记录失败不阻断回合主流程（catch 后日志 + ack 错误标记，学习状态由 outbox 补）。
- **state-audit 断言更新**：既有的库表清单白名单加入新表（word_lists/learning_items/mastery_states/evidence_events/spontaneous_encounters/spontaneous_words/evidence_outbox）与 `session_events.internal` 列。

## 15. 测试策略

- **fsrs.py**：守卫测试（`len(默认参数)==19`）；固定 now + rating 序列 → 断言 due/stability/difficulty/reps/lapses 具体值与 py-fsrs 锁定版本一致；进入条件（scaffolded_success_count/attempts）。
- **evidence.py**：`update_score` 公式（正/负/上下界收敛/轴隔离——一次证据只动一维）；规则逐条（去重 / no_attempt / uncertain / 错后重试 / 求助后成功 / exposure 计数）；**日闸**（同日低评分重排、高评分跳过、跨日重置）。
- **wordbook.py**：导入管线（解析/去重/词典命中/未命中留空/已知词不重复/**输入约束 422**/pos 来源规则/多义词全导入）。
- **scheduler.py**：选词（due datetime 比较、新词、薄弱词排除 asr_confidence 轴、**carrier 可行性过滤**、**确定性 seed（注入固定 now 断言同输出）**、空表）。
- **encounters.py**：明细行 + 聚合 upsert + 三条偶遇规则（asked / 连续两轮 / 仅 NPC）+ 提升流程（lemmas）。
- **concepts.py**：resolve_word_id 缓存命中/miss、冲突取最早创建、导入后失效。
- **outbox**：事务失败 → payload 入 outbox → 启动重放幂等。
- **引擎集成**：ws harness 产出一轮 → 证据落库 → 三维分更新 → FSRS 排期 → `session_events` 可回放且幂等、evidence 事件 internal 不推送。
- **API**：4 + 1 端点（导入幂等、spontaneous 列表/提升、summary 分页、evidence 时间线）。
- **前端**：ProgressView 渲染 + summary fetch + 分页 + 提升按钮 + 策略角标（vitest `--maxWorkers=1`）；tsc。
- **全量回归**：`uv run pytest`（apps/api + scene-schema + scene-compiler）+ web vitest + tsc。

## 16. 依赖与版本

- 后端：新增唯一第三方运行时依赖 **py-fsrs**（PyPI 发行名 `fsrs`，纯 Python）；**锁定 `fsrs>=5,<6`**（5.1.3 即 FSRS-5/19 参数，6.x 为 FSRS-6）；守卫测试仍断言 `len(默认参数)==19` 防升级踩线；余为标准库 + 既有依赖。
- 前端：无新依赖（hash 路由 + fetch）。
- 常量进 `settings.py`：`evidence_policy_version="v1"`、`fsrs_algorithm_version="fsrs-5"`、`score_alpha=0.35`、`fsrs_retention=0.9`、`fsrs_min_confidence=0.6`。
- 实施顺序（评审）：迁移/DDL 在数据模型冻结后；`evidence.py` 在 fsrs 包装与 resolve_word_id 缓存就绪后；API 与前端最后。
