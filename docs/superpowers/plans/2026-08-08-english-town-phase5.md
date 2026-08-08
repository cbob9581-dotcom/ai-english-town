# 阶段 5（WorldMemory + 词级对齐评分）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 session_events + 学习引擎表抽取结构化 WorldMemory 喂给 Scene Director / Tutor / NPC，并把 utterance 级 ASR 置信度升级为词级对齐置信度（诚实命名，非发音评测）。

**Architecture:** 子阶段 A（记忆抽取）——`memory_state` 单例表 + 触发式重建（`apply_memory_updates` 只在新场景/新词进 known/learning/review/新求助词时 +revision）+ `world_summary.snapshot` 事件（重放可重建）；子阶段 B（词级对齐）——asr-worker 输出 `words`（门槛开关 + 自检降级）→ 词级打分器 → 新证据源 `word_production`（轴 `asr_word_confidence`，新列）→ 证据详情逐词可视化。子阶段 C（云端图像）只写文档，不写代码。

**Tech Stack:** Python 3 / FastAPI / SQLite WAL / faster-whisper / React 19 + TS / vitest / pytest / uv / pnpm。

## Global Constraints（继承 spec 逐字）

- **本地优先 / 16GB 严格串行**：全本地、零新模型依赖（`word_timestamps` 是 faster-whisper 现成能力）；任何时刻单个测试进程，web `--maxWorkers=1`。
- **单事务**：`apply_memory_updates` 与证据记录在同一连接同一事务提交（`record_evidence` 内联）；抽取失败 catch + 日志、revision 不推进，不阻断主流程。场景进入/求助走独立事务（各自持 `events.write_lock`）。
- **幂等**：`world_summary.snapshot` 事件按 `event_id` 幂等（复用 `event_store.append_in_tx`）；证据侧沿用 phase-4 event_id 去重。
- **确定性**：`build_world_summary` 是唯一事实源（纯读库重建，跨会话扫 `session_events`）；一切时间敏感函数显式注入 `now: datetime`。
- **时间一律 ISO 8601 datetime（UTC）**：`memory_state.updated_at` / snapshot 事件时间均 tz-aware UTC ISO。
- **版本字段落表**：`memory_policy_version = "v1"`、`pronunciation_policy_version = "v1"`、`enable_word_timestamps = False`（默认关）、`word_timestamp_min_model = "whisper-large-v3"`、`pronunciation_audio_consent = False`（默认关）进 `settings.py`。
- **revision 只在实质变化时 +1**（spec §4.3 should_touch，逐字）：
  - 新场景首次进入足迹（`scene_enter` 且 archetype 不在 `cache.visited_archetypes`）→ +1；
  - **已知场景再进入（仅计数/lastAt 变）→ 不变**（否则 prefetch 键 `(archetype_id, revision)` 每次进场失效，违背用户点①"避免 scene_prefetch cache invalidation"）；
  - 证据使某词进入 known/learning/review（原 `last_known_word_ids` 无此词）→ +1；
  - 新增求助词（不在 `last_helped_set`）→ +1；
  - 一轮多证据最多 +1（`_store_summary` 后 `cache.refresh()`，同一轮后续证据命中已刷新缓存 → 不重复 +）。
- **语义诚实**：任何面向用户/证据轴的"发音"字样必须区分「ASR 置信度」与「发音评测」；词级结果是前者。轴名 `asr_word_confidence_score`，ProgressView 标注 `Word-level ASR confidence (experimental, from aligned timestamps)`。
- **state-audit 白名单**新增 `memory_state` 表（Task 1 同步）；`mastery_states` 加 `asr_word_confidence_score` 列（加列不影响表级白名单）。
- **词级打分分支（冻结）**：目标词 lemma 在 words 中 → `word.probability`；lemma 在 user_text 但不在 words → 0.15（说但被误识别）；lemma 不在 user_text → 不产生词级证据；words 整体缺失/`None`/`[]` → 不产生词级证据（phase-4 行为）。
- **`word_production` 证据**：不进 `_RATING`（不排期）**且不计数**（`apply_evidence` 内独立分支：只落证据明细 + 更新 `asr_word_confidence_score` 轴分，防与 classify_round 双重计数、防扰动复习节奏）。
- **期望比对只用 dictionary IPA**；不引 eSpeak G2P 新依赖。
- **云端图像本轮不实现**：只写文档缝（Task 1 Step 5 核对），不写代码。

---

## 文件结构

```
services/asr-worker/asr_worker/whisper_engine.py   Task 6  word_timestamps 开关 + 模型名 + 门槛
services/asr-worker/asr_worker/streaming.py        Task 6  finalize 携带 words
services/asr-worker/asr_worker/selfcheck.py        Task 6  报告 word_timestamps 生效态
services/asr-worker/asr_worker/server.py           Task 6  _load 读 env + 透传
services/asr-worker/tests/test_streaming.py        Task 6  words 三态 + 门槛
apps/api/app/settings.py                           Task 1  5 个新字段 + env
apps/api/app/learning/store.py                     Task 1/7 memory_state 表 + 新列 + all_words 列
apps/api/tests/test_state_audit.py                 Task 1  白名单 + memory_state
apps/api/tests/test_migration.py                   Task 1  旧库迁移幂等
apps/api/app/learning/memory.py                    Task 2  MemoryCache/MemoryStore/apply_memory_updates/build/format
apps/api/tests/test_memory.py                      Task 2  抽取/模板/revision 语义
apps/api/app/learning/engine.py                    Task 3/7 self.memory + record_evidence 挂钩 + record_round words
apps/api/app/scene_lifecycle.py                    Task 3/4 enter_scene 挂钩 + fill_scene revision/world_summary
apps/api/app/learning/encounters.py                Task 3  record_ask events 挂钩
apps/api/app/llm/scene_director.py                 Task 4  propose/_build_messages/Protocol world_summary
apps/api/app/llm/mock.py                           Task 4  MockSceneDirector 签名同步
apps/api/app/llm/tutor.py                          Task 4  reply world_summary 注入
apps/api/app/llm/npc_actor.py                      Task 4  stream_reply world_summary 注入
apps/api/app/scene_prefetch.py                     Task 4  键 (archetype_id, revision)
apps/api/app/voice_round.py                        Task 4/7/8 run_round world_summary/words/WAV 落盘
apps/api/app/ws.py                                 Task 4/7/8 _prefetch_for/_run_round/record_ask events/SessionState.settings
apps/api/app/main.py                               Task 3  wiring MemoryStore
apps/api/app/learning/word_confidence.py           Task 7  词级打分器
apps/api/app/learning/scores.py                    Task 7  WEIGHTS + word_production
apps/api/app/learning/evidence.py                  Task 7  分支 + 列映射
apps/api/tests/test_memory_hooks.py                Task 3  revision 语义 + 重放
apps/api/tests/test_memory_injection.py            Task 4  注入 + prefetch 键
apps/api/tests/test_word_confidence.py             Task 7  三分支
apps/api/app/learning/memory_smoke.py              Task 5  回放脚本
apps/api/tests/test_memory_smoke.py                Task 5  回放 + 不暴涨
apps/api/tests/test_audio_consent.py               Task 8  授权/未授权/打断
apps/api/app/learning/api.py                       Task 7  asrWordConfidence 字段
apps/web/src/ProgressView.tsx                      Task 9  标注 + 词级标记
apps/web/tests/ProgressView.test.tsx               Task 9  标注/值/词级标记断言
```

---

## Task 1: Settings + 数据模型迁移 + state-audit 白名单

**Files:**
- Modify: `apps/api/app/settings.py`
- Modify: `apps/api/app/learning/store.py`
- Test: `apps/api/tests/test_state_audit.py`、`apps/api/tests/test_settings.py`、`apps/api/tests/test_migration.py`

**Interfaces:**
- Produces: `Settings.memory_policy_version/pronunciation_policy_version/enable_word_timestamps/word_timestamp_min_model/pronunciation_audio_consent`（含 `_ENV_FIELDS`）；`LearningStore.SCHEMA` 含 `memory_state` 表 + `mastery_states.asr_word_confidence_score` 列；`LearningStore.__init__` 末尾 `_migrate()`（旧库 ALTER 加列，幂等）；state-audit `ALLOWED_TABLES` 含 `memory_state`。

- [ ] **Step 1: 写失败测试**

```python
# apps/api/tests/test_settings.py（追加到既有文件末尾）
def test_phase5_settings_defaults():
    s = Settings()
    assert s.memory_policy_version == "v1"
    assert s.pronunciation_policy_version == "v1"
    assert s.enable_word_timestamps is False
    assert s.word_timestamp_min_model == "whisper-large-v3"
    assert s.pronunciation_audio_consent is False
```

```python
# apps/api/tests/test_migration.py（新建）
import sqlite3
from app.learning.store import LearningStore

_OLD_MASTERY = """
CREATE TABLE mastery_states(
  user_id TEXT NOT NULL DEFAULT 'local',
  word_id TEXT NOT NULL REFERENCES learning_items(word_id),
  productive_score REAL NOT NULL DEFAULT 0.0,
  receptive_score REAL NOT NULL DEFAULT 0.0,
  asr_confidence_score REAL NOT NULL DEFAULT 0.0,
  state TEXT NOT NULL DEFAULT 'new', due TEXT,
  stability REAL NOT NULL DEFAULT 0.0, difficulty REAL NOT NULL DEFAULT 0.0,
  reps INTEGER NOT NULL DEFAULT 0, lapses INTEGER NOT NULL DEFAULT 0,
  last_review TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  exposure_count INTEGER NOT NULL DEFAULT 0, help_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0,
  scaffolded_success_count INTEGER NOT NULL DEFAULT 0,
  last_scheduled_date TEXT, last_scheduled_rating INTEGER,
  fsrs_algorithm_version TEXT NOT NULL DEFAULT 'fsrs-5',
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, word_id)
);
"""

def test_mastery_states_has_new_column_fresh():
    conn = sqlite3.connect(":memory:")
    st = LearningStore(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(mastery_states)")}
    assert "asr_word_confidence_score" in cols
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "memory_state" in tables

def test_migration_adds_column_and_memory_state_to_existing_db(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_MASTERY)          # 模拟 phase-4 旧库（无新列/新表）
    conn.commit(); conn.close()
    conn2 = sqlite3.connect(db)
    st = LearningStore(conn2)                  # 构造跑 _migrate()
    cols = {r[1] for r in conn2.execute("PRAGMA table_info(mastery_states)")}
    assert "asr_word_confidence_score" in cols
    tables = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "memory_state" in tables
    st._migrate()                              # 幂等：再跑不报错
    assert "asr_word_confidence_score" in {r[1] for r in conn2.execute("PRAGMA table_info(mastery_states)")}
```

