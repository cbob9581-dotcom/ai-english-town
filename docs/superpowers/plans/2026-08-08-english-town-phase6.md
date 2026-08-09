# Phase-6 收尾（GOP 发音评测 + deferred minors + 性能回填）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 兑现主 spec §17 里程碑 5 收尾——引入音素级 GOP 发音评测（新证据轴 `pronunciation_score`）+ 清空 phase-5 全部 deferred minors + 回填 VERSION_LOCK 性能门禁。

**Architecture:** 三子项目串行推进。A（GOP）新增 asr-worker 侧 wav2vec2 音素 CTC 模型 + torchaudio `forced_align`，通过 `POST /pronounce` 端点供 api 侧「先算后写」调用（锁外推理、锁内短事务写证据列）；前置 `scripts/ipa-coverage.py` 覆盖率门决定 A 是否可实施（<80% partial / <50% defer A→phase-7）。B（deferred minors）批量修复测试空转与生产健壮性缺陷。C（性能回填）纯环境操作，装 CUDA runtime + 下载 kokoro，回填 VERSION_LOCK，失败如实记录不臆造。

**Tech Stack:** Python 3.13（uv）/ FastAPI / faster-whisper（ctranslate2）/ torch + torchaudio（CPU 默认）/ transformers / py-fsrs 5.x / React + vitest。

## Global Constraints

- **16GB 严格串行**：任何时刻单个测试进程；web vitest 必须 `--maxWorkers=1`；禁止并发/并行进程。
- **先算后写（事务边界）**：GOP 的 HTTP+GPU 推理必须在 `events.write_lock` 外（engine.py:42 `record_evidence` 全程持锁）；锁内只做短事务 SQL 写入。绝不在锁内做 HTTP+GPU 推理。
- **执行顺序**：`C.step2（CUDA runtime）→ B 诊断类 → 真机验证 word_timestamps → A 覆盖率门 → A 实施 → C.step1/3/4`。A 实施必须等覆盖率门裁决后。
- **降级优先**：GOP 任何失败（模型缺失/加载失败/音频未授权/词窗过短/对齐异常）→ 回退 phase-5 词级置信度代理，绝不阻断回合，证据路径零回归。
- **语义诚实**：`pronunciation_score` = 音素级 GOP 评测，**可空**（`REAL` 无默认，NULL=未评测）；`_migrate` 加列**不含** `NOT NULL DEFAULT 0.0`。
- **弃 degraded 枚举**：降级是系统条件非学习者观测 → 不写 `pronunciation_gop` 证据行，降级计数记运行侧日志（不污染证据流）。
- **GOP 公式**：`margin(p) = log P(p|X_p) − max_{p'≠p} log P(p'|X_p)`（分母排除自身，防正确音素 margin=0 全塌缩）；排除 CTC blank 帧；分母限**英语音素子集**。
- **词窗**：复用 phase-5 `words` 的 `start/end`，双侧 pad `pronunciation_gop_word_pad_ms`（默认 100ms）+ clamp 到整段边界；词窗 < `pronunciation_gop_min_word_ms`（120ms）或词未命中 `words` → 该词回退代理。
- **模型**：`facebook/wav2vec2-lv-60-espeak-cv-ft`，**默认 CPU**（`pronunciation_gop_device="cpu"`），惰性加载（首次评分才 load）；走 GPU 与 whisper 串行不并发。**动手前必须 dump 核对** torchaudio `forced_align` 签名与模型实际 alphabet（不臆造符号集）。
- **新依赖只进 asr-worker venv**，不进 api。torch/torchaudio/transformers 走 CPU-only wheel（`https://download.pytorch.org/whl/cpu`）。
- **确定性**：GOP 评分纯函数（音频段 + 期望音素序列 → 分数）；时间敏感函数显式注入 `now`；测试 mock → 精确值、真模型 → 区间（不写死浮点）。
- **配置字段**（settings.py，env 同 pattern）：`pronunciation_gop_enabled=False`、`pronunciation_gop_model="facebook/wav2vec2-lv-60-espeak-cv-ft"`、`pronunciation_gop_device="cpu"`、`pronunciation_gop_min_word_ms=120`、`pronunciation_gop_word_pad_ms=100`、`pronunciation_gop_min_conf=None`。
- **薄弱词排序**：维持 `productive_score + receptive_score`（scheduler.py:29 既有行为）；GOP 轴**不得**加入选词/排期/薄弱词排序。
- **时间一律 ISO 8601 datetime（UTC）**。测试基线：api 241 / web 51 / asr-worker 8 全绿。
- **诚实失败**：任何下载/安装失败 → 保持 deferred 标注 + 记录失败原因；**绝不臆造数值**（VERSION_LOCK 既定决策）。

---

### Task 1: C.step2 — 装 CUDA runtime + DLL 版本快照（环境，协调者）

**Files:**
- Modify: `VERSION_LOCK.md`（回填 CUDA runtime / cuDNN / 3s 转写 deferred 行）

**Interfaces:**
- Consumes: asr-worker venv（faster-whisper/ctranslate2 4.8.1 已装）
- Produces: `VERSION_LOCK.md` 更新后的 CUDA runtime 行；供 Task 6（真机验证 word_timestamps）的前置条件

> 本任务与 Task 6、Task 13、Task 14 是**环境/协调者任务**（需要网络下载 + venv 变更 + 系统级操作），**不派发 implementer 子代理**，由协调者执行并如实记录。其余任务派发子代理。

- [ ] **Step 1: 装 nvidia CUDA 运行时包（cuBLAS/cuDNN 12.x）到 asr-worker venv**

`cublas64_12.dll` 缺失阻断 faster-whisper GPU 转写。用 pip 包把 DLL 装进 venv（ctranslate2 4.8.1 需 cuBLAS 12）：

```bash
uv add --project services/asr-worker nvidia-cublas-cu12 nvidia-cudnn-cu12
```

若 uv 网络失败 → 如实记录（不臆造），跳到 Step 4 保持 deferred。

- [ ] **Step 2: 记录 DLL 版本快照**

```bash
uv run --project services/asr-worker python -c "import nvidia.cublas.lib, nvidia.cudnn.lib, os; print('cublas dlls:', [f for f in os.listdir(os.path.dirname(nvidia.cublas.lib.__file__))]); print('cudnn dlls:', [f for f in os.listdir(os.path.dirname(nvidia.cudnn.lib.__file__))])"
```

把 cublas64_12/cudnn DLL 的确切文件名记入 VERSION_LOCK.md（对照 ctranslate2 4.8.1 对 cuBLAS 12 的依赖，防 torch 与 ctranslate2 的 CUDA 运行时版本冲突）。

- [ ] **Step 3: 验证 cuBLAS 错误消失**

```bash
uv run --project services/asr-worker python -m asr_worker.selfcheck
```

期望：不再报 `Library cublas64_12.dll is not found`。若 DLL 找不到（PATH 未含 site-packages/nvidia/cublas/bin），设 `PATH` 含该目录后重跑：

```bash
$env:PATH = (uv run --project services/asr-worker python -c "import nvidia.cublas.lib, os; print(os.path.dirname(nvidia.cublas.lib.__file__))") + ";" + $env:PATH
```

`transcribe_ok` 需真实英文语音才为 True（静音转写文本为空）。cuBLAS 错误消失即视为本步通过。

- [ ] **Step 4: 回填 VERSION_LOCK.md**

CUDA runtime / cuDNN 行更新为实测版本（成功）或保持 deferred + 记录失败原因（失败）。绝不臆造数值。

- [ ] **Step 5: 提交**

```bash
git add VERSION_LOCK.md
git commit -m "docs(lock): record CUDA runtime install result (step2)"
```

---

### Task 2: B1 — FSRS stability guard + record_evidence 补日志 + due datetime 比较

**Files:**
- Modify: `apps/api/app/learning/fsrs.py:26-36`
- Modify: `apps/api/app/learning/engine.py:63-68`
- Modify: `apps/api/app/learning/memory.py:129`
- Modify: `apps/api/tests/test_memory_hooks.py:34-35`
- Create: `apps/api/tests/test_fsrs_guard.py`
- Create: `apps/api/tests/test_outbox_log.py`

**Interfaces:**
- Consumes: `to_fsrs_card(row, card_id)`（既有签名）、`build_world_summary(events, conn, user_id, *, now)`
- Produces: `to_fsrs_card` 对 `stability<=0` 的非 new 态走新卡路径（guard）；`record_evidence` 失败时打印 `evidence dropped to outbox: {e!r}`；`build_world_summary` 的 due 比较改为 datetime 解析

- [ ] **Step 1: 写失败测试 `apps/api/tests/test_fsrs_guard.py`**

```python
from app.learning.fsrs import to_fsrs_card


def test_stability_zero_routes_to_new_card_path():
    # 非 new 态 + stability=0.0：guard 走新卡路径（Learning/step 0），不得触发 py-fsrs ZeroDivisionError
    row = {"state": "learning", "stability": 0.0, "difficulty": 5.0,
           "due": "2026-08-08T00:00:00Z", "last_review": None}
    card = to_fsrs_card(row, card_id=42)
    assert card.state.name == "Learning"


def test_positive_stability_kept():
    row = {"state": "review", "stability": 3.0, "difficulty": 5.0,
           "due": "2026-08-08T00:00:00Z", "last_review": "2026-08-07T00:00:00Z"}
    card = to_fsrs_card(row, card_id=42)
    assert card.state.name == "Review"
    assert card.stability == 3.0
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run --project apps/api pytest tests/test_fsrs_guard.py -v
```

期望：FAIL（`to_fsrs_card` 对 stability=0 直接构造 Card → py-fsrs 内部 ZeroDivisionError，或 state 非 Learning）。

- [ ] **Step 3: fsrs.py 加 guard**

```python
def to_fsrs_card(row: Mapping, *, card_id: int) -> Card:
    """mastery_states 行 → py-fsrs Card。state='new' 或 stability<=0（非新态但无效稳定性）
    → 全新 Card（默认 Learning/step 0）。guard 防 py-fsrs 对 0 稳定性卡 ZeroDivisionError
    （0**负幂）；走新卡路径而非硬塞 1.0，避免破坏复习节奏。"""
    if row["state"] == "new" or row.get("stability", 0.0) <= 0:
        return Card(card_id=card_id)
    due = datetime.fromisoformat(row["due"]) if row.get("due") else datetime.now(timezone.utc)
    last = datetime.fromisoformat(row["last_review"]) if row.get("last_review") else None
    step = 0 if row["state"] == "learning" else (1 if row["state"] == "relearning" else None)
    return Card(card_id=card_id, state=_STATE_TO_FSRS[row["state"]], step=step,
                stability=row["stability"], difficulty=row["difficulty"],
                due=due, last_review=last)
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run --project apps/api pytest tests/test_fsrs_guard.py -v
```

期望：PASS。

- [ ] **Step 5: 写失败测试 `apps/api/tests/test_outbox_log.py`**

```python
import json
from datetime import datetime, timezone

from app.event_store import EventStore
from app.learning import fsrs as fsrs_mod
from app.learning.evidence import classify_round
from app.learning.engine import LearningEngine
from app.learning.store import LearningStore
from app.settings import Settings


def _seed(conn):
    conn.execute("INSERT INTO word_lists(user_id,list_id,name,created_at) "
                 "VALUES('local','l1','q','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO learning_items(word_id,user_id,lemma,pos,ipa,scene_tags,source,list_id,created_at) "
                 "VALUES('word_loaf_n_1','local','loaf','n','/loʊf/','[\"bakery\"]','quest','l1','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO mastery_states(user_id,word_id,state,stability,difficulty,due,last_review,updated_at) "
                 "VALUES('local','word_loaf_n_1','learning',3.0,5.0,'2026-08-08T00:00:00Z','2026-08-08T00:00:00Z','2026-08-08T12:00:00Z')")


def test_evidence_failure_logs_and_lands_outbox(tmp_path, monkeypatch, capsys):
    """apply_evidence 内部抛错（FSRS 排期）→ 事务回滚、入 outbox、且有一行日志（不再静默吞）。"""
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    eng = LearningEngine(store, events, Settings())

    def boom(*a, **k):
        raise RuntimeError("scheduler boom")

    monkeypatch.setattr(fsrs_mod, "schedule", boom)
    draft = classify_round({"word_loaf_n_1": "loaf"}, "what do you need", "I want a loaf",
                           0.9, target_word_ids={"word_loaf_n_1"})[0]
    evidence = {"evidence_id": "ev_x", "event_seq": 0, "session_id": "s1", "attempt_id": "a",
                "turn_id": "t", "objective_id": None, "word_id": draft["word_id"],
                "source": draft["source"], "prompt_level": draft["prompt_level"],
                "axis": draft["axis"], "result": draft["result"], "confidence": draft["confidence"],
                "evidence_policy_version": "v1", "fsrs_algorithm_version": "fsrs-5",
                "created_at": "2026-08-08T12:00:00Z"}
    seq = eng.record_evidence("s1", evidence, event_id="ev_x")
    assert seq is None
    out = store.outbox_drain("local")
    assert len(out) == 1
    assert json.loads(out[0])["evidence_id"] == "ev_x"
    assert "evidence dropped to outbox" in capsys.readouterr().out
```

