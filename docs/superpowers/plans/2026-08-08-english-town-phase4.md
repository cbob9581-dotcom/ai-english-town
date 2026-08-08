# 英语小镇 阶段 4：学习引擎 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「词表导入 + 证据 + FSRS-5 排期 + 每场选词 + 偶遇词流」落地为可运行系统：6 张业务表 + outbox、py-fsrs 包装层、证据更新规则、4+1 个 HTTP 端点、最小进度页。

**Architecture:** 独立 `app/learning/` 模块（纯函数层 + store 层 + engine 层 + api 层），通过既有接缝接入：`resolve_word_id`（concepts.py）、`scene_factory`（main.py）、`_run_round`/`_handle_companion_ask`（ws.py）。FSRS 用 py-fsrs（钉 5.x），引擎复用 `event_store` 的同一 SQLite 连接 + 锁实现**单一事务**（证据事件 + evidence_events + mastery_states 一次 COMMIT）。

**Tech Stack:** Python 3.13 / FastAPI / SQLite（WAL）/ py-fsrs `fsrs>=5,<6`（纯 Python，唯一新增运行时依赖）/ React + zustand（前端最小页）。

## Global Constraints

- **16GB / 严格串行**：任何时刻单个测试进程；web 测试 `--maxWorkers=1`。**禁止并行子代理**。
- **单一事务**：一次 `record_evidence` = session_events(evidence, internal=1) + evidence_events + mastery_states 在**同一连接同一事务**内 COMMIT；引擎复用 `event_store.connection` + `event_store._lock`（sequence 分配必须同锁，否则两个连接并发 `MAX(sequence)+1` 会撞 sequence）。
- **幂等**：`event_id` 唯一；重复提交 INSERT OR IGNORE，不重复计算。
- **时间一律 ISO 8601 datetime（UTC，`YYYY-MM-DDTHH:MM:SSZ`）**；`due`/`last_review`/`created_at`/`last_scheduled_date` 全 datetime；时间敏感函数显式注入 `now: datetime`（tz-aware UTC）。
- **ASR confidence 归一化**：`confidence = exp(avg_logprob)`（avg_logprob 为负，越高越好；−0.3→0.74）。阈值 `fsrs_min_confidence=0.6` 作用在归一化后的值上。**绝不可把负 avg_logprob 直接存 evidence**。
- **ASR 噪声不惩罚**：目标词未检出 → `no_attempt`（权重 0、不进 FSRS、不计尝试）；conf<0.6 → `uncertain`（同前）；`error` **仅** companion 明确纠错路径（v1 回合流无此信号，则该 source 不产生，仅引擎/测试支持）。
- **日闸**：每 `(user_id, word_id)` 每自然日最多一次 FSRS 调度；当日代表评分 = 当日有效评分最低值；已调度后更低评分→重排（降级），更高评分→跳过排期、只更新分/计数。
- **py-fsrs 钉 5.x**：`fsrs>=5,<6`（5.1.3 即 FSRS-5，19 参数）。`Scheduler(desired_retention=0.9, enable_fuzzing=False, learning_steps=(), relearning_steps=())`——fuzzing 关闭才确定性；learning_steps 置空使首次评分直进 Review（无 1min/10min 学习步，与日闸一致）。`Card(card_id=...)` 必须显式传 card_id（默认构造有 `time.sleep(0.001)`）。
- **pos 一律短格式**：`n`/`v`/`adj`...（与 catalog/entities.json/concepts.py 一致，**不是** noun/verb）。
- **carrier ∈ {object, action, phrase}**（主 spec §3:34）：object 词 `slot_categories ∩ 场景 propSlot 类别 ≠ ∅` 才可选中。
- **版本常量**：`evidence_policy_version="v1"`、`fsrs_algorithm_version="fsrs-5"`、`score_alpha=0.35`、`fsrs_retention=0.9`、`fsrs_min_confidence=0.6` 进 `settings.py`；写入每条证据/掌握态。
- **resolve_word_id 缓存**：进程内 dict + 最早创建冲突规则 + `invalidate_word_id_cache()` 导入/提升后清。
- 依赖范围：后端唯一新增 `fsrs>=5,<6`；前端无新依赖。
- state-audit 白名单：main 接线（Task 13）之后所有 make_app 测试都会建学习表并可能写 spontaneous 表——`ALLOWED_TABLES` 更新与 `assert changed == ALLOWED_TABLES` 放宽**必须在 Task 13 同步完成**（不是最后任务）。
- **`LearningStore` 复用 `event_store.connection`，设置 `row_factory = sqlite3.Row` + `PRAGMA foreign_keys=ON`**。Row 同时支持整数与键访问（event_store 现有 `r[0]`/`r[1]` 索引不受影响）。FK ON 使缺失 learning_items 的证据写入抛错 → 引擎 outbox 兜底路径可测。

---

### Task 1: py-fsrs 依赖 + `learning/fsrs.py` 包装层

**Files:**
- Modify: `apps/api/pyproject.toml`（dependencies 加 `fsrs>=5,<6`）
- Create: `apps/api/app/learning/__init__.py`
- Create: `apps/api/app/learning/fsrs.py`
- Create: `apps/api/tests/learning/test_fsrs.py`
- Create: `apps/api/tests/learning/__init__.py`

**Interfaces:**
- Consumes: py-fsrs 5.1.3（PyPI `fsrs`）。
- Produces:
  - `guard_parameter_count() -> int`（断言 `len(DEFAULT_PARAMETERS) == 19`）
  - `to_fsrs_card(row: Mapping, *, card_id: int) -> Card`（mastery_states 行 → py-fsrs Card；state='new' → 全新 Card；card_id 必须显式传——py-fsrs 默认 ctor 会 sleep 1ms）
  - `from_fsrs_card(card: Card) -> dict`（Card → mastery_states 列；State 映射 Learning→learning/Review→review/Relearning→relearning）
  - `schedule(card: Card, rating: int, now: datetime) -> Card`（`review_card` 返回 `tuple[Card, ReviewLog]`，取 `[0]`）

- [ ] **Step 1: 加依赖**

在 `apps/api/pyproject.toml` `dependencies` 加一行 `"fsrs>=5,<6",`（保持字母序，fastapi 后、httpx 前）。

运行：`cd e:/ai-english && uv add --project apps/api "fsrs>=5,<6"`（若 uv 不可用则 `uv sync`）。预期：安装成功且 `python -c "import fsrs; from fsrs import DEFAULT_PARAMETERS; print(len(DEFAULT_PARAMETERS))"` 输出 `19`。

- [ ] **Step 2: 写失败测试**

`apps/api/tests/learning/test_fsrs.py`：

```python
from datetime import datetime, timezone, timedelta

import pytest
from fsrs import Card, Rating

from app.learning.fsrs import from_fsrs_card, guard_parameter_count, schedule, to_fsrs_card


def test_fsrs5_has_19_parameters() -> None:
    assert guard_parameter_count() == 19


def test_fresh_good_schedule_golden() -> None:
    now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    card = Card(card_id=1)
    updated = schedule(card, Rating.Good, now)
    assert updated.state.name == "Review"          # learning_steps=() → 直进 Review
    assert updated.last_review == now
    assert (updated.due - now).days == 3
    assert updated.stability == pytest.approx(3.173, abs=1e-3)
    assert updated.difficulty == pytest.approx(5.282434422319005, abs=1e-9)


def test_second_good_extends_interval() -> None:
    now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    card = Card(card_id=1)
    s1 = schedule(card, Rating.Good, now)
    s2 = schedule(s1, Rating.Good, now + timedelta(days=7))
    assert (s2.due - (now + timedelta(days=7))).days == 19
    assert s2.stability == pytest.approx(18.858155152579183, abs=1e-9)


def test_again_on_fresh_card_short_interval() -> None:
    now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
    updated = schedule(Card(card_id=3), Rating.Again, now)
    assert (updated.due - now).days == 1
    assert updated.stability == pytest.approx(0.40255, abs=1e-5)


def test_roundtrip_to_from_fsrs_card() -> None:
    row = {"state": "review", "stability": 3.173, "difficulty": 5.28,
           "due": "2026-08-11T12:00:00+00:00", "last_review": "2026-08-08T12:00:00+00:00"}
    card = to_fsrs_card(row, card_id=7)
    assert card.state.name == "Review"
    out = from_fsrs_card(card)
    assert out["state"] == "review"
    assert out["due"] == row["due"]
    assert out["stability"] == pytest.approx(3.173)


def test_new_state_maps_to_fresh_card() -> None:
    row = {"state": "new", "stability": 0.0, "difficulty": 0.0, "due": None, "last_review": None}
    card = to_fsrs_card(row, card_id=5)
    assert card.state.name == "Learning"   # Card() 默认 Learning/step 0
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_fsrs.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.learning.fsrs'`）。

- [ ] **Step 4: 实现**

`apps/api/app/learning/__init__.py`：空文件。

`apps/api/app/learning/fsrs.py`：

```python
"""py-fsrs 5.x 包装层：FSRS-5 排期，确定性强制的薄封装。
py-fsrs 5.x 的 review_card 返回 (Card, ReviewLog)，且 Card 无 New 态
（只有 Learning/Review/Relearning）——'new' 由引擎维护（尚未 schedule）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

from fsrs import DEFAULT_PARAMETERS, Card, Rating, Scheduler

_STATE_TO_FSRS = {"learning": "Learning", "review": "Review", "relearning": "Relearning"}
_FSRS_TO_STATE = {"Learning": "learning", "Review": "review", "Relearning": "relearning"}


def guard_parameter_count() -> int:
    """FSRS-5 = 19 参数；升级踩线（FSRS-6 = 21）立即暴露。"""
    assert len(DEFAULT_PARAMETERS) == 19, f"expected FSRS-5 (19 params), got {len(DEFAULT_PARAMETERS)}"
    return len(DEFAULT_PARAMETERS)


def _scheduler() -> Scheduler:
    return Scheduler(desired_retention=0.9, enable_fuzzing=False,
                     learning_steps=(), relearning_steps=())


def to_fsrs_card(row: Mapping, *, card_id: int) -> Card:
    """mastery_states 行 → py-fsrs Card。state='new' → 全新 Card（默认 Learning/step 0）。
    card_id 必须显式传入：Card() 默认构造含 time.sleep(0.001)。"""
    if row["state"] == "new":
        return Card(card_id=card_id)
    due = datetime.fromisoformat(row["due"]) if row.get("due") else datetime.now(timezone.utc)
    last = datetime.fromisoformat(row["last_review"]) if row.get("last_review") else None
    step = 0 if row["state"] == "learning" else (1 if row["state"] == "relearning" else None)
    return Card(card_id=card_id, state=_STATE_TO_FSRS[row["state"]], step=step,
                stability=row["stability"], difficulty=row["difficulty"],
                due=due, last_review=last)


def from_fsrs_card(card: Card) -> dict:
    return {"state": _FSRS_TO_STATE[card.state.name],
            "due": card.due.isoformat(),
            "last_review": card.last_review.isoformat() if card.last_review else None,
            "stability": card.stability, "difficulty": card.difficulty}


def schedule(card: Card, rating: int, now: datetime) -> Card:
    """now 必须 tz-aware UTC（review_card 校验）。返回更新后的 Card。"""
    updated, _log = _scheduler().review_card(card, Rating(rating), now)
    return updated
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_fsrs.py -v`
Expected: PASS（7 个）。

- [ ] **Step 6: Commit**

```bash
git add apps/api/pyproject.toml apps/api/uv.lock apps/api/app/learning apps/api/tests/learning
git commit -m "feat(learning): py-fsrs 5.x wrapper + guard (FSRS-5, 19 params)
```
（uv.lock 若在项目根则 `git add uv.lock`；按实际位置调整。）

---

### Task 2: `learning/scores.py` 三维分更新公式

**Files:**
- Create: `apps/api/app/learning/scores.py`
- Create: `apps/api/tests/learning/test_scores.py`

**Interfaces:**
- Consumes: Task 1 无依赖。
- Produces:
  - `WEIGHTS: dict[str, tuple[float, str]]` — source → (weight, axis)：`spontaneous_production`→(1.0,'productive')、`prompted_production`→(0.65,'productive')、`repetition`→(0.4,'asr_confidence')、`action_understanding`→(0.55,'receptive')、`help`→(−0.35,'productive')、`error`→(−0.5,'productive')
  - `update_score(score: float, weight: float, confidence: float, *, alpha: float = 0.35) -> float`
  - `normalize_asr_confidence(avg_logprob: float) -> float`（`exp(avg_logprob)`）

- [ ] **Step 1: 写失败测试**

`apps/api/tests/learning/test_scores.py`：

```python
import math

import pytest

from app.learning.scores import WEIGHTS, normalize_asr_confidence, update_score


def test_positive_delta_bounded_upper() -> None:
    assert update_score(0.0, 1.0, 1.0) == 0.35                       # 0 + .35*1*(1-0)
    assert update_score(0.9, 1.0, 1.0) == pytest.approx(0.9 + 0.35 * 0.1)
    assert update_score(1.0, 1.0, 1.0) == 1.0                        # 封顶


def test_negative_delta_bounded_lower() -> None:
    assert update_score(0.5, -0.35, 1.0) == pytest.approx(0.5 - 0.35 * 0.35 * 0.5)
    assert update_score(0.0, -0.35, 1.0) == 0.0                      # 保底


def test_confidence_scales_delta() -> None:
    lo = update_score(0.0, 1.0, 0.5)   # delta = 0.5
    hi = update_score(0.0, 1.0, 1.0)   # delta = 1.0
    assert 0.0 < lo < hi < 1.0


def test_weights_match_spec() -> None:
    assert WEIGHTS["spontaneous_production"] == (1.0, "productive")
    assert WEIGHTS["prompted_production"] == (0.65, "productive")
    assert WEIGHTS["repetition"] == (0.4, "asr_confidence")
    assert WEIGHTS["action_understanding"] == (0.55, "receptive")
    assert WEIGHTS["help"] == (-0.35, "productive")
    assert WEIGHTS["error"] == (-0.5, "productive")


def test_normalize_asr_confidence() -> None:
    assert normalize_asr_confidence(-0.3) == pytest.approx(math.exp(-0.3))
    assert 0.0 < normalize_asr_confidence(0.0) < 1.0
    assert normalize_asr_confidence(-100.0) == pytest.approx(0.0, abs=1e-9)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_scores.py -v`
Expected: FAIL（`No module named 'app.learning.scores'`）。

- [ ] **Step 3: 实现**

`apps/api/app/learning/scores.py`：

```python
"""三维分更新：一次证据只更新其 axis 对应的一维。
公式（评审第 1 点）：delta = weight * confidence；
  delta>=0 → min(1, s + alpha*delta*(1-s))；delta<0 → max(0, s + alpha*delta*s)。
alpha 随 evidence_policy_version 版本化（settings.score_alpha）。"""
from __future__ import annotations

import math

WEIGHTS: dict[str, tuple[float, str]] = {
    "spontaneous_production": (1.0, "productive"),
    "prompted_production": (0.65, "productive"),
    "repetition": (0.4, "asr_confidence"),
    "action_understanding": (0.55, "receptive"),
    "help": (-0.35, "productive"),
    "error": (-0.5, "productive"),
}


def update_score(score: float, weight: float, confidence: float, *, alpha: float = 0.35) -> float:
    delta = weight * confidence
    if delta >= 0:
        return min(1.0, score + alpha * delta * (1.0 - score))
    return max(0.0, score + alpha * delta * score)


def normalize_asr_confidence(avg_logprob: float) -> float:
    """ASR confidence 是 Whisper avg_logprob（负数，越高越好），归一化到 (0,1)。"""
    return math.exp(max(-40.0, avg_logprob))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_scores.py -v`