```python
# apps/api/tests/test_state_audit.py —— 白名单追加 memory_state（回合触场景进入会动它）
ALLOWED_TABLES = {"session_events", "llm_calls", "tutor_cache",
                  "word_lists", "learning_items", "mastery_states",
                  "evidence_events", "spontaneous_encounters", "spontaneous_words",
                  "evidence_outbox", "memory_state"}
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run --project apps/api pytest apps/api/tests/test_settings.py apps/api/tests/test_migration.py apps/api/tests/test_state_audit.py -q`
Expected: FAIL（Settings 无新字段 / 无新列 / 白名单不含 memory_state）。

- [ ] **Step 3: 实现**

`apps/api/app/settings.py`（阶段 4 块后追加）：

```python
    # --- 阶段 5：WorldMemory + 词级对齐评分 ---
    memory_policy_version: str = "v1"
    pronunciation_policy_version: str = "v1"
    enable_word_timestamps: bool = False          # 默认关；开则 asr-worker 输出 words
    word_timestamp_min_model: str = "whisper-large-v3"
    pronunciation_audio_consent: bool = False     # 默认关；开才落盘 WAV
```

`_ENV_FIELDS` 追加 5 条：`memory_policy_version`→`MEMORY_POLICY_VERSION`、`pronunciation_policy_version`→`PRONUNCIATION_POLICY_VERSION`、`enable_word_timestamps`→`ENABLE_WORD_TIMESTAMPS`、`word_timestamp_min_model`→`WORD_TIMESTAMP_MIN_MODEL`、`pronunciation_audio_consent`→`PRONUNCIATION_AUDIO_CONSENT`（沿用现有 bool 分支解析）。

`apps/api/app/learning/store.py`：`SCHEMA` 的 `mastery_states` 建表语句 `asr_confidence_score REAL NOT NULL DEFAULT 0.0,` 之后加：

```sql
  asr_word_confidence_score REAL NOT NULL DEFAULT 0.0,
```

`SCHEMA` 末尾追加：

```sql
CREATE TABLE IF NOT EXISTS memory_state(
  user_id            TEXT NOT NULL DEFAULT 'local',
  world_summary_json TEXT NOT NULL,
  revision           INTEGER NOT NULL DEFAULT 0,
  updated_at         TEXT NOT NULL,
  PRIMARY KEY(user_id)
);
```

`LearningStore.__init__` 末尾（`executescript(SCHEMA)` 之后）调用迁移，并新增方法：

```python
        self._migrate()

    def _migrate(self) -> None:
        """旧库幂等迁移：mastery_states 缺 asr_word_confidence_score 列则 ALTER 加列。"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(mastery_states)")}
        if "asr_word_confidence_score" not in cols:
            self.conn.execute(
                "ALTER TABLE mastery_states ADD COLUMN asr_word_confidence_score REAL NOT NULL DEFAULT 0.0")
        self.conn.commit()
```

`apps/api/tests/test_state_audit.py`：`ALLOWED_TABLES` 改为上表（含 `memory_state`）。

- [ ] **Step 4: 运行验证通过**

Run: `uv run --project apps/api pytest apps/api/tests/test_settings.py apps/api/tests/test_migration.py apps/api/tests/test_state_audit.py -q`
Expected: PASS（全绿）。

- [ ] **Step 5: 核对云端图像预留缝（只读不写）**

设计文档 §11 已含缝形状（前端条件分支 + 空 stub 路由）。本步仅核对 `docs/superpowers/specs/2026-08-08-english-town-phase5-design.md` §11 无改动，**不写任何图像代码**。

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/settings.py apps/api/app/learning/store.py apps/api/tests/test_state_audit.py apps/api/tests/test_settings.py apps/api/tests/test_migration.py
git commit -m "feat(api): phase-5 settings + memory_state schema + word-confidence column + audit whitelist"
```

---

## Task 2: `memory.py` — MemoryCache + MemoryStore + apply_memory_updates + 抽取器

**Files:**
- Create: `apps/api/app/learning/memory.py`
- Test: `apps/api/tests/test_memory.py`

**Interfaces:**
- Consumes: `LearningStore`（conn 与事件同库）、`EventStore.append_in_tx`、`Settings.memory_policy_version`（取值 `"v1"` 写死进 JSON）。
- Produces（对齐 spec §4.4 命名）：
  - `class MemoryCache(conn)`: `refresh()` 载入 `known_word_ids: set[str]`、`helped: set[tuple[str,str]]`（lemma,pos）、`visited_archetypes: set[str]`。
  - `class MemoryStore(conn)`: `get_world_summary(user_id) -> dict | None`、`get_revision(user_id) -> int`、`_store_summary(conn, user_id, summary, now, events, reason, session_id)`（UPDATE + revision+1 + snapshot 事件，**不 commit**——调用方持锁/事务）、`apply_memory_updates(conn, events, user_id, *, scene_enter=None, evidence=None, ask=None, now) -> bool`（should_touch 判定 + 重建 + `_store_summary`；不取锁）。
  - `build_world_summary(events, conn, user_id, *, now) -> dict`（纯读库重建，跨会话扫 `session_events` 的 `scene.entered`）。
  - `format_world_summary(summary: dict | None) -> str`（spec §4.5 模板，逐字字段语义）。

- [ ] **Step 1: 写失败测试**

```python
# apps/api/tests/test_memory.py（新建）
import json, sqlite3
from datetime import datetime, timezone
from app.event_store import EventStore
from app.learning.memory import MemoryStore, build_world_summary, format_world_summary
from app.learning.store import LearningStore

def _now() -> datetime:
    return datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)

def _seed(conn):
    conn.execute("INSERT INTO word_lists(user_id,list_id,name,created_at) VALUES('local','l1','q','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO learning_items(word_id,user_id,lemma,pos,ipa,scene_tags,source,list_id,created_at) "
                 "VALUES('word_loaf_n_1','local','loaf','n','/loʊf/','[\"bakery\"]','quest','l1','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO learning_items(word_id,user_id,lemma,pos,ipa,scene_tags,source,list_id,created_at) "
                 "VALUES('word_shelf_n_1','local','shelf','n','/ʃɛlf/','[\"bakery\"]','quest','l1','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO mastery_states(user_id,word_id,state,due,productive_score,receptive_score,updated_at) "
                 "VALUES('local','word_loaf_n_1','learning','2026-08-08T11:00:00Z',0.6,0.5,'2026-08-08T12:00:00Z')")
    conn.execute("INSERT INTO mastery_states(user_id,word_id,state,updated_at) "
                 "VALUES('local','word_shelf_n_1','new','2026-08-08T12:00:00Z')")

def test_build_world_summary_from_events_and_tables(tmp_path):
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    events.append("s1", "scene.entered", {"archetypeId": "bakery", "sceneId": "sc1", "generationId": "g1", "revision": 1, "source": "connect"})
    events.append("s1", "scene.entered", {"archetypeId": "bakery", "sceneId": "sc2", "generationId": "g2", "revision": 1, "source": "exit"})
    summary = build_world_summary(events, conn, "local", now=_now())
    assert summary["memoryPolicyVersion"] == "v1"
    assert summary["scenes"]["bakery"]["count"] == 2
    assert summary["wordMastery"]["known"] == 1          # learning 计入 known
    assert summary["wordMastery"]["learning"] == 1
    assert summary["wordMastery"]["reviewDue"] == 1      # loaf due 11:00 <= now 12:00
    assert [w["lemma"] for w in summary["wordMastery"]["weakWords"]] == ["loaf"]  # 唯一非 new 词
    assert summary["userProfile"]["productiveAvg"] == 0.6
    assert summary["userProfile"]["helpCount"] == 0

def test_format_world_summary_template():
    s = {"scenes": {"plaza": {"count": 3}, "bakery": {"count": 2}},
         "wordMastery": {"known": 8, "learning": 5, "reviewDue": 3,
                         "weakWords": [{"lemma": "shelf"}, {"lemma": "jar"}]},
         "userProfile": {"helpCount": 4},
         "askedWords": [{"lemma": "loaf"}]}
    block = format_world_summary(s)
    assert "Visited scenes: plaza × 3, bakery × 2" in block
    assert "Known words: 8, learning: 5, review due: 3" in block
    assert "Top weak words: shelf, jar" in block
    assert "Words user explicitly asked about: loaf" in block

def test_new_scene_enter_bumps_revision_once(tmp_path):
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    mem = MemoryStore(conn)
    now = _now()
    events.append("s1", "scene.entered", {"archetypeId": "plaza", "sceneId": "s1", "generationId": "g1", "revision": 1, "source": "connect"})
    with events.write_lock:
        assert mem.apply_memory_updates(conn, events, "local", scene_enter="plaza", now=now) is True
        conn.commit()
    assert mem.get_revision("local") == 1
    events.append("s1", "scene.entered", {"archetypeId": "plaza", "sceneId": "s2", "generationId": "g2", "revision": 1, "source": "exit"})
    with events.write_lock:
        assert mem.apply_memory_updates(conn, events, "local", scene_enter="plaza", now=now) is False
        conn.commit()
    assert mem.get_revision("local") == 1          # 已知场景再进 → 不变（prefetch 不误失效）
    events.append("s1", "scene.entered", {"archetypeId": "bakery", "sceneId": "s3", "generationId": "g3", "revision": 1, "source": "exit"})
    with events.write_lock:
        assert mem.apply_memory_updates(conn, events, "local", scene_enter="bakery", now=now) is True
        conn.commit()
    assert mem.get_revision("local") == 2          # 新场景 → +1

def test_apply_memory_updates_store_and_snapshot(tmp_path):
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    mem = MemoryStore(conn)
    now = _now()
    assert mem.get_revision("local") == 0
    assert mem.get_world_summary("local") is None
    with events.write_lock:
        mem.apply_memory_updates(conn, events, "local", scene_enter="plaza", now=now)
        conn.commit()
    assert mem.get_revision("local") == 1
    assert mem.get_world_summary("local")["memoryPolicyVersion"] == "v1"
    snaps = [e for e in events.list_after("s1", 0) if e["event_type"] == "world_summary.snapshot"]
    assert len(snaps) == 1
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run --project apps/api pytest apps/api/tests/test_memory.py -q`
Expected: FAIL（无 `app.learning.memory` 模块）。

- [ ] **Step 3: 实现 `apps/api/app/learning/memory.py`**

```python
"""WorldMemory 结构化抽取：物化单例快照 + 触发式重建（apply_memory_updates）。
build_world_summary 是唯一事实源（纯读库重建，跨会话）；revision 只随实质变化 +1
（新场景首次进入 / 新词进入 known/learning/review / 新求助词）。"""
from __future__ import annotations

import json
from datetime import datetime