- [ ] **Step 6: 运行确认失败**

```bash
uv run --project apps/api pytest tests/test_outbox_log.py -v
```

期望：FAIL（`"evidence dropped to outbox" not in captured output`——当前 except 无日志）。

- [ ] **Step 7: engine.py 补日志（收窄 + 可观测）**

```python
            except Exception as e:  # noqa: BLE001 —— 证据失败不杀回合；入 outbox 下次补
                conn.rollback()
                print(f"evidence dropped to outbox: {e!r}", flush=True)
                try:
                    self.store.outbox_push("local", json.dumps(evidence, ensure_ascii=False))
                except Exception:  # noqa: BLE001 —— outbox 也失败则只丢日志
                    pass
                return None
```

> 说明：fsrs guard（Step 3）消除了已知失败类（0 稳定性 ZeroDivisionError），本步让任何残余失败可见；outbox 无界重试随之可观测（有日志，不再静默）。这就是评审点 7「收窄 + 补日志」的兑现。

- [ ] **Step 8: 运行确认通过**

```bash
uv run --project apps/api pytest tests/test_outbox_log.py -v
```

期望：PASS。

- [ ] **Step 9: 写 due datetime 比较测试（追加到 `apps/api/tests/test_memory_hooks.py`）**

```python
def test_review_due_parsed_as_datetime_z_suffix(tmp_path):
    """due 带 Z 后缀：字符串比较会因 'Z' > '+' 而错判为未到期；datetime 解析后精确比较。"""
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    conn.execute("INSERT INTO mastery_states(user_id,word_id,state,due,updated_at) "
                 "VALUES('local','word_loaf_n_1','review','2026-08-08T00:00:00Z','2026-08-08T12:00:00Z')")
    summary = build_world_summary(events, conn, "local",
                                  now=datetime(2026, 8, 8, 0, 0, 1, tzinfo=timezone.utc))
    assert summary["wordMastery"]["reviewDue"] == 1
```

- [ ] **Step 10: memory.py:129 改为 datetime 比较**

```python
        if r["state"] != "new" and r["due"] and datetime.fromisoformat(r["due"]) <= now:
            due += 1
```

（Python 3.11+ `fromisoformat` 原生支持 `Z` 后缀。）

- [ ] **Step 11: 修正 `test_memory_hooks.test_one_round` 的 seed 与证据唯一性**

把 `test_memory_hooks.py:34-35` 的 seed 改为带正 stability/due/last_review（真实日闸执行，非空转），并把三个 `_evidence()` 调用传唯一 `evidence_id`：

```python
def test_one_round_multi_evidence_revision_at_most_one(tmp_path):
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    # 预置 loaf 已 known（learning）+ 正 stability：prompted 证据经日闸真实排期（非空转）
    conn.execute("INSERT INTO mastery_states(user_id,word_id,state,stability,difficulty,due,last_review,updated_at) "
                 "VALUES('local','word_loaf_n_1','learning',3.0,5.0,'2026-08-08T00:00:00Z',"
                 "'2026-08-08T00:00:00Z','2026-08-08T12:00:00Z')")
    eng = LearningEngine(store, events, Settings())
    eng.record_evidence("s1", _evidence(source="help", result="neutral", evidence_id="ev_h1"), event_id="h1")
    assert eng.memory.get_revision("local") == 1
    eng.record_evidence("s1", _evidence(evidence_id="ev_p1"), event_id="p1")
    assert eng.memory.get_revision("local") == 1
    eng.record_evidence("s1", _evidence(source="action_understanding", prompt_level=1, evidence_id="ev_c1"), event_id="c1")
    assert eng.memory.get_revision("local") == 1
    # 三条证据全部真实落库（evidence_id 唯一，不再被 INSERT OR IGNORE 丢行）
    assert len(store.evidence_for_word("local", "word_loaf_n_1")) == 3
```

- [ ] **Step 12: 跑相关测试确认全绿**

```bash
uv run --project apps/api pytest tests/test_memory_hooks.py tests/test_fsrs_guard.py tests/test_outbox_log.py -v
```

期望：全部 PASS。

- [ ] **Step 13: 全量回归（api，串行）**

```bash
uv run --project apps/api pytest -q
```

期望：241 全绿（含新增）。若有失败先修。

- [ ] **Step 14: 提交**

```bash
git add apps/api/app/learning/fsrs.py apps/api/app/learning/engine.py apps/api/app/learning/memory.py apps/api/tests/test_memory_hooks.py apps/api/tests/test_fsrs_guard.py apps/api/tests/test_outbox_log.py
git commit -m "fix(api): FSRS stability<=0 guard + evidence outbox log + due datetime compare"
```

---

### Task 3: B2 — 测试正确性修复（interrupt 时序 / ask-hook 负向 / evidence_id 唯一 / snapshot 重放幂等）

**Files:**
- Modify: `apps/api/tests/test_audio_consent.py`
- Modify: `apps/api/tests/test_memory_hooks.py`

**Interfaces:**
- Consumes: `run_round(...)` 的 stream 协议（`npc.speech.delta` / `npc.speech.commit`）、`record_ask(store, user_id, session_id, lemma, pos, turn_id, *, now, events=None)`、`build_world_summary`
- Produces: `test_interrupt_does_not_write` 变成真实 mid-stream 取消断言（cancel 落在首次 yield 之后）

- [ ] **Step 1: 改 `_Actor` 先 yield 首个 delta 再阻塞**

`apps/api/tests/test_audio_consent.py`：

```python
class _Actor:
    async def stream_reply(self, **kw):
        yield {"type": "npc.speech.delta", "text": "Here is a"}
        await asyncio.sleep(10)   # 阻塞：让 cancel 落在首次 yield 之后的流中途
```

- [ ] **Step 2: 改 `_run` 让任务越过 asr await 再 cancel，并返回事件流供断言**

```python
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
        await asyncio.sleep(0.05)   # 让 run_round 越过 asr await、消费首个 delta、进入 actor 阻塞
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
    else:
        await task
    return tmp_path / "pronunciation-audio", events

def test_interrupt_does_not_write(tmp_path):
    root, events = asyncio.run(_run(tmp_path, consent=True, interrupted=True))
    assert not (root / "s1").exists()
    # 取消发生在流中途（asr 已跑、首个 delta 已消费、未 commit）→ 补写部分轮次事件
    turns = [e for e in events.list_after("s1", 0) if e["event_type"] == "dialogue.turn"]
    assert len(turns) == 1
```

其余测试调用处改为 `root, events = asyncio.run(_run(...))`。

- [ ] **Step 3: 运行确认通过**

```bash
uv run --project apps/api pytest tests/test_audio_consent.py -v
```

期望：PASS（若旧实现下中断发生在 asr await（try 外），无 `dialogue.turn` 事件 → 断言暴露 → 本任务修复的是测试本身，实现无需改动）。

- [ ] **Step 4: 加 ask-hook 负向对照（追加到 `apps/api/tests/test_memory_hooks.py`）**

```python
def test_record_ask_without_events_suppresses_hook(tmp_path):
    """省略 events= 应抑制记忆钩子（负向对照：不是任何 record_ask 都触发 +revision）。"""
    events = EventStore(tmp_path / "e.db")
    store = LearningStore(events.connection)
    store.memory = MemoryStore(events.connection)
    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    record_ask(store, "local", "s1", "torch", "n", "turn1", now=now)   # 无 events=
    assert store.memory.get_revision("local") == 0
    assert store.memory.get_world_summary("local") is None
```

- [ ] **Step 5: 加 snapshot 重放幂等测试（追加到 `apps/api/tests/test_memory_hooks.py`）**

```python
def test_replaying_snapshot_events_does_not_bump_revision(tmp_path):
    """world_summary.snapshot 是 internal 事件；apply_memory_updates 对 evidence 外的
    重放（含内部 snapshot）不推进 revision —— 重放幂等。"""
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    _seed(conn)
    eng = LearningEngine(store, events, Settings())
    eng.record_evidence("s1", _evidence(source="help", result="neutral", evidence_id="ev_h1"), event_id="h1")
    assert eng.memory.get_revision("local") == 1
    conn.execute(
        "INSERT INTO session_events(sequence, event_id, session_id, event_type, payload_json, internal, created_at) "
        "VALUES((SELECT COALESCE(MAX(sequence),0)+1 FROM session_events WHERE session_id='s1'), 'mem_replay', 's1', "
        "'world_summary.snapshot', '{\"summary\":{}}', 1, '2026-08-08T12:00:00Z')")
    conn.commit()
    summary = eng.memory.get_world_summary("local")
    assert summary is not None
    assert eng.memory.get_revision("local") == 1   # 重放不 +revision
```

- [ ] **Step 6: 跑测试确认全绿**

```bash
uv run --project apps/api pytest tests/test_audio_consent.py tests/test_memory_hooks.py -v
```

期望：全部 PASS。

- [ ] **Step 7: 全量回归（api，串行）**

```bash
uv run --project apps/api pytest -q
```

期望：全绿。

- [ ] **Step 8: 提交**

```bash
git add apps/api/tests/test_audio_consent.py apps/api/tests/test_memory_hooks.py
git commit -m "test(api): real mid-stream interrupt + ask-hook negative + unique evidence_id + snapshot replay idempotency"
```

---

### Task 4: B3 — selfcheck 传 ASR_MODEL（必须 C 前）

**Files:**
- Modify: `services/asr-worker/asr_worker/selfcheck.py:28`
- Create: `services/asr-worker/tests/test_selfcheck.py`

**Interfaces:**
- Produces: `selfcheck.run()` 用 `WhisperEngine.load("auto", model=os.environ.get("ASR_MODEL"))` 加载（与服务器行为一致），供 Task 6 真机验证

- [ ] **Step 1: 写失败测试 `services/asr-worker/tests/test_selfcheck.py`**

```python
import os

from asr_worker import selfcheck


def test_selfcheck_loads_asr_model_from_env(monkeypatch):
    monkeypatch.setenv("ASR_MODEL", "distil-large-v3")
    calls: dict = {}

    class _FakeEngine:
        device = "cpu"
        model_name = "distil-large-v3"

        def transcribe(self, audio):
            return {"text": "hello world", "segments": [], "language": "en",
                    "avg_logprob": -0.1, "words": []}

    def _fake_load(device, model=None):
        calls["device"] = device
        calls["model"] = model
        return _FakeEngine()

    monkeypatch.setattr(selfcheck.WhisperEngine, "load", staticmethod(_fake_load))
    result = selfcheck.run(wav_path=None)
    assert calls["model"] == "distil-large-v3"   # 硬编码 "auto" 时此断言失败
    assert result["transcribe_ok"] is True
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run --project services/asr-worker pytest tests/test_selfcheck.py -v
```

期望：FAIL（`calls["model"] is None`）。

- [ ] **Step 3: selfcheck.py:28 修复**

```python
        engine = WhisperEngine.load("auto", model=os.environ.get("ASR_MODEL"))
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run --project services/asr-worker pytest tests/test_selfcheck.py -v
```

期望：PASS。

- [ ] **Step 5: 提交**

```bash
git add services/asr-worker/asr_worker/selfcheck.py services/asr-worker/tests/test_selfcheck.py
git commit -m "fix(asr): selfcheck loads ASR_MODEL to match server behavior"
```

---

### Task 5: B4 — 代码卫生批量（小修）

**Files:**
- Modify: `apps/api/app/voice_round.py:21-36`（`_write_consent_audio` 补日志 + JSON 先写）
- Modify: `apps/api/app/scene_prefetch.py:1-2`（过时 docstring）
- Modify: `apps/api/app/learning/memory_smoke.py`（docstring + `arch is None` guard + `max(bumps, default=0)`）
- Modify: `apps/api/app/learning/memory.py:136-139`（SUM(help_count) 意图注释）
- Modify: `apps/api/tests/test_audio_consent.py:1-2`（删未用 `json, Path`）

**Interfaces:**
- Consumes: 无跨任务接口
- Produces: 无（纯卫生）

- [ ] **Step 1: `_write_consent_audio` 补日志 + JSON 先写**

`apps/api/app/voice_round.py`：