Expected: PASS（5 个）。

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/learning/scores.py apps/api/tests/learning/test_scores.py
git commit -m "feat(learning): 3-axis update_score (ALPHA=0.35 bounded asymmetric) + ASR conf normalization"
```

---

### Task 3: `event_store` 单事务支持（`append_in_tx` + `internal` 列）

**Files:**
- Modify: `apps/api/app/event_store.py`
- Modify: `apps/api/tests/test_event_store.py`

**Interfaces:**
- Consumes: Task 1 无依赖（这是 event_store 自身扩展）。
- Produces:
  - `EventStore.write_lock` property → `self._lock`（引擎复用）
  - `EventStore.append_in_tx(conn, session_id, event_type, payload, event_id=None, internal=False) -> int`：**在调用方提供的连接上写，不 COMMIT，不取锁（调用方已持锁）**；幂等（event_id 查重返回既有 seq）。
  - `EventStore.append(..., internal: bool = False)` 新参数（默认 False，既有调用不变）。
  - `session_events` 表加 `internal INTEGER NOT NULL DEFAULT 0` 列（建表 + 老库 ALTER 迁移）。

- [ ] **Step 1: 写失败测试**

在 `apps/api/tests/test_event_store.py` 追加：

```python
def test_append_internal_flag_and_in_tx(tmp_path) -> None:
    store = EventStore(tmp_path / "e.db")
    with store.write_lock:
        seq = store.append_in_tx(store.connection, "s1", "evidence",
                                 {"k": "v"}, event_id="ev_1", internal=True)
        assert seq == 1
        dup = store.append_in_tx(store.connection, "s1", "evidence",
                                 {"k": "v"}, event_id="ev_1", internal=True)
        assert dup == seq                      # 幂等
    store.connection.commit()
    rows = store.list_after("s1", 0)
    assert rows[0]["event_type"] == "evidence"
    assert rows[0]["payload"] == {"k": "v"}
    internal = store.connection.execute(
        "SELECT internal FROM session_events WHERE event_id='ev_1'").fetchone()[0]
    assert internal == 1


def test_append_in_tx_rollback_keeps_nothing(tmp_path) -> None:
    store = EventStore(tmp_path / "e.db")
    with store.write_lock:
        store.append_in_tx(store.connection, "s1", "evidence", {"k": 1}, event_id="ev_x")
    store.connection.rollback()               # 调用方事务失败
    assert store.list_after("s1", 0) == []    # 未 COMMIT 不落库


def test_append_default_internal_zero(tmp_path) -> None:
    store = EventStore(tmp_path / "e.db")
    store.append("s1", "scene.patch", {"op": []})
    internal = store.connection.execute(
        "SELECT internal FROM session_events ORDER BY sequence").fetchone()[0]
    assert internal == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/test_event_store.py -v`
Expected: 新测试 FAIL（`append_in_tx` 不存在 / `internal` 列不存在）。

- [ ] **Step 3: 实现**

修改 `apps/api/app/event_store.py`：

`_SCHEMA` 里 `session_events` 建表语句加 `internal INTEGER NOT NULL DEFAULT 0,`（放在 `created_at` 前）。

`__init__` 里 `executescript` 后加老库迁移：

```python
        # 老库迁移：session_events.internal 列（新增；幂等）
        cols = {r[1] for r in self.connection.execute("PRAGMA table_info(session_events)")}
        if "internal" not in cols:
            self.connection.execute("ALTER TABLE session_events ADD COLUMN internal INTEGER NOT NULL DEFAULT 0")
        self.connection.commit()
```

`append` 签名加 `internal: bool = False`，INSERT 语句加 `internal` 列与值：

```python
    def append(self, session_id: str, event_type: str, payload: dict,
               event_id: str | None = None, internal: bool = False) -> int:
        """返回该事件的 sequence。event_id 相同则幂等（返回既有 sequence）。"""
        with self._lock:
            row = self.connection.execute(
                "SELECT sequence FROM session_events WHERE event_id = ?", (event_id,)
            ).fetchone() if event_id else None
            if row is not None:
                return int(row[0])
            seq_row = self.connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            seq = int(seq_row[0])
            self.connection.execute(
                "INSERT OR IGNORE INTO session_events(sequence, event_id, session_id, event_type, payload_json, internal, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (seq, event_id or str(uuid.uuid4()), session_id, event_type,
                 json.dumps(payload, ensure_ascii=False), 1 if internal else 0, _utcnow()),
            )
            self.connection.commit()
            return seq
```

加新方法 + property：

```python
    @property
    def write_lock(self) -> threading.Lock:
        """引擎复用同一锁：session_events sequence 分配必须单点串行。"""
        return self._lock

    def append_in_tx(self, conn, session_id: str, event_type: str, payload: dict,
                     event_id: str | None = None, internal: bool = False) -> int:
        """在调用方连接 conn 上写，不 COMMIT、不取锁（调用方持 write_lock）。
        幂等同 append。用于引擎单一事务（证据事件 + 学习表一次 COMMIT）。"""
        row = conn.execute(
            "SELECT sequence FROM session_events WHERE event_id = ?", (event_id,)
        ).fetchone() if event_id else None
        if row is not None:
            return int(row[0])
        seq_row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        seq = int(seq_row[0])
        conn.execute(
            "INSERT OR IGNORE INTO session_events(sequence, event_id, session_id, event_type, payload_json, internal, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (seq, event_id or str(uuid.uuid4()), session_id, event_type,
             json.dumps(payload, ensure_ascii=False), 1 if internal else 0, _utcnow()),
        )
        return seq
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/test_event_store.py apps/api/tests/test_state_audit.py -v`
Expected: 全部 PASS（含既有 3 个原有测试——确认 `internal=0` 默认值不破坏既有断言）。注意 `test_state_audit.py` 此刻仍通过（学习表尚未建）。

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/event_store.py apps/api/tests/test_event_store.py
git commit -m "feat(learning): event_store.append_in_tx + internal column for single-transaction evidence"
```

---

### Task 4: `learning/store.py` LearningStore（6 业务表 + outbox schema）

**Files:**
- Create: `apps/api/app/learning/store.py`
- Create: `apps/api/tests/learning/test_store.py`

**Interfaces:**
- Consumes: Task 3（`event_store.connection` + `write_lock`）。
- Produces:
  - `SCHEMA: str` — 6 表 + outbox 的 CREATE TABLE IF NOT EXISTS（DDL 见 spec §4，含 user_id/版本/计数/objective_id 可空/carrier）
  - `class LearningStore:`
    - `__init__(self, conn)` — 执行 SCHEMA
    - `import_words(self, user_id: str, list_id: str, name: str, items: list[dict]) -> tuple[int, int, int, int]` → `(imported, known, missing_metadata, total)`
    - `get_item(self, user_id, word_id) -> dict | None`
    - `list_items_by_scene(self, user_id, archetype_id) -> list[dict]`（scene_tags 含该场景）
    - `get_mastery(self, user_id, word_id) -> dict | None`
    - `get_mastery_for_scene(self, user_id, word_ids) -> dict[str, dict]`
    - `upsert_mastery(self, user_id, word_id, **fields)`（state/due/stability/.../counts/updated_at）
    - `upsert_mastery_counts(self, user_id, word_id, *, attempts=0, success_count=0, scaffolded_success_count=0, help_count=0, exposure_count=0)`
    - `add_evidence(self, user_id, evidence: dict) -> None`（INSERT OR IGNORE）
    - `evidence_for_word(self, user_id, word_id) -> list[dict]`
    - `all_words(self, user_id) -> list[dict]`（join learning_items + mastery_states）
    - `outbox_push(self, user_id, payload_json: str) -> None`
    - `outbox_drain(self, user_id) -> list[dict]`（取全部 + DELETE，返回 payload 列表）
    - `resolve_word_id_from_store(self, lemma, pos, sense=1) -> str | None`（查 learning_items，多 sense 取 created_at 最早）

- [ ] **Step 1: 写失败测试**

`apps/api/tests/learning/test_store.py`（用 tmp db + 复用 EventStore 连接）：

```python
import json
from pathlib import Path

import pytest

from app.event_store import EventStore
from app.learning.store import LearningStore


@pytest.fixture()
def store(tmp_path) -> LearningStore:
    events = EventStore(tmp_path / "e.db")
    return LearningStore(events.connection)


def test_import_and_resolve(store) -> None:
    imported, known, missing, total = store.import_words(
        "local", "l1", "bakery list",
        [{"lemma": "loaf", "pos": "n", "sense": "1", "ipa": "/loʊf/", "cefr": "A2",
          "scene_tags": json.dumps(["bakery"]), "carrier": "object",
          "slot_categories": json.dumps(["food"]), "source": "quest", "created_at": "2026-08-08T00:00:00+00:00"}])
    assert (imported, known, missing, total) == (1, 0, 0, 1)
    item = store.get_item("local", "word_loaf_n_1")
    assert item["lemma"] == "loaf"
    assert json.loads(item["scene_tags"]) == ["bakery"]
    assert store.resolve_word_id_from_store("loaf", "n") == "word_loaf_n_1"


def test_import_known_dedup(store) -> None:
    store.import_words("local", "l1", "x", [{"lemma": "loaf", "pos": "n", "sense": "1",
                                             "scene_tags": "[]", "carrier": "phrase",
                                             "slot_categories": "[]", "source": "quest",
                                             "created_at": "2026-08-08T00:00:00+00:00"}])
    imported, known, missing, total = store.import_words(
        "local", "l2", "y", [{"lemma": "loaf", "pos": "n", "sense": "1",
                              "scene_tags": "[]", "carrier": "phrase", "slot_categories": "[]",
                              "source": "quest", "created_at": "2026-08-08T00:00:00+00:00"}])
    assert (imported, known) == (0, 1)


def test_mastery_crud(store) -> None:
    store.import_words("local", "l1", "x", [{"lemma": "loaf", "pos": "n", "sense": "1",
                                             "scene_tags": "[]", "carrier": "phrase",
                                             "slot_categories": "[]", "source": "quest",
                                             "created_at": "2026-08-08T00:00:00+00:00"}])
    assert store.get_mastery("local", "word_loaf_n_1") is None
    store.upsert_mastery("local", "word_loaf_n_1", due="2026-08-09T00:00:00+00:00", state="review")
    m = store.get_mastery("local", "word_loaf_n_1")
    assert m["state"] == "review"
    store.upsert_mastery_counts("local", "word_loaf_n_1", attempts=2, success_count=1,
                                scaffolded_success_count=1)
    m = store.get_mastery("local", "word_loaf_n_1")
    assert m["attempts"] == 2 and m["success_count"] == 1


def test_evidence_dedup_and_list(store) -> None:
    # FK ON：证据必须落在已导入词上
    store.import_words("local", "l1", "x", [{"lemma": "loaf", "pos": "n", "sense": "1",
                                             "scene_tags": "[]", "carrier": "phrase",
                                             "slot_categories": "[]", "source": "quest",
                                             "created_at": "2026-08-08T00:00:00+00:00"}])
    ev = {"evidence_id": "ev_1", "event_seq": 1, "session_id": "s1", "attempt_id": "a1",
          "turn_id": "t1", "objective_id": "obj_plaza_w1", "word_id": "word_loaf_n_1",
          "source": "prompted_production", "prompt_level": 1, "axis": "productive",
          "result": "success", "confidence": 0.86,
          "evidence_policy_version": "v1", "fsrs_algorithm_version": "fsrs-5",
          "created_at": "2026-08-08T00:00:00+00:00"}
    store.add_evidence("local", ev)
    store.add_evidence("local", ev)          # INSERT OR IGNORE 去重
    rows = store.evidence_for_word("local", "word_loaf_n_1")
    assert len(rows) == 1


def test_outbox_push_drain(store) -> None:
    store.outbox_push("local", json.dumps({"evidenceId": "ev_1"}))
    store.outbox_push("local", json.dumps({"evidenceId": "ev_2"}))
    drained = store.outbox_drain("local")
    assert [json.loads(p)["evidenceId"] for p in drained] == ["ev_1", "ev_2"]
    assert store.outbox_drain("local") == []   # 二次排空为空
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_store.py -v`
Expected: FAIL（`No module named 'app.learning.store'`）。

- [ ] **Step 3: 实现**

`apps/api/app/learning/store.py`：DDL 逐字对齐 spec §4.1–4.6 + session_events 变更已在 Task 3。**事务语义（关键）**：`add_evidence` / `upsert_mastery` / `upsert_mastery_counts` **不 commit**——由引擎在单一事务内调用并统一 COMMIT；`import_words` / `outbox_push` / `outbox_drain` 是独立事务自行 commit。**`resolve_word_id_from_store` 多 sense 取 `created_at` 最早（并列取 rowid 最小）**：