class MemoryCache:
    """进程内只读快照缓存：known/learning/review 词集 + 求助词集 + 已访问场景集。
    决定 should_touch；_store_summary 后 refresh() 使同轮后续证据不重复 +revision。"""
    def __init__(self, conn) -> None:
        self.conn = conn
        self.refresh()

    def refresh(self) -> None:
        rows = self.conn.execute(
            "SELECT word_id FROM mastery_states WHERE user_id='local' AND state IN ('learning','review','relearning')").fetchall()
        self.known_word_ids: set[str] = {r[0] for r in rows}
        helped: set[tuple[str, str]] = set()
        for r in self.conn.execute(
                "SELECT li.lemma, COALESCE(li.pos,'') FROM evidence_events ev "
                "JOIN learning_items li ON li.word_id=ev.word_id AND li.user_id=ev.user_id "
                "WHERE ev.user_id='local' AND ev.source='help'"):
            helped.add((r[0], r[1]))
        for r in self.conn.execute(
                "SELECT lemma, COALESCE(pos,'') FROM spontaneous_words WHERE user_id='local' AND asked=1"):
            helped.add((r[0], r[1]))
        self.helped = helped
        row = self.conn.execute(
            "SELECT world_summary_json FROM memory_state WHERE user_id='local'").fetchone()
        self.visited_archetypes: set[str] = set(json.loads(row[0]).get("scenes", {})) if row else set()