```python
def _write_consent_audio(session_id: str, utterance_id: str, pcm: bytes,
                         settings, meta: dict) -> None:
    """授权时写 JSON 元数据 + WAV；失败仅日志。调用方仅在回合完全成功（replied=True）时调用。
    先写 JSON 后写 WAV：JSON 是事实源，WAV 写失败只留无音频的元数据，不产生孤儿 WAV。"""
    if not getattr(settings, "pronunciation_audio_consent", False):
        return
    try:
        root = Path(settings.tutor_cache_dir).parent / "pronunciation-audio"
        dirpath = root / session_id
        dirpath.mkdir(parents=True, exist_ok=True)
        (dirpath / f"{utterance_id}.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        with wave.open(str(dirpath / f"{utterance_id}.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(pcm)
    except Exception as e:  # noqa: BLE001 —— 落盘失败不影响回合/评分
        print(f"consent audio write failed: {e}", flush=True)
```

- [ ] **Step 2: `scene_prefetch.py:1-2` docstring 更新**

```python
"""ScenePlan 服务端缓存：预取 Director 提案（非骨架——骨架本地无条件快）。
键 = (archetype_id, revision)，TTL 过期即失效。"""
```

- [ ] **Step 3: `memory_smoke.py` 修复**

```python
"""memory_smoke：回放 session_events 的 scene.entered → WorldSummary + 最终 revision + 增长频率。
实测「一轮多证据/连续进场 revision 不暴涨」。
用法: cd apps/api && uv run -m app.learning.memory_smoke <db_path>"""
```

```python
    for r in rows:
        arch = json.loads(r[0]).get("archetypeId")
        if arch is None:
            continue
        ...
    return {
        ...
        "revisionGrowth": {"touches": len([b for b in bumps if b > 0]),
                           "maxStep": max(bumps, default=0)},
    }
```

- [ ] **Step 4: `memory.py` SUM(help_count) 意图注释**

在 `apps/api/app/learning/memory.py:136` 的 `prof = conn.execute(...)` 前加注释：

```python
    # help_count 的 SUM 与 AVG 同受 `state != 'new'` 过滤 → 下计（求助词多处于 new 前）。
    # 意图性：userProfile 描述复习中学习者，与 reviewDue/weakWords 口径一致（维持现状，不加分拆）。
```

- [ ] **Step 5: `test_audio_consent.py` 删未用 import**

`import asyncio, json, wave` → `import asyncio, wave`；`from pathlib import Path` 删除（wave 仍用）。

- [ ] **Step 6: 回归（api 串行）**

```bash
uv run --project apps/api pytest -q
```

期望：全绿。

- [ ] **Step 7: 提交**

```bash
git add apps/api/app/voice_round.py apps/api/app/scene_prefetch.py apps/api/app/learning/memory_smoke.py apps/api/app/learning/memory.py apps/api/tests/test_audio_consent.py
git commit -m "chore(api): consent-audio JSON-first + log, stale docstrings, smoke guard, SUM comment"
```

---

### Task 6: 真机验证 word_timestamps（协调者，C 解锁后冒烟）

**Files:**
- Modify: `VERSION_LOCK.md`（3s 转写 / 词级时间戳实测行）

**Interfaces:**
- Consumes: Task 1（CUDA runtime）；Task 4（selfcheck 传 ASR_MODEL）
- Produces: word_timestamps 真机验证结论 + 词窗拦截比例；决定 A 地基是否成立

> 环境任务，协调者执行。目标：faster-whisper 在这台机器上**第一次真实转写** + 验证 `word_timestamps` 输出（phase-5 新功能从未真机跑过）。

- [ ] **Step 1: 准备真实英文音频**

按顺序尝试：
1. kokoro TTS 合成（若 `voices-v1.0.bin` 已在缓存，`uv run --project services/tts-worker python -m tts_worker.selfcheck` 的 `audio_base64`）；
2. 否则下载一个短英文 WAV 样本（如 `https://download.pytorch.org/torchaudio/tutorial-assets/LJ037-0171.wav`）到 `.selfcheck-hello.wav`；
3. 否则如实记录「deferred: 无真实英文音频可用」。

- [ ] **Step 2: 真机转写 + 词级时间戳**

```bash
$env:ASR_MODEL = "distil-large-v3"; $env:ENABLE_WORD_TIMESTAMPS = "true"; $env:WORD_TIMESTAMP_MIN_MODEL = "distil-large-v3"
uv run --project services/asr-worker python -m asr_worker.selfcheck .selfcheck-hello.wav
```

期望：`transcribe_ok: true`、`word_timestamps: true`、`sample` 非空。

- [ ] **Step 3: 记录词窗拦截比例**

对 selfcheck 返回的转写文本，人工/脚本统计：whisper `words` 中 `start/end` 均非空且时长 ≥ 120ms 的词占全部词的比例；`<120ms` 或未命中的词记入拦截。写入 VERSION_LOCK 的「词级时间戳实测」行。若无真实音频 → 记录 deferred 及原因。

- [ ] **Step 4: 回填 VERSION_LOCK.md**

3s 转写行 / 新增「word_timestamps 真机验证」行更新为实测值（或 deferred + 原因）。提交：

```bash
git add VERSION_LOCK.md
git commit -m "docs(lock): real-machine word_timestamps verification result"
```

---

### Task 7: A-1 — asr_worker/ipa.py（trie 分词器 + 覆盖率纯函数）+ 单测

**Files:**
- Create: `services/asr-worker/asr_worker/ipa.py`
- Create: `services/asr-worker/tests/test_ipa.py`

**Interfaces:**
- Consumes: 无（纯 Python，无 torch）
- Produces:
  - `DICT_IPA_SYMBOLS: tuple[str, ...]`（词典 IPA 符号表，长符号优先）
  - `IPA_TO_ESPEAK: dict[str, str]`（词典符号 → 模型音素符号；**由 Task 8 dump 后回填**）
  - `ENGLISH_ESPEAK_SYMBOLS: frozenset[str]`（英语音素子集；**由 Task 8 dump 后回填**）
  - `tokenize_ipa(ipa: str, *, symbols=None) -> list[str]`（trie 最长匹配；元字符剥离；未匹配抛 ValueError）
  - `coverage_report(words: list[tuple[str, str | None]], mapping: dict[str, str], english_symbols: frozenset[str]) -> dict`

- [ ] **Step 1: 写失败测试 `services/asr-worker/tests/test_ipa.py`**

```python
import pytest

from asr_worker.ipa import coverage_report, tokenize_ipa


def test_tokenize_loaf_real_dict_format():
    # 实测 dictionary.json IPA：带斜杠、无空格、含双元音
    assert tokenize_ipa("/loʊf/") == ["l", "oʊ", "f"]


def test_tokenize_order_real_dict_format():
    # 含重音符号 ˈ 与长音符 ː：重音剥离、长音是符号一部分
    assert tokenize_ipa("/ˈɔːrdər/") == ["ɔː", "r", "d", "ə", "r"]


def test_tokenize_bread_buy():
    assert tokenize_ipa("/bred/") == ["b", "r", "e", "d"]
    assert tokenize_ipa("/baɪ/") == ["b", "aɪ"]


def test_tokenize_unmapped_symbol_raises():
    with pytest.raises(ValueError):
        tokenize_ipa("/bɹɛd/")   # ɹ 不在符号表 → 抛错（让覆盖率为 0 而非静默）


def test_coverage_full_ok():
    words = [("loaf", "/loʊf/"), ("bread", "/bred/"), ("order", "/ˈɔːrdər/"), ("buy", "/baɪ/")]
    mapping = {"l": "l", "oʊ": "o", "f": "f", "b": "b", "r": "r", "e": "e",
               "d": "d", "aɪ": "aI", "ɔː": "O:"}
    english = frozenset("l o f b r e d aI O:".split())
    report = coverage_report(words, mapping, english)
    assert report["ratio"] == 1.0
    assert report["verdict"] == "ok"
    assert report["unmapped_symbols"] == set()


def test_coverage_below_half_defers():
    words = [(f"w{i}", "/loʊf/") for i in range(10)] + [("x", "/baɪ/"), ("y", "/bred/"), ("z", "/ˈɔːrdər/")]
    mapping = {"l": "l", "oʊ": "o", "f": "f"}
    english = frozenset("l o f".split())
    report = coverage_report(words, mapping, english)
    assert report["ratio"] == 10 / 13
    assert report["verdict"] == "defer"
    assert "aɪ" in report["unmapped_symbols"]
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run --project services/asr-worker pytest tests/test_ipa.py -v
```

期望：FAIL（module 不存在）。

- [ ] **Step 3: 实现 `services/asr-worker/asr_worker/ipa.py`**

```python
"""IPA → 音素序列分词器 + 覆盖率纯函数（GOP A 前置门的核心）。
词典 IPA 实测格式：`/loʊf/`、`/ˈɔːrdər/` —— 带斜杠包裹、无空格、含重音(ˈ)/长音(ː)。
分词用 trie 最长匹配（非空格切分）：按长符号优先逐符号匹配；斜杠/重音/连字符为元字符剥离。
IPA_TO_ESPEAK / ENGLISH_ESPEAK_SYMBOLS 是「词典符号 ↔ 模型音素符号」的桥，由
scripts/ipa-coverage.py（Task 8）真机 dump 模型 alphabet 后回填；score_gop 运行时读取。"""
from __future__ import annotations

# 词典（CMU 风格）IPA 符号表。长符号优先保证 trie 最长匹配先命中。
# 若真实 dictionary.json 含未列符号，tokenize_ipa 抛 ValueError → 覆盖率门暴露该符号，补进表即可。
DICT_IPA_SYMBOLS: tuple[str, ...] = (
    "iː", "eɪ", "aɪ", "ɔɪ", "aʊ", "oʊ", "ɑː", "ɔː", "uː", "ɜː",        # 双元音/长元音
    "ɪ", "ɛ", "e", "æ", "ɒ", "ʊ", "ʌ", "ə", "ɚ", "ɝ",                  # 短元音/中央元音
    "tʃ", "dʒ", "θ", "ð", "ʃ", "ʒ", "ŋ",                                # 双字符/特有辅音
    "p", "b", "t", "d", "k", "ɡ", "f", "v", "s", "z", "h", "m", "n", "l", "r", "j", "w",
)

# 词典 IPA 符号 → 模型音素符号。KEY 是 tokenize_ipa 产出的词典符号；VALUE 是模型词表符号。
# Task 8 覆盖率门 dump 模型 alphabet 后回填（identity-first + 人工补全），随测试覆盖扩展。
IPA_TO_ESPEAK: dict[str, str] = {}

# 模型词表中「英语音素」子集（GOP margin 分母用；排除其他语言结构性异类音素与 blank）。
# Task 8 覆盖率门 dump 后回填。
ENGLISH_ESPEAK_SYMBOLS: frozenset[str] = frozenset()

_STRIP_CHARS = "ˈˌ-"   # 重音主次、连字符是元字符，剥离；ː 是符号组成部分，保留


def tokenize_ipa(ipa: str, *, symbols: tuple[str, ...] = DICT_IPA_SYMBOLS) -> list[str]:
    """词典 IPA → 音素符号列表。剥离 /ˈˌ- 元字符后按最长符号匹配（trie）。
    未匹配字符抛 ValueError（调用方据此判该词覆盖率 0，而非静默错分）。"""
    syms = sorted(symbols, key=len, reverse=True)
    body = ipa.strip().strip("/").translate(str.maketrans("", "", _STRIP_CHARS))
    out: list[str] = []
    i = 0
    while i < len(body):
        for s in syms:
            if body.startswith(s, i):
                out.append(s)
                i += len(s)
                break
        else:
            raise ValueError(f"unmapped IPA symbol at {body[i:]!r} in {ipa!r}")
    return out


def coverage_report(words: list[tuple[str, str | None]], mapping: dict[str, str],
                    english_symbols: frozenset[str]) -> dict:
    """对 dictionary.json 全部目标词 IPA 逐符号算覆盖率。
    规则：词全部音素符号在 mapping 且映射后 ∈ english_symbols → coverable；否则 partial。
    裁决：ratio ≥ 0.8 → ok；0.5 ≤ ratio < 0.8 → partial；< 0.5 → defer（A 转 phase-7）。"""
    total = len(words)
    coverable = partial = 0
    unmapped: set[str] = set()
    unaligned: set[str] = set()
    for _lemma, ipa in words:
        if not ipa:
            partial += 1
            continue
        try:
            toks = tokenize_ipa(ipa)
        except ValueError:
            partial += 1
            continue
        mapped: list[str] = []
        ok = True
        for t in toks:
            e = mapping.get(t)
            if e is None:
                unmapped.add(t)
                ok = False
            else:
                mapped.append(e)
        if ok and all(s in english_symbols for s in mapped):
            coverable += 1
        else:
            partial += 1
            if ok:
                unaligned.update(s for s in mapped if s not in english_symbols)
    ratio = coverable / total if total else 0.0
    verdict = "ok" if ratio >= 0.8 else ("partial" if ratio >= 0.5 else "defer")
    return {"total": total, "coverable": coverable, "partial": partial,
            "ratio": round(ratio, 4), "verdict": verdict,
            "unmapped_symbols": sorted(unmapped), "unaligned_symbols": sorted(unaligned)}
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run --project services/asr-worker pytest tests/test_ipa.py -v
```