```python
"""learning 六表 + outbox 的 schema 与 CRUD。连接复用 event_store 的同一连接。
事务语义：add_evidence/upsert_* 不 commit（引擎单事务控制）；import_words/outbox_* 独立 commit。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

SCHEMA = """
CREATE TABLE IF NOT EXISTS word_lists(
  user_id    TEXT NOT NULL DEFAULT 'local',
  list_id    TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  source     TEXT NOT NULL DEFAULT 'import',
  created_at TEXT NOT NULL,
  UNIQUE(user_id, list_id)
);
CREATE TABLE IF NOT EXISTS learning_items(
  word_id      TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL DEFAULT 'local',
  lemma        TEXT NOT NULL,
  pos          TEXT,
  sense        TEXT,
  ipa          TEXT,
  cefr         TEXT,
  scene_tags   TEXT NOT NULL DEFAULT '[]',
  carrier      TEXT NOT NULL DEFAULT 'phrase',
  slot_categories TEXT NOT NULL DEFAULT '[]',
  source       TEXT NOT NULL,
  list_id      TEXT,
  created_at   TEXT NOT NULL,
  UNIQUE(user_id, lemma, pos, sense)
);
CREATE TABLE IF NOT EXISTS mastery_states(
  user_id             TEXT NOT NULL DEFAULT 'local',
  word_id             TEXT NOT NULL REFERENCES learning_items(word_id),
  productive_score    REAL NOT NULL DEFAULT 0.0,
  receptive_score     REAL NOT NULL DEFAULT 0.0,
  asr_confidence_score REAL NOT NULL DEFAULT 0.0,
  state               TEXT NOT NULL DEFAULT 'new',
  due                 TEXT,
  stability           REAL NOT NULL DEFAULT 0.0,
  difficulty          REAL NOT NULL DEFAULT 0.0,
  reps                INTEGER NOT NULL DEFAULT 0,
  lapses              INTEGER NOT NULL DEFAULT 0,
  last_review         TEXT,
  attempts            INTEGER NOT NULL DEFAULT 0,
  exposure_count      INTEGER NOT NULL DEFAULT 0,
  help_count          INTEGER NOT NULL DEFAULT 0,
  success_count       INTEGER NOT NULL DEFAULT 0,
  scaffolded_success_count INTEGER NOT NULL DEFAULT 0,
  last_scheduled_date TEXT,
  last_scheduled_rating INTEGER,
  fsrs_algorithm_version TEXT NOT NULL DEFAULT 'fsrs-5',
  updated_at          TEXT NOT NULL,
  PRIMARY KEY(user_id, word_id)
);
CREATE TABLE IF NOT EXISTS evidence_events(
  evidence_id           TEXT PRIMARY KEY,
  user_id               TEXT NOT NULL DEFAULT 'local',
  event_seq             INTEGER NOT NULL,
  session_id            TEXT NOT NULL,
  attempt_id            TEXT NOT NULL,
  turn_id               TEXT NOT NULL,
  objective_id          TEXT,
  word_id               TEXT NOT NULL REFERENCES learning_items(word_id),
  source                TEXT NOT NULL,
  prompt_level          INTEGER NOT NULL,
  axis                  TEXT NOT NULL,
  result                TEXT NOT NULL,
  confidence            REAL NOT NULL,
  evidence_policy_version TEXT NOT NULL DEFAULT 'v1',
  fsrs_algorithm_version  TEXT NOT NULL DEFAULT 'fsrs-5',
  created_at            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spontaneous_encounters(
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL DEFAULT 'local',
  session_id    TEXT NOT NULL,
  lemma         TEXT NOT NULL,
  pos           TEXT,
  turn_id       TEXT NOT NULL,
  encounter_no  INTEGER NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spontaneous_words(
  user_id      TEXT NOT NULL DEFAULT 'local',
  lemma        TEXT NOT NULL,
  pos          TEXT,
  first_seen_at  TEXT NOT NULL,
  last_seen_at   TEXT NOT NULL,
  encounter_count INTEGER NOT NULL DEFAULT 0,
  asked        INTEGER NOT NULL DEFAULT 0,
  promoted     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(user_id, lemma, pos)
);
CREATE TABLE IF NOT EXISTS evidence_outbox(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    TEXT NOT NULL DEFAULT 'local',
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


class LearningStore:
    def __init__(self, conn) -> None:
        self.conn = conn
        # Row 同时支持整数与键访问：event_store 的 tuple 索引（r[0]/r[1]）不受影响，
        # 学习层可用 m["state"]/m["due"]。FK ON 保证证据必须落在已导入词上（outbox 可测）。
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def import_words(self, user_id: str, list_id: str, name: str, items: list[dict]) -> tuple[int, int, int, int]:
        """独立事务（自行 commit）。word_id 缺省从 lemma_pos_sense 派生。"""
        created = items[0]["created_at"] if items else _utcnow()
        self.conn.execute(
            "INSERT OR IGNORE INTO word_lists(user_id, list_id, name, source, created_at) VALUES(?,?,?,?,?)",
            (user_id, list_id, name, "import", created))
        imported = known = missing = 0
        for it in items:
            lemma, pos, sense = it["lemma"], it["pos"], str(it.get("sense", "1"))
            word_id = it.get("word_id") or f"word_{lemma}_{pos}_{sense}"
            if self.conn.execute(
                    "SELECT 1 FROM learning_items WHERE user_id=? AND lemma=? AND pos=? AND sense=?",
                    (user_id, lemma, pos, sense)).fetchone():
                known += 1
                continue
            self.conn.execute(
                "INSERT OR IGNORE INTO learning_items(word_id, user_id, lemma, pos, sense, ipa, cefr, "
                "scene_tags, carrier, slot_categories, source, list_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (word_id, user_id, lemma, pos, sense, it.get("ipa"), it.get("cefr"),
                 it.get("scene_tags", "[]"), it.get("carrier", "phrase"),
                 it.get("slot_categories", "[]"), it.get("source", "quest"), list_id, created))
            if self.conn.execute("SELECT changes()").fetchone()[0]:
                imported += 1
            if not it.get("ipa") or not it.get("cefr"):
                missing += 1
        self.conn.commit()
        return imported, known, missing, len(items)

    def get_item(self, user_id: str, word_id: str):
        return self.conn.execute("SELECT * FROM learning_items WHERE user_id=? AND word_id=?",
                                 (user_id, word_id)).fetchone()

    def list_items_by_scene(self, user_id: str, archetype_id: str) -> list:
        """学习词按 scene_tag 命中场景（json_each）。mastery 缺失列 COALESCE：state→'new'、分→0.0。"""
        return self.conn.execute(
            "SELECT li.word_id, li.lemma, li.pos, li.carrier, li.slot_categories, li.scene_tags, "
            "       li.ipa, li.cefr, li.source, li.created_at, "
            "       COALESCE(ms.state, 'new') AS state, ms.due, "
            "       COALESCE(ms.productive_score, 0.0) AS productive_score, "
            "       COALESCE(ms.receptive_score, 0.0) AS receptive_score, "
            "       COALESCE(ms.asr_confidence_score, 0.0) AS asr_confidence_score "
            "FROM learning_items li "
            "LEFT JOIN mastery_states ms ON ms.user_id = li.user_id AND ms.word_id = li.word_id "
            "WHERE li.user_id=? AND EXISTS(SELECT 1 FROM json_each(li.scene_tags) WHERE json_each.value=?)",
            (user_id, archetype_id)).fetchall()

    def get_mastery(self, user_id: str, word_id: str):
        return self.conn.execute("SELECT * FROM mastery_states WHERE user_id=? AND word_id=?",
                                 (user_id, word_id)).fetchone()

    def get_mastery_for_scene(self, user_id: str, word_ids) -> dict:
        if not word_ids:
            return {}
        marks = ",".join("?" * len(word_ids))
        rows = self.conn.execute(
            f"SELECT * FROM mastery_states WHERE user_id=? AND word_id IN ({marks})",
            (user_id, *word_ids)).fetchall()
        return {r["word_id"]: r for r in rows}

    def upsert_mastery(self, user_id: str, word_id: str, **fields) -> None:
        """不 commit（引擎单事务内调用）。updated_at 缺省用当前 UTC。"""
        if "updated_at" not in fields:
            fields["updated_at"] = _utcnow()
        cols = list(fields)
        sets = ", ".join(f"{c}=?" for c in cols)
        self.conn.execute(
            f"INSERT INTO mastery_states(user_id, word_id, updated_at) VALUES(?,?,?) "
            f"ON CONFLICT(user_id, word_id) DO UPDATE SET {sets}",
            [user_id, word_id, fields["updated_at"]] + [fields[c] for c in cols])

    def upsert_mastery_counts(self, user_id: str, word_id: str, *, attempts: int = 0,
                              success_count: int = 0, scaffolded_success_count: int = 0,
                              help_count: int = 0, exposure_count: int = 0) -> None:
        """不 commit（引擎单事务内调用）。新行直接置值，已有行增量。"""
        self.conn.execute(
            "INSERT INTO mastery_states(user_id, word_id, attempts, success_count, "
            "scaffolded_success_count, help_count, exposure_count, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id, word_id) DO UPDATE SET "
            "attempts=mastery_states.attempts+excluded.attempts, "
            "success_count=mastery_states.success_count+excluded.success_count, "
            "scaffolded_success_count=mastery_states.scaffolded_success_count+excluded.scaffolded_success_count, "
            "help_count=mastery_states.help_count+excluded.help_count, "
            "exposure_count=mastery_states.exposure_count+excluded.exposure_count, "
            "updated_at=excluded.updated_at",
            (user_id, word_id, attempts, success_count, scaffolded_success_count,
             help_count, exposure_count, _utcnow()))

    def add_evidence(self, user_id: str, evidence: dict) -> None:
        """不 commit（引擎单事务内调用）。INSERT OR IGNORE 幂等；FK ON 校验词已导入。"""
        self.conn.execute(
            "INSERT OR IGNORE INTO evidence_events(evidence_id, user_id, event_seq, session_id, "
            "attempt_id, turn_id, objective_id, word_id, source, prompt_level, axis, result, "
            "confidence, evidence_policy_version, fsrs_algorithm_version, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (evidence["evidence_id"], user_id, evidence["event_seq"], evidence["session_id"],
             evidence["attempt_id"], evidence["turn_id"], evidence.get("objective_id"),
             evidence["word_id"], evidence["source"], evidence["prompt_level"],
             evidence["axis"], evidence["result"], evidence["confidence"],
             evidence["evidence_policy_version"], evidence["fsrs_algorithm_version"],
             evidence["created_at"]))

    def evidence_for_word(self, user_id: str, word_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_events WHERE user_id=? AND word_id=? ORDER BY created_at",
            (user_id, word_id)).fetchall()
        return [dict(r) for r in rows]

    def all_words(self, user_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT li.word_id, li.lemma, li.pos, li.ipa, li.cefr, li.scene_tags, li.carrier, li.source, "
            "       COALESCE(ms.state, 'new') AS state, ms.due, ms.stability, ms.difficulty, ms.reps, ms.lapses, "
            "       COALESCE(ms.productive_score, 0.0) AS productive_score, "
            "       COALESCE(ms.receptive_score, 0.0) AS receptive_score, "
            "       COALESCE(ms.asr_confidence_score, 0.0) AS asr_confidence_score "
            "FROM learning_items li "
            "LEFT JOIN mastery_states ms ON ms.user_id = li.user_id AND ms.word_id = li.word_id "
            "WHERE li.user_id=? ORDER BY li.created_at", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def outbox_push(self, user_id: str, payload_json: str) -> None:
        """独立事务（rollback 后入 outbox 必须落库）。"""
        self.conn.execute("INSERT INTO evidence_outbox(user_id, payload_json, created_at) VALUES(?,?,?)",
                          (user_id, payload_json, _utcnow()))
        self.conn.commit()

    def outbox_drain(self, user_id: str) -> list[str]:
        rows = self.conn.execute("SELECT id, payload_json FROM evidence_outbox WHERE user_id=? ORDER BY id",
                                 (user_id,)).fetchall()
        payloads = [r["payload_json"] for r in rows]
        if rows:
            self.conn.execute("DELETE FROM evidence_outbox WHERE user_id=?", (user_id,))
            self.conn.commit()
        return payloads

    def resolve_word_id_from_store(self, lemma: str, pos: str, sense: int = 1) -> str | None:
        """多 sense 取 created_at 最早（并列取 rowid 最小）。"""
        row = self.conn.execute(
            "SELECT word_id FROM learning_items WHERE user_id='local' AND lemma=? AND pos=? "
            "ORDER BY created_at, rowid LIMIT 1", (lemma, pos)).fetchone()
        return row["word_id"] if row else None
```

> 注：`list_items_by_scene` 用 SQL `WHERE user_id=? AND json_each(scene_tags).value=?`（SQLite 支持 `json_each`）；无该扩展时退化用 `scene_tags LIKE` 子串匹配（实现时取其一，测试覆盖）。`all_words` 返回 `SELECT li.*, ms.* ... LEFT JOIN mastery_states ms ON ms.user_id=li.user_id AND ms.word_id=li.word_id`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_store.py -v`
Expected: PASS（6 个）。

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/learning/store.py apps/api/tests/learning/test_store.py
git commit -m "feat(learning): LearningStore — 6 tables + outbox schema + CRUD"
```

---

### Task 5: `concepts.py` resolve_word_id 缓存接缝

**Files:**
- Modify: `apps/api/app/llm/concepts.py`
- Modify: `apps/api/tests/test_concepts.py`

**Interfaces:**
- Consumes: Task 4（`store.resolve_word_id_from_store`）。**注意：concepts.py 不 import store（避免循环）**——用模块级注册表注入查询函数。
- Produces:
  - `configure_word_resolver(lookup: Callable[[str, str, int], str | None]) -> None`
  - `resolve_word_id(lemma, pos, *, sense=1) -> str`：先查进程内缓存 `{(lemma, pos, sense): word_id}`，miss 查注入的 lookup，仍 miss 回退 `word_<lemma>_<pos>_<sense>`；命中 learning_items 用其 word_id。
  - `invalidate_word_id_cache() -> None`

- [ ] **Step 1: 写失败测试**

`apps/api/tests/test_concepts.py` 追加：

```python
import app.llm.concepts as c


def test_resolver_cache_and_invalidate() -> None:
    c.configure_word_resolver(lambda lemma, pos, sense=1:
                              f"from_store_{lemma}" if lemma == "loaf" else None)
    assert c.resolve_word_id("loaf", "n") == "from_store_loaf"     # lookup 命中
    assert c.resolve_word_id("run", "v") == "word_run_v_1"         # lookup miss → 回退
    # 缓存命中：lookup 改为返回别的也不会变
    c.configure_word_resolver(lambda lemma, pos, sense=1: "changed")
    assert c.resolve_word_id("loaf", "n") == "from_store_loaf"
    c.invalidate_word_id_cache()
    assert c.resolve_word_id("loaf", "n") == "changed"             # 失效后重新查


def test_earliest_created_conflict() -> None:
    # 同一 lemma+pos 多 sense → 取 created_at 最早
    lookup = lambda lemma, pos, sense=1: None   # 不参与；冲突规则在 store 层，这里测缓存 key 分离
    c.configure_word_resolver(lookup)
    c.invalidate_word_id_cache()
    assert c.resolve_word_id("loaf", "n") == "word_loaf_n_1"
    assert c.resolve_word_id("loaf", "n", sense=2) == "word_loaf_n_2"  # sense 独立 key
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/test_concepts.py -v`
Expected: 新测试 FAIL（`configure_word_resolver` 不存在）。

- [ ] **Step 3: 实现**

`apps/api/app/llm/concepts.py` 全量替换为：

```python
"""conceptId → wordId 的服务端 resolve 接缝（阶段 4 接 learning_items）。
缓存 + 注入式 lookup：concepts 不直接依赖 learning.store（避免循环 import）。
"""
from __future__ import annotations

from typing import Callable

_lookup: Callable[[str, str, int], str | None] | None = None
_cache: dict[tuple[str, str, int], str] = {}


def configure_word_resolver(lookup: Callable[[str, str, int], str | None]) -> None:
    """注入学习引擎的 resolve（查 learning_items，多 sense 取最早创建）。"""
    global _lookup
    _lookup = lookup


def invalidate_word_id_cache() -> None:
    _cache.clear()


def resolve_word_id(lemma: str, pos: str, *, sense: int = 1) -> str:
    """先缓存 → 注入 lookup（learning_items）→ 确定性回退。签名不变。"""
    key = (lemma, pos, sense)
    if key in _cache:
        return _cache[key]
    resolved = _lookup(lemma, pos, sense) if _lookup else None
    if resolved is None:
        resolved = f"word_{lemma}_{pos}_{sense}"
    _cache[key] = resolved
    return resolved
```