class MemoryStore:
    def __init__(self, conn) -> None:
        self.conn = conn
        self.cache = MemoryCache(conn)

    def get_world_summary(self, user_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT world_summary_json FROM memory_state WHERE user_id=?", (user_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def get_revision(self, user_id: str) -> int:
        row = self.conn.execute("SELECT revision FROM memory_state WHERE user_id=?", (user_id,)).fetchone()
        return int(row[0]) if row else 0

    def _store_summary(self, conn, user_id: str, summary: dict, now: datetime,
                       events, reason: str, session_id: str = "s1") -> None:
        """调用方单事务内：UPDATE memory_state + revision+1 + snapshot 事件。不 commit。"""
        summary["updatedAt"] = now.isoformat()
        summary["memoryPolicyVersion"] = summary.get("memoryPolicyVersion", "v1")
        revision = self.get_revision(user_id) + 1
        conn.execute(
            "INSERT INTO memory_state(user_id, world_summary_json, revision, updated_at) "
            "VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "world_summary_json=excluded.world_summary_json, revision=excluded.revision, updated_at=excluded.updated_at",
            (user_id, json.dumps(summary, ensure_ascii=False), revision, now.isoformat()))
        events.append_in_tx(conn, session_id, "world_summary.snapshot",
                            {"summary": summary, "revision": revision, "reason": reason},
                            event_id=f"mem_{revision}", internal=True)
        self.cache.refresh()

    def apply_memory_updates(self, conn, events, user_id: str, *,
                             scene_enter: str | None = None,
                             evidence: dict | None = None,
                             ask: dict | None = None,
                             now: datetime) -> bool:
        """should_touch → 重建 + _store_summary。不取锁、不 commit（调用方单事务/持 write_lock）。
        命中三种实质变化之一才 +revision：新场景首次进入 / 证据使词进 known-learning-review / 新求助词。"""
        touched = False
        reason = "memory"
        if scene_enter is not None:
            touched = scene_enter not in self.cache.visited_archetypes
            reason = f"scene:{scene_enter}"
        elif evidence is not None:
            wid = evidence["word_id"]
            if evidence["source"] == "help":
                row = conn.execute(
                    "SELECT lemma, COALESCE(pos,'') FROM learning_items WHERE user_id=? AND word_id=?",
                    (user_id, wid)).fetchone()
                touched = bool(row) and (row[0], row[1]) not in self.cache.helped
                reason = f"help:{wid}"
            else:
                row = conn.execute(
                    "SELECT state FROM mastery_states WHERE user_id=? AND word_id=?",
                    (user_id, wid)).fetchone()
                touched = (row is not None and row[0] in ("learning", "review", "relearning")
                           and wid not in self.cache.known_word_ids)
                reason = f"evidence:{wid}"
        elif ask is not None:
            key = (ask["lemma"], ask.get("pos") or "")
            touched = key not in self.cache.helped
            reason = f"ask:{key[0]}"
        if not touched:
            return False
        summary = build_world_summary(events, conn, user_id, now=now)
        session_id = evidence["session_id"] if evidence is not None else (ask.get("session_id") or "s1")
        self._store_summary(conn, user_id, summary, now, events, reason, session_id=session_id)
        return True


def build_world_summary(events, conn, user_id: str, *, now: datetime) -> dict:
    """纯读库重建（唯一事实源；snapshot 重放共用）。跨会话扫 session_events。"""
    scenes: dict[str, dict] = {}
    rows = conn.execute(
        "SELECT payload_json FROM session_events WHERE event_type='scene.entered' ORDER BY sequence").fetchall()
    for r in rows:
        p = json.loads(r[0])
        a = p.get("archetypeId")
        cur = scenes.setdefault(a, {"count": 0, "lastAt": None})
        cur["count"] += 1
        cur["lastAt"] = p.get("generationId", "")
    mrows = conn.execute(
        "SELECT ms.word_id, li.lemma, ms.state, ms.due, ms.productive_score, ms.receptive_score "
        "FROM mastery_states ms JOIN learning_items li ON li.word_id=ms.word_id AND li.user_id=ms.user_id "
        "WHERE ms.user_id=?", (user_id,)).fetchall()
    states: dict[str, int] = {"new": 0, "learning": 0, "review": 0, "relearning": 0}
    due = 0
    weak = []
    for r in mrows:
        states[r["state"]] = states.get(r["state"], 0) + 1
        if r["state"] != "new" and r["due"] and r["due"] <= now.isoformat():
            due += 1
        if r["state"] != "new":
            weak.append({"wordId": r["word_id"], "lemma": r["lemma"],
                         "sum": (r["productive_score"] or 0.0) + (r["receptive_score"] or 0.0)})
    weak.sort(key=lambda w: w["sum"])
    prof = conn.execute(
        "SELECT AVG(productive_score) AS p, AVG(receptive_score) AS r, "
        "AVG(asr_word_confidence_score) AS awc, SUM(help_count) AS hc "
        "FROM mastery_states WHERE user_id=?", (user_id,)).fetchone()
    err_rows = conn.execute(
        "SELECT ev.word_id, li.lemma, COUNT(*) AS n FROM evidence_events ev "
        "JOIN learning_items li ON li.word_id=ev.word_id AND li.user_id=ev.user_id "
        "WHERE ev.user_id=? AND ev.source='error' GROUP BY ev.word_id ORDER BY n DESC LIMIT 3",
        (user_id,)).fetchall()
    asked = conn.execute(
        "SELECT lemma FROM spontaneous_words WHERE user_id=? AND asked=1", (user_id,)).fetchall()
    known = states["learning"] + states["review"] + states["relearning"]
    return {
        "memoryPolicyVersion": "v1",
        "scenes": {a: {"count": s["count"], "lastAt": s["lastAt"]} for a, s in scenes.items()},
        "wordMastery": {"known": known, "learning": states["learning"],
                        "review": states["review"], "reviewDue": due,
                        "weakWords": [{"wordId": w["wordId"], "lemma": w["lemma"]} for w in weak[:3]]},
        "userProfile": {"productiveAvg": round(prof["p"] or 0.0, 3),
                        "receptiveAvg": round(prof["r"] or 0.0, 3),
                        "asrWordConfAvg": round(prof["awc"] or 0.0, 3),
                        "helpCount": int(prof["hc"] or 0),
                        "commonErrorWords": [{"wordId": r[0], "lemma": r[1]} for r in err_rows]},
        "askedWords": [{"lemma": r[0]} for r in asked],
    }


def format_world_summary(summary: dict | None) -> str:
    """spec §4.5 最小模板（基线，字段语义不得偏离）。空摘要 → 空串（等价现状无块）。"""
    if not summary:
        return ""
    lines = ["User memory summary:"]
    scenes = summary.get("scenes", {})
    if scenes:
        lines.append("  Visited scenes: " + ", ".join(f"{a} × {s['count']}" for a, s in scenes.items()))
    wm = summary.get("wordMastery", {})
    lines.append(f"  Known words: {wm.get('known', 0)}, learning: {wm.get('learning', 0)}, "
                 f"review due: {wm.get('reviewDue', 0)}")
    weak = [w["lemma"] for w in wm.get("weakWords", [])]
    if weak:
        lines.append("  Top weak words: " + ", ".join(weak))
    asked = [w["lemma"] for w in summary.get("askedWords", [])]
    if asked:
        lines.append("  Words user explicitly asked about: " + ", ".join(asked))
    return "\n".join(lines)
```

- [ ] **Step 4: 运行验证通过**

Run: `uv run --project apps/api pytest apps/api/tests/test_memory.py -q`
Expected: PASS。若 `test_apply_memory_updates_store_and_snapshot` 的 snapshot 断言不符（snapshot 事件 session_id 用 `evidence.session_id` 或 `"s1"`），按实际落法修正（`apply_memory_updates` 无 evidence/ask 时 session_id 回退 `"s1"`，`list_after("s1",0)` 可查到）。

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/learning/memory.py apps/api/tests/test_memory.py
git commit -m "feat(api): WorldMemory MemoryStore + apply_memory_updates + build_world_summary + prompt template"
```

---

## Task 3: 挂钩记忆更新（单事务）

**Files:**
- Modify: `apps/api/app/learning/engine.py`（`__init__` 建 `self.memory`；`record_evidence` in-tx 调 `apply_memory_updates(evidence=...)`）
- Modify: `apps/api/app/scene_lifecycle.py`（`enter_scene` 在 `scene.entered` 后独立事务调 `apply_memory_updates(scene_enter=...)`）
- Modify: `apps/api/app/learning/encounters.py`（`record_ask` 加 `events=None`，独立事务调 `apply_memory_updates(ask=...)`）
- Modify: `apps/api/app/learning/store.py`（`__init__` 加 `self.memory: MemoryStore | None = None`）
- Modify: `apps/api/app/main.py`（`store.memory = engine.memory`；`app.state.memory = engine.memory`）
- Test: `apps/api/tests/test_memory_hooks.py`

**Interfaces:**
- Consumes: `MemoryStore.apply_memory_updates`、`build_world_summary`（Task 2）。
- Produces: `engine.memory`；`record_ask(store, user_id, session_id, lemma, pos, turn_id, *, now, events=None)`。

- [ ] **Step 1: 写失败测试**

```python
# apps/api/tests/test_memory_hooks.py（新建）
from datetime import datetime, timezone
from app.event_store import EventStore
from app.learning.engine import LearningEngine
from app.learning.memory import build_world_summary
from app.learning.store import LearningStore
from app.settings import Settings

def _now() -> datetime:
    return datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)

def _seed(conn):
    conn.execute("INSERT INTO word_lists(user_id,list_id,name,created_at) VALUES('local','l1','q','2026-08-08T00:00:00Z')")
    for wid, lemma in (("word_loaf_n_1", "loaf"), ("word_jar_n_1", "jar")):
        conn.execute("INSERT INTO learning_items(word_id,user_id,lemma,pos,ipa,scene_tags,source,list_id,created_at) "
                     "VALUES(?,?,'n','/x/','[\"bakery\"]','quest','l1','2026-08-08T00:00:00Z')",
                     (wid, lemma))

def _evidence(word_id="word_loaf_n_1", source="prompted_production", result="success",
              prompt_level=1, confidence=0.9, evidence_id="ev_x"):
    return {"evidence_id": evidence_id, "event_seq": 0, "session_id": "s1", "attempt_id": "a",
            "turn_id": "t", "objective_id": None, "word_id": word_id, "source": source,
            "prompt_level": prompt_level, "axis": "productive", "result": result,
            "confidence": confidence, "evidence_policy_version": "v1",
            "fsrs_algorithm_version": "fsrs-5", "created_at": "2026-08-08T12:00:00Z"}

def test_one_round_multi_evidence_revision_at_most_one(tmp_path):
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    conn.execute("INSERT INTO mastery_states(user_id,word_id,updated_at) VALUES('local','word_loaf_n_1','2026-08-08T12:00:00Z')")
    eng = LearningEngine(store, events, Settings())
    # 一轮：help（新求助）→ +1；随后同词 prompted / click → 不重复 +
    eng.record_evidence("s1", _evidence(source="help", result="neutral"), event_id="h1")
    assert eng.memory.get_revision("local") == 1
    eng.record_evidence("s1", _evidence(), event_id="p1")
    assert eng.memory.get_revision("local") == 1
    eng.record_evidence("s1", _evidence(source="action_understanding", prompt_level=1), event_id="c1")
    assert eng.memory.get_revision("local") == 1

def test_new_word_entering_learning_bumps_revision(tmp_path):
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    # 预置 loaf: attempts=1, scaffolded=1, state=new → 一次 prompted success 即进入 learning
    conn.execute("INSERT INTO mastery_states(user_id,word_id,state,attempts,scaffolded_success_count,updated_at) "
                 "VALUES('local','word_loaf_n_1','new',1,1,'2026-08-08T12:00:00Z')")
    eng = LearningEngine(store, events, Settings())
    assert eng.memory.get_revision("local") == 0
    eng.record_evidence("s1", _evidence(), event_id="e1")
    assert eng.memory.get_revision("local") == 1          # loaf 进入 learning → +1
    eng.record_evidence("s1", _evidence(), event_id="e2")  # 同词已 known → 不变
    assert eng.memory.get_revision("local") == 1

def test_snapshot_events_replay_rebuilds_summary(tmp_path):
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    eng = LearningEngine(store, events, Settings())
    eng.record_evidence("s1", _evidence(source="help", result="neutral"), event_id="e1")
    assert eng.memory.get_revision("local") == 1
    rebuilt = build_world_summary(events, conn, "local", now=_now())
    saved = eng.memory.get_world_summary("local")
    assert rebuilt["wordMastery"] == saved["wordMastery"]
    snaps = [e for e in events.list_after("s1", 0) if e["event_type"] == "world_summary.snapshot"]
    assert len(snaps) == 1
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run --project apps/api pytest apps/api/tests/test_memory_hooks.py -q`
Expected: FAIL（`LearningEngine` 无 `memory` 属性）。

- [ ] **Step 3: 实现**

`apps/api/app/learning/engine.py`：

```python
from app.learning.memory import MemoryStore

class LearningEngine:
    def __init__(self, store: LearningStore, events, settings) -> None:
        self.store = store
        self.events = events
        self.settings = settings
        self.memory = MemoryStore(store.conn)
```

`record_evidence` 事务内（`apply_evidence` 之后、`conn.commit()` 之前）：

```python
                seq = self.events.append_in_tx(conn, session_id, "evidence",
                                               _internal_payload(evidence),
                                               event_id=event_id, internal=True)
                _now = datetime.fromisoformat(evidence["created_at"])
                apply_evidence(self.store, "local", evidence, now=_now)
                try:
                    self.memory.apply_memory_updates(conn, self.events, "local",
                                                     evidence=evidence, now=_now)
                except Exception:  # noqa: BLE001 —— 记忆抽取失败不杀证据事务、revision 不推进
                    pass
                conn.commit()
```

`apps/api/app/scene_lifecycle.py` `enter_scene`：`events.append(session_id, "scene.entered", {...})` 之后、`await send(skeleton)` 之前：

```python
    mem = getattr(app.state, "memory", None)
    if mem is not None:
        try:
            with events.write_lock:
                mem.apply_memory_updates(events.connection, events, "local",
                                         scene_enter=target, now=datetime.now(timezone.utc))
                events.connection.commit()
        except Exception:  # noqa: BLE001 —— 记忆失败不杀进场
            pass
```

（`enter_scene` 已 `from datetime import datetime, timezone`，`datetime.now(timezone.utc)` 直接可用。）

`apps/api/app/learning/encounters.py` `record_ask`：

```python
def record_ask(store: LearningStore, user_id: str, session_id: str,
               lemma: str, pos: str, turn_id: str, *, now: datetime,
               events=None) -> None:
    record_exposure(store, user_id, session_id, lemma, pos, turn_id, now=now)
    store.conn.execute(
        "UPDATE spontaneous_words SET asked=1 WHERE user_id=? AND lemma=? AND pos=?",
        (user_id, lemma, pos))
    store.conn.commit()
    mem = getattr(store, "memory", None)
    if events is not None and mem is not None:
        try:
            with events.write_lock:
                mem.apply_memory_updates(events.connection, events, user_id,
                                         ask={"lemma": lemma, "pos": pos,
                                              "session_id": session_id},
                                         now=now)
                events.connection.commit()
        except Exception:  # noqa: BLE001 —— 记忆失败不杀求助
            pass
```

`apps/api/app/learning/store.py` `__init__` 末尾：`self.memory: MemoryStore | None = None`（`from __future__ import annotations` 已在文件顶部，注解安全）。`apps/api/app/main.py` 里 `engine = LearningEngine(store, events, settings)` 之后：`store.memory = engine.memory; app.state.memory = engine.memory`。

- [ ] **Step 4: 运行验证通过**

Run: `uv run --project apps/api pytest apps/api/tests/test_memory_hooks.py -q`
Expected: PASS。

- [ ] **Step 5: 全量回归（含既有 state-audit）**

Run: `uv run --project apps/api pytest apps/api -q`
Expected: 全绿（回合触场景进入会动 `memory_state`，已在白名单）。

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/learning/engine.py apps/api/app/scene_lifecycle.py apps/api/app/learning/encounters.py apps/api/app/learning/store.py apps/api/app/main.py apps/api/tests/test_memory_hooks.py
git commit -m "feat(api): wire apply_memory_updates into evidence/scene/ask transactions"
```

---

## Task 4: WorldSummary → LLM 注入 + scene_prefetch 键扩展

**Files:**
- Modify: `apps/api/app/llm/scene_director.py`（`_build_messages`、`propose`、`SceneDirector` Protocol）
- Modify: `apps/api/app/llm/mock.py`（`MockSceneDirector.propose` 签名）
- Modify: `apps/api/app/scene_prefetch.py`（键 `(archetype_id, revision)`）
- Modify: `apps/api/app/scene_lifecycle.py`（`fill_scene`：prefetch get/put + director world_summary）
- Modify: `apps/api/app/ws.py`（`_prefetch_for`：revision + world_summary；`_run_round`：传 world_summary）
- Modify: `apps/api/app/llm/tutor.py`（`reply` world_summary）
- Modify: `apps/api/app/llm/npc_actor.py`（`stream_reply` world_summary）
- Modify: `apps/api/app/voice_round.py`（`run_round` 加 `world_summary=None`，透传给 actor）
- Test: `apps/api/tests/test_memory_injection.py`

**Interfaces:**
- Consumes: `MemoryStore.get_world_summary/get_revision`、`format_world_summary`（Task 2）。
- Produces:
  - `LlmSceneDirector.propose(*, archetype_id, archetype, catalog, recent_scenes, world_summary=None, attempt="enter")`；`_build_messages(archetype, catalog, recent_scenes, world_summary=None)`——payload 增 `worldSummary` 块（非空时）。
  - `MockSceneDirector.propose(..., world_summary=None, ...)`（忽略参数）。
  - `CompanionTutor.reply(*, session_id, generation_id, word_id, word, world_summary=None)`——user 消息增 `worldSummary`（非空时）。
  - `NpcActor.stream_reply(*, session_id, generation_id, turn_id, utterance_id, user_text, recent_turns, budget_exceeded=False, world_summary=None)`。
  - `ScenePrefetchCache.get(archetype_id, revision)` / `put(archetype_id, revision, proposal)`；`invalidate(archetype_id)` 不变。

- [ ] **Step 1: 写失败测试**

```python
# apps/api/tests/test_memory_injection.py（新建）
import asyncio, json
from datetime import datetime, timezone
from app.event_store import EventStore
from app.learning.memory import MemoryStore
from app.learning.store import LearningStore
from app.settings import Settings

_WORLD = {"memoryPolicyVersion": "v1",
          "scenes": {"plaza": {"count": 3, "lastAt": None}, "bakery": {"count": 2, "lastAt": None}},
          "wordMastery": {"known": 8, "learning": 5, "review": 0, "reviewDue": 3,
                          "weakWords": [{"wordId": "word_shelf_n_1", "lemma": "shelf"}]},
          "userProfile": {"productiveAvg": 0.7, "receptiveAvg": 0.8, "asrWordConfAvg": 0.0,
                          "helpCount": 4, "commonErrorWords": []},
          "askedWords": [{"lemma": "loaf"}]}

def test_director_build_messages_embeds_world_summary():
    from app.llm.scene_director import LlmSceneDirector
    class _NoSlots:
        def concepts_in(self, cat): raise AssertionError("no slots")
        def npcs_in(self, role): raise AssertionError("no slots")
    d = LlmSceneDirector(object(), Settings(), None)
    arch = {"archetypeId": "bakery", "displayName": "Bakery", "propSlots": [], "npcSlots": []}
    messages = d._build_messages(arch, _NoSlots(), ["plaza"], world_summary=_WORLD)
    payload = json.loads(messages[1]["content"])
    assert payload["recentScenes"] == ["plaza"]
    assert "User memory summary:" in payload["worldSummary"]
    assert "Known words: 8, learning: 5, review due: 3" in payload["worldSummary"]

def test_tutor_reply_injects_world_summary(tmp_path):
    from types import SimpleNamespace
    from app.llm.tutor import CompanionTutor
    captured = {}
    class FakeLog:
        def record(self, **k): pass
    class FakeClient:
        async def complete_json(self, messages, **k):
            captured["user"] = messages[1]["content"]
            return SimpleNamespace(json={"word": "loaf", "scaffold": "A loaf is bread."}, usage=None)
    class FakeCache:
        def get(self, wid): return None
        def audio_path(self, wid): return str(tmp_path / f"{wid}.mp3")
        def put(self, *a): pass
    async def tts(text): return {"audioBase64": "AA==", "sampleRate": 24000}
    t = CompanionTutor(FakeClient(), Settings(), FakeLog(), FakeCache(), tts)
    asyncio.run(t.reply(session_id="s1", generation_id="g1", word_id="w1", word="loaf",
                        world_summary=_WORLD))
    assert "User memory summary:" in captured["user"]
    assert "Visited scenes: plaza × 3, bakery × 2" in captured["user"]

def test_prefetch_cache_key_includes_revision(tmp_path):
    from app.scene_prefetch import ScenePrefetchCache
    c = ScenePrefetchCache(ttl_s=60)
    c.put("bakery", 0, {"v": 1})
    assert c.get("bakery", 0) == {"v": 1}
    assert c.get("bakery", 1) is None          # revision 变化 → 未命中（需重查 Director）

def test_prefetch_hit_when_only_scene_count_changes(tmp_path):
    from app.scene_prefetch import ScenePrefetchCache
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    mem = MemoryStore(conn)
    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    events.append("s1", "scene.entered", {"archetypeId": "plaza", "sceneId": "s1", "generationId": "g1", "revision": 1, "source": "connect"})
    with events.write_lock:
        mem.apply_memory_updates(conn, events, "local", scene_enter="plaza", now=now)
        conn.commit()
    assert mem.get_revision("local") == 1
    cache = ScenePrefetchCache()
    cache.put("plaza", mem.get_revision("local"), {"v": 1})
    events.append("s1", "scene.entered", {"archetypeId": "plaza", "sceneId": "s2", "generationId": "g2", "revision": 1, "source": "exit"})
    with events.write_lock:
        mem.apply_memory_updates(conn, events, "local", scene_enter="plaza", now=now)
        conn.commit()
    assert mem.get_revision("local") == 1      # 场景计数变但非新场景 → revision 不变（spec §10b）
    assert cache.get("plaza", mem.get_revision("local")) == {"v": 1}   # 缓存仍命中
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run --project apps/api pytest apps/api/tests/test_memory_injection.py -q`
Expected: FAIL（`_build_messages` 不接 `world_summary` / `ScenePrefetchCache.get` 无 revision 参数）。

- [ ] **Step 3: 实现**

`apps/api/app/llm/scene_director.py`：

```python
from app.learning.memory import format_world_summary

class SceneDirector(Protocol):
    async def propose(self, *, archetype_id: str, archetype: dict, catalog,
                      recent_scenes: list[str], world_summary: dict | None = None,
                      attempt: str = "enter") -> dict: ...

    def _build_messages(self, archetype: dict, catalog, recent_scenes: list[str],
                        world_summary: dict | None = None) -> list[dict]:
        ...（现状 slots/npc_slots 构造不变）...
        payload = {"archetypeId": archetype["archetypeId"],
                   "displayName": archetype["displayName"],
                   "propSlots": slots, "npcSlots": npc_slots,
                   "recentScenes": recent_scenes}
        block = format_world_summary(world_summary)
        if block:
            payload["worldSummary"] = block
        return [
            {"role": "system", "content": _DIRECTOR_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    async def propose(self, *, archetype_id: str, archetype: dict, catalog,
                      recent_scenes: list[str], world_summary: dict | None = None,
                      attempt: str = "enter") -> dict:
        messages = self._build_messages(archetype, catalog, recent_scenes, world_summary)
        ...（其余不变）...
```

`apps/api/app/llm/mock.py` `MockSceneDirector.propose`：签名加 `world_summary: dict | None = None`（忽略，不动 body）。

`apps/api/app/scene_prefetch.py`（键改二元组；invalidate 语义不变）：

```python
    def get(self, archetype_id: str, revision: int) -> dict | None:
        item = self._items.get((archetype_id, revision))
        if item is None:
            return None
        expires_at, proposal = item
        if time.time() > expires_at:
            self._items.pop((archetype_id, revision), None)
            return None
        return proposal

    def put(self, archetype_id: str, revision: int, proposal: dict) -> None:
        key = (archetype_id, revision)
        if len(self._items) >= self._maxsize and key not in self._items:
            oldest = min(self._items, key=lambda k: self._items[k][0])
            self._items.pop(oldest, None)
        self._items[key] = (time.time() + self._ttl_s, proposal)

    def invalidate(self, archetype_id: str) -> None:
        self._items = {k: v for k, v in self._items.items() if k[0] != archetype_id}
```

`apps/api/app/scene_lifecycle.py` `fill_scene`（`cached = app.state.prefetch.get(archetype_id)` 行）：

```python
        mem = getattr(app.state, "memory", None)
        revision = mem.get_revision("local") if mem else 0
        cached = app.state.prefetch.get(archetype_id, revision)
        if cached is not None:
            await _apply_proposal(..., proposal=cached)
            return
        ...
        world_summary = mem.get_world_summary("local") if mem else None
        proposal = await app.state.director.propose(
            archetype_id=archetype_id,
            archetype=scenes.get_archetype(archetype_id),
            catalog=app.state.catalog,
            recent_scenes=recent_scenes(events, session_id),
            world_summary=world_summary,
            attempt="enter")
        app.state.prefetch.put(archetype_id, revision, cleaned)
```

`apps/api/app/ws.py` `_prefetch_for`（`prefetch.get`/`director.propose`/`prefetch.put` 三处）：

```python
        mem = getattr(app.state, "memory", None)
        revision = mem.get_revision("local") if mem else 0
        world_summary = mem.get_world_summary("local") if mem else None
        if app.state.prefetch.get(archetype_id, revision) is not None:
            return
        ...
                    proposal = await app.state.director.propose(
                        archetype_id=archetype_id,
                        archetype=app.state.scenes.get_archetype(archetype_id),
                        catalog=app.state.catalog,
                        recent_scenes=[], world_summary=world_summary, attempt="prefetch")
            app.state.prefetch.put(archetype_id, revision, proposal)
```

`apps/api/app/llm/tutor.py` `reply`：签名加 `world_summary: dict | None = None`；reply 内 user 消息构造：

```python
                user = {"word": word}
                block = format_world_summary(world_summary)
                if block:
                    user["worldSummary"] = block
                messages = [
                    {"role": "system", "content": TUTOR_SYSTEM_PROMPT.format(max_scaffold_chars=self._settings.llm_max_scaffold_chars)},
                    {"role": "user", "content": json.dumps(user)},
                ]
```

（`reply` 内改 import `format_world_summary` from `app.learning.memory`。）

`apps/api/app/llm/npc_actor.py` `stream_reply`：签名加 `world_summary: dict | None = None`；`_build_messages` 里 user content 追加 `format_world_summary(world_summary)` 生成的行（无则不加，不破坏既有 `_scene_hint`/recent_turns 结构）。

`apps/api/app/voice_round.py` `run_round`：签名加 `world_summary: dict | None = None`；`actor.stream_reply(...)` 调用处透传 `world_summary=world_summary`。

`apps/api/app/ws.py` `_run_round`：

```python
        mem = getattr(app.state, "memory", None)
        world_summary = mem.get_world_summary("local") if mem else None
        result = await run_round(..., state, budget_exceeded=budget_exceeded,
                                 world_summary=world_summary)
```

- [ ] **Step 4: 运行验证通过**

Run: `uv run --project apps/api pytest apps/api/tests/test_memory_injection.py -q`
Expected: PASS。

- [ ] **Step 5: 全量回归**

Run: `uv run --project apps/api pytest apps/api -q`
Expected: 全绿。

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/llm/scene_director.py apps/api/app/llm/mock.py apps/api/app/scene_lifecycle.py apps/api/app/ws.py apps/api/app/llm/tutor.py apps/api/app/llm/npc_actor.py apps/api/app/scene_prefetch.py apps/api/app/voice_round.py apps/api/tests/test_memory_injection.py
git commit -m "feat(api): inject WorldSummary into director/tutor/npc + prefetch key (archetype, revision)"
```

---

## Task 5: memory_smoke.py + revision 增长频率

**Files:**
- Create: `apps/api/app/learning/memory_smoke.py`
- Test: `apps/api/tests/test_memory_smoke.py`

**Interfaces:**
- Consumes: `MemoryStore.apply_memory_updates/get_revision/get_world_summary`、`build_world_summary`、`EventStore`、`LearningStore`。
- Produces: `memory_smoke.main(db_path: Path) -> dict`（回放 `scene.entered` → 摘要 + 最终 revision + revisionHistory/bumpsPerScene）。

- [ ] **Step 1: 写失败测试**

```python
# apps/api/tests/test_memory_smoke.py（新建）
from app.event_store import EventStore
from app.learning.memory_smoke import main as smoke_main
from app.learning.store import LearningStore

def test_smoke_outputs_summary_and_bounded_growth(tmp_path):
    db = tmp_path / "e.db"
    events = EventStore(db)
    store = LearningStore(events.connection)
    events.append("s1", "scene.entered", {"archetypeId": "plaza", "sceneId": "s1", "generationId": "g1", "revision": 1, "source": "connect"})
    events.append("s1", "scene.entered", {"archetypeId": "bakery", "sceneId": "s2", "generationId": "g2", "revision": 1, "source": "exit"})
    events.append("s1", "scene.entered", {"archetypeId": "bakery", "sceneId": "s3", "generationId": "g3", "revision": 1, "source": "exit"})
    out = smoke_main(db)
    assert out["summary"]["scenes"]["bakery"]["count"] == 2
    assert out["revision"] == 2                       # plaza 新 + bakery 新 = 2（第 2 次 bakery 不变）
    assert out["revisionGrowth"]["maxStep"] == 1      # 单次最多 +1（不暴涨）
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run --project apps/api pytest apps/api/tests/test_memory_smoke.py -q`
Expected: FAIL（无 `app.learning.memory_smoke` 模块）。

- [ ] **Step 3: 实现 `apps/api/app/learning/memory_smoke.py`**

```python
"""memory_smoke：回放 session_events 的 scene.entered → WorldSummary + 最终 revision + 增长频率。
实测「一轮多证据/连续进场 revision 不暴涨」。用法: uv run --project apps/api python -m app.learning.memory_smoke <db_path>"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.event_store import EventStore
from app.learning.memory import MemoryStore, build_world_summary
from app.learning.store import LearningStore


def main(db_path: Path) -> dict:
    events = EventStore(db_path)
    conn = events.connection
    store = LearningStore(conn)
    mem = MemoryStore(conn)
    now = datetime.now(timezone.utc)
    revisions: list[int] = []
    rows = conn.execute(
        "SELECT payload_json FROM session_events WHERE event_type='scene.entered' ORDER BY sequence").fetchall()
    for r in rows:
        arch = json.loads(r[0]).get("archetypeId")
        with events.write_lock:
            mem.apply_memory_updates(conn, events, "local", scene_enter=arch, now=now)
            conn.commit()
        revisions.append(mem.get_revision("local"))
    bumps = [b - a for a, b in zip([0] + revisions[:-1], revisions)]
    return {
        "revision": mem.get_revision("local"),
        "summary": mem.get_world_summary("local") or build_world_summary(events, conn, "local", now=now),
        "revisionHistory": revisions,
        "revisionGrowth": {"touches": len([b for b in bumps if b > 0]),
                           "maxStep": max(bumps, default=1)},
    }


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("english_town.db")
    print(json.dumps(main(path), ensure_ascii=False, indent=2))
```

- [ ] **Step 4: 运行验证通过**

Run: `uv run --project apps/api pytest apps/api/tests/test_memory_smoke.py -q`
Expected: PASS。

- [ ] **Step 5: 手动冒烟（真实库，无数据则空摘要即可）**

Run: `uv run --project apps/api python -m app.learning.memory_smoke english_town.db`
Expected: 打印 `revision`/`summary`/`revisionHistory`/`revisionGrowth`。

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/learning/memory_smoke.py apps/api/tests/test_memory_smoke.py
git commit -m "feat(api): memory_smoke replay script + revision-growth check"
```

---

## Task 6: asr-worker 词级时间戳（门槛开关 + 自检降级）

**Files:**
- Modify: `services/asr-worker/asr_worker/whisper_engine.py`
- Modify: `services/asr-worker/asr_worker/streaming.py`
- Modify: `services/asr-worker/asr_worker/selfcheck.py`
- Modify: `services/asr-worker/asr_worker/server.py`
- Test: `services/asr-worker/tests/test_streaming.py`

**Interfaces:**
- Consumes: faster-whisper `word_timestamps=True`。
- Produces:
  - `WhisperEngine(model, device, model_name)` dataclass；`load(device="auto", model=None)`（env `ASR_MODEL` 可指定）；`transcribe(audio, final=False, *, word_timestamps=False)`。
  - `word_timestamps_active(engine, env_enable: bool, min_model: str) -> bool`（模型名 `startswith(min_model)` 才算过门槛）。
  - `RollingTranscriber(transcribe_fn, window_s=6.0, check_ms=300.0, word_timestamps=False)`；`finalize` 透传 `words`（无则不带 key）。

- [ ] **Step 1: 写失败测试**

```python
# services/asr-worker/tests/test_streaming.py 追加
def test_finalize_returns_words_when_enabled():
    class FakeEngine:
        def transcribe(self, audio, final=False, **kwargs):
            return {"text": "a loaf", "segments": [{"start": 0.1, "end": 0.9, "text": "a loaf"}],
                    "language": "en", "avg_logprob": -0.2,
                    "words": [{"word": "a", "start": 0.1, "end": 0.3, "probability": 0.9},
                              {"word": "loaf", "start": 0.4, "end": 0.9, "probability": 0.95}]}
    from asr_worker.streaming import RollingTranscriber, UtteranceState
    rt = RollingTranscriber(FakeEngine().transcribe, word_timestamps=True)
    out = rt.finalize(UtteranceState("u1"), object(), 16000)
    assert out["finalText"] == "a loaf"
    assert out["words"][1]["word"] == "loaf"
    assert abs(out["words"][1]["probability"] - 0.95) < 1e-6

def test_finalize_words_absent_when_disabled():
    class FakeEngine:
        def transcribe(self, audio, final=False, **kwargs):
            return {"text": "hi", "segments": [], "language": "en", "avg_logprob": -0.3}
    from asr_worker.streaming import RollingTranscriber, UtteranceState
    out = RollingTranscriber(FakeEngine().transcribe, word_timestamps=False).finalize(UtteranceState("u1"), object(), 16000)
    assert "words" not in out        # 未开启 → 无 words 键（API 侧据此回退）

def test_finalize_words_empty_on_silence():
    class FakeEngine:
        def transcribe(self, audio, final=False, **kwargs):
            return {"text": "", "segments": [], "language": "en", "avg_logprob": -1.0, "words": []}
    from asr_worker.streaming import RollingTranscriber, UtteranceState
    out = RollingTranscriber(FakeEngine().transcribe, word_timestamps=True).finalize(UtteranceState("u1"), object(), 16000)
    assert out["words"] == []        # 静音 → 空数组（API 侧按"空→回退"处理，不按 None）

def test_word_timestamps_gate_by_model_floor():
    from asr_worker.whisper_engine import word_timestamps_active
    class E:
        model_name = "distil-large-v3"
    class E2:
        model_name = "whisper-large-v3"
    assert word_timestamps_active(E(), True, "whisper-large-v3") is False   # 低于门槛
    assert word_timestamps_active(E2(), True, "whisper-large-v3") is True
    assert word_timestamps_active(E2(), False, "whisper-large-v3") is False  # env 关
```

- [ ] **Step 2: 运行验证失败**

Run: `cd services/asr-worker && uv run --project . pytest tests -q`
Expected: FAIL（finalize 不输出 `words` / 无 `word_timestamps_active`）。

- [ ] **Step 3: 实现**

`services/asr-worker/asr_worker/whisper_engine.py`：

```python
@dataclass
class WhisperEngine:
    model: Any
    device: str
    model_name: str = ""

    @classmethod
    def load(cls, device: str = "auto", model: str | None = None) -> "WhisperEngine":
        if device == "auto":
            try:
                name = model or "distil-large-v3"
                return cls(WhisperModel(name, device="cuda", compute_type="float16"), "cuda", name)
            except Exception:
                name = model or "small.en"
                return cls(WhisperModel(name, device="cpu", compute_type="int8"), "cpu", name)
        name = model or "distil-large-v3"
        return cls(WhisperModel(name, device=device, compute_type="float16"), device, name)

    def transcribe(self, audio: Any, final: bool = False, *, word_timestamps: bool = False) -> dict:
        segments, info = self.model.transcribe(
            audio, language="en", beam_size=3 if final else 1, vad_filter=False,
            word_timestamps=word_timestamps,
        )
        segs = []
        words = []
        for s in segments:
            segs.append({"start": s.start, "end": s.end, "text": s.text.strip()})
            if word_timestamps:
                for w in (s.words or []):
                    words.append({"word": w.word, "start": w.start, "end": w.end,
                                  "probability": w.probability})
        out = {"text": " ".join(s["text"] for s in segs).strip(),
               "segments": segs, "language": info.language,
               "avg_logprob": float(info.avg_logprob)}
        if word_timestamps:
            out["words"] = words
        return out


def word_timestamps_active(engine, env_enable: bool, min_model: str) -> bool:
    """门槛：env 开 + 模型名 >= min_model（startswith 前缀匹配）。否则自动禁用（回退 utterance 级）。"""
    return env_enable and (engine.model_name or "").startswith(min_model)
```

`services/asr-worker/asr_worker/streaming.py`：

```python
    def __init__(self, transcribe_fn, window_s: float = 6.0, check_ms: float = 300.0,
                 word_timestamps: bool = False) -> None:
        self.transcribe_fn = transcribe_fn
        self.window_s = window_s
        self.check_ms = check_ms
        self.word_timestamps = word_timestamps
        ...

    def finalize(self, utterance: UtteranceState, samples: object, sample_rate: int) -> dict:
        result = self.transcribe_fn(samples, final=True, word_timestamps=self.word_timestamps)
        ...
        out = {"type": "final", "utteranceId": utterance.utterance_id,
               "finalText": text, "segments": segments, "language": lang, "confidence": conf}
        if isinstance(result, dict) and result.get("words") is not None:
            out["words"] = result["words"]
        self._reset()
        return out
```

`services/asr-worker/asr_worker/server.py`：

```python
import os
from asr_worker.whisper_engine import WhisperEngine, word_timestamps_active

@app.on_event("startup")
def _load() -> None:
    global ENGINE
    ENGINE = WhisperEngine.load("auto", model=os.environ.get("ASR_MODEL"))
    ENGINE.word_timestamps_enabled = word_timestamps_active(
        ENGINE,
        os.environ.get("ENABLE_WORD_TIMESTAMPS", "").lower() == "true",
        os.environ.get("WORD_TIMESTAMP_MIN_MODEL", "whisper-large-v3"))
```

`ws_asr` 里 `rt = RollingTranscriber(ENGINE.transcribe, word_timestamps=getattr(ENGINE, "word_timestamps_enabled", False))`；`rt_finalize` 同理传参。

`services/asr-worker/asr_worker/selfcheck.py` `run()` 成功路径返回 dict 增：

```python
        "word_timestamps": word_timestamps_active(
            engine,
            os.environ.get("ENABLE_WORD_TIMESTAMPS", "").lower() == "true",
            os.environ.get("WORD_TIMESTAMP_MIN_MODEL", "whisper-large-v3")),
```

- [ ] **Step 4: 运行验证通过**

Run: `cd services/asr-worker && uv run --project . pytest tests -q`
Expected: PASS。

- [ ] **Step 5: 全量回归（asr-worker 独立套件）**

Run: 同上，全绿。

- [ ] **Step 6: Commit**

```bash
git add services/asr-worker/asr_worker/whisper_engine.py services/asr-worker/asr_worker/streaming.py services/asr-worker/asr_worker/selfcheck.py services/asr-worker/asr_worker/server.py services/asr-worker/tests/test_streaming.py
git commit -m "feat(asr): word_timestamps gated by model floor + env flag, selfcheck reports"
```

---

## Task 7: 词级打分器 + `word_production` 证据源

**Files:**
- Create: `apps/api/app/learning/word_confidence.py`
- Modify: `apps/api/app/learning/scores.py`（WEIGHTS + `word_production`）
- Modify: `apps/api/app/learning/evidence.py`（`apply_evidence` 独立分支 + 列映射）
- Modify: `apps/api/app/learning/engine.py`（`record_round` 加 `words`）
- Modify: `apps/api/app/voice_round.py`（`run_round` 返回 `words`）
- Modify: `apps/api/app/ws.py`（`_run_round` 传 `words` 给 `record_round`）
- Modify: `apps/api/app/learning/store.py`（`all_words` 增 `asr_word_confidence_score`）
- Modify: `apps/api/app/learning/api.py`（`_word_summary` scores 增 `asrWordConfidence`）
- Test: `apps/api/tests/test_word_confidence.py`

**Interfaces:**
- Consumes: `token_contains`（lexmatch）、`classify_round`。
- Produces:
  - `score_word_confidence(words: list[dict], scene_words: dict[str, str], user_text: str) -> dict[str, float]`（返回 `word_id → score`，只含确有词级信号的词；分支见 Global Constraints 冻结值）。
  - WEIGHTS 增 `"word_production": (0.4, "asr_word_confidence")`；`_RATING` **不含** `word_production`；`apply_evidence` 对 `word_production` 独立分支（只落证据 + 轴分，不计数、不排期）。
  - `record_round(session_id, scene_words, npc_text, user_text, confidence, *, turn_id, target_word_ids, attempt_id=None, words=None)`——words 可用时补 `word_production` 证据。
  - `run_round` 返回 dict 增 `"words"`；`_word_summary.scores.asrWordConfidence`。

- [ ] **Step 1: 写失败测试**

```python
# apps/api/tests/test_word_confidence.py（新建）
from app.learning.word_confidence import score_word_confidence

def test_hit_returns_word_probability():
    scene = {"word_loaf_n_1": "loaf", "word_jar_n_1": "jar"}
    words = [{"word": "loaf", "start": 0.4, "end": 0.9, "probability": 0.95}]
    out = score_word_confidence(words, scene, "I want a loaf")
    assert abs(out["word_loaf_n_1"] - 0.95) < 1e-6
    assert "word_jar_n_1" not in out          # 用户没说 jar → 无词级信号

def test_misrecognized_low_score():
    scene = {"word_loaf_n_1": "loaf"}
    words = [{"word": "roof", "start": 0.4, "end": 0.9, "probability": 0.9}]
    out = score_word_confidence(words, scene, "I want a loaf")   # 说了 loaf，ASR 听成 roof
    assert out["word_loaf_n_1"] == 0.15

def test_word_not_in_user_text_no_evidence():
    scene = {"word_loaf_n_1": "loaf", "word_jar_n_1": "jar"}
    words = [{"word": "loaf", "start": 0.4, "end": 0.9, "probability": 0.9}]
    out = score_word_confidence(words, scene, "show me the jar")
    assert "word_loaf_n_1" not in out           # loaf 未在 user_text → 无词级信号
    assert out["word_jar_n_1"] == 0.15          # jar 在 user_text 但 words 未检出 → 0.15（说但误识）

def test_no_words_returns_empty():
    scene = {"word_loaf_n_1": "loaf"}
    assert score_word_confidence([], scene, "I want a loaf") == {}
    assert score_word_confidence(None, scene, "I want a loaf") == {}
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run --project apps/api pytest apps/api/tests/test_word_confidence.py -q`
Expected: FAIL（无模块）。

- [ ] **Step 3: 实现**

`apps/api/app/learning/word_confidence.py`：

```python
"""词级对齐打分器：faster-whisper word_timestamps → 目标词词级置信度。
语义诚实（spec §5.1）：词后验 = 解码器信心，非发音评测。"""
from __future__ import annotations

from app.llm.lexmatch import token_contains

MISRECOGNIZED_SCORE = 0.15   # user 说了目标词但 ASR 未在 words 中识别到


def score_word_confidence(words: list[dict] | None, scene_words: dict[str, str],
                          user_text: str) -> dict[str, float]:
    """对 scene_words 中用户实际产出（user_text 命中）的目标词打分。
    命中 words → word.probability；说了但不在 words → MISRECOGNIZED_SCORE；未说 → 不计。"""
    if not words:
        return {}
    word_by_text = {}
    for w in words:
        t = (w.get("word") or "").strip().lower()
        if t and t not in word_by_text:
            word_by_text[t] = w.get("probability", 0.0)
    out: dict[str, float] = {}
    for word_id, lemma in scene_words.items():
        if not token_contains(user_text, lemma):
            continue                        # 用户未产出该词 → 无词级信号
        prob = word_by_text.get(lemma.lower())
        out[word_id] = float(prob) if prob is not None else MISRECOGNIZED_SCORE
    return out
```

`apps/api/app/learning/scores.py` WEIGHTS 增：

```python
    "word_production": (0.4, "asr_word_confidence"),
```

`apps/api/app/learning/evidence.py` `apply_evidence`（`store.add_evidence` 之后插分支）：

```python
    store.add_evidence(user_id, ev)
    source, result = ev["source"], ev["result"]
    weight, axis = WEIGHTS[source]
    wid = ev["word_id"]
    if source == "word_production":
        # 词级置信度：只更新 asr_word_confidence_score 轴分 + 证据明细；
        # 不计数（防与 classify_round 双重计 attempts）、不进排期（实验性，不扰动复习节奏）。
        m = store.get_mastery(user_id, wid)
        if m is None:
            store.upsert_mastery(user_id, wid, updated_at=now.isoformat())
            m = store.get_mastery(user_id, wid)
        store.upsert_mastery(user_id, wid,
                             asr_word_confidence_score=update_score(m["asr_word_confidence_score"], weight, ev["confidence"]),
                             updated_at=now.isoformat())
        return
    m = store.get_mastery(user_id, wid)
    if m is None:
        store.upsert_mastery(user_id, wid, updated_at=now.isoformat())
        m = store.get_mastery(user_id, wid)
    col = {"productive": "productive_score", "receptive": "receptive_score",
           "asr_confidence": "asr_confidence_score",
           "asr_word_confidence": "asr_word_confidence_score"}[axis]
    ...（其余不变）
```

`apps/api/app/learning/engine.py` `record_round`：

```python
    def record_round(self, session_id: str, scene_words: dict, npc_text: str,
                     user_text: str, confidence: float, *, turn_id: str,
                     target_word_ids: set[str], attempt_id: str | None = None,
                     words: list[dict] | None = None) -> int:
        conf = normalize_asr_confidence(confidence) if confidence < 0 else confidence
        drafts = classify_round(scene_words, npc_text, user_text, conf,
                                target_word_ids=target_word_ids)
        for d in drafts:
            now = datetime.now(timezone.utc)
            evidence = {...现状构造不变...}
            self.record_evidence(session_id, evidence, event_id=evidence["evidence_id"])
        if words is not None:
            self._record_word_production(session_id, scene_words, user_text, words,
                                         drafts, turn_id, attempt_id)
        return len(drafts)

    def _record_word_production(self, session_id: str, scene_words: dict, user_text: str,
                                words: list[dict], drafts: list[dict], turn_id: str,
                                attempt_id: str | None) -> None:
        from app.learning.word_confidence import score_word_confidence
        draft_ids = {d["word_id"] for d in drafts}
        now = datetime.now(timezone.utc)
        for wid, score in score_word_confidence(words, scene_words, user_text).items():
            if wid not in draft_ids:
                continue
            evidence = {
                "evidence_id": f"ev_{uuid.uuid4().hex[:12]}",
                "event_seq": 0, "session_id": session_id,
                "attempt_id": attempt_id or f"attempt_{turn_id}",
                "turn_id": turn_id,
                "objective_id": f"obj_scene_{wid}",
                "word_id": wid, "source": "word_production", "prompt_level": 0,
                "axis": "asr_word_confidence", "result": "success", "confidence": score,
                "evidence_policy_version": self.settings.evidence_policy_version,
                "fsrs_algorithm_version": self.settings.fsrs_algorithm_version,
                "created_at": now.isoformat(),
            }
            self.record_evidence(session_id, evidence, event_id=evidence["evidence_id"])
```

（`_record_word_production` 内新增词级证据走 `record_evidence`，天然 in-tx 且触发 `apply_memory_updates(evidence=...)`；词级证据 source 非 help、词已 known → should_touch 为 False，不 bump。文件头已 import `uuid`。）

`apps/api/app/voice_round.py` `run_round` 空文本与成功返回均带 words：

```python
    if not final_text:
        return {"finalText": "", "turnId": turn_id, "replied": False,
                "npcText": "", "confidence": conf, "words": asr_result.get("words")}
    ...
    return {"finalText": final_text, "turnId": turn_id, "replied": True,
            "npcText": accumulated.strip(), "confidence": conf,
            "words": asr_result.get("words")}
```

`apps/api/app/ws.py` `_run_round`：`record_round(..., target_word_ids=state.target_word_ids, words=result.get("words"))`。

`apps/api/app/learning/store.py` `all_words` SELECT 增列：

```python
        "       COALESCE(ms.asr_confidence_score, 0.0) AS asr_confidence_score, "
        "       COALESCE(ms.asr_word_confidence_score, 0.0) AS asr_word_confidence_score "
```

`apps/api/app/learning/api.py` `_word_summary` scores：

```python
            "scores": {"productive": r["productive_score"], "receptive": r["receptive_score"],
                       "asrConfidence": r["asr_confidence_score"],
                       "asrWordConfidence": r["asr_word_confidence_score"]},
```

- [ ] **Step 4: 运行验证通过**

Run: `uv run --project apps/api pytest apps/api/tests/test_word_confidence.py -q`
Expected: PASS。

- [ ] **Step 5: 全量回归**

Run: `uv run --project apps/api pytest apps/api -q`
Expected: 全绿。

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/learning/word_confidence.py apps/api/app/learning/scores.py apps/api/app/learning/evidence.py apps/api/app/learning/engine.py apps/api/app/voice_round.py apps/api/app/ws.py apps/api/app/learning/store.py apps/api/app/learning/api.py apps/api/tests/test_word_confidence.py
git commit -m "feat(api): word-level ASR confidence scorer + word_production evidence (non-scheduling, non-counting)"
```

---

## Task 8: 授权音频落盘（成功回合才写盘）

**Files:**
- Modify: `apps/api/app/voice_round.py`（`_write_consent_audio` + 成功路径调用）
- Modify: `apps/api/app/ws.py`（`SessionState.__init__` 存 `self.settings = settings`）
- Test: `apps/api/tests/test_audio_consent.py`

**Interfaces:**
- Consumes: `Settings.pronunciation_audio_consent`、`Settings.tutor_cache_dir`（父目录 = data 根，WAV 落 `data/pronunciation-audio/{session_id}/{utterance_id}.wav`）、`run_round` 的 `audio_pcm16`。
- Produces: `run_round` 在 `replied=True` 且授权时写 WAV + JSON 元数据；打断/未授权 → 不写。

- [ ] **Step 1: 写失败测试**

```python
# apps/api/tests/test_audio_consent.py（新建）
import asyncio, json, wave
from pathlib import Path
from app.event_store import EventStore
from app.voice_round import run_round
from app.settings import Settings

class _State:
    def __init__(self, settings):
        self.settings = settings
        self.scene = None
        self._seq = 0
        self.active_turn_id = None
        self.played_ms = 0
        self.is_playing = False
    def new_turn_id(self):
        self._seq += 1
        return f"turn_{self._seq}"
    @property
    def generation_id(self): return "gen_x"

class _Actor:
    async def stream_reply(self, **kw):
        yield {"type": "npc.speech.commit", "text": "Here is a loaf."}

async def _run(tmp_path, *, consent: bool, interrupted: bool):
    events = EventStore(tmp_path / "e.db")
    settings = Settings(pronunciation_audio_consent=consent, tutor_cache_dir=tmp_path / "tc")
    state = _State(settings)
    pcm = b"\x00\x00" * 1600
    async def asr(audio): return {"finalText": "loaf", "confidence": -0.2, "segments": [], "language": "en"}
    async def tts(t): return {"audioBase64": "AA==", "sampleRate": 24000, "ms": 100}
    async def send(x): pass
    task = asyncio.create_task(run_round("s1", "u1", pcm, events, asr, tts, send, _Actor(), state))
    if interrupted:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
    else:
        await task
    return tmp_path / "pronunciation-audio"

def test_consent_on_success_writes_wav(tmp_path):
    root = asyncio.run(_run(tmp_path, consent=True, interrupted=False))
    wav = root / "s1" / "u1.wav"
    assert wav.exists()
    with wave.open(str(wav), "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == 16000

def test_interrupt_does_not_write(tmp_path):
    root = asyncio.run(_run(tmp_path, consent=True, interrupted=True))
    assert not (root / "s1").exists()

def test_no_consent_does_not_write(tmp_path):
    root = asyncio.run(_run(tmp_path, consent=False, interrupted=False))
    assert not (root / "s1").exists()
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run --project apps/api pytest apps/api/tests/test_audio_consent.py -q`
Expected: FAIL（无 WAV 写出）。

- [ ] **Step 3: 实现**

`apps/api/app/ws.py` `SessionState.__init__` 首行加：`self.settings = settings`（settings 已在参数里）。

`apps/api/app/voice_round.py` 顶部 import + 模块级 helper：

```python
import wave
from pathlib import Path

def _write_consent_audio(session_id: str, utterance_id: str, pcm: bytes,
                         settings, meta: dict) -> None:
    """授权时写 WAV + 元数据；失败仅日志。调用方仅在回合完全成功（replied=True）时调用。"""
    if not getattr(settings, "pronunciation_audio_consent", False):
        return
    try:
        root = Path(settings.tutor_cache_dir).parent / "pronunciation-audio"
        dirpath = root / session_id
        dirpath.mkdir(parents=True, exist_ok=True)
        with wave.open(str(dirpath / f"{utterance_id}.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(pcm)
        (dirpath / f"{utterance_id}.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001 —— 落盘失败不影响回合/评分
        pass
```

`run_round` 成功路径（`return {...replied: True...}` 之前）：

```python
        _write_consent_audio(session_id, utterance_id, audio_pcm16, state.settings,
                             {"turnId": turn_id, "finalText": final_text, "sampleRate": 16000})
        return {"finalText": final_text, "turnId": turn_id, "replied": True,
                "npcText": accumulated.strip(), "confidence": conf,
                "words": asr_result.get("words")}
```

（`CancelledError` 分支不调用——打断即丢弃。）

- [ ] **Step 4: 运行验证通过**

Run: `uv run --project apps/api pytest apps/api/tests/test_audio_consent.py -q`
Expected: PASS。

- [ ] **Step 5: 全量回归**

Run: `uv run --project apps/api pytest apps/api -q`
Expected: 全绿。

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/voice_round.py apps/api/app/ws.py apps/api/tests/test_audio_consent.py
git commit -m "feat(api): consent-gated WAV persistence on successful rounds only"
```

---

## Task 9: 证据详情逐词可视化 + ProgressView 标注

**Files:**
- Modify: `apps/web/src/ProgressView.tsx`
- Modify: `apps/web/tests/ProgressView.test.tsx`
- Test: `apps/web/tests/ProgressView.test.tsx`

**Interfaces:**
- Consumes: 后端 `_word_summary.scores.asrWordConfidence`（Task 7）、evidence 端点 items（`axis`/`confidence` 已在）。
- Produces: ProgressView 标注更新为 `Word-level ASR confidence (experimental, from aligned timestamps)`；词行显示词级分（优先 `asrWordConfidence`，回退 `asrConfidence`）；证据详情对 `axis === "asr_word_confidence"` 行显示 `词级 · {score}` 标记（逐词可视化的最小兑现）。

- [ ] **Step 1: 写失败测试**

```tsx
// apps/web/tests/ProgressView.test.tsx 追加
import { render, screen } from '@testing-library/react';
import ProgressView from '../src/ProgressView';

test('shows word-level ASR confidence label and value', async () => {
  global.fetch = jest.fn(async () => ({
    json: async () => ({
      strategy: { evidencePolicyVersion: 'v1', fsrsAlgorithmVersion: 'fsrs-5' },
      totals: { quest: 1, free: 0, dueToday: 0 },
      page: 1, pageSize: 50, totalWords: 1,
      words: [{
        wordId: 'w1', lemma: 'loaf', pos: 'n', ipa: '/loʊf/', cefr: 'A1',
        sceneTags: [], source: 'quest', carrier: 'object',
        scores: { productive: 0.6, receptive: 0.5, asrConfidence: 0.0, asrWordConfidence: 0.95 },
        fsrs: { state: 'learning', due: null, reps: 0, lapses: 0 },
        evidenceCount: 1, lastEvidenceAt: null,
      }],
    }),
  }) as jest.Mock);
  render(<ProgressView />);
  expect(await screen.findByText(/Word-level ASR confidence/i)).toBeInTheDocument();
  expect(await screen.findByText(/0\.95/)).toBeInTheDocument();
});

test('marks word-level evidence rows in detail', async () => {
  const evRows = [{ evidence_id: 'e1', source: 'word_production', axis: 'asr_word_confidence',
                    result: 'success', confidence: 0.88, created_at: '2026-08-08T12:00:00Z' }];
  global.fetch = jest.fn(async (url: string) => ({
    json: async () => url.includes('/evidence')
      ? { items: evRows }
      : { strategy: { evidencePolicyVersion: 'v1', fsrsAlgorithmVersion: 'fsrs-5' },
          totals: { quest: 0, free: 0, dueToday: 0 }, page: 1, pageSize: 50, totalWords: 1,
          words: [{ wordId: 'w1', lemma: 'loaf', pos: 'n', ipa: null, cefr: null,
                    sceneTags: [], source: 'quest', carrier: null,
                    scores: { productive: 0, receptive: 0, asrConfidence: 0, asrWordConfidence: 0.88 },
                    fsrs: { state: 'learning', due: null, reps: 0, lapses: 0 },
                    evidenceCount: 1, lastEvidenceAt: null }] },
  }) as jest.Mock);
  render(<ProgressView />);
  const row = await screen.findByText(/loaf/);
  row.click();
  expect(await screen.findByText(/词级 · 0\.88/)).toBeInTheDocument();
});
```

- [ ] **Step 2: 运行验证失败**

Run: `cd apps/web && npx vitest run tests/ProgressView.test.tsx --maxWorkers=1`
Expected: FAIL（无 `asrWordConfidence` 字段 / 标注文案不匹配 / 无"词级"标记）。

- [ ] **Step 3: 实现**

`apps/web/src/ProgressView.tsx`：

```tsx
interface WordSummary {
  ...
  scores: { productive: number; receptive: number; asrConfidence: number; asrWordConfidence: number };
}
```

词行 chip（替换 170-172 行）：

```tsx
              <span style={chipStyle} title="实验性 · 词级对齐 ASR 置信度（非发音评测）">
                Word-level ASR confidence{' '}
                {(w.scores.asrWordConfidence ?? w.scores.asrConfidence).toFixed(2)}
              </span>
```

证据详情渲染（line 194 处）：

```tsx
                    <div key={ev.evidence_id}>
                      {ev.created_at} · {ev.axis} · {ev.result}
                      {ev.axis === 'asr_word_confidence' && (
                        <span style={chipStyle} title="词级对齐时间戳">词级 · {ev.confidence.toFixed(2)}</span>
                      )}
                      （置信度 {ev.confidence.toFixed(2)}）
                    </div>
```

（后端 evidence 端点已返回 `axis`/`confidence`；`word_production` 行天然满足 `axis === "asr_word_confidence"`。逐词窗口 `start/end` 本轮不落库——证据行本身即"逐词"粒度。）

- [ ] **Step 4: 运行验证通过（web）**

Run: `cd apps/web && npx vitest run tests/ProgressView.test.tsx --maxWorkers=1`
Expected: PASS。

- [ ] **Step 5: 全量回归（web）**

Run: `cd apps/web && npx vitest run --maxWorkers=1 && npx tsc --noEmit`
Expected: 全绿（既有 49 + 新增；tsc 0）。

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/ProgressView.tsx apps/web/tests/ProgressView.test.tsx
git commit -m "feat(web): word-level ASR confidence label + evidence timeline marker"
```

---

## 自审记录（写作后核对）

**1. Spec 覆盖核对：**
- 子阶段 A：settings/迁移/白名单（T1）✓、MemoryStore/抽取/should_touch（T2）✓、事件挂钩/快照（T3）✓、LLM 注入 + prefetch 键 + §10b 双向（T4）✓、memory_smoke（T5）✓。
- 子阶段 B：asr-worker words + 门槛 + 自检（T6）✓、打分器三分支 + word_production 轴/列/不排期不计数（T7）✓、授权落盘三路径（T8）✓、逐词可视化 + 标注（T9）✓。
- 子阶段 C：仅文档（T1 Step 5）✓，无实现代码 ✓。
- 设计 §4.4 接口名对齐：`get_world_summary`/`get_revision`/`apply_memory_updates` ✓（计划用 spec 命名，不用早前 draft 的 touch_*）。

**2. 占位扫描：** 无 `assert True`/TBD。所有步骤含具体代码与断言。`test_tutor_reply_injects_world_summary` 用 FakeClient 捕获 user 消息做真断言（非占位）。

**3. 类型一致性（已对真实代码核对）：**
- `record_evidence(session_id, evidence, *, event_id=None)`（engine.py:34）→ in-tx 挂钩 ✓；`record_round(...)` 签名扩展 `words=None`，`_run_round`（ws.py:116-120）传 `words=result.get("words")` ✓；`run_round` 返回 dict 增 `words`（voice_round.py:41/81）✓。
- `apply_evidence`（evidence.py:53）word_production 分支在 `add_evidence` 后、计数前 ✓；列映射增 `asr_word_confidence` ✓（store.py `all_words` 同步增列）。
- `ScenePrefetchCache.get/put` 两参（scene_prefetch.py:14/24）——`fill_scene`（scene_lifecycle.py:133/149）与 `_prefetch_for`（ws.py:257/267）调用处同步改 ✓。
- `enter_scene` 有 `app`/`events`/`session_id`（scene_lifecycle.py:46）→ 场景挂钩可用 `app.state.memory` + `events.write_lock` ✓。
- `SessionState.__init__(settings)`（ws.py:23）→ Task 8 只加 `self.settings = settings` ✓。
- `WhisperEngine.load(device)`（whisper_engine.py:16）→ 加 `model=None` + `model_name` 字段；`RollingTranscriber.__init__`（streaming.py:32）加 `word_timestamps` ✓。
- `_word_summary`（api.py:78）scores 增 `asrWordConfidence`，ProgressView `WordSummary.scores` 同步 ✓。

**4. 已知决策（写死在 Global Constraints）：** 场景 should_touch 只认"新 archetype 首次进入"（§4.7/§10b 测试兜底）；词级分支冻结值；word_production 不排期不计数；words 缺失时回退 phase-4；snapshot 事件 session_id 取触发者（evidence.session_id / ask.session_id / 默认 "s1"）。