期望：全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add services/asr-worker/asr_worker/ipa.py services/asr-worker/tests/test_ipa.py
git commit -m "feat(asr): IPA trie tokenizer + coverage pure functions (A gate core)"
```

---

### Task 8: A-2 — scripts/ipa-coverage.py + 装 torch/torchaudio/transformers + 模型 dump + 跑门裁决

**Files:**
- Create: `scripts/ipa-coverage.py`
- Modify: `services/asr-worker/pyproject.toml`（torch/torchaudio/transformers + CPU index）
- Modify: `services/asr-worker/asr_worker/ipa.py`（回填 `IPA_TO_ESPEAK` / `ENGLISH_ESPEAK_SYMBOLS`）
- Create: `services/asr-worker/tests/test_ipa_coverage_cli.py`（CLI 裁决逻辑单测，用 `--mock`）
- Modify: `docs/superpowers/specs/2026-08-08-english-town-phase6-design.md`（裁决记录；可选附注）

**Interfaces:**
- Consumes: Task 7 的 `tokenize_ipa` / `coverage_report` / `IPA_TO_ESPEAK` / `ENGLISH_ESPEAK_SYMBOLS`
- Produces: 覆盖率裁决 `verdict ∈ {ok, partial, defer, model_unavailable}`；`IPA_TO_ESPEAK` / `ENGLISH_ESPEAK_SYMBOLS` 回填值；fixtures 文件 `assets/wordbook/ipa-symbols.json`

> **协调者注意**：`--mock` 单测由 implementer 完成；**真机 dump + 模型下载 + 跑门**由协调者在 Task 6（word_timestamps 验证）后执行。若模型下载失败（本机网络已知多次失败）→ 裁决 `model_unavailable` → **A 全链 defer**（跳过 Task 9-12，phase-6 只交 B+C，A 转 phase-7），如实记录不臆造。

- [ ] **Step 1: pyproject 加依赖（CPU-only torch）**

`services/asr-worker/pyproject.toml`：

```toml
[project]
name = "asr-worker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastapi", "uvicorn", "faster-whisper", "scene-schema",
                "torch", "torchaudio", "transformers"]

[tool.uv.sources]
scene-schema = { workspace = true }
torch = { index = "pytorch-cpu" }
torchaudio = { index = "pytorch-cpu" }