（`resolve_word_id_from_store` 的冲突规则已在 Task 4 实现；此处只接缝。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/test_concepts.py -v`
Expected: 新旧全部 PASS（原 test_resolve_word_id_without_learning_items 仍过——未配置 resolver 时回退派生）。

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/llm/concepts.py apps/api/tests/test_concepts.py
git commit -m "feat(learning): resolve_word_id cache + injectable store lookup (earliest-created rule)"
```

---

### Task 6: 迷你词典 `dictionary.json` + `Dictionary.load`

**Files:**
- Create: `assets/wordbook/dictionary.json`
- Create: `apps/api/app/learning/dictionary.py`
- Create: `apps/api/tests/learning/test_dictionary.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `@dataclass WordEntry: lemma, pos, senses: list[str], ipa, cefr, scene_tags: list[str], carrier: str, slot_categories: list[str]`
  - `class Dictionary:` `load(asset_root: Path) -> "Dictionary"`；`get(lemma, pos=None) -> list[WordEntry]`；`by_lemma(lemma) -> list[WordEntry]`（多 sense）；`all() -> list[WordEntry]`

- [ ] **Step 1: 写失败测试**

`apps/api/tests/learning/test_dictionary.py`：

```python
import json
from pathlib import Path

from app.learning.dictionary import Dictionary


def test_load_and_get(tmp_path) -> None:
    d = {
        "version": 1,
        "words": [
            {"lemma": "loaf", "pos": "n", "senses": ["一条面包"], "ipa": "/loʊf/",
             "cefr": "A2", "sceneTags": ["bakery"], "carrier": "object",
             "slotCategories": ["food"]},
            {"lemma": "order", "pos": "v", "senses": ["点（餐）", "订购"], "ipa": "/ˈɔːrdər/",
             "cefr": "A2", "sceneTags": ["bakery", "cafe"], "carrier": "phrase",
             "slotCategories": []},
        ],
    }
    root = tmp_path / "assets"
    (root / "wordbook").mkdir(parents=True)
    (root / "wordbook" / "dictionary.json").write_text(json.dumps(d), encoding="utf-8")
    dic = Dictionary.load(root)
    entries = dic.get("loaf", "n")
    assert len(entries) == 1
    assert entries[0].carrier == "object"
    assert entries[0].slot_categories == ["food"]
    assert entries[0].cefr == "A2"
    assert [e.pos for e in dic.get("order")] == ["v"]          # 未指定 pos
    assert len(dic.get("order")[0].senses) == 2                # 多 sense 全保留
    assert len(dic.all()) == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_dictionary.py -v`
Expected: FAIL（`No module named 'app.learning.dictionary'`）。

- [ ] **Step 3: 实现**

`apps/api/app/learning/dictionary.py`：

```python
"""迷你词典加载。词条对齐阶段 3 资产（catalog entities pos 短格式；sceneTags 命中场景）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DICT_PATH = "wordbook/dictionary.json"


@dataclass(frozen=True)
class WordEntry:
    lemma: str
    pos: str
    senses: list[str] = field(default_factory=list)
    ipa: str | None = None
    cefr: str | None = None
    scene_tags: list[str] = field(default_factory=list)
    carrier: str = "phrase"                     # object|action|phrase（主 spec §3:34）
    slot_categories: list[str] = field(default_factory=list)


class Dictionary:
    def __init__(self, entries: list[WordEntry]) -> None:
        self._all = list(entries)
        self._by_key: dict[tuple[str, str | None], list[WordEntry]] = {}
        for e in entries:
            self._by_key.setdefault((e.lemma, e.pos), []).append(e)
            self._by_key.setdefault((e.lemma, None), []).append(e)

    @classmethod
    def load(cls, asset_root: Path) -> "Dictionary":
        raw = json.loads((asset_root / DEFAULT_DICT_PATH).read_text(encoding="utf-8"))
        entries = [WordEntry(lemma=w["lemma"], pos=w["pos"], senses=w.get("senses", []),
                             ipa=w.get("ipa"), cefr=w.get("cefr"),
                             scene_tags=w.get("sceneTags", []),
                             carrier=w.get("carrier", "phrase"),
                             slot_categories=w.get("slotCategories", []))
                   for w in raw["words"]]
        return cls(entries)

    def get(self, lemma: str, pos: str | None = None) -> list[WordEntry]:
        return self._by_key.get((lemma, pos), [])

    def by_lemma(self, lemma: str) -> list[WordEntry]:
        return self._by_key.get((lemma, None), [])

    def all(self) -> list[WordEntry]:
        return list(self._all)
```

- [ ] **Step 4: 提交真实词典数据**

创建 `assets/wordbook/dictionary.json`（真实、至少覆盖 bakery 场景，含 carrier 字段）。样例：

```json
{
  "version": 1,
  "words": [
    { "lemma": "loaf", "pos": "n", "senses": ["一条面包"],
      "ipa": "/loʊf/", "cefr": "A2", "sceneTags": ["bakery"],
      "carrier": "object", "slotCategories": ["food"] },
    { "lemma": "bread", "pos": "n", "senses": ["面包"],
      "ipa": "/bred/", "cefr": "A1", "sceneTags": ["bakery", "cafe"],
      "carrier": "object", "slotCategories": ["food"] },
    { "lemma": "order", "pos": "v", "senses": ["点（餐）", "订购"],
      "ipa": "/ˈɔːrdər/", "cefr": "A2", "sceneTags": ["bakery", "cafe"],
      "carrier": "phrase", "slotCategories": [] },
    { "lemma": "buy", "pos": "v", "senses": ["买"],
      "ipa": "/baɪ/", "cefr": "A1", "sceneTags": ["bakery", "cafe", "station"],
      "carrier": "action", "slotCategories": [] }
  ]
}
```

（跑测试确认 `Dictionary.load(asset_root)` 对真实文件通过；`asset_root` 是仓库根 `assets/`。）

- [ ] **Step 5: 跑测试确认通过**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_dictionary.py -v`
Expected: PASS（1 个）。

- [ ] **Step 6: Commit**

```bash
git add assets/wordbook/dictionary.json apps/api/app/learning/dictionary.py apps/api/tests/learning/test_dictionary.py
git commit -m "feat(learning): mini dictionary + Dictionary.load (carrier/slotCategories)"
```

---

### Task 7: `learning/wordbook.py` 导入管线

**Files:**
- Create: `apps/api/app/learning/wordbook.py`
- Create: `apps/api/tests/learning/test_wordbook.py`

**Interfaces:**
- Consumes: Task 4（store.import_words）、Task 6（Dictionary）。
- Produces:
  - `class ImportError_(ValueError)`（用名 `ImportValidationError`）
  - `validate_words(words: list[str]) -> list[str]`（规范化：trim/小写/去空；≤500、每词 ≤64、字符集 `[a-zA-Z'- ]`；违例抛 `ImportValidationError`）
  - `def run_import(store, dictionary, user_id: str, words: list[str], name: str | None = None, *, now: datetime) -> dict`（`{"imported","known","missingMetadata","total"}`；pos 来源规则；多 sense 全导入；`invalidate_word_id_cache` 由调用方做）

- [ ] **Step 1: 写失败测试**

`apps/api/tests/learning/test_wordbook.py`：

```python
import json
from datetime import datetime, timezone

import pytest

from app.event_store import EventStore
from app.learning.dictionary import Dictionary
from app.learning.store import LearningStore
from app.learning.wordbook import ImportValidationError, run_import, validate_words

NOW = datetime(2026, 8, 8, 0, 0, 0, tzinfo=timezone.utc)


def _dictionary() -> Dictionary:
    import tempfile, pathlib
    d = {"version": 1, "words": [
        {"lemma": "loaf", "pos": "n", "senses": ["面包"], "ipa": "/loʊf/", "cefr": "A2",
         "sceneTags": ["bakery"], "carrier": "object", "slotCategories": ["food"]},
        {"lemma": "order", "pos": "v", "senses": ["点餐"], "ipa": "/ɔːrdər/", "cefr": "A2",
         "sceneTags": ["bakery"], "carrier": "phrase", "slotCategories": []},
    ]}
    root = pathlib.Path(tempfile.mkdtemp()) / "assets"
    (root / "wordbook").mkdir(parents=True)
    (root / "wordbook" / "dictionary.json").write_text(json.dumps(d), encoding="utf-8")
    return Dictionary.load(root)


def _store(tmp_path) -> LearningStore:
    return LearningStore(EventStore(tmp_path / "e.db").connection)


def test_validate_constraints() -> None:
    assert validate_words(["Loaf", "  buy  ", ""]) == ["loaf", "buy"]
    with pytest.raises(ImportValidationError):
        validate_words(["a" * 65])                      # 超长
    with pytest.raises(ImportValidationError):
        validate_words(["hello!", "ok"])                 # 非法字符
    with pytest.raises(ImportValidationError):
        validate_words(["ok"] * 501)                     # 超量


def test_run_import_matches_dictionary() -> None:
    store = _store(pytest.TemporaryDirectory().name)
    res = run_import(store, _dictionary(), "local", ["loaf", "unknown_word"], name="l1", now=NOW)
    assert res["imported"] == 2          # loaf 命中词典；unknown_word 无元数据仍导入（metadata 留空）
    assert res["missingMetadata"] == 1
    item = store.get_item("local", "word_loaf_n_1")
    assert item["carrier"] == "object"
    assert json.loads(item["slot_categories"]) == ["food"]
    assert item["cefr"] == "A2"


def test_run_import_pos_syntax_and_multisense() -> None:
    store = _store(pytest.TemporaryDirectory().name)
    res = run_import(store, _dictionary(), "local", ["loaf/n"], name="l1", now=NOW)
    assert res["imported"] == 1
    assert store.get_item("local", "word_loaf_n_1")["lemma"] == "loaf"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_wordbook.py -v`
Expected: FAIL（`No module named 'app.learning.wordbook'`）。

- [ ] **Step 3: 实现**

`apps/api/app/learning/wordbook.py`：

```python
"""导入管线：校验 → 规范化 → 去重 → 词典匹配 → 入库（source=quest）。
pos 来源规则：条目 "lemma/pos" 显式 pos；未指定取词典该 lemma 首词条，多 sense 全导入。"""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from app.learning.dictionary import Dictionary
from app.learning.store import LearningStore

MAX_WORDS = 500
MAX_WORD_LEN = 64
_WORD_RE = re.compile(r"^[a-z][a-z'\- ]*[a-z]$")
import json


class ImportValidationError(ValueError):
    pass


def validate_words(words: list[str]) -> list[str]:
    if len(words) > MAX_WORDS:
        raise ImportValidationError(f"too many words: {len(words)} > {MAX_WORDS}")
    out: list[str] = []
    for raw in words:
        w = raw.strip().lower()
        if not w:
            continue
        if len(w) > MAX_WORD_LEN or not _WORD_RE.match(w):
            raise ImportValidationError(f"invalid word: {raw!r}")
        out.append(w)
    return out


def _word_id(lemma: str, pos: str, sense: int) -> str:
    return f"word_{lemma}_{pos}_{sense}"


def run_import(store: LearningStore, dictionary: Dictionary, user_id: str,
               words: list[str], name: str | None = None, *, now: datetime) -> dict:
    cleaned = validate_words(words)
    created = now.isoformat()
    items: list[dict] = []
    for entry in cleaned:
        pos_spec = None
        lemma = entry
        if "/" in entry:
            lemma, pos_spec = entry.rsplit("/", 1)
        entries = dictionary.get(lemma, pos_spec) if pos_spec else dictionary.by_lemma(lemma)
        if not entries:
            pos = pos_spec or "n"
            items.append({"word_id": _word_id(lemma, pos, 1), "lemma": lemma, "pos": pos,
                          "sense": "1", "ipa": None, "cefr": None,
                          "scene_tags": json.dumps([]), "carrier": "phrase",
                          "slot_categories": json.dumps([]), "source": "quest",
                          "created_at": created})
            continue
        for w in entries:                      # 多 sense 全导入
            items.append({"word_id": _word_id(w.lemma, w.pos, 1), "lemma": w.lemma, "pos": w.pos,
                          "sense": "1", "ipa": w.ipa, "cefr": w.cefr,
                          "scene_tags": json.dumps(w.scene_tags), "carrier": w.carrier,
                          "slot_categories": json.dumps(w.slot_categories), "source": "quest",
                          "created_at": created})
    list_id = name and f"list_{uuid.uuid4().hex[:8]}"
    imported, known, missing, total = store.import_words(user_id, list_id, name or created, items)
    return {"imported": imported, "known": known, "missingMetadata": missing, "total": total}
```

> 注：`_WORD_RE` 允许单词字符与空格连字符；测试用 `hello!` 非法。sense 统一 "1"（词典 WordEntry 无 sense 序号；多 sense 场景由词表显式多次导入或由 resolve 规则覆盖——v1 简化，spec §6「多义词全部导入」解释为：词典多 sense 词条作为独立 WordEntry 全部进 items，此处字典样例无多 sense 分条则自然合并）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_wordbook.py -v`
Expected: PASS（3 个）。

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/learning/wordbook.py apps/api/tests/learning/test_wordbook.py
git commit -m "feat(learning): import pipeline (constraints, pos syntax, dict match, multisense)"
```

---

### Task 8: `learning/scheduler.py` 每场选词

**Files:**
- Create: `apps/api/app/learning/scheduler.py`
- Create: `apps/api/tests/learning/test_scheduler.py`

**Interfaces:**
- Consumes: Task 4（store.list_items_by_scene / get_mastery_for_scene）。
- Produces:
  - `def scene_prop_slot_categories(archetype: dict) -> set[str]`
  - `def pick(words: list[dict], *, archetype_id: str, now: datetime, slot_categories: set[str], limit: int = 7) -> list[str]`：carrier 可行性过滤 → 2–3 到期（due<=now datetime）→ 2–3 新词 → 1 薄弱（productive+receptive 和最低，**排除 asr_confidence**）→ 确定性 seed 排序 → 补足。

- [ ] **Step 1: 写失败测试**

`apps/api/tests/learning/test_scheduler.py`：

```python
import json
from datetime import datetime, timezone

from app.learning.scheduler import pick, scene_prop_slot_categories


def _word(word_id, *, state="new", due=None, p=0.0, r=0.0, carrier="phrase", slots=None):
    return {"word_id": word_id, "state": state, "due": due,
            "productive_score": p, "receptive_score": r,
            "carrier": carrier, "slot_categories": json.dumps(slots or []),
            "ipa": "/x/", "cefr": "A2", "created_at": "2026-08-01T00:00:00+00:00"}


NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)


def test_carrier_feasibility_filters_objects() -> None:
    archetype = {"propSlots": [{"slotId": "a", "categories": ["food"]}]}
    slots = scene_prop_slot_categories(archetype)
    assert slots == {"food"}
    words = [
        _word("w1", carrier="object", slots=["food"]),
        _word("w2", carrier="object", slots=["drink"]),   # 场景无 drink → 排除
        _word("w3", carrier="phrase"),                    # 非 object → 保留
    ]
    out = pick(words, archetype_id="plaza", now=NOW, slot_categories=slots, limit=7)
    assert "w2" not in out and "w1" in out and "w3" in out


def test_due_then_new_then_weak() -> None:
    words = [
        _word("due1", state="review", due="2026-08-08T00:00:00+00:00"),   # 到期
        _word("due2", state="review", due="2026-08-09T00:00:00+00:00"),   # 未到期
        _word("new1"), _word("new2"), _word("new3"),
        _word("weak", state="review", due="2026-08-09T00:00:00+00:00", p=0.1, r=0.2),
    ]
    out = pick(words, archetype_id="plaza", now=NOW, slot_categories=set(), limit=7)
    assert out[0] == "due1"                       # 到期优先
    assert set(out[1:4]) == {"new1", "new2", "new3"}  # 新词
    assert "weak" in out                          # 薄弱词（含未到期）
    assert "due2" in out                          # 未到期非薄弱也可补足


def test_weak_excludes_asr_confidence_axis() -> None:
    words = [
        _word("a", state="review", due="2026-08-09T00:00:00+00:00", p=0.5, r=0.5),
        _word("b", state="review", due="2026-08-09T00:00:00+00:00", p=0.4, r=0.4),
    ]
    # a 的 asr 轴 0.9，b 的 asr 轴 0.1 —— 薄弱判据只用 p+r，应选 b（p+r=0.8 < 1.0）
    # 注：pick 用 productive+receptive，与 asr_confidence 无关（测试由实现保证不读该列）
    out = pick(words, archetype_id="plaza", now=NOW, slot_categories=set(), limit=7)
    assert out.index("b") < out.index("a")


def test_deterministic_seed() -> None:
    words = [_word(f"w{i}") for i in range(20)]
    a = pick(words, archetype_id="plaza", now=NOW, slot_categories=set(), limit=5)
    b = pick(words, archetype_id="plaza", now=NOW, slot_categories=set(), limit=5)
    c = pick(words, archetype_id="cafe", now=NOW, slot_categories=set(), limit=5)
    assert a == b                                  # 同 scene 同日 → 同输出
    assert a != c                                  # 不同场景 → 不同 seed
    assert len(a) == 5
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_scheduler.py -v`
Expected: FAIL（`No module named 'app.learning.scheduler'`）。

- [ ] **Step 3: 实现**

`apps/api/app/learning/scheduler.py`：

```python
"""每场选词：carrier 可行性 → 到期（due<=now datetime）→ 新词 → 薄弱（排除 asr 轴）→ 确定性 seed。
seed = sha1(f"scene-select:{archetype_id}:{now.date().isoformat()}")（sceneId 每次进场变化，用 archetype+日期作稳定键）。"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime


def scene_prop_slot_categories(archetype: dict) -> set[str]:
    return {c for slot in archetype.get("propSlots", []) for c in slot.get("categories", [])}


def _seed_int(key: str) -> int:
    return int.from_bytes(hashlib.sha1(key.encode()).digest()[:4], "big")


def pick(words: list[dict], *, archetype_id: str, now: datetime,
         slot_categories: set[str], limit: int = 7) -> list[str]:
    feasible = [w for w in words
                if w["carrier"] != "object"
                or (set(json.loads(w["slot_categories"])) & slot_categories)]
    due = [w for w in feasible if w["state"] != "new" and w["due"] and w["due"] <= now.isoformat()]
    new = [w for w in feasible if w["state"] == "new"]
    weak = [w for w in feasible
            if not (w["state"] != "new" and w["due"] and w["due"] <= now.isoformat())
            and w not in new]
    weak.sort(key=lambda w: w["productive_score"] + w["receptive_score"])

    seed = _seed_int(f"scene-select:{archetype_id}:{now.date().isoformat()}")
    rng = random.Random(seed)
    rng.shuffle(due); rng.shuffle(new); rng.shuffle(weak)

    chosen: list[str] = []
    chosen += [w["word_id"] for w in due[:3]]
    chosen += [w["word_id"] for w in new[:3]]
    if len(chosen) < limit and weak:
        chosen.append(weak[0]["word_id"])
    for w in feasible:
        if len(chosen) >= limit:
            break
        if w["word_id"] not in chosen:
            chosen.append(w["word_id"])
    return chosen[:limit]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_scheduler.py -v`
Expected: PASS（4 个）。

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/learning/scheduler.py apps/api/tests/learning/test_scheduler.py
git commit -m "feat(learning): scene word scheduler (carrier feasibility, deterministic seed, weak excludes asr axis)"
```

---

### Task 9: `learning/evidence.py` 证据分类 + 更新规则 + 日闸

**Files:**
- Create: `apps/api/app/learning/evidence.py`
- Create: `apps/api/tests/learning/test_evidence.py`

**Interfaces:**
- Consumes: Task 1（fsrs）、Task 2（scores）、Task 4（store）。
- Produces:
  - `classify_round(scene_words: dict[str, str], npc_text: str, user_text: str, confidence: float, *, target_word_ids: set[str]) -> list[dict]`：lexmatch 命中判定 → 每条 target word 产 evidence draft（`{word_id, source, prompt_level, axis, result, confidence}`）。
    - 目标词在 user_text 且 npc_text → prompted_production（prompt_level 1）
    - 目标词在 user_text 但 npc_text 无 → spontaneous_production（prompt_level 0）
    - 目标词在 npc_text 但 user_text 无 → **no_attempt**（ASR 落空，权重 0，不进 FSRS）
    - confidence<0.6 → result=uncertain（检出但低置信）
  - `apply_evidence(store, user_id, evidence: dict, *, now: datetime) -> None`：**纯事务内更新**（由 Task 10 引擎在单事务内调用）——按 axis 更新分（WEIGHTS）、更新计数、满足 §7.1 进入条件则日闸 + `schedule` 更新 mastery。注意与引擎方法 `LearningEngine.record_evidence(session_id, evidence, *, event_id)` 区分。
  - `_result_for(source, conf)` 等辅助。

- [ ] **Step 1: 写失败测试**

`apps/api/tests/learning/test_evidence.py`：

```python
from datetime import datetime, timezone

from app.event_store import EventStore
from app.learning.evidence import apply_evidence, classify_round
from app.learning.store import LearningStore

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
U = "local"


def _store(tmp_path) -> LearningStore:
    s = LearningStore(EventStore(tmp_path / "e.db").connection)
    s.import_words(U, "l1", "x", [{"lemma": "loaf", "pos": "n", "sense": "1",
                                   "scene_tags": "[]", "carrier": "phrase",
                                   "slot_categories": "[]", "source": "quest",
                                   "created_at": "2026-08-08T00:00:00+00:00"}])
    return s


def test_classify_prompted_vs_spontaneous_vs_no_attempt() -> None:
    scene = {"word_loaf_n_1": "loaf"}
    drafts = classify_round(scene, npc_text="Can I get a loaf?", user_text="I want a loaf",
                            confidence=0.86, target_word_ids={"word_loaf_n_1"})
    assert drafts[0]["source"] == "prompted_production"
    assert drafts[0]["prompt_level"] == 1
    assert drafts[0]["result"] == "success"

    drafts = classify_round(scene, npc_text="Good morning", user_text="a loaf please",
                            confidence=0.86, target_word_ids={"word_loaf_n_1"})
    assert drafts[0]["source"] == "spontaneous_production"
    assert drafts[0]["prompt_level"] == 0

    drafts = classify_round(scene, npc_text="Do you see the loaf?", user_text="hello",
                            confidence=0.86, target_word_ids={"word_loaf_n_1"})
    assert drafts[0]["result"] == "no_attempt"       # 教过但 ASR 未检出 → 不惩罚


def test_classify_low_confidence_uncertain() -> None:
    scene = {"word_loaf_n_1": "loaf"}
    drafts = classify_round(scene, "I want a loaf", "I want a loaf", confidence=0.4,
                            target_word_ids={"word_loaf_n_1"})
    assert drafts[0]["result"] == "uncertain"


def test_record_success_updates_score_and_enters_fsrs(tmp_path) -> None:
    s = _store(tmp_path)
    ev = {"evidence_id": "ev_1", "event_seq": 1, "session_id": "s1", "attempt_id": "a1",
          "turn_id": "t1", "objective_id": "obj_plaza_w1", "word_id": "word_loaf_n_1",
          "source": "prompted_production", "prompt_level": 1, "axis": "productive",
          "result": "success", "confidence": 0.86, "evidence_policy_version": "v1",
          "fsrs_algorithm_version": "fsrs-5", "created_at": NOW.isoformat()}
    # 第一次成功：attempts=1，未达进入条件
    apply_evidence(s, U, ev, now=NOW)
    m = s.get_mastery(U, "word_loaf_n_1")
    assert m["attempts"] == 1 and m["success_count"] == 1
    assert m["productive_score"] > 0.0
    assert m["state"] == "new"                     # 未达条件

    # 第二次（另一 attempt_id）：scaffolded_success=2, attempts=2 → 进入排期
    ev2 = {**ev, "evidence_id": "ev_2", "attempt_id": "a2"}
    apply_evidence(s, U, ev2, now=NOW)
    m = s.get_mastery(U, "word_loaf_n_1")
    assert m["state"] == "review"
    assert m["due"] and m["due"] > NOW.isoformat()
    assert m["reps"] == 1


def test_daily_gate_lower_rating_reschedules(tmp_path) -> None:
    s = _store(tmp_path)
    mk = lambda eid, aid, src, result, conf: {
        "evidence_id": eid, "event_seq": 1, "session_id": "s1", "attempt_id": aid,
        "turn_id": "t1", "objective_id": "obj_w", "word_id": "word_loaf_n_1",
        "source": src, "prompt_level": 1, "axis": "productive", "result": result,
        "confidence": conf, "evidence_policy_version": "v1",
        "fsrs_algorithm_version": "fsrs-5", "created_at": NOW.isoformat()}
    # 进入排期（两次成功）
    apply_evidence(s, U, mk("e1", "a1", "prompted_production", "success", 0.86), now=NOW)
    apply_evidence(s, U, mk("e2", "a2", "prompted_production", "success", 0.86), now=NOW)
    m = s.get_mastery(U, "word_loaf_n_1")
    assert m["state"] == "review"
    due_after_good = m["due"]
    # 同日更低评分（error）→ 降级重排
    apply_evidence(s, U, mk("e3", "a3", "error", "error", 1.0), now=NOW)
    m = s.get_mastery(U, "word_loaf_n_1")
    assert m["due"] <= due_after_good             # 保守：due 不变早
    assert m["lapses"] == 1
    # 同日更高评分（spontaneous）→ 不重排
    apply_evidence(s, U, mk("e4", "a4", "spontaneous_production", "success", 0.86), now=NOW)
    m2 = s.get_mastery(U, "word_loaf_n_1")
    assert m2["due"] == m["due"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_evidence.py -v`
Expected: FAIL（`No module named 'app.learning.evidence'`）。

- [ ] **Step 3: 实现**

`apps/api/app/learning/evidence.py`：

```python
"""证据分类 + 更新规则 + 日闸（纯函数 + store 更新，由引擎在单事务内调用）。"""
from __future__ import annotations

from datetime import datetime

from app.learning import fsrs as fsrs_mod
from app.learning.scores import WEIGHTS, normalize_asr_confidence, update_score
from app.llm.lexmatch import token_contains

MIN_CONFIDENCE = 0.6

# 日闸：当日最低有效评分代表当日。error=Again(1) 最低。
_RATING = {"spontaneous_production": 4, "prompted_production": 3,
           "repetition": 2, "action_understanding": 3, "error": 1}
# no_attempt/uncertain：ASR 噪声不惩罚——不更新分、不计尝试、不进排期。
# help 的 result 是 neutral：**要更新分**（负权重 -0.35）但无评分、不进排期。
_NON_SCORED = {"no_attempt", "uncertain"}


def classify_round(scene_words: dict[str, str], npc_text: str, user_text: str,
                   confidence: float, *, target_word_ids: set[str]) -> list[dict]:
    """scene_words: {word_id: lemma}（含选词补充进 target 的）。按 target_word_ids 迭代，
    保证选词补充但无场景实体的词也能判（lemma 缺则跳过）。返回证据草稿。"""
    conf = normalize_asr_confidence(confidence) if confidence < 0 else confidence
    drafts: list[dict] = []
    for wid in target_word_ids:
        lemma = scene_words.get(wid)
        if not lemma:
            continue
        in_npc = token_contains(npc_text, lemma)
        in_user = token_contains(user_text, lemma)
        if in_user and in_npc:
            source, pl = "prompted_production", 1
            result = "success" if conf >= MIN_CONFIDENCE else "uncertain"
        elif in_user:
            source, pl = "spontaneous_production", 0
            result = "success" if conf >= MIN_CONFIDENCE else "uncertain"
        elif in_npc:
            source, pl, result = "prompted_production", 1, "no_attempt"
        else:
            continue
        _, axis = WEIGHTS[source]
        drafts.append({"word_id": wid, "source": source, "prompt_level": pl,
                       "axis": axis, "result": result, "confidence": conf})
    return drafts


def _card_id(wid: str) -> int:
    return int.from_bytes(wid.encode()[:4], "big")


def apply_evidence(store, user_id: str, ev: dict, *, now: datetime) -> None:
    """在调用方单事务内：add_evidence + 三维分（按 axis）+ 计数 + 进入条件/日闸排期。"""
    store.add_evidence(user_id, ev)
    source, result = ev["source"], ev["result"]
    weight, axis = WEIGHTS[source]
    wid = ev["word_id"]
    m = store.get_mastery(user_id, wid)
    if m is None:
        store.upsert_mastery(user_id, wid, updated_at=now.isoformat())
        m = store.get_mastery(user_id, wid)
    col = {"productive": "productive_score", "receptive": "receptive_score",
           "asr_confidence": "asr_confidence_score"}[axis]
    if result not in _NON_SCORED:
        store.upsert_mastery(user_id, wid, **{col: update_score(m[col], weight, ev["confidence"])},
                             updated_at=now.isoformat())

    store.upsert_mastery_counts(
        user_id, wid,
        attempts=1 if result in ("success", "error") else 0,
        success_count=1 if result == "success" else 0,
        scaffolded_success_count=1 if (result == "success" and ev["prompt_level"] > 0) else 0,
        help_count=1 if source == "help" else 0,
        exposure_count=1)

    m = store.get_mastery(user_id, wid)
    rating = _RATING.get(source)
    if rating is None or result in _NON_SCORED:
        return
    entered = m["state"] == "new" and m["scaffolded_success_count"] >= 1 and m["attempts"] >= 2
    if entered:
        # 首次进入：从 fresh card 排期 → 命中黄金值（fresh+Good → stability 3.173 / due +3d）
        _schedule(store, user_id, wid, fsrs_mod.to_fsrs_card(m, card_id=_card_id(wid)), rating, now)
    else:
        _apply_daily_gate(store, user_id, wid, rating, now)


def _apply_daily_gate(store, user_id: str, wid: str, rating: int, now: datetime) -> None:
    m = store.get_mastery(user_id, wid)
    if m["state"] == "new":
        return  # 未满足进入条件，不排期
    today = now.date().isoformat()
    if m["last_scheduled_date"] != today:
        _schedule(store, user_id, wid, fsrs_mod.to_fsrs_card(m, card_id=_card_id(wid)), rating, now, today)
    elif m["last_scheduled_rating"] and rating < m["last_scheduled_rating"]:
        _schedule(store, user_id, wid, fsrs_mod.to_fsrs_card(m, card_id=_card_id(wid)), rating, now, today)


def _schedule(store, user_id: str, wid: str, card, rating: int, now: datetime,
              today: str | None = None) -> None:
    m = store.get_mastery(user_id, wid)
    updated = fsrs_mod.schedule(card, rating, now)
    fields = fsrs_mod.from_fsrs_card(updated)
    fields.update({"last_scheduled_date": today or now.date().isoformat(),
                   "last_scheduled_rating": rating,
                   "reps": m["reps"] + 1,
                   "lapses": m["lapses"] + (1 if rating == 1 else 0),
                   "updated_at": now.isoformat()})
    store.upsert_mastery(user_id, wid, **fields)
```