[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true
```

```bash
uv sync --project services/asr-worker
```

> 若 CPU index 网络失败 → 协调者记录，按 `model_unavailable` 裁决路径走（A defer）。

- [ ] **Step 2: 写失败测试 `services/asr-worker/tests/test_ipa_coverage_cli.py`**

```python
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ipa-coverage.py"


def test_cli_mock_verdict_ok():
    out = subprocess.run([sys.executable, str(SCRIPT), "--mock"], capture_output=True, text=True)
    assert out.returncode == 0
    import json
    report = json.loads(out.stdout)
    assert report["verdict"] == "ok"
    assert report["total"] == 4
```

- [ ] **Step 3: 运行确认失败**

```bash
uv run --project services/asr-worker pytest tests/test_ipa_coverage_cli.py -v
```

期望：FAIL（`scripts/ipa-coverage.py` 不存在）。

- [ ] **Step 4: 实现 `scripts/ipa-coverage.py`**

```python
"""GOP 前置门：dictionary.json 全部目标词 IPA × wav2vec2 模型 alphabet → 覆盖率裁决。
<80% → A partial（缺映射词回退代理）；<50% → A defer（phase-6 只交 B+C，A 转 phase-7）。
用法:
  uv run --project services/asr-worker python scripts/ipa-coverage.py --mock          # 单测/演示（内置 mock 映射）
  uv run --project services/asr-worker python scripts/ipa-coverage.py --vocab x.json  # 用已有 dump（确定性）
  uv run --project services/asr-worker python scripts/ipa-coverage.py                  # 真机 dump 模型 alphabet
产物：把 IPA_TO_ESPEAK / ENGLISH_ESPEAK_SYMBOLS 回填进 asr_worker/ipa.py，并写 assets/wordbook/ipa-symbols.json。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from asr_worker.ipa import DICT_IPA_SYMBOLS, ENGLISH_ESPEAK_SYMBOLS, IPA_TO_ESPEAK, coverage_report

MODEL_ID = "facebook/wav2vec2-lv-60-espeak-cv-ft"
ROOT = Path(__file__).resolve().parents[1]

# 参照映射（espeak-ng 音素表惯例；仅当 dump 显示 espeak ASCII 符号集时启用。
# 若 dump 显示 Unicode IPA，则多数词典符号与模型符号同形，identity 映射即够）。
# 词典符号 → espeak ASCII（待真机核对，不臆造最终值）
_ESPEAK_ASCII_REFERENCE = {
    "iː": "i:", "ɪ": "I", "e": "e", "ɛ": "E", "æ": "a", "ɑː": "A:", "ɒ": "Q", "ɔː": "O:",
    "ʊ": "U", "uː": "u:", "ʌ": "V", "ɜː": "3:", "ə": "@", "eɪ": "eI", "aɪ": "aI",
    "ɔɪ": "OI", "aʊ": "aU", "oʊ": "@U",
    "tʃ": "tS", "dʒ": "dZ", "θ": "T", "ð": "D", "ʃ": "S", "ʒ": "Z", "ŋ": "N",
    "p": "p", "b": "b", "t": "t", "d": "d", "k": "k", "ɡ": "g", "f": "f", "v": "v",
    "s": "s", "z": "z", "h": "h", "m": "m", "n": "n", "l": "l", "r": "r", "j": "j", "w": "w",
}


def load_dictionary_words(asset_root: Path) -> list[tuple[str, str | None]]:
    raw = json.loads((asset_root / "wordbook" / "dictionary.json").read_text(encoding="utf-8"))
    return [(w["lemma"], w.get("ipa")) for w in raw["words"]]


def build_mapping_from_vocab(vocab: dict) -> tuple[dict[str, str], set[str]]:
    """第一遍：词典符号与模型词表同形 → 直接映射；不同形 → 依 _ESPEAK_ASCII_REFERENCE 尝试；
    仍未解 → 记 unmapped（人工补）。返回 (mapping, english_symbols)。"""
    mapping: dict[str, str] = {}
    for sym in DICT_IPA_SYMBOLS:
        if sym in vocab:
            mapping[sym] = sym
        elif _ESPEAK_ASCII_REFERENCE.get(sym) in vocab:
            mapping[sym] = _ESPEAK_ASCII_REFERENCE[sym]
    english = {mapping[s] for s in mapping} | set(vocab)
    # 剔除特殊 token（blank/word 分隔）出英语子集：vocab 中纯音素符号（无 < > [ ] 包裹）
    english = {s for s in english if not (s.startswith("<") or s.startswith("["))}
    return mapping, english


def dump_vocab(model_id: str) -> dict:
    """dump 模型词表 → {symbol: id}。模型未下载/加载失败 → 抛异常（调用方裁决 model_unavailable）。"""
    from transformers import Wav2Vec2Processor
    processor = Wav2Vec2Processor.from_pretrained(model_id)
    return dict(processor.tokenizer.get_vocab())


def backfill(mapping: dict[str, str], english: frozenset[str], asset_root: Path) -> None:
    """把映射/英语子集写回 asr_worker/ipa.py，并落 fixtures 供确定性测试复用。"""
    ipa_py = ROOT / "services" / "asr-worker" / "asr_worker" / "ipa.py"
    text = ipa_py.read_text(encoding="utf-8")
    import re
    mapping_literal = "{\n" + "".join(f"    {k!r}: {v!r},\n" for k, v in sorted(mapping.items())) + "}"
    english_literal = "frozenset(" + repr(sorted(english)) + ")"
    text = re.sub(r"IPA_TO_ESPEAK: dict\[str, str\] = \{\}",
                  f"IPA_TO_ESPEAK: dict[str, str] = {mapping_literal}", text)
    text = re.sub(r"ENGLISH_ESPEAK_SYMBOLS: frozenset\[str\] = frozenset\(\)",
                  f"ENGLISH_ESPEAK_SYMBOLS: frozenset[str] = {english_literal}", text)
    ipa_py.write_text(text, encoding="utf-8")
    (asset_root / "wordbook" / "ipa-symbols.json").write_text(
        json.dumps({"ipa_to_espeak": mapping, "english_symbols": sorted(english)},
                   ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocab", help="已有 dump 的 vocab json（跳过模型加载，确定性）")
    ap.add_argument("--mock", action="store_true", help="内置 mock 词表跑覆盖率逻辑（单测）")
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args(argv)
    words = load_dictionary_words(ROOT / "assets")
    if args.mock:
        mapping = {"l": "l", "oʊ": "o", "f": "f", "b": "b", "r": "r", "e": "e",
                   "d": "d", "aɪ": "aI", "ɔː": "O:"}
        english = frozenset("l o f b r e d aI O:".split())
        report = coverage_report(words, mapping, english)
    elif args.vocab:
        vocab = json.loads(Path(args.vocab).read_text(encoding="utf-8"))
        mapping, english = build_mapping_from_vocab(vocab)
        report = coverage_report(words, mapping, english)
    else:
        try:
            vocab = dump_vocab(MODEL_ID)
        except Exception as e:  # noqa: BLE001 —— 模型未下载/加载失败 → 如实裁决
            print(json.dumps({"verdict": "model_unavailable", "error": str(e),
                              "total": len(words)}, ensure_ascii=False))
            return 2
        mapping, english = build_mapping_from_vocab(vocab)
        report = coverage_report(words, mapping, english)
        backfill(mapping, frozenset(english), ROOT / "assets")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verdict"] != "defer" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 运行确认通过**

```bash
uv run --project services/asr-worker pytest tests/test_ipa_coverage_cli.py -v
```

期望：PASS。

- [ ] **Step 6: 协调者真机跑门（需 Task 6 后）**

```bash
uv run --project services/asr-worker python scripts/ipa-coverage.py --device cpu
```

- 裁决 `ok`（ratio ≥ 0.8）→ **A 继续**（Task 9-12）。
- 裁决 `partial`（0.5–0.8）→ **A 继续**，缺映射词回退代理（记录在裁决报告）。
- 裁决 `defer`（< 0.5）或 `model_unavailable` → **A defer**，phase-6 只交 B+C，A 转 phase-7，跳过 Task 9-12。
- 对 `report["unmapped_symbols"]` / `["unaligned_symbols"]`：依 `_ESPEAK_ASCII_REFERENCE` 人工补全映射后**重跑**一次（映射表为可扩展 dict）。

- [ ] **Step 7: 记录裁决 + 提交**

把裁决结果（ratio / verdict / unmapped）追加到设计文档 `docs/superpowers/specs/2026-08-08-english-town-phase6-design.md` 的 §4.3 附注或评审响应文档；提交：

```bash
git add scripts/ipa-coverage.py services/asr-worker/pyproject.toml services/asr-worker/uv.lock services/asr-worker/asr_worker/ipa.py services/asr-worker/tests/test_ipa_coverage_cli.py assets/wordbook/ipa-symbols.json docs/superpowers/specs/2026-08-08-english-town-phase6-design.md
git commit -m "feat(scripts): IPA coverage gate CLI + torch deps + gate verdict (A preflight)"
```

---

### Task 9: A-3 — asr-worker pronunciation.py（PhonemeAligner + score_gop + /pronounce）+ selfcheck gop_ok

**Files:**
- Create: `services/asr-worker/asr_worker/pronunciation.py`
- Modify: `services/asr-worker/asr_worker/server.py`（新增 `POST /pronounce`）
- Modify: `services/asr-worker/asr_worker/selfcheck.py`（新增 `gop_ok` 三态）
- Create: `services/asr-worker/tests/test_pronunciation.py`

**Interfaces:**
- Consumes: Task 7 的 `tokenize_ipa` / `IPA_TO_ESPEAK` / `ENGLISH_ESPEAK_SYMBOLS`（Task 8 已回填）
- Produces:
  - `expected_phonemes_from_ipa(ipa, *, ipa_to_espeak=None) -> list[str] | None`
  - `score_gop(audio_wav: bytes, expected_phonemes: list[str], aligner, *, device="cpu") -> dict | None`
  - `PhonemeAligner(device)` + `.available` / `.align(audio, expected_phonemes) -> AlignResult`
  - `POST /pronounce {wav_b64, ipa, device?}` → `{"gop", "phoneme_scores", "degraded"}`
  - `selfcheck.run()` 输出 `gop_ok` 三态（`ok`/`degraded`/`unavailable`）

> **动手前必须 dump 核对**（评审未决验证项）：在本 venv 装好 torch/torchaudio 后先跑
> `python -c "import torchaudio.functional as f, inspect; print(inspect.signature(f.forced_align))"`，
> 核对 `forced_align(emission, targets, input_lengths, target_lengths, blank=0)` 签名与返回 `(paths, neg_log_likelihood)`；若签名不同，按实测调整 Step 3/4 的对齐调用。

- [ ] **Step 1: 写失败测试 `services/asr-worker/tests/test_pronunciation.py`**

```python
import math

import numpy as np
import pytest

from asr_worker.pronunciation import AlignResult, _target_segments, expected_phonemes_from_ipa, score_gop


class _MockAligner:
    """固定后验 + 固定音素区间：断言 margin 公式、分母排除自身、blank 排除，mock 断言精确值。"""
    def __init__(self, posteriors, segments, symbol_by_col):
        self._posteriors = posteriors
        self._segments = segments
        self._symbol_by_col = symbol_by_col

    def align(self, audio, expected_phonemes):
        return AlignResult(self._posteriors, self._segments, self._symbol_by_col)


def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def test_target_segments_skip_blank_frames():
    path = [0, 0, 1, 1, 1, 1]            # 0=blank；2..5=token1（l）
    segs = _target_segments(path, [1], blank=0, id_to_phone={1: "l"})
    assert segs == [("l", 2, 5)]         # blank 帧不进入音素区间


def test_score_gop_margin_excludes_self_and_blank():
    # 列：0=l, 1=o, 2=f, 3=x（同属英语子集，竞争者）, 4=blank（非英语子集，应排除）
    symbol_by_col = {0: "l", 1: "o", 2: "f", 3: "x", 4: "<pad>"}
    posteriors = np.array([
        [0.8, 0.05, 0.05, 0.05, 0.0],    # l 帧：P(l)=0.8
        [0.05, 0.7, 0.10, 0.10, 0.05],   # o 帧：P(o)=0.7
        [0.05, 0.10, 0.70, 0.10, 0.05],  # f 帧：P(f)=0.7
    ], dtype=np.float32)
    segments = [("l", 0, 1), ("o", 1, 2), ("f", 2, 3)]
    aligner = _MockAligner(posteriors, segments, symbol_by_col)
    english = frozenset({"l", "o", "f", "x"})
    result = score_gop(b"\x00\x00" * 1600, ["l", "o", "f"], aligner,
                       ipa_to_espeak={"l": "l", "oʊ": "o", "f": "f"}, english_symbols=english)
    assert result is not None and result["degraded"] is False
    # l: log(0.8) − max(log 0.05, log 0.05, log 0.05) = log(16) → sigmoid
    assert result["phoneme_scores"]["l"] == pytest.approx(_sigmoid(math.log(16)), rel=1e-4)
    # o: log(0.7) − max(log 0.05, log 0.10, log 0.10)=log(0.7)−log(0.10)=log(7)
    assert result["phoneme_scores"]["o"] == pytest.approx(_sigmoid(math.log(7)), rel=1e-4)
    # gop = mean of 3 phone scores
    assert result["gop"] == pytest.approx(
        sum(result["phoneme_scores"].values()) / 3, rel=1e-6)


def test_expected_phonemes_from_ipa_unmapped_returns_none():
    assert expected_phonemes_from_ipa("/loʊf/", ipa_to_espeak={"l": "l", "oʊ": "o"}) is None   # f 缺映射
    assert expected_phonemes_from_ipa("/loʊf/", ipa_to_espeak={"l": "l", "oʊ": "o", "f": "f"}) == ["l", "o", "f"]


def test_score_gop_empty_phonemes_degrades():
    assert score_gop(b"\x00\x00" * 16, [], _MockAligner(np.zeros((1, 3), np.float32), [], {0: "l"})) is None
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run --project services/asr-worker pytest tests/test_pronunciation.py -v
```

期望：FAIL（module 不存在）。

- [ ] **Step 3: 实现 `services/asr-worker/asr_worker/pronunciation.py`**

```python
"""GOP 音素级发音评测：wav2vec2 音素 CTC 模型 + torchaudio forced_align。
公式（评审阻塞 3 修正）：margin(p) = log P(p|X_p) − max_{p'≠p} log P(p'|X_p)
  —— 分母排除自身（否则正确音素 margin=0 全塌缩）、排除 CTC blank 帧、分母限英语音素子集。
score_gop 是纯函数（注入 aligner）；生产 aligner = torchaudio + HF wav2vec2，惰性加载。"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from asr_worker.ipa import ENGLISH_ESPEAK_SYMBOLS, IPA_TO_ESPEAK, tokenize_ipa

log = logging.getLogger(__name__)

MODEL_ID = "facebook/wav2vec2-lv-60-espeak-cv-ft"
BLANK_ID = 0


@dataclass
class AlignResult:
    posteriors: np.ndarray            # (T, C) 概率，含 blank 列
    segments: list[tuple[str, int, int]]   # (espeak 音素, i0, i1) 目标音素帧区间（不含 blank 帧）
    symbol_by_col: dict[int, str]     # 列 → 模型符号名


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _target_segments(paths: list[int], targets: list[int], *, blank: int,
                     id_to_phone: dict[int, str]) -> list[tuple[str, int, int]]:
    """CTC 路径 → 目标音素帧区间 [(phone, i0, i1)]：跳过 blank，按目标顺序分组连续同音素。"""
    segs: list[tuple[str, int, int]] = []
    i, n = 0, len(paths)
    while i < n and len(segs) < len(targets):
        if paths[i] == blank:
            i += 1
            continue
        j = i
        while j < n and paths[j] == paths[i]:
            j += 1
        segs.append((id_to_phone.get(paths[i], f"?{paths[i]}"), i, j))
        i = j
    return segs


def expected_phonemes_from_ipa(ipa: str, *, ipa_to_espeak: dict[str, str] | None = None) -> list[str] | None:
    """词典 IPA → espeak 音素序列。任一符号缺映射 → None（该词回退代理，/pronounce 返回 degraded）。"""
    table = IPA_TO_ESPEAK if ipa_to_espeak is None else ipa_to_espeak
    try:
        toks = tokenize_ipa(ipa)
    except ValueError:
        return None
    phones: list[str] = []
    for t in toks:
        e = table.get(t)
        if e is None:
            return None
        phones.append(e)
    return phones


class PhonemeAligner:
    """惰性加载 wav2vec2 音素 CTC 模型；对齐得到目标音素帧区间 + 帧级后验。默认 CPU。"""

    def __init__(self, device: str = "cpu") -> None:
        self.device = device
        self._model = None
        self._processor = None

    def load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
        self._processor = Wav2Vec2Processor.from_pretrained(MODEL_ID)
        self._model = Wav2Vec2ForCTC.from_pretrained(MODEL_ID).to(self.device).eval()

    @property
    def available(self) -> bool:
        try:
            self.load()
            return True
        except Exception as e:  # noqa: BLE001 —— 模型未下载/加载异常 → 全链路回退代理
            log.warning("phoneme model load failed: %s", e)
            return False

    def _symbol_by_col(self) -> dict[int, str]:
        ids = list(range(self._processor.tokenizer.vocab_size))
        toks = self._processor.tokenizer.convert_ids_to_tokens(ids)
        return {i: (t if t is not None else "<pad>") for i, t in enumerate(toks)}

    def align(self, audio: np.ndarray, expected_phonemes: list[str]) -> AlignResult:
        """audio: 16k float32 词窗段。返回 AlignResult（对齐 API 以实机 dump 为准，见 Task 9 头注）。"""
        import torch
        from torchaudio.functional import forced_align
        self.load()
        ids = [self._processor.tokenizer.convert_tokens_to_ids(p) for p in expected_phonemes]
        with torch.inference_mode():
            feats = self._processor(torch.from_numpy(audio).float(),
                                    sampling_rate=16000, return_tensors="pt").input_values
            logits = self._model(feats.to(self.device)).logits[0]     # (T, C)
            probs = torch.softmax(logits, dim=-1)
            input_lengths = torch.tensor([logits.shape[0]])
            target_lengths = torch.tensor([len(ids)])
            paths, _nll = forced_align(logits.cpu(), torch.tensor([ids]),
                                       input_lengths, target_lengths, blank=BLANK_ID)
        id_to_phone = dict(zip(ids, expected_phonemes))
        segments = _target_segments(paths[0].tolist(), ids, blank=BLANK_ID, id_to_phone=id_to_phone)
        return AlignResult(probs.cpu().numpy(), segments, self._symbol_by_col())


def _margin_scores(posteriors: np.ndarray, segments: list[tuple[str, int, int]],
                   english_symbols: frozenset[str], symbol_by_col: dict[int, str]) -> dict[str, float]:
    """每个目标音素：对对齐帧区间求 P(p) 均值，margin = log P(p) − max_{p'≠p, p'∈英语子集} log P(p')。
    blank 列不在英语子集内 → 分母自动排除。"""
    out: dict[str, float] = {}
    for phone, i0, i1 in segments:
        block = posteriors[i0:i1]
        if block.shape[0] == 0:
            continue
        p_self = float(block[:, symbol_by_col[phone]].mean())
        comps = [float(block[:, c].mean())
                 for c in range(posteriors.shape[1])
                 if c != symbol_by_col[phone]
                 and symbol_by_col[c] in english_symbols]
        p_comp = max(comps) if comps else 0.0
        margin = math.log(p_self) - (math.log(p_comp) if p_comp > 0 else 0.0)
        out[phone] = _sigmoid(margin)
    return out


def score_gop(audio_wav: bytes, expected_phonemes: list[str], aligner, *,
              device: str = "cpu", ipa_to_espeak: dict[str, str] | None = None,
              english_symbols: frozenset[str] | None = None) -> dict | None:
    """audio_wav: 目标词音频段（16k mono PCM16）。expected_phonemes: espeak 音素序列。
    aligner 注入（生产 = PhonemeAligner，测试 = mock 固定后验）。
    → {"gop": 0.0..1.0, "phoneme_scores": {phone: 0.0..1.0}, "degraded": False} | None（降级）"""
    if not expected_phonemes:
        return None
    audio = np.frombuffer(audio_wav, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size < 1600:              # < 100ms 音频，无评分意义
        return None
    try:
        res = aligner.align(audio, expected_phonemes)
    except Exception as e:  # noqa: BLE001 —— 对齐异常 → 降级（记日志，不写证据）
        log.warning("forced align failed: %s", e)
        return None
    english = ENGLISH_ESPEAK_SYMBOLS if english_symbols is None else english_symbols
    phoneme_scores = _margin_scores(res.posteriors, res.segments, english, res.symbol_by_col)
    if not phoneme_scores:
        return None
    return {"gop": float(np.mean(list(phoneme_scores.values()))),
            "phoneme_scores": phoneme_scores, "degraded": False}
```

- [ ] **Step 4: server.py 新增 `POST /pronounce`**

```python
from asr_worker.pronunciation import PhonemeAligner, expected_phonemes_from_ipa, score_gop

PRONUNCIATION: "PhonemeAligner | None" = None


class PronounceRequest(BaseModel):
    wav_b64: str
    ipa: str
    device: str = "cpu"


@app.post("/pronounce")
async def pronounce(req: "PronounceRequest") -> dict:
    """GOP 评分：16k mono PCM16 词窗段 + 词典 IPA → 音素级 GOP。
    模型缺失/缺映射/对齐异常 → degraded 响应（api 侧据此回退词级代理，不产生证据）。"""
    import base64
    import logging
    global PRONUNCIATION
    if PRONUNCIATION is None:
        PRONUNCIATION = PhonemeAligner(device=req.device)
    if not PRONUNCIATION.available:
        return {"gop": None, "phoneme_scores": {}, "degraded": True}
    phones = expected_phonemes_from_ipa(req.ipa)
    if phones is None:
        return {"gop": None, "phoneme_scores": {}, "degraded": True}
    try:
        result = score_gop(base64.b64decode(req.wav_b64), phones, PRONUNCIATION, device=req.device)
    except Exception as e:  # noqa: BLE001 —— 评分失败降级，不污染证据流
        logging.getLogger("asr_worker").warning("pronounce failed: %s", e)
        return {"gop": None, "phoneme_scores": {}, "degraded": True}
    if result is None:
        return {"gop": None, "phoneme_scores": {}, "degraded": True}
    return result
```

- [ ] **Step 5: selfcheck.py 新增 `gop_ok` 三态**

在 `selfcheck.run` 返回 dict 中加字段：

```python
    gop_ok = "unavailable"   # 未启用 / 模型缺失
    if os.environ.get("PRONUNCIATION_GOP_ENABLED", "").lower() == "true":
        try:
            from asr_worker.pronunciation import PhonemeAligner
            dev = os.environ.get("PRONUNCIATION_GOP_DEVICE", "cpu")
            gop_ok = "ok" if PhonemeAligner(device=dev).available else "degraded"
        except Exception as e:  # noqa: BLE001
            log(f"gop selfcheck failed: {e}")
            gop_ok = "unavailable"
```

在 `run()` 的**两个返回分支**（load 失败、transcribe 失败）与成功返回都带上 `gop_ok`。

- [ ] **Step 6: 运行确认通过**

```bash
uv run --project services/asr-worker pytest tests/test_pronunciation.py -v
```

期望：全部 PASS（mock 精确值断言；真模型区间断言在 Task 9 之后的可选 validation 步骤）。

- [ ] **Step 7: 全量回归（asr-worker，串行）**

```bash
uv run --project services/asr-worker pytest -q
```

期望：全绿（原 8 + 新增）。

- [ ] **Step 8: 提交**

```bash
git add services/asr-worker/asr_worker/pronunciation.py services/asr-worker/asr_worker/server.py services/asr-worker/asr_worker/selfcheck.py services/asr-worker/tests/test_pronunciation.py
git commit -m "feat(asr): GOP scorer (margin excludes self/blank, english denominator) + /pronounce + gop_ok selfcheck"
```

---

### Task 10: A-4 — api 证据接入（settings + WEIGHTS + 可空列 + apply_evidence 分支 + record_round gop_scores + _word_summary）

**Files:**
- Modify: `apps/api/app/settings.py`
- Modify: `apps/api/app/learning/scores.py`
- Modify: `apps/api/app/learning/store.py`（`_migrate` + `all_words`）
- Modify: `apps/api/app/learning/evidence.py`（apply_evidence 分支）
- Modify: `apps/api/app/learning/engine.py`（record_round + `_record_gop`）
- Modify: `apps/api/app/learning/api.py`（`_word_summary` 加 `pronunciation`）
- Modify: `apps/api/tests/test_migration.py`
- Create: `apps/api/tests/test_pronunciation_evidence.py`

**Interfaces:**
- Consumes: Task 9 的 `POST /pronounce` 响应形状 `{"gop", "phoneme_scores", "degraded"}`
- Produces:
  - `Settings.pronunciation_gop_*` 六字段
  - `WEIGHTS["pronunciation_gop"] = (0.5, "pronunciation_gop")`
  - `mastery_states.pronunciation_score REAL`（可空）列
  - `apply_evidence` 对 `source == "pronunciation_gop"` 只更新轴分、不计数、不进排期（参照 word_production 先例）
  - `record_round(..., gop_scores: dict[str, float] | None = None)`
  - `/progress/summary` 的 `scores.pronunciation`（可空）

- [ ] **Step 1: settings.py 加字段**

dataclass 阶段 5 块后加：

```python
    # --- 阶段 6：GOP 音素级发音评测 ---
    pronunciation_gop_enabled: bool = False          # 默认关
    pronunciation_gop_model: str = "facebook/wav2vec2-lv-60-espeak-cv-ft"
    pronunciation_gop_device: str = "cpu"            # 默认 CPU，规避 ct2/torch CUDA 冲突
    pronunciation_gop_min_word_ms: int = 120         # 词窗最短时长，过短不评
    pronunciation_gop_word_pad_ms: int = 100         # 词窗双侧 pad，补偿 whisper 时间戳误差
    pronunciation_gop_min_conf: float | None = None  # 阈值先 None；kokoro golden 分布量后再定
```

`_ENV_FIELDS` 加六条：

```python
        "pronunciation_gop_enabled": "PRONUNCIATION_GOP_ENABLED",
        "pronunciation_gop_model": "PRONUNCIATION_GOP_MODEL",
        "pronunciation_gop_device": "PRONUNCIATION_GOP_DEVICE",
        "pronunciation_gop_min_word_ms": "PRONUNCIATION_GOP_MIN_WORD_MS",
        "pronunciation_gop_word_pad_ms": "PRONUNCIATION_GOP_WORD_PAD_MS",
        "pronunciation_gop_min_conf": "PRONUNCIATION_GOP_MIN_CONF",
```

`from_env` 的解析链在 `elif isinstance(default, float):` 后插入 min_conf 特例：

```python
                elif field == "pronunciation_gop_min_conf":
                    kw[field] = float(raw) if raw not in ("", "none", "null") else None
```

- [ ] **Step 2: scores.py 加 WEIGHTS 项**

```python
    "pronunciation_gop": (0.5, "pronunciation_gop"),
```

- [ ] **Step 3: store.py `_migrate` 加可空列 + `all_words` 选列**

```python
    def _migrate(self) -> None:
        """旧库幂等迁移：mastery_states 缺列则 ALTER 加列。
        asr 代理列 NOT NULL DEFAULT 0.0；pronunciation_score 可空（NULL=未评测，与 0 分区分）。"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(mastery_states)")}
        if "asr_word_confidence_score" not in cols:
            self.conn.execute(
                "ALTER TABLE mastery_states ADD COLUMN asr_word_confidence_score REAL NOT NULL DEFAULT 0.0")
        if "pronunciation_score" not in cols:
            self.conn.execute("ALTER TABLE mastery_states ADD COLUMN pronunciation_score REAL")
        self.conn.commit()
```

`all_words` SELECT 加（不 COALESCE，保留 NULL）：

```python
            "       ms.asr_word_confidence_score AS asr_word_confidence_score, "
            "       ms.pronunciation_score AS pronunciation_score "
```

- [ ] **Step 4: evidence.py 加 pronunciation_gop 分支**

在 `apply_evidence` 的 `word_production` 分支（evidence.py:59-69）之后加：

```python
    if source == "pronunciation_gop":
        # 音素级 GOP：只更新 pronunciation_score 轴分（可空列，NULL=未评测）+ 证据明细；
        # 不计数（与词级置信度同理，防双重计 attempts）、不进排期、不参与选词/薄弱词排序。
        m = store.get_mastery(user_id, wid)
        if m is None:
            store.upsert_mastery(user_id, wid, updated_at=now.isoformat())
            m = store.get_mastery(user_id, wid)
        store.upsert_mastery(user_id, wid,
                             pronunciation_score=update_score(m["pronunciation_score"] or 0.0, weight, ev["confidence"]),
                             updated_at=now.isoformat())
        return
```

- [ ] **Step 5: engine.py 加 `gop_scores` 参数 + `_record_gop`**

`record_round` 签名与尾部：

```python
    def record_round(self, session_id: str, scene_words: dict, npc_text: str,
                     user_text: str, confidence: float, *, turn_id: str,
                     target_word_ids: set[str], attempt_id: str | None = None,
                     words: list[dict] | None = None,
                     gop_scores: dict[str, float] | None = None) -> int:
        conf = normalize_asr_confidence(confidence) if confidence < 0 else confidence
        drafts = classify_round(scene_words, npc_text, user_text, conf,
                                target_word_ids=target_word_ids)
        for d in drafts:
            now = datetime.now(timezone.utc)
            evidence = {...}
            self.record_evidence(session_id, evidence, event_id=evidence["evidence_id"])
        if words is not None:
            self._record_word_production(session_id, scene_words, user_text, words,
                                         drafts, turn_id, attempt_id)
        if gop_scores:
            self._record_gop(session_id, drafts, turn_id, attempt_id, gop_scores)
        return len(drafts)
```

新增方法：

```python
    def _record_gop(self, session_id: str, drafts: list[dict], turn_id: str,
                    attempt_id: str | None, gop_scores: dict[str, float]) -> None:
        """先算后写：gop_scores 是锁外 /pronounce 的纯数据；这里锁内只写证据 + 更新列。
        只对用户产出词（在 drafts 中）写入；result 依 min_conf（None=只入证据不判）。"""
        draft_ids = {d["word_id"] for d in drafts}
        now = datetime.now(timezone.utc)
        min_conf = self.settings.pronunciation_gop_min_conf
        for wid, gop in gop_scores.items():
            if wid not in draft_ids:
                continue
            result = "success" if (min_conf is None or gop >= min_conf) else "uncertain"
            evidence = {
                "evidence_id": f"ev_{uuid.uuid4().hex[:12]}",
                "event_seq": 0, "session_id": session_id,
                "attempt_id": attempt_id or f"attempt_{turn_id}",
                "turn_id": turn_id, "objective_id": f"obj_scene_{wid}",
                "word_id": wid, "source": "pronunciation_gop", "prompt_level": 0,
                "axis": "pronunciation_gop", "result": result, "confidence": gop,
                "evidence_policy_version": self.settings.evidence_policy_version,
                "fsrs_algorithm_version": self.settings.fsrs_algorithm_version,
                "created_at": now.isoformat(),
            }
            self.record_evidence(session_id, evidence, event_id=evidence["evidence_id"])
```

- [ ] **Step 6: api.py `_word_summary` 加 `pronunciation`**

```python
            "scores": {"productive": r["productive_score"], "receptive": r["receptive_score"],
                       "asrConfidence": r["asr_confidence_score"],
                       "asrWordConfidence": r["asr_word_confidence_score"],
                       "pronunciation": r["pronunciation_score"]},
```

- [ ] **Step 7: 写失败测试 `apps/api/tests/test_pronunciation_evidence.py`**

```python
from app.event_store import EventStore
from app.learning.engine import LearningEngine
from app.learning.store import LearningStore
from app.settings import Settings


def _build(tmp_path, name):
    """EventStore + LearningStore + 预置 loaf 已 known（learning，正 stability）。
    镜像 test_word_confidence._build 线程化端到端模式。"""
    events = EventStore(tmp_path / name)
    conn = events.connection
    store = LearningStore(conn)
    conn.execute("INSERT INTO word_lists(user_id,list_id,name,created_at) "
                 "VALUES('local','l1','q','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO learning_items(word_id,user_id,lemma,pos,ipa,scene_tags,source,list_id,created_at) "
                 "VALUES('word_loaf_n_1','local','loaf','n','/loʊf/','[\"bakery\"]','quest','l1','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO mastery_states(user_id,word_id,state,stability,difficulty,due,last_review,updated_at) "
                 "VALUES('local','word_loaf_n_1','learning',3.0,5.0,'2026-08-08T00:00:00Z','2026-08-08T00:00:00Z','2026-08-08T12:00:00Z')")
    eng = LearningEngine(store, events, Settings())
    return store, eng


def test_gop_evidence_persisted_through_record_round(tmp_path):
    store, eng = _build(tmp_path, "e.db")
    n = eng.record_round(
        "s1", {"word_loaf_n_1": "loaf"}, "what do you need", "I want a loaf", 0.9,
        turn_id="t1", target_word_ids={"word_loaf_n_1"},
        words=[{"word": "loaf", "start": 0.4, "end": 0.9, "probability": 0.95}],
        gop_scores={"word_loaf_n_1": 0.8})
    assert n == 1
    rows = [r for r in store.evidence_for_word("local", "word_loaf_n_1")
            if r["source"] == "pronunciation_gop"]
    assert len(rows) == 1
    assert rows[0]["axis"] == "pronunciation_gop"
    assert rows[0]["result"] == "success"      # min_conf=None → 只入证据不判
    assert abs(rows[0]["confidence"] - 0.8) < 1e-6
    m = store.get_mastery("local", "word_loaf_n_1")
    # update_score(0.0, 0.5, 0.8) = min(1, 0 + 0.35*0.5*0.8) = 0.14
    assert m["pronunciation_score"] is not None
    assert abs(m["pronunciation_score"] - 0.14) < 1e-6


def test_gop_non_counting_and_non_scheduling(tmp_path):
    store_a, eng_a = _build(tmp_path, "a.db")
    store_b, eng_b = _build(tmp_path, "b.db")
    kw = {"session_id": "s1", "scene_words": {"word_loaf_n_1": "loaf"},
          "npc_text": "what do you need", "user_text": "I want a loaf", "confidence": 0.9,
          "turn_id": "t1", "target_word_ids": {"word_loaf_n_1"},
          "words": [{"word": "loaf", "start": 0.4, "end": 0.9, "probability": 0.95}]}
    eng_a.record_round(**kw)                                            # gop_scores=None
    eng_b.record_round(**kw, gop_scores={"word_loaf_n_1": 0.8})
    a = store_a.get_mastery("local", "word_loaf_n_1")
    b = store_b.get_mastery("local", "word_loaf_n_1")
    # 不计数：attempts/success/exposure 两 run 一致
    for col in ("attempts", "success_count", "exposure_count",
                "scaffolded_success_count", "help_count"):
        assert a[col] == b[col], col
    # 不进排期：排期字段一致（日期精度比较）
    for col in ("state", "stability", "difficulty", "reps", "lapses",
                "last_scheduled_rating", "last_scheduled_date"):
        assert a[col] == b[col], col
    assert a["due"] and b["due"] and a["due"][:10] == b["due"][:10]
    # 轴分：仅带 gop 的 run 落 pronunciation_score；无 gop → NULL（未评测）
    assert a["pronunciation_score"] is None
    assert b["pronunciation_score"] is not None
    # 无 gop_scores → 无 pronunciation_gop 证据；词级代理证据不受影响
    assert len([r for r in store_a.evidence_for_word("local", "word_loaf_n_1")
                if r["source"] == "pronunciation_gop"]) == 0


def test_gop_evidence_absent_when_user_did_not_produce(tmp_path):
    store, eng = _build(tmp_path, "e.db")
    eng.record_round(
        "s1", {"word_loaf_n_1": "loaf"}, "what do you need", "show me the jar", 0.9,
        turn_id="t1", target_word_ids={"word_loaf_n_1"}, words=None,
        gop_scores={"word_loaf_n_1": 0.8})   # 词不在 drafts（user 未产出）→ 不写 GOP 证据
    assert len([r for r in store.evidence_for_word("local", "word_loaf_n_1")
                if r["source"] == "pronunciation_gop"]) == 0
```

- [ ] **Step 8: `test_migration.py` 加可空列幂等断言**

参照既有 `test_migration_adds_column_and_memory_state_to_existing_db` 模式，加：

```python
def test_migrate_pronunciation_score_nullable_idempotent(tmp_path):
    """加列幂等 + 可空无默认（NULL=未评测；与 asr 代理列的 NOT NULL DEFAULT 0.0 区分）。"""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_MASTERY)          # 模拟 phase-4 旧库（无新列）
    conn.commit(); conn.close()
    conn2 = sqlite3.connect(db)
    st = LearningStore(conn2)                  # 构造跑 _migrate()
    rows = conn2.execute("PRAGMA table_info(mastery_states)").fetchall()
    cols = {r[1] for r in rows}
    assert "pronunciation_score" in cols
    row = next(r for r in rows if r[1] == "pronunciation_score")
    assert row[3] == 0 and row[4] is None      # notnull=0、无默认值（可空）
    st._migrate()                              # 幂等：再跑不报错
    assert "pronunciation_score" in {r[1] for r in conn2.execute("PRAGMA table_info(mastery_states)")}
```

（`_OLD_MASTERY` / `sqlite3` 为该文件既有内容，直接沿用。）

- [ ] **Step 9: 运行确认通过**

```bash
uv run --project apps/api pytest tests/test_pronunciation_evidence.py tests/test_migration.py -v
```

期望：全部 PASS。

- [ ] **Step 10: 全量回归（api，串行）**

```bash
uv run --project apps/api pytest -q
```

期望：全绿。

- [ ] **Step 11: 提交**

```bash
git add apps/api/app/settings.py apps/api/app/learning/scores.py apps/api/app/learning/store.py apps/api/app/learning/evidence.py apps/api/app/learning/engine.py apps/api/app/learning/api.py apps/api/tests/test_migration.py apps/api/tests/test_pronunciation_evidence.py
git commit -m "feat(api): pronunciation_score nullable column + pronunciation_gop evidence axis + gop_scores wiring"
```

---

### Task 11: A-5 — api gop_client + ws 先算后写接线

**Files:**
- Create: `apps/api/app/learning/gop_client.py`
- Modify: `apps/api/app/ws.py:117-127`（`_run_round` 锁外调 gop，再 record_round 传 gop_scores）
- Create: `apps/api/tests/test_gop_client.py`

**Interfaces:**
- Consumes: Task 10 的 `record_round(..., gop_scores=...)`、`Settings.pronunciation_gop_*`、`store.get_item("local", word_id)["ipa"]`、`settings.tutor_cache_dir`
- Produces:
  - `_slice_wav_pcm(wav_path, start_s, end_s) -> bytes | None`
  - `score_words_gop(*, wav_path, target_word_ids, scene_words, words, ipa_by_id, settings, pronounce_url, http_post) -> dict[str, float]`
  - `maybe_score_round_gop(*, store, session_id, utterance_id, settings, scene_words, words, target_word_ids, http_post) -> dict[str, float] | None`
  - `post_json(url, body) -> dict`（httpx 辅助）

- [ ] **Step 1: 写失败测试 `apps/api/tests/test_gop_client.py`**

```python
import asyncio
import wave

import pytest

from app.event_store import EventStore
from app.learning.engine import LearningEngine
from app.learning.gop_client import maybe_score_round_gop, score_words_gop
from app.learning.store import LearningStore
from app.settings import Settings


def _write_wav(path, seconds=1.0, sr=16000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"\x00\x00" * int(sr * seconds))


async def _run(coro):
    return await coro


def test_slice_wav_pcm_clamps_to_boundaries(tmp_path):
    from app.learning.gop_client import _slice_wav_pcm
    wav = tmp_path / "u.wav"
    _write_wav(wav, seconds=1.0)
    assert _slice_wav_pcm(wav, 0.0, 0.5) is not None and len(_slice_wav_pcm(wav, 0.0, 0.5)) == 16000
    assert _slice_wav_pcm(wav, 0.5, 2.0) is not None   # end clamp 到 1.0s
    assert _slice_wav_pcm(wav, 2.0, 3.0) is None       # 越界空窗


def test_score_words_gop_pad_and_min_word_ms(tmp_path):
    wav = tmp_path / "u.wav"
    _write_wav(wav, seconds=1.0)
    settings = Settings(pronunciation_gop_word_pad_ms=100, pronunciation_gop_min_word_ms=120)
    calls = []

    async def http_post(url, body):
        calls.append((url, body["ipa"], len(body["wav_b64"])))
        return {"gop": 0.8, "phoneme_scores": {"l": 0.8}, "degraded": False}

    words = [{"word": "loaf", "start": 0.4, "end": 0.6, "probability": 0.95},
             {"word": "a", "start": 0.1, "end": 0.11, "probability": 0.9}]   # 55ms 词窗 → 过短跳过
    scores = asyncio.run(score_words_gop(
        wav_path=wav, target_word_ids={"word_loaf_n_1", "word_a_n_1"},
        scene_words={"word_loaf_n_1": "loaf", "word_a_n_1": "a"},
        words=words, ipa_by_id={"word_loaf_n_1": "/loʊf/", "word_a_n_1": "/ə/"},
        settings=settings, pronounce_url="http://x/pronounce", http_post=http_post))
    assert scores == {"word_loaf_n_1": 0.8}
    assert len(calls) == 1                       # 过短词窗被拦截，不调 /pronounce


def test_score_words_gop_degraded_or_http_error_skipped(tmp_path):
    wav = tmp_path / "u.wav"
    _write_wav(wav, seconds=1.0)
    settings = Settings()

    async def http_post(url, body):
        return {"gop": None, "phoneme_scores": {}, "degraded": True}   # 降级 → 跳过

    words = [{"word": "loaf", "start": 0.1, "end": 0.5, "probability": 0.9}]
    scores = asyncio.run(score_words_gop(
        wav_path=wav, target_word_ids={"word_loaf_n_1"},
        scene_words={"word_loaf_n_1": "loaf"}, words=words,
        ipa_by_id={"word_loaf_n_1": "/loʊf/"}, settings=settings,
        pronounce_url="http://x/pronounce", http_post=http_post))
    assert scores == {}


def test_maybe_score_round_gop_lock_not_held(tmp_path):
    """先算后写时序断言：/pronounce HTTP 调用发生时 events.write_lock 未被持有（锁外）。"""
    events = EventStore(tmp_path / "e.db")
    conn = events.connection
    store = LearningStore(conn)
    conn.execute("INSERT INTO word_lists(user_id,list_id,name,created_at) "
                 "VALUES('local','l1','q','2026-08-08T00:00:00Z')")
    conn.execute("INSERT INTO learning_items(word_id,user_id,lemma,pos,ipa,scene_tags,source,list_id,created_at) "
                 "VALUES('word_loaf_n_1','local','loaf','n','/loʊf/','[\"bakery\"]','quest','l1','2026-08-08T00:00:00Z')")
    wav = tmp_path / "tc" / "pronunciation-audio" / "s1" / "u1.wav"
    _write_wav(wav)
    seen = []

    async def http_post(url, body):
        seen.append(events.write_lock.locked())   # 应为 False（锁外）
        return {"gop": 0.8, "phoneme_scores": {}, "degraded": False}

    settings = Settings(pronunciation_gop_enabled=True, tutor_cache_dir=tmp_path / "tc")
    scores = asyncio.run(maybe_score_round_gop(
        store=store, session_id="s1", utterance_id="u1", settings=settings,
        scene_words={"word_loaf_n_1": "loaf"}, words=[{"word": "loaf", "start": 0.1, "end": 0.5, "probability": 0.9}],
        target_word_ids={"word_loaf_n_1"}, http_post=http_post))
    assert seen == [False]                       # /pronounce 发生在锁外
    assert scores == {"word_loaf_n_1": 0.8}


def test_maybe_score_round_gop_disabled_or_no_wav_returns_none(tmp_path):
    events = EventStore(tmp_path / "e.db")
    store = LearningStore(events.connection)

    async def http_post(url, body):
        raise AssertionError("不应调用")

    settings = Settings(pronunciation_gop_enabled=False)
    assert asyncio.run(maybe_score_round_gop(
        store=store, session_id="s1", utterance_id="u1", settings=settings,
        scene_words={}, words=None, target_word_ids=set(), http_post=http_post)) is None
    settings2 = Settings(pronunciation_gop_enabled=True, tutor_cache_dir=tmp_path / "tc")
    assert asyncio.run(maybe_score_round_gop(
        store=store, session_id="s1", utterance_id="u1", settings=settings2,
        scene_words={}, words=None, target_word_ids=set(), http_post=http_post)) is None   # 无 WAV → 静默跳过
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run --project apps/api pytest tests/test_gop_client.py -v
```

期望：FAIL（module 不存在）。

- [ ] **Step 3: 实现 `apps/api/app/learning/gop_client.py`**

```python
"""GOP 先算后写客户端：run_round 成功后、record_round 前（锁外）调 asr-worker /pronounce。
词窗从授权落盘 WAV 按 whisper word_timestamps 切出（双侧 pad + clamp 到边界）；
缺词窗/未命中/降级/超时 → 该词跳过（无 evidence，回退词级代理）。"""
from __future__ import annotations

import base64
import wave
from pathlib import Path

import httpx


def _slice_wav_pcm(wav_path, start_s: float, end_s: float) -> bytes | None:
    """整段 16k mono PCM16 WAV → [start_s, end_s) 的原始 PCM 切片（clamp 到边界）。
    格式不符/越界空窗 → None。"""
    try:
        with wave.open(str(wav_path), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                return None
            sr = w.getframerate()
            n = w.getnframes()
            i0 = max(0, int(start_s * sr))
            i1 = min(n, int(end_s * sr))
            if i1 <= i0:
                return None
            w.setpos(i0)
            return w.readframes(i1 - i0)
    except Exception:
        return None


def _word_window(word: dict, *, pad_s: float, min_s: float) -> tuple[float, float] | None:
    """whisper 词级时间戳（cross-attention，误差几十~百 ms）双侧 pad + clamp。
    窗口时长 < min_s → None（词窗过短不评）。"""
    if word.get("start") is None or word.get("end") is None:
        return None
    start = max(0.0, float(word["start"]) - pad_s)
    end = float(word["end"]) + pad_s
    if end - start < min_s:
        return None
    return start, end


async def score_words_gop(*, wav_path, target_word_ids, scene_words, words,
                          ipa_by_id, settings, pronounce_url, http_post) -> dict[str, float]:
    """对每个目标词：切词窗 → POST /pronounce → {word_id: gop}。
    http_post(url, body) -> dict（生产 = post_json，测试 = mock）。任何失败/降级 → 跳过。"""
    word_by_text: dict[str, dict] = {}
    for w in words or []:
        t = (w.get("word") or "").strip().lower()
        if t and t not in word_by_text:
            word_by_text[t] = w
    out: dict[str, float] = {}
    for wid in target_word_ids:
        lemma = scene_words.get(wid)
        ipa = ipa_by_id.get(wid)
        if not lemma or not ipa:
            continue
        word = word_by_text.get(lemma.lower())
        if word is None:
            continue
        win = _word_window(word, pad_s=settings.pronunciation_gop_word_pad_ms / 1000.0,
                           min_s=settings.pronunciation_gop_min_word_ms / 1000.0)
        if win is None:
            continue
        pcm = _slice_wav_pcm(wav_path, *win)
        if pcm is None:
            continue
        try:
            resp = await http_post(pronounce_url,
                                   {"wav_b64": base64.b64encode(pcm).decode(), "ipa": ipa})
        except Exception:
            continue
        if not isinstance(resp, dict) or resp.get("degraded") or resp.get("gop") is None:
            continue
        out[wid] = float(resp["gop"])
    return out


async def maybe_score_round_gop(*, store, session_id, utterance_id, settings,
                                scene_words, words, target_word_ids, http_post) -> dict[str, float] | None:
    """先算后写入口：settings 关 / 授权 WAV 缺失 → None（不调 /pronounce）。
    **必须在 events.write_lock 外调用**（锁内调 HTTP+GPU 推理会阻塞全应用证据写入）。"""
    if not getattr(settings, "pronunciation_gop_enabled", False):
        return None
    root = Path(settings.tutor_cache_dir).parent / "pronunciation-audio"
    wav_path = root / session_id / f"{utterance_id}.wav"
    if not wav_path.exists():
        return None
    ipa_by_id: dict[str, str] = {}
    for wid in target_word_ids:
        row = store.get_item("local", wid)
        if row and row["ipa"]:
            ipa_by_id[wid] = row["ipa"]
    pronounce_url = settings.asr_ws_url.replace("/ws/asr", "/pronounce")
    return await score_words_gop(wav_path=wav_path, target_word_ids=target_word_ids,
                                 scene_words=scene_words, words=words, ipa_by_id=ipa_by_id,
                                 settings=settings, pronounce_url=pronounce_url, http_post=http_post)


async def post_json(url: str, body: dict) -> dict:
    """生产 http_post：httpx 单次调用（与 workers.py 模式一致）。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 4: ws.py `_run_round` 先算后写接线**

把 `apps/api/app/ws.py:118-127` 的 `learning.record_round(...)` 改为：

```python
                learning = getattr(app.state, "learning", None)
                if learning and result.get("replied") and state.scene is not None:
                    from app.learning.gop_client import maybe_score_round_gop, post_json
                    # 先算后写：锁外调 /pronounce 得 word_gop（纯数据），锁内只写证据。
                    gop_scores = await maybe_score_round_gop(
                        store=learning.store, session_id=session_id, utterance_id=utterance_id,
                        settings=settings, scene_words=state.scene_words,
                        words=result.get("words"), target_word_ids=state.target_word_ids,
                        http_post=post_json)
                    try:
                        learning.record_round(
                            session_id, state.scene_words,
                            result.get("npcText", ""), result.get("finalText", ""),
                            result.get("confidence", -0.5), turn_id=result["turnId"],
                            target_word_ids=state.target_word_ids,
                            words=result.get("words"), gop_scores=gop_scores)
                    except Exception:  # noqa: BLE001 —— 学习证据失败不杀回合
                        pass
```

> `maybe_score_round_gop` 在 `record_round` 之前 await（锁外）；`record_round` 内部锁内只做短事务 SQL 写入。

- [ ] **Step 5: 运行确认通过**

```bash
uv run --project apps/api pytest tests/test_gop_client.py -v
```

期望：全部 PASS。

- [ ] **Step 6: 全量回归（api，串行）**

```bash
uv run --project apps/api pytest -q
```

期望：全绿。

- [ ] **Step 7: 提交**

```bash
git add apps/api/app/learning/gop_client.py apps/api/app/ws.py apps/api/tests/test_gop_client.py
git commit -m "feat(api): GOP precompute client (compute-before-write) + ws wiring"
```

---

### Task 12: A-6 — ProgressView 收敛（只留 Pronunciation GOP + M2 修复）

**Files:**
- Modify: `apps/web/src/ProgressView.tsx`
- Modify: `apps/web/tests/ProgressView.test.tsx`

**Interfaces:**
- Consumes: Task 10 的 `scores.pronunciation`（可空）字段
- Produces: 用户可见只展示 Pronunciation GOP；ASR 代理列降级到证据详情；M2 词级 chip 与 generic 置信度双渲染消除

- [ ] **Step 1: 改失败测试 `apps/web/tests/ProgressView.test.tsx`**

SUMMARY fixture 的 `scores` 加 `pronunciation`，并把「Word-level ASR confidence」标签断言改为「Pronunciation GOP」：

```tsx
const SUMMARY = {
  ...
  words: [
    { wordId: 'word_loaf_n_1', lemma: 'loaf', pos: 'n', ipa: '/loʊf/', cefr: 'A2',
      sceneTags: ['bakery'], source: 'quest', carrier: 'object',
      scores: { productive: 0.7, receptive: 0.5, asrConfidence: 0.6,
                asrWordConfidence: 0.9, pronunciation: 0.8 },
      fsrs: { state: 'review', due: '2026-08-09T00:00:00Z', reps: 2, lapses: 0 },
      evidenceCount: 3, lastEvidenceAt: null },
  ],
};

it('shows Pronunciation GOP label', async () => {
  render(<ProgressView />);
  await waitFor(() => screen.getByText(/Pronunciation GOP/));
  expect(await screen.findByText(/0\.8/)).toBeInTheDocument();
});

test('does not show ASR proxy chip in row when pronunciation present', async () => {
  render(<ProgressView />);
  await waitFor(() => screen.getByText(/loaf/));
  expect(screen.queryByText(/Word-level ASR confidence/)).not.toBeInTheDocument();
});
```

第三个 test（词级证据详情 `词级 · 0.88`）保留不变（ASR 代理列降级到详情仍显示）。

- [ ] **Step 2: 运行确认失败**

```bash
cd apps/web && npx vitest run tests/ProgressView.test.tsx --maxWorkers=1
```

期望：FAIL（`Pronunciation GOP` 标签不存在 / ASR chip 仍在行内）。

- [ ] **Step 3: 改 `apps/web/src/ProgressView.tsx`**

scores 类型加 `pronunciation`：

```tsx
  scores: { productive: number; receptive: number; asrConfidence: number;
            asrWordConfidence: number; pronunciation: number | null };
```

行内 chip 替换（第 170-173 行）：

```tsx
              {w.scores.pronunciation != null && (
                <span style={chipStyle} title="音素级发音评测（GOP）">
                  Pronunciation GOP {w.scores.pronunciation.toFixed(2)}
                </span>
              )}
```

详情区 M2 修复（第 196-199 行）：`asr_word_confidence` 行显示词级 chip 但**不再**叠加 generic 置信度：

```tsx
                      {ev.axis === 'asr_word_confidence' && (
                        <span style={chipStyle} title="词级对齐时间戳">词级 · {ev.confidence.toFixed(2)}</span>
                      )}
                      {ev.axis !== 'asr_word_confidence' && (
                        <span>（置信度 {ev.confidence.toFixed(2)}）</span>
                      )}
```

- [ ] **Step 4: 运行确认通过**

```bash
cd apps/web && npx vitest run tests/ProgressView.test.tsx --maxWorkers=1
```

期望：全部 PASS。

- [ ] **Step 5: web 全量 + tsc（串行）**

```bash
cd apps/web && npx tsc -b && npx vitest run --maxWorkers=1
```

期望：tsc 0 错误 + 全量 vitest 全绿。

- [ ] **Step 6: 提交**

```bash
git add apps/web/src/ProgressView.tsx apps/web/tests/ProgressView.test.tsx
git commit -m "feat(web): ProgressView shows only Pronunciation GOP; ASR proxies to detail (M2)"
```

---

### Task 13: C.step1/3/4 — kokoro 下载 + VERSION_LOCK 回填 + P50/P95（协调者，环境）

**Files:**
- Modify: `VERSION_LOCK.md`

**Interfaces:**
- Consumes: Task 6（真机转写已通，若有）
- Produces: `e2e_voice_ok` 状态 + VERSION_LOCK 回填（成功）或 deferred + 失败原因

> 环境任务，协调者执行；16GB 严格串行，一次只跑一个 worker。任一下载/安装失败 → 保持 deferred + 记录失败原因，绝不臆造数值。

- [ ] **Step 1: 下载 kokoro 模型（步骤 1）**

```bash
uv run --project services/tts-worker python -m tts_worker.selfcheck
```

期望：`ok` + `synthesize_secs` 实测值；`voices-v1.0.bin` 落缓存。失败 → 记录「deferred: model download required」+ 原因。

- [ ] **Step 2: 全量启动自检**

```bash
uv run python scripts/startup-selfcheck.py
```

记录 `gpu` / `tts.load_secs` / `tts.synthesize_secs` / `asr.load_secs` / `asr.transcribe_secs` / `asr.peak_vram_mib` / `e2e_voice_ok`。

- [ ] **Step 3: 回填 VERSION_LOCK.md（步骤 3）**

把上述实测值填进「待模型下载后实测回填」各 deferred 行；成功项去掉 deferred 标注，失败项保持 + 原因。

- [ ] **Step 4: 延迟 P50/P95（步骤 4）**

```bash
uv run python tests/latency/measure.py
```

需 asr-worker + tts-worker 启动。回填 `p50_ms/p95_ms`；目标 `p95_ms < 1500`（热运行）。失败 → 记录原因 + 保持 deferred。

- [ ] **Step 5: 提交**

```bash
git add VERSION_LOCK.md
git commit -m "docs(lock): backfill perf metrics (C.step1/3/4)"
```

---

### Task 14: 环境清理 — 损坏 ACL 空壳目录（协调者）

**Files:**
- 删除：phase-3/4 遗留的损坏 ACL 空壳目录（memory 记录 2 个；`git worktree` 残留或 `.superpowers` 空壳）

**Interfaces:**
- Consumes: memory 记录（phase3-progress / phase4-progress）
- Produces: 空壳目录移除（不删内容数据）

> 环境任务，协调者执行。仅删项目内的损坏 ACL 空壳目录，不碰内容数据；若涉及提权删除先与用户确认。

- [ ] **Step 1: 定位空壳**

```bash
git worktree list
dir .superpowers 2>$null
```

对比 phase-3/4 memory 记录的路径，确认是损坏 ACL 空壳（空目录或无效 worktree 注册）。

- [ ] **Step 2: 提权删除（Remove-Item -Force / icacls reset）**

```powershell
icacls "<path>" /reset
Remove-Item -Recurse -Force "<path>"
```

- [ ] **Step 3: 验证**

```bash
git worktree prune
Test-Path "<path>"
```

期望：路径不存在、worktree 列表干净。若删除被拒 → 记录并留待提权。

- [ ] **Step 4: 全量最终回归（api + web + asr-worker 串行）**

```bash
uv run --project apps/api pytest -q
cd apps/web && npx tsc -b && npx vitest run --maxWorkers=1
uv run --project services/asr-worker pytest -q
```

期望：api 全绿 / web 全绿 + tsc 0 / asr-worker 全绿。

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "chore: cleanup damaged ACL empty dirs + final regression green"
```

---

## 自审注记

- **Spec 覆盖**：§4.2（模型/CPU/惰性加载/降级）→ Task 8/9；§4.3（公式/分词器/词窗/确定性）→ Task 7/9；§4.4（/pronounce 接口）→ Task 9 + Task 11；§4.5（先算后写/可空列/弃 degraded）→ Task 10/11；§4.6（settings）→ Task 10；§4.7（测试）→ Task 7/8/9/10/11/12；§5.1-5.4（deferred minors）→ Task 2/3/4/5/14；§6（C 步骤）→ Task 1/6/13；§7（容错）→ 各任务内。
- **接口决策（plan 明示）**：`/pronounce` 请求体用 `ipa` 而非设计草拟的 `expected_phonemes`——分词器/映射表在 asr-worker，api 无 torch 依赖（设计 §4.4 精神：新增依赖只进 asr-worker），故由 asr-worker 在 `expected_phonemes_from_ipa` 内转换。
- **诚实失败路径**：Task 8 门 `model_unavailable`/`defer` → 跳过 Task 9-12，phase-6 只交 B+C（A 转 phase-7）；Task 1/6/13 下载失败 → VERSION_LOCK 保持 deferred + 原因。所有失败如实记录。
- **未入计划项（评审建议级）**：kokoro golden 排序断言（正确词 GOP > 改读音）在 Task 13 真机环境可用后补作 validation，不设门禁；授权 WAV 保留/清理策略属设计缺失，计划不臆造，留待 phase-7 或设计补充时定。