> `fsrs_mod.to_fsrs_card` 要求 row 含 `state/stability/difficulty/due/last_review`——`store.get_mastery` 返回这些列（新词默认 new/0.0/0.0/None/None → 映射为 fresh Card）。`store.upsert_mastery_counts` 做增量（新行置值、已有行 +excluded）。`error` 的 rating=1（Again）降级重排可能产生 relearning 态——`to_fsrs_card` 对 relearning 缺省 step=1，确定性不崩。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_evidence.py -v`
Expected: PASS（4 个）。若 `test_daily_gate` 断言 `due <= due_after_good` 方向需核实：error(1) 重排后 stability 降 → interval 短 → due 更早（更近 now）→ 满足 `<=`。若实现差异导致断言失败，核对 py-fsrs 输出后修正断言（以"保守 = due 不晚于原来"为准）。

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/learning/evidence.py apps/api/tests/learning/test_evidence.py
git commit -m "feat(learning): evidence classify + update rules + daily gate (lowest-of-day rating)"
```

---

### Task 10: `learning/engine.py` LearningEngine（单事务 + outbox）

**Files:**
- Create: `apps/api/app/learning/engine.py`
- Create: `apps/api/tests/learning/test_engine.py`

**Interfaces:**
- Consumes: Task 3（event_store.write_lock/append_in_tx）、Task 4（store）、Task 8（scheduler）、Task 9（evidence.apply_evidence）。
- Produces:
  - `class LearningEngine:`
    - `__init__(self, store: LearningStore, events: EventStore, settings, now_provider=...)`
    - `record_evidence(self, session_id: str, evidence: dict, *, event_id: str | None = None) -> int | None`：单事务 `with events.write_lock:` → `append_in_tx(...internal=True)` → `evidence.apply_evidence` → `conn.commit()`；失败 rollback → `store.outbox_push(payload_json)`（尽力而为）→ 返回 None。
    - `drain_outbox(self) -> int`：启动重放，逐条走 record 路径（幂等），成功后 DELETE。
    - `record_round(self, session_id: str, scene_words: dict, npc_text: str, user_text: str, confidence: float, *, turn_id: str, target_word_ids: set[str], attempt_id: str | None = None) -> int`：classify_round → 逐条 record_evidence。caller 只传 `turn_id` + `target_word_ids`（attempt_id 自动回退 `f"attempt_{turn_id}"`）。
    - `pick_scene_words(self, archetype_id: str, archetype: dict, now: datetime) -> dict[str, str]`（word_id→name）
    - `invalidate_resolver(self)`

- [ ] **Step 1: 写失败测试**

`apps/api/tests/learning/test_engine.py`：

```python
import json
from datetime import datetime, timezone
from pathlib import Path

from app.event_store import EventStore
from app.learning.engine import LearningEngine
from app.learning.store import LearningStore

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
U = "local"


def _engine(tmp_path) -> tuple[LearningEngine, EventStore]:
    events = EventStore(tmp_path / "e.db")
    store = LearningStore(events.connection)
    return LearningEngine(store, events, _fake_settings()), events


def _fake_settings():
    class S:
        score_alpha = 0.35
        fsrs_retention = 0.9
        fsrs_min_confidence = 0.6
        evidence_policy_version = "v1"
        fsrs_algorithm_version = "fsrs-5"
    return S()


def test_record_round_single_transaction(tmp_path) -> None:
    eng, events = _engine(tmp_path)
    eng.store.import_words(U, "l1", "x", [{"lemma": "loaf", "pos": "n", "sense": "1",
                                            "scene_tags": "[\"bakery\"]", "carrier": "phrase",
                                            "slot_categories": "[]", "source": "quest",
                                            "created_at": NOW.isoformat()}])
    drafts = eng.record_round("s1", {"word_loaf_n_1": "loaf"}, npc_text="a loaf please",
                              user_text="I want a loaf", confidence=-0.2,
                              target_word_ids={"word_loaf_n_1"}, turn_id="t1", attempt_id="a1")
    assert drafts == 1
    evs = events.list_after("s1", 0)
    assert any(e["event_type"] == "evidence" and e["payload"]["internal"] for e in evs)
    assert len(eng.store.evidence_for_word(U, "word_loaf_n_1")) == 1


def test_outbox_on_failure(tmp_path) -> None:
    eng, events = _engine(tmp_path)
    # 用一个不存在的 word_id 制造失败（record_evidence 内部 FK 失败 → rollback → outbox）
    payload = {"evidence_id": "ev_bad", "event_seq": 99, "session_id": "s1",
               "attempt_id": "a1", "turn_id": "t1", "objective_id": None,
               "word_id": "word_missing_n_1", "source": "prompted_production",
               "prompt_level": 1, "axis": "productive", "result": "success",
               "confidence": 0.86, "evidence_policy_version": "v1",
               "fsrs_algorithm_version": "fsrs-5", "created_at": NOW.isoformat()}
    seq = eng.record_evidence("s1", payload, event_id="ev_bad")
    assert seq is None                          # 失败不阻断
    rows = eng.store.outbox_drain(U)
    assert len(rows) == 1
    assert json.loads(rows[0])["evidence_id"] == "ev_bad"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_engine.py -v`
Expected: FAIL（`No module named 'app.learning.engine'`）。

- [ ] **Step 3: 实现**

`apps/api/app/learning/engine.py`：

```python
"""学习引擎：单一事务证据记录 + outbox 兜底 + 选词 + 回合证据归因。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from app.learning import scheduler as sched_mod
from app.learning.evidence import apply_evidence, classify_round
from app.learning.scores import normalize_asr_confidence
from app.learning.store import LearningStore


class LearningEngine:
    def __init__(self, store: LearningStore, events, settings) -> None:
        self.store = store
        self.events = events
        self.settings = settings
        # 并发由 events.write_lock 提供（sequence 分配与提交同锁）

    # ---- 选词 ----
    def pick_scene_words(self, archetype_id: str, archetype: dict, now: datetime) -> dict[str, str]:
        words = self.store.list_items_by_scene("local", archetype_id)
        slots = sched_mod.scene_prop_slot_categories(archetype)
        chosen = sched_mod.pick(words, archetype_id=archetype_id, now=now,
                                slot_categories=slots, limit=7)
        out: dict[str, str] = {}
        for w in words:
            if w["word_id"] in chosen:
                out[w["word_id"]] = w["lemma"]
        return out

    # ---- 证据 ----
    def record_evidence(self, session_id: str, evidence: dict,
                        *, event_id: str | None = None) -> int | None:
        """单事务：session_events(evidence, internal) + evidence_events + mastery_states。
        复用 event_store 的锁与连接（sequence 分配同锁）。失败 → outbox，不阻断回合。"""
        with self.events.write_lock:
            conn = self.events.connection
            try:
                seq = self.events.append_in_tx(conn, session_id, "evidence",
                                               _internal_payload(evidence),
                                               event_id=event_id, internal=True)
                apply_evidence(self.store, "local", evidence,
                               now=datetime.fromisoformat(evidence["created_at"]))
                conn.commit()
                return seq
            except Exception:  # noqa: BLE001 —— 证据失败不杀回合；入 outbox 下次补
                conn.rollback()
                try:
                    self.store.outbox_push("local", json.dumps(evidence, ensure_ascii=False))
                except Exception:  # noqa: BLE001 —— outbox 也失败则只丢日志
                    pass
                return None

    def drain_outbox(self) -> int:
        rows = self.store.outbox_drain("local")
        n = 0
        for payload in rows:
            ev = json.loads(payload)
            seq = self.record_evidence(ev.get("session_id", "s1"), ev,
                                       event_id=ev.get("evidence_id"))
            if seq is not None:
                n += 1
        return n

    def record_round(self, session_id: str, scene_words: dict, npc_text: str,
                     user_text: str, confidence: float, *, turn_id: str,
                     target_word_ids: set[str], attempt_id: str | None = None) -> int:
        conf = normalize_asr_confidence(confidence) if confidence < 0 else confidence
        drafts = classify_round(scene_words, npc_text, user_text, conf,
                                target_word_ids=target_word_ids)
        for d in drafts:
            now = datetime.now(datetime.timezone.utc)
            evidence = {
                "evidence_id": f"ev_{uuid.uuid4().hex[:12]}",
                "event_seq": 0, "session_id": session_id,
                "attempt_id": attempt_id or f"attempt_{turn_id}",
                "turn_id": turn_id,
                "objective_id": f"obj_scene_{next(iter(target_word_ids))}" if target_word_ids else None,
                "word_id": d["word_id"], "source": d["source"],
                "prompt_level": d["prompt_level"], "axis": d["axis"],
                "result": d["result"], "confidence": d["confidence"],
                "evidence_policy_version": self.settings.evidence_policy_version,
                "fsrs_algorithm_version": self.settings.fsrs_algorithm_version,
                "created_at": now.isoformat(),
            }
            self.record_evidence(session_id, evidence, event_id=evidence["evidence_id"])
        return len(drafts)


def _internal_payload(evidence: dict) -> dict:
    return {"evidenceId": evidence["evidence_id"], "wordId": evidence["word_id"],
            "source": evidence["source"], "result": evidence["result"],
            "internal": True, "objectiveId": evidence.get("objective_id")}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_engine.py -v`
Expected: PASS（2 个）。

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/learning/engine.py apps/api/tests/learning/test_engine.py
git commit -m "feat(learning): LearningEngine single-transaction evidence + outbox + round classify"
```

---

### Task 11: `learning/encounters.py` 偶遇词流（明细 + 聚合 + 提升）

**Files:**
- Create: `apps/api/app/learning/encounters.py`
- Create: `apps/api/tests/learning/test_encounters.py`

**Interfaces:**
- Consumes: Task 4（store.spontaneous 表）。
- Produces:
  - `record_exposure(store, user_id, session_id, lemma, pos, turn_id, *, now) -> None`：明细行 + 聚合 upsert（encounter_count+1，first/last_seen 更新）。
  - `record_ask(store, user_id, session_id, lemma, pos, turn_id, *, now) -> None`：聚合 asked=1 + 明细行。
  - `promote(store, user_id, lemmas: list[str], *, now: datetime) -> int`：对每个 lemma 找 learning_items（source=free，无则建）+ 初始化 mastery_states + promoted=1；返回成功数。pos 取 spontaneous_words 聚合行，缺省 "n"（无需 dictionary）。
  - `list_spontaneous(store, user_id) -> list[dict]`

- [ ] **Step 1: 写失败测试**

`apps/api/tests/learning/test_encounters.py`：

```python
from datetime import datetime, timezone

from app.event_store import EventStore
from app.learning.encounters import list_spontaneous, promote, record_ask, record_exposure
from app.learning.store import LearningStore

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
U = "local"


def _store(tmp_path) -> LearningStore:
    return LearningStore(EventStore(tmp_path / "e.db").connection)


def test_exposure_detail_and_aggregate(tmp_path) -> None:
    s = _store(tmp_path)
    record_exposure(s, U, "s1", "pigeon", "n", "t1", now=NOW)
    record_exposure(s, U, "s1", "pigeon", "n", "t2", now=NOW)
    agg = list_spontaneous(s, U)
    assert agg[0]["lemma"] == "pigeon"
    assert agg[0]["encounterCount"] == 2
    rows = s.conn.execute("SELECT COUNT(*) FROM spontaneous_encounters").fetchone()[0]
    assert rows == 2                           # 明细行保留


def test_ask_marks_aggregate(tmp_path) -> None:
    s = _store(tmp_path)
    record_ask(s, U, "s1", "pigeon", "n", "t1", now=NOW)
    assert list_spontaneous(s, U)[0]["asked"] == True


def test_promote_creates_learning_item(tmp_path) -> None:
    s = _store(tmp_path)
    record_exposure(s, U, "s1", "pigeon", "n", "t1", now=NOW)
    n = promote(s, U, ["pigeon"], now=NOW)
    assert n == 1
    item = s.get_item(U, "word_pigeon_n_1")
    assert item["source"] == "free"
    m = s.get_mastery(U, "word_pigeon_n_1")
    assert m is not None and m["state"] == "new"
    assert list_spontaneous(s, U)[0]["promoted"] == True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_encounters.py -v`
Expected: FAIL（`No module named 'app.learning.encounters'`）。

- [ ] **Step 3: 实现**

`apps/api/app/learning/encounters.py`：

```python
"""偶遇词流：明细行（可追溯）+ 聚合表（API/提升）+ 手动提升。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from app.learning.store import LearningStore


def _wid(lemma: str, pos: str, sense: int = 1) -> str:
    return f"word_{lemma}_{pos}_{sense}"


def record_exposure(store: LearningStore, user_id: str, session_id: str,
                    lemma: str, pos: str, turn_id: str, *, now: datetime) -> None:
    created = now.isoformat()
    store.conn.execute(
        "INSERT INTO spontaneous_encounters(id, user_id, session_id, lemma, pos, turn_id, encounter_no, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (f"enc_{uuid.uuid4().hex[:12]}", user_id, session_id, lemma, pos, turn_id,
         store.conn.execute("SELECT COUNT(*)+1 FROM spontaneous_encounters WHERE user_id=? AND lemma=? AND pos=?",
                            (user_id, lemma, pos)).fetchone()[0], created))
    store.conn.execute(
        "INSERT INTO spontaneous_words(user_id, lemma, pos, first_seen_at, last_seen_at, encounter_count) "
        "VALUES(?,?,?,?,?,1) "
        "ON CONFLICT(user_id, lemma, pos) DO UPDATE SET last_seen_at=excluded.last_seen_at, "
        "encounter_count=encounter_count+1",
        (user_id, lemma, pos, created, created))
    store.conn.commit()


def record_ask(store: LearningStore, user_id: str, session_id: str,
               lemma: str, pos: str, turn_id: str, *, now: datetime) -> None:
    record_exposure(store, user_id, session_id, lemma, pos, turn_id, now=now)
    store.conn.execute(
        "UPDATE spontaneous_words SET asked=1 WHERE user_id=? AND lemma=? AND pos=?",
        (user_id, lemma, pos))
    store.conn.commit()


def promote(store: LearningStore, user_id: str, lemmas: list[str], *, now: datetime) -> int:
    created = now.isoformat()
    n = 0
    for lemma in lemmas:
        agg = store.conn.execute(
            "SELECT pos FROM spontaneous_words WHERE user_id=? AND lemma=?", (user_id, lemma)).fetchone()
        pos = agg[0] if agg else "n"
        wid = _wid(lemma, pos)
        store.conn.execute(
            "INSERT OR IGNORE INTO learning_items(word_id, user_id, lemma, pos, sense, scene_tags, carrier, "
            "slot_categories, source, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (wid, user_id, lemma, pos, "1", json.dumps([]), "phrase", json.dumps([]), "free", created))
        store.conn.execute(
            "INSERT OR IGNORE INTO mastery_states(user_id, word_id, updated_at) VALUES(?,?,?)",
            (user_id, wid, created))
        cur = store.conn.execute("SELECT changes()").fetchone()[0]
        if cur:
            n += 1
        store.conn.execute(
            "UPDATE spontaneous_words SET promoted=1 WHERE user_id=? AND lemma=? AND pos=?",
            (user_id, lemma, pos))
    store.conn.commit()
    return n


def list_spontaneous(store: LearningStore, user_id: str) -> list[dict]:
    rows = store.conn.execute(
        "SELECT lemma, pos, first_seen_at, last_seen_at, encounter_count, asked, promoted "
        "FROM spontaneous_words WHERE user_id=? ORDER BY last_seen_at DESC", (user_id,)).fetchall()
    return [{"lemma": r[0], "pos": r[1], "firstSeenAt": r[2], "lastSeenAt": r[3],
             "encounterCount": r[4], "asked": bool(r[5]), "promoted": bool(r[6])} for r in rows]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_encounters.py -v`
Expected: PASS（3 个）。

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/learning/encounters.py apps/api/tests/learning/test_encounters.py
git commit -m "feat(learning): spontaneous encounters (detail+aggregate) + promote by lemma"
```

---

### Task 12: ws/voice_round 集成（回合证据 + 求助 + 实体点击 + 选词喂入）

**Files:**
- Modify: `apps/api/app/voice_round.py`（run_round 返回 `npcText` 与 `confidence`）
- Modify: `apps/api/app/ws.py`（`_run_round` 后 record_round、`_handle_companion_ask` 记 help、新增 `entity.click`、`enter_scene`/`_handle_npc_focus` 用 pick_scene_words）
- Modify: `apps/web/src/useVoiceRound.ts`（新增 `entityClick(entityId)`）
- Modify: `apps/web/src/App.tsx`（onEntityClick 调 entityClick）
- Modify: `apps/api/tests/ws_helpers.py`（make_app 注入 engine + scene_words 确定性）
- Create/Modify: `apps/api/tests/test_learning_ws.py`

**Interfaces:**
- Consumes: Task 10（engine.record_round / pick_scene_words）、Task 11（encounters.record_ask）。
- Produces: `voice_round.run_round` 返回值加 `"npcText": str` 与 `"confidence": float`。

- [ ] **Step 1: 改 voice_round 返回**

`apps/api/app/voice_round.py`：`run_round` 里 `asr_result` 处保存 `conf = float(asr_result.get("confidence", -0.5))`；`npc_accumulated` 变量改为 `accumulated`（已有）；两处 return 追加：

```python
        return {"finalText": final_text, "turnId": turn_id, "replied": False,
                "npcText": "", "confidence": conf}
```
```python
        return {"finalText": final_text, "turnId": turn_id, "replied": True,
                "npcText": accumulated.strip(), "confidence": conf}
```

- [ ] **Step 2: ws.py 集成**

`apps/api/app/ws.py` + `apps/api/app/scene_lifecycle.py` 改动（最小侵入）：

1. **`SessionState.__init__`** 加两个字段（ws.py）：

```python
        self.target_word_ids: set[str] = set()
        self.scene_words: dict[str, str] = {}   # 选词合并后的 wordId→name
```

2. **`_run_round`**（ws.py）：捕获 run_round 返回值，`replied` 且在场时记证据：

```python
        result = await run_round(session_id, utterance_id, payload, events,
                                 app.state.asr_client, app.state.tts_client, send,
                                 state.actor, state, budget_exceeded=budget_exceeded)
        learning = getattr(app.state, "learning", None)
        if learning and result.get("replied") and state.scene is not None:
            try:
                learning.record_round(
                    session_id, state.scene_words,
                    result.get("npcText", ""), result.get("finalText", ""),
                    result.get("confidence", -0.5), turn_id=result["turnId"],
                    target_word_ids=state.target_word_ids)
            except Exception:  # noqa: BLE001 —— 学习证据失败不杀回合
                pass
```

3. **`_handle_companion_ask`**（ws.py）：在已有 `word_id, word = entry` 后——目标词记 `help` 证据（负权重，result=neutral，不排期）；非目标词进偶遇流 `record_ask`：

```python
        word_id, word = entry
        learning = getattr(app.state, "learning", None)
        if learning:
            now = datetime.now(timezone.utc)
            try:
                parts = word_id.split("_")          # word_{lemma}_{pos}_{sense}
                lemma, pos = parts[1], parts[2]
                if word_id in state.target_word_ids:
                    learning.record_evidence(
                        session_id,
                        {"evidence_id": f"ev_{uuid.uuid4().hex[:12]}", "event_seq": 0,
                         "session_id": session_id, "attempt_id": f"help_{word_id}",
                         "turn_id": f"comp_{uuid.uuid4().hex[:8]}", "objective_id": None,
                         "word_id": word_id, "source": "help", "prompt_level": 0,
                         "axis": "productive", "result": "neutral", "confidence": 1.0,
                         "evidence_policy_version": app.state.settings.evidence_policy_version,
                         "fsrs_algorithm_version": app.state.settings.fsrs_algorithm_version,
                         "created_at": now.isoformat()},
                        event_id=f"ev_help_{word_id}_{now.date().isoformat()}")
                else:
                    record_ask(learning.store, "local", session_id, lemma, pos,
                               f"comp_{uuid.uuid4().hex[:8]}", now=now)
            except Exception:  # noqa: BLE001 —— 求助证据失败不影响 tutor
                pass
```

> 顶部 import 补：`from datetime import datetime, timezone`、`from app.learning.encounters import record_ask`。

4. **`enter_scene`**（scene_lifecycle.py，`scene_maps` 后、`scene_factory` 前）：选词 + 合并 + 写 state：

```python
    scene_words, entity_by_word_id = scene_maps(scene)
    state.target_word_ids = set()
    state.scene_words = scene_words
    learning = getattr(app.state, "learning", None)
    if learning:
        try:
            chosen = learning.pick_scene_words(target, scenes.get_archetype(target),
                                               datetime.now(timezone.utc))
            state.target_word_ids = set(chosen)
            state.scene_words = {**scene_words, **chosen}   # 选词补充无场景实体的词
        except Exception:  # noqa: BLE001 —— 选词失败退化为无目标词（不杀进场）
            pass
    state.actor = app.state.scene_factory(state.scene_words, entity_by_word_id, npc_id=default_npc)
```

> 该文件需补 `from datetime import datetime, timezone`。

5. **`_handle_npc_focus`**（ws.py）：actor 重建用 `state.scene_words` 而非 `scene_maps`：

```python
        scene_words, entity_by_word_id = scene_maps(scene)
        state.actor = app.state.scene_factory(state.scene_words, entity_by_word_id, npc_id=npc_id)
```

6. **新增 `entity.click` 控制消息**（ws.py 消息循环 `elif t == "entity.click":` → handler）：

```python
    async def _handle_entity_click(entity_id: str) -> None:
        if state.scene is None:
            return
        entry = next(((e["semantics"]["wordId"], e["semantics"]["name"])
                      for e in state.scene.entities
                      if e["id"] == entity_id and e.get("semantics", {}).get("wordId")), None)
        if entry is None:
            return
        word_id, _word = entry
        learning = getattr(app.state, "learning", None)
        if not learning or word_id not in state.target_word_ids:
            return
        now = datetime.now(timezone.utc)
        try:
            learning.record_evidence(
                session_id,
                {"evidence_id": f"ev_{uuid.uuid4().hex[:12]}", "event_seq": 0,
                 "session_id": session_id, "attempt_id": f"click_{word_id}",
                 "turn_id": f"click_{uuid.uuid4().hex[:8]}", "objective_id": None,
                 "word_id": word_id, "source": "action_understanding", "prompt_level": 1,
                 "axis": "receptive", "result": "success", "confidence": 1.0,
                 "evidence_policy_version": app.state.settings.evidence_policy_version,
                 "fsrs_algorithm_version": app.state.settings.fsrs_algorithm_version,
                 "created_at": now.isoformat()},
                event_id=f"ev_click_{word_id}_{now.date().isoformat()}")
        except Exception:  # noqa: BLE001
            pass
```

7. **v1 限制注明**：`rebuild_from_events`（重连重放）不重算选词——重连后 `target_word_ids` 为空，证据仅从新进场后产生。`build_history` 事件重放不受影响（internal evidence 事件不回放给前端）。

8. **`ws_helpers.make_app` 加 `asr_text` 参数**（默认 `"hello"`，供 `test_learning_ws` 注入目标词文本）：

```python
def make_app(tmp_path, scenario: str = "ok", slow_delta_s: float = 0.0,
             stream_text_override: str | None = None, scene_director=None,
             asr_text: str = "hello"):
    ...
    async def fake_asr(samples: bytes):
        return {"finalText": asr_text, "segments": [], "language": "en", "confidence": -0.3}
```

- [ ] **Step 3: 前端 entity.click**

`apps/web/src/useVoiceRound.ts` 新增（模式同 `askCompanion`）：

```ts
  const entityClick = useCallback((entityId: string) => {
    socketRef.current?.sendControl({ type: 'entity.click', entityId });
  }, []);
```

返回对象里加 `entityClick`。`App.tsx` 的 `onEntityClick` 改为：

```tsx
        onEntityClick={(e) => { toggleDiscover(e.id); setFocusEntity(e); entityClick(e.id); }}
```

- [ ] **Step 4: 测试**

`apps/api/tests/test_learning_ws.py`：**learning 只注入本测试自己的 app 实例**（不改 make_app 全局——否则审计测试也会带 learning、companion.ask 会写 spontaneous 表、Task 15 前全红）。用 `asr_text` 注入目标词文本，先 `store.import_words` 导入含 plaza scene_tags 的词：

```python
import asyncio
import json

from app.learning.engine import LearningEngine
from app.learning.store import LearningStore
from app.ws import ws_session
from tests.ws_helpers import FakeWS, audio_end, audio_frame, audio_start, make_app


async def test_round_writes_evidence(tmp_path) -> None:
    events, app = make_app(tmp_path, asr_text="a loaf please")
    store = LearningStore(events.connection)
    app.state.learning = LearningEngine(store, events, app.state.settings)
    store.import_words("local", "l1", "x", [{"lemma": "loaf", "pos": "n", "sense": "1",
                                              "scene_tags": json.dumps(["plaza"]), "carrier": "phrase",
                                              "slot_categories": "[]", "source": "quest",
                                              "created_at": "2026-08-08T00:00:00+00:00"}])
    ws = FakeWS([audio_start("u1"), audio_frame(), audio_end("u1")], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.6)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    if st.round_task and not st.round_task.done():
        st.round_task.cancel()
    evs = store.evidence_for_word("local", "word_loaf_n_1")
    assert len(evs) == 1                      # spontaneous_production success（fake_asr 说了 loaf，NPC 未必教）
    m = store.get_mastery("local", "word_loaf_n_1")
    assert m["productive_score"] > 0.0        # exp(-0.3)=0.74 ≥ 0.6 → success 计分
    rows = events.list_after("sess-x", 0)
    assert any(e["event_type"] == "evidence" and e["payload"].get("internal") for e in rows)
```

> 需要 `import pytest`。fake_asr confidence=-0.3 → exp(-0.3)≈0.74 ≥0.6 → success。MockAdapter scenario="ok" 的 NPC 文本不含 "loaf" → classify 给 `spontaneous_production`（user 检出、npc 未教）——这仍是成功证据，断言只查 evidence 行数/计分，不依赖 prompted/spontaneous 分支。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/test_learning_ws.py -v`
Expected: PASS。
Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api -v`
Expected: 全量 PASS——`test_state_audit` 用默认 make_app（learning **未**注入、asr_text="hello" 无目标词）→ 不写学习表；Task 12 各 make_app 测试也未注入 → 审计保持绿。

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/voice_round.py apps/api/app/ws.py apps/api/app/scene_lifecycle.py apps/web/src/useVoiceRound.ts apps/web/src/App.tsx apps/api/tests/test_learning_ws.py apps/api/tests/ws_helpers.py
git commit -m "feat(learning): wire evidence into round/companion/entity-click + scene word selection"
```

---

### Task 13: HTTP 端点 + main.py 接线 + lifespan 启动补账

**Files:**
- Create: `apps/api/app/learning/api.py`（APIRouter）
- Modify: `apps/api/app/main.py`（create_app 构造 store/engine/dictionary + include router + lifespan drain_outbox + configure_word_resolver）
- Modify: `apps/api/app/settings.py`（新增常量）
- Create: `apps/api/tests/learning/test_learning_api.py`

**Interfaces:**
- Consumes: Task 7（wordbook.run_import）、Task 10（engine）、Task 11（encounters）。
- Produces:
  - `learning_router = APIRouter(prefix="/api")`：4+1 端点（POST import、GET spontaneous、POST spontaneous/import、GET progress/summary、GET progress/words/{wordId}/evidence）。
  - `settings.py` 新增：`evidence_policy_version="v1"`、`fsrs_algorithm_version="fsrs-5"`、`score_alpha=0.35`、`fsrs_retention=0.9`、`fsrs_min_confidence=0.6`。

- [ ] **Step 1: settings 常量**

`apps/api/app/settings.py` 加 5 个字段 + `_ENV_FIELDS`（5 项）：

```python
    # --- 阶段 4：学习引擎 ---
    evidence_policy_version: str = "v1"
    fsrs_algorithm_version: str = "fsrs-5"
    score_alpha: float = 0.35
    fsrs_retention: float = 0.9
    fsrs_min_confidence: float = 0.6
```

`_ENV_FIELDS` dict 末尾追加（注意缩进与既有项对齐）：

```python
        "evidence_policy_version": "EVIDENCE_POLICY_VERSION",
        "fsrs_algorithm_version": "FSRS_ALGORITHM_VERSION",
        "score_alpha": "SCORE_ALPHA",
        "fsrs_retention": "FSRS_RETENTION",
        "fsrs_min_confidence": "FSRS_MIN_CONFIDENCE",
```

> `from_env` 对 `score_alpha`/`fsrs_retention`/`fsrs_min_confidence` 走 float 分支（settings.py:70-73 既有逻辑自动覆盖）。

- [ ] **Step 2: api.py 端点**

`apps/api/app/learning/api.py`：

```python
"""学习引擎 HTTP 端点（挂 main.py）。写侧走引擎单写锁。错误统一 {detail}。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.learning.encounters import list_spontaneous, promote
from app.learning.wordbook import ImportValidationError, run_import
from app.llm.concepts import invalidate_word_id_cache

router = APIRouter(prefix="/api")


def _eng(request: Request):
    return request.app.state.learning


def _dict(request: Request):
    return request.app.state.dictionary


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/word-lists/import")
def import_words(req: Request, body: dict):
    eng = _eng(req)
    try:
        res = run_import(eng.store, _dict(req), "local", body.get("words", []),
                         name=body.get("name"), now=_now())
    except ImportValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    invalidate_word_id_cache()
    return res


@router.get("/word-lists/spontaneous")
def list_spont(req: Request):
    return {"items": list_spontaneous(_eng(req).store, "local")}


@router.post("/word-lists/spontaneous/import")
def promote_spont(req: Request, body: dict):
    lemmas = body.get("lemmas", [])
    n = promote(_eng(req).store, "local", lemmas, now=_now())
    invalidate_word_id_cache()
    return {"promoted": n, "unknown": max(0, len(lemmas) - n)}


@router.get("/progress/summary")
def summary(req: Request):
    eng = _eng(req)
    rows = eng.store.all_words("local")
    now = _now()
    due = [r for r in rows if r["state"] != "new" and r["due"] and r["due"] <= now.isoformat()]
    quest = sum(1 for r in rows if r["source"] == "quest")
    free = sum(1 for r in rows if r["source"] == "free")
    page = max(1, int(req.query_params.get("page", 1)))
    page_size = min(100, max(1, int(req.query_params.get("page_size", 50))))
    start = (page - 1) * page_size
    return {
        "strategy": {"evidencePolicyVersion": eng.settings.evidence_policy_version,
                     "fsrsAlgorithmVersion": eng.settings.fsrs_algorithm_version},
        "totals": {"quest": quest, "free": free, "dueToday": len(due)},
        "page": page, "pageSize": page_size, "totalWords": len(rows),
        "words": [_word_summary(r) for r in rows[start:start + page_size]],
    }


@router.get("/progress/words/{word_id}/evidence")
def word_evidence(req: Request, word_id: str):
    items = _eng(req).store.evidence_for_word("local", word_id)
    return {"items": items}


def _word_summary(r: dict) -> dict:
    import json as _json
    return {"wordId": r["word_id"], "lemma": r["lemma"], "pos": r["pos"], "ipa": r["ipa"],
            "cefr": r["cefr"], "sceneTags": _json.loads(r["scene_tags"]), "source": r["source"],
            "carrier": r["carrier"],
            "scores": {"productive": r["productive_score"], "receptive": r["receptive_score"],
                       "asrConfidence": r["asr_confidence_score"]},
            "fsrs": {"state": r["state"], "due": r["due"], "reps": r["reps"], "lapses": r["lapses"]},
            "evidenceCount": len(_eng(req).store.evidence_for_word("local", r["word_id"])),
            "lastEvidenceAt": None}
```

- [ ] **Step 3: main.py 接线**

`apps/api/app/main.py` `create_app` 内（`events` 构造后）：

```python
    from app.learning.api import router as learning_router
    from app.learning.dictionary import Dictionary
    from app.learning.engine import LearningEngine
    from app.learning.store import LearningStore
    from app.llm import concepts

    store = LearningStore(events.connection)
    dictionary = Dictionary.load(settings.asset_root)
    engine = LearningEngine(store, events, settings)
    concepts.configure_word_resolver(store.resolve_word_id_from_store)

    app.state.learning = engine
    app.state.dictionary = dictionary
    app.include_router(learning_router)
```

`lifespan` 启动时补账（yield 前）：

```python
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            n = app.state.learning.drain_outbox()
            if n:
                print(f"learning: drained {n} outbox entries")
        except Exception:  # noqa: BLE001 —— 启动补账失败不阻断
            pass
        yield
        await client.aclose()
```

> 注意 `main.py` 顶部 `scenes = SceneStore(...)` 在 `settings` 后；`dictionary` 需 `settings.asset_root`。`scene_factory` 之前已定义；把 store/dictionary 构造放在 `settings` 赋值后、`app.state` 赋值前。

- [ ] **Step 4: 测试**

`apps/api/tests/learning/test_learning_api.py`（用 fastapi.testclient + 复用 ws_helpers.make_app 的注入方式）：

```python
from fastapi.testclient import TestClient
from app.main import create_app
from app.settings import Settings
from app.event_store import EventStore
from app.learning.dictionary import Dictionary
from app.learning.engine import LearningEngine
from app.learning.store import LearningStore


def _client(tmp_path) -> TestClient:
    events = EventStore(tmp_path / "e.db")
    app = create_app(events, Settings(asset_root=Path(__file__).resolve().parents[4] / "assets"))
    store = LearningStore(events.connection)
    app.state.learning = LearningEngine(store, events, app.state.settings)
    app.state.dictionary = Dictionary.load(app.state.settings.asset_root)
    return TestClient(app)
```

断言：import 返回 200+结构；spontaneous 提升；summary 含 strategy 与 dueToday；word evidence 返回 items。

- [ ] **Step 5: 跑 test_learning_api 确认通过**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api/tests/learning/test_learning_api.py -v`
Expected: PASS（≥4 个）。

- [ ] **Step 6: state-audit 白名单更新 + 全量回归**

main.py 接线后**所有** make_app 测试都会建学习表，且 `companion.ask` 非目标词会写 `spontaneous_encounters`——`test_state_audit.py` 必须同步放宽（Global Constraints 已声明）。

`ALLOWED_TABLES` 加 7 张学习表：

```python
ALLOWED_TABLES = {"session_events", "llm_calls", "tutor_cache",
                  "word_lists", "learning_items", "mastery_states",
                  "evidence_events", "spontaneous_encounters", "spontaneous_words",
                  "evidence_outbox"}
```

断言改为 `changed <= ALLOWED_TABLES`（一个合法回合**允许**动学习表，但**不强制**全动——取决于该回合是否命中目标词）；删除 `assert changed == ALLOWED_TABLES`，保留「不越权」核心。

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api -v`
Expected: 全量 PASS。state-audit 用默认 make_app（learning 已注入、asr_text="hello" 无目标词）→ 回合只多写 `spontaneous_encounters`（companion.ask fountain）→ subset 断言通过。

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/learning/api.py apps/api/app/main.py apps/api/app/settings.py apps/api/tests/learning/test_learning_api.py apps/api/tests/test_state_audit.py
git commit -m "feat(learning): HTTP endpoints + main wiring + startup outbox drain + settings constants + state-audit whitelist"
```

---

### Task 14: 前端最小进度页（`/progress`）

**Files:**
- Create: `apps/web/src/ProgressView.tsx`
- Modify: `apps/web/src/App.tsx`（hash 路由 `#/progress`）
- Create: `apps/web/src/ProgressView.test.tsx`（vitest）

**Interfaces:**
- Consumes: `GET /api/progress/summary`、`GET /api/progress/words/{wordId}/evidence`、`POST /api/word-lists/spontaneous/import`。
- Produces: `ProgressView` 组件（策略角标 + 概览卡 + 分页词表 + 按词展开证据时间线 + 提升按钮）。

- [ ] **Step 1: 写失败测试**

`apps/web/src/ProgressView.test.tsx`（mock fetch）：

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import ProgressView from './ProgressView';

const SUMMARY = {
  strategy: { evidencePolicyVersion: 'v1', fsrsAlgorithmVersion: 'fsrs-5' },
  totals: { quest: 1, free: 1, dueToday: 1 },
  page: 1, pageSize: 50, totalWords: 2,
  words: [
    { wordId: 'word_loaf_n_1', lemma: 'loaf', pos: 'n', ipa: '/loʊf/', cefr: 'A2',
      sceneTags: ['bakery'], source: 'quest', carrier: 'object',
      scores: { productive: 0.7, receptive: 0.5, asrConfidence: 0.6 },
      fsrs: { state: 'review', due: '2026-08-09T00:00:00Z', reps: 2, lapses: 0 },
      evidenceCount: 3, lastEvidenceAt: null },
  ],
};

describe('ProgressView', () => {
  beforeEach(() => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/summary')) return Promise.resolve({ json: () => Promise.resolve(SUMMARY) } as any);
      return Promise.resolve({ json: () => Promise.resolve({ items: [] }) } as any);
    });
  });

  it('renders strategy badge and totals', async () => {
    render(<ProgressView />);
    await waitFor(() => screen.getByText(/策略 v1 · FSRS v5/));
    expect(screen.getByText(/loaf/)).toBeInTheDocument();
    expect(screen.getByText(/今日到期 1/)).toBeInTheDocument();
  });

  it('shows experimental label for ASR confidence axis', async () => {
    render(<ProgressView />);
    await waitFor(() => screen.getByText(/ASR 置信度代理/));
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd e:/ai-english/apps/web && pnpm vitest run src/ProgressView.test.tsx --maxWorkers=1`
Expected: FAIL（ProgressView 不存在）。

- [ ] **Step 3: 实现 ProgressView.tsx**

hash 路由（App.tsx `isProgress` 分支 `if (window.location.hash === '#/progress') return <ProgressView />;`）。组件：`useEffect` fetch summary；渲染策略角标、三概览卡、词表（分页）、点词展开证据时间线（fetch word evidence）、提升按钮（POST lemmas）。样式复用 index.css 令牌。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd e:/ai-english/apps/web && pnpm vitest run src/ProgressView.test.tsx --maxWorkers=1 && pnpm tsc`
Expected: PASS + tsc 0。

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/ProgressView.tsx apps/web/src/ProgressView.test.tsx apps/web/src/App.tsx
git commit -m "feat(web): /progress minimal page (strategy badge, paginated words, evidence timeline)"
```

---

### Task 15: 全量回归（纯验证门）

**Files:**
- 无（白名单已在 Task 13 Step 6 完成；本任务无代码改动）

**Interfaces:**
- Consumes: 全任务产出。

- [ ] **Step 1: 全量回归**

Run: `cd e:/ai-english && uv run --project apps/api pytest apps/api -v`（163 → 新增学习测试全绿）
Run: `cd e:/ai-english/packages/scene-schema/python && uv run pytest`（3）
Run: `cd e:/ai-english/packages/scene-compiler && uv run pytest`（5）
Run: `cd e:/ai-english/apps/web && pnpm vitest run --maxWorkers=1 && pnpm tsc`（47 + 新）
Expected: 全绿。

- [ ] **Step 2: 确认工作树干净**

Run: `cd e:/ai-english && git status --short && git log --oneline -5`
Expected: 干净（Task 13 Step 7 已含白名单）。若有遗漏改动：`git add` 后补 commit 说明。

---

## 计划自审（writing-plans Self-Review）

**1. Spec 覆盖核对：**
- §4 六表+outbox+internal 列 → Task 3/4 ✅；§4.8 resolve 缓存 → Task 5 ✅
- §5 词典 + carrier/slotCategories → Task 6 ✅
- §6 导入管线 + 约束 + pos 语法 + 多 sense → Task 7 ✅
- §7 py-fsrs 包装 + guard + 黄金值 → Task 1 ✅；进入条件/日闸 → Task 9 ✅
- §8 公式/权重/轴隔离/置信度归一化 → Task 2/9 ✅；记录顺序单事务 → Task 10 ✅
- §9 挂钩（round/companion/action/选词喂入）→ Task 12 ✅（error 源 v1 无信号，仅引擎/测试支持——spec 已注明）
- §10 选词（carrier/due datetime/薄弱排除 asr/确定性 seed）→ Task 8 ✅
- §11 偶遇流（明细+聚合+提升 lemmas）→ Task 11 ✅
- §12 4+1 端点 → Task 13 ✅（分页 + 独立 evidence 端点）
- §13 进度页（角标/实验性标注/分页/时间线）→ Task 14 ✅
- §14 单事务 + outbox + 启动补账 + internal 不回放 → Task 3/10/13 ✅
- §15 测试逐条 → 各 Task ✅
- §16 依赖 py-fsrs `>=5,<6` + 常量 → Task 1/13 ✅

**2. 占位符扫描：** 无 TBD/TODO；每 Task 有具体测试代码与实现代码。Task 9 的 `_apply_daily_gate` 方向断言注明「若 py-fsrs 输出差异则核对后修正」——这是对真实算法输出的诚实处理，非占位。

**3. 类型/签名一致性：**
- `schedule(card, rating, now)` Task 1 定义，Task 9 调用一致 ✅
- `upsert_mastery_counts` 增量语义：Task 4 定义、Task 9 调用一致 ✅
- `record_evidence(store, user_id, ev, *, now)` 与引擎 `record_evidence(session_id, evidence, *, event_id)` 同名不同签名——**易混**。修正：Task 9 的纯函数改名 `apply_evidence(store, user_id, ev, *, now)`；引擎方法保持 `record_evidence`。已在上文 Task 9 Step 3 与 Task 10 使用 `apply_evidence` 区分 ✅
- `list_items_by_scene` 返回含 `word_id/state/due/productive_score/receptive_score/carrier/slot_categories` —— scheduler.pick 消费字段一致 ✅
- `store.get_mastery` 返回 `state/stability/difficulty/due/last_review/reps/lapses/last_scheduled_date/last_scheduled_rating` 等列 —— evidence/engine 消费一致 ✅

**4. 已发现并修正的计划内缺陷：**
- 原计划中 Task 9 的纯函数与 Task 10 引擎方法同名 → 改 `apply_evidence` 消除混淆。
- 原计划 Task 9 引 `learning/lexmatch_` 多余模块 → 直接复用 `app.llm.lexmatch`。
- 原计划 `run_round` 返回值追加字段 → 既有调用 `_run_round` 只看 `result["replied"]` 等，向后兼容。
- 原计划 state-audit 白名单放最后任务 → 但 main 接线（Task 13）后所有 make_app 测试即建学习表，白名单必须**同 Task 13** 更新（Global Constraints + Task 13 Step 6），Task 15 降级为纯回归门。
- Task 13 api.py 缺 import（run_import/ImportValidationError/invalidate_word_id_cache/list_spontaneous/promote）→ 补全；`page` 缺 `int()` 转类型 → 修正；`_ENV_FIELDS` 计数 4→5 并补 5 项 env 映射。
- 原 Task 13 Step 1 `_ENV_FIELDS`（4 项）与字段数不符 → 统一为 5 项。
- 自审发现 `apply_evidence` 改名只改了叙述、代码块仍是 `record_evidence`（Task 9 def/tests/import + Task 10 import）→ 补齐全部 9 处（含错别名 `classify_round as apply_evidence`）。
- Task 10 Interfaces `record_round` 参数顺序/`attempt_id` 缺省与实现不一致 → 对齐 `(*, turn_id, target_word_ids, attempt_id=None)`。
- Task 11 Interfaces `promote(..., *, now, dictionary)` 带 dictionary 但实现/调用均无 → 移除，注明 pos 取自 spontaneous_words、缺省 "n"。
- Task 1 Interfaces `to_fsrs_card(row)` 缺 `card_id`（Task 9 调用按关键字传）→ 补齐。

---

计划已保存至 `docs/superpowers/plans/2026-08-08-english-town-phase4.md`。

**两种执行方式：**

**1. Subagent-Driven（推荐）** — 每个 Task 派一个全新子代理实现 + 测试 + 提交，任务间两阶段评审，快速迭代。

**2. Inline 执行** — 本会话用 executing-plans 逐任务执行，批量 + 检查点。

选哪种？
