# 英语小镇 · 阶段 2：流式 NPC Actor + Companion Tutor 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把阶段 1 的 `scripted_npc` 本地回复替换为 DeepSeek 驱动的**流式 NPC Actor**（纯英文文本流 → 按句切分 → 逐句 TTS → `delta`/`commit`/`metadata`），并接入 **Companion Tutor**（点击实体 → 带读 + 缓存），同时补齐音频健壮性（AEC / ducking / barge-in）、append-only 打断、双端过期丢弃、成本护栏与分角色超时。验收＝对话稳定（mock 可测）、可打断（真机 + mock 取消均可测）、无状态越权（写实断言）。

**Architecture:** 所有新组件都在 `apps/api` 内（不新增进程）：`app/llm/` 适配层（provider 无关 OpenAI 兼容客户端 + 确定性 mock + 纯函数 chunker/lexmatch/proposals + npc_actor + tutor），配 `llm_log.py`（`llm_calls` 表）。WS 回合改为独立 asyncio 任务（可取消），`run_round` 消费流式回复、逐句 TTS。前端只感知新增消息类型：delta/commit/metadata、companion.ask/reply，以及 ducking/barge-in 的 `RmsGate` 改造。

**Tech Stack:** pnpm workspace（Node 24）+ uv workspace（Python 3.13）；FastAPI + Uvicorn（单 worker）+ SQLite WAL（`session_events` / `llm_calls` / `tutor_cache` 同库）；`openai` SDK（pin，OpenAI 兼容端点，DeepSeek `deepseek-chat` 默认）；React 19 + TS + Vitest（jsdom）。

## Global Constraints

以下约束来自已批准的 spec（`docs/superpowers/specs/2026-08-06-english-town-phase2-design.md` v2；本计划在 Task 3 对 chunker 规则做过两处措辞精度修正，已同步 spec），每个任务隐式遵守：

- **LLM 适配层 provider 无关**：OpenAI 兼容（`base_url` / `model` / `api_key` 可配置），DeepSeek `deepseek-chat` 默认；**无 key 自动落 mock**；拒绝 `deepseek-reasoner` 类模型（不支持 JSON Output，构造时 `ValueError`）。
- **NPC = 纯英文文本流式**（`stream=True`，非 JSON），按句切分（句号/问号/叹号处切句、完整短句照发；20 词强制断句、词边界不断词）→ 逐句 TTS（**串行**：一句 TTS 完成再处理下一句）→ `npc.speech.delta` → 流结束 `npc.speech.commit`（全文，幂等）→ 服务端从 `commit.text` 用 `lexmatch` 派生 `candidateWordIds`（**无任何 LLM 自报字段**）→ `npc.turn.metadata`。TTS 只消费 speech 通道，不能按单词切块。
- **`settings.py` 新增字段**（默认值逐字）：`llm_base_url="https://api.deepseek.com"`、`llm_api_key=""`（env `DEEPSEEK_API_KEY`）、`llm_model="deepseek-chat"`、`llm_connect_timeout_s=1.5`、`llm_ttft_timeout_s=2.0`、`llm_total_timeout_npc_s=3.0`、`llm_total_timeout_tutor_s=6.0`、`llm_max_speech_chars=200`、`llm_max_scaffold_chars=120`、`llm_temperature_npc=0.8`、`llm_temperature_tutor=0.3`、`llm_max_tokens_npc=320`、`llm_max_tokens_tutor=320`、`llm_session_call_cap=200`、`llm_concurrency_limit=2`。全部可 env 覆盖（`Settings.from_env()`）。
- **`llm_calls` 表**（与 `session_events` 同一 SQLite）：见 Task 1 建表 SQL；`fallback_reason` 枚举仅允许 `timeout|connect|invalid_json|schema_reject|length_truncated|no_key|budget|none`；每次调用写一行（含降级原因），超时/校验失败/预算拦截记 `ok=0`。
- **双通道消息**：`npc.speech.delta` / `npc.speech.commit` / `npc.turn.metadata` / `tts.audio.start` / `tts.audio.end` 全部带 `generationId` + `turnId`。`commit` 是权威全文，前端用它**覆盖**已累积的 delta 字幕。
- **双端过期丢弃硬规则**：服务端发送前比对 `session.currentGenerationId`（阶段 2 静态场景＝会话级固定值），不匹配一律不发；前端持有 `currentTurnId` + `currentGenerationId`，收到非当前 turnId 的 `delta/commit/metadata/tts.audio.start` 一律丢弃——不入字幕、不进播放队列。
- **音频健壮性（必做）**：`getUserMedia` 显式 `{ echoCancellation: true, noiseSuppression: true, autoGainControl: true }`；TTS 播放期 RMS ducking（增益因子默认 ~2×）；退出播放后 **300ms** 才恢复；barge-in **连续 300ms** 超阈才算有效（默认，可配置）；服务端 `playbackState`（`tts.audio.start` 置 playing，`tts.audio.end`/打断 置 idle）；播放中收到 `audio.start` 视为 barge-in：取消 pending 回合 + 标记打断；**spurious 守卫**——`audio.start` 后 500ms 内无任何音频帧到达则自动忽略。耳机与扬声器两种都要冒烟。
- **append-only 事件日志**：`session_events` 不得 UPDATE；`dialogue.turn` 先写库（含全文 npcText）再发 `commit`；打断追加独立 `dialogue.turn.interrupted { generationId, turnId, playedMs }`；读取时由投影合并，不需要新表。播放中打断的两条路径：① 用户 barge-in（§音频健壮性）；② 显式 `playback.interrupted`。
- **Companion Tutor**：前端 `companion.ask` **只传 `entityId`**（不传 word）；word/wordId 服务端从编译场景查（单一事实源）；查 `tutor_cache[word_id]` 命中 → 0 LLM 0 TTS；未命中 → 一次 JSON-mode 调用返回 `{word, scaffold}` → 校验 → TTS 读出单词 → 写缓存 → `companion.reply` + 音频。超时/校验失败 → 降级为只读单词（scaffold 缺省），不阻断带读。
- **历史裁剪**：最近 **10 轮**、~1200 字符预算；**从旧到新丢弃且保持成对**；被打断的 NPC turn 标注 `Rosa (interrupted after 1.8s): "..."`。
- **安全**：系统 prompt 只有程序模板（trusted）；用户文本/转写/历史作为**结构化字段**放进 user 消息（`{"transcript": ..., "recent_turns": [...]}`）；LLM 输出严格白名单 + 服务端校验（超长**直接判失败降级，绝不截断**）；`llm_calls` 不存原始麦克风音频、不含 key。
- **成本护栏**：单 session LLM 调用上限 `llm_session_call_cap=200`（超限强制 scripted + 记 `fallback_reason=budget`）；per-session 并发信号量 `llm_concurrency_limit=2`；同 entityId 的 in-flight tutor 请求合并。
- **分角色超时与采样**：NPC connect 1.5s / ttft 2.0s / total 3.0s，`temperature=0.8`；Tutor connect 1.5s / total 6.0s，`temperature=0.3`。连接错误只重试一次。
- **全局既有约束**（沿用阶段 1）：Uvicorn 单 worker；SQLite WAL + 单写队列；先写库再发送；无 `innerHTML`；组件白名单；坐标 `0..1000`；热区 ≥44px；单场景 DOM 实体 ≤40；不加载外部 URL。
- **范围外（本阶段不做）**：Silero VAD、api↔asr 流式 WS、中文语言模式、gesture 产出与渲染、渐进提示、Narrative Repair 自动修复、Scene Director/PreloadManager/Patch、学习引擎/证据入账。

---

### Task 1: LLM 配置 + llm_calls 记账（settings + llm_log + 测试 fakes）

**Files:**
- Modify: `apps/api/app/settings.py`（加字段 + `from_env()`）
- Create: `apps/api/app/llm_log.py`
- Create: `apps/api/tests/test_llm_log.py`
- Create: `apps/api/tests/fakes.py`
- Modify: `apps/api/app/main.py`（`create_app` 默认用 `Settings.from_env()`，仅此一处）

**Interfaces:**
- Consumes: 无（首个任务；`EventStore` 已存在，`events.connection` 是公开的 `sqlite3.Connection`）
- Produces:
  - `Settings` 新增字段（默认值见 Global Constraints）+ 额外 `tutor_cache_dir: Path = Path("data/tutor-audio")` + `Settings.from_env() -> Settings`
  - `LlmLog(connection: sqlite3.Connection)`：
    - `record(*, session_id: str, role: str, model: str, generation_id: str | None = None, turn_id: str | None = None, utterance_id: str | None = None, prompt_tokens: int | None = None, completion_tokens: int | None = None, latency_ms: int | None = None, ttft_ms: int | None = None, finish_reason: str | None = None, fallback_reason: str = "none", attempt: int = 1, ok: bool = True, error: str | None = None) -> int`
    - `count_session_calls(session_id: str) -> int`
    - `recent(session_id: str, limit: int = 20) -> list[dict]`（按 id 升序返回原始行，供测试/调试）
  - `llm_log.FALLBACK_REASONS: frozenset[str]`
  - `tests/fakes.py`：`FakeLlmLog`（Task 5+ 注入用）

- [ ] **Step 1: 写失败的测试**

`apps/api/tests/test_llm_log.py`：

```python
import pytest

from app.event_store import EventStore
from app.llm_log import LlmLog, FALLBACK_REASONS
from app.settings import Settings


def test_settings_llm_defaults() -> None:
    s = Settings()
    assert s.llm_base_url == "https://api.deepseek.com"
    assert s.llm_api_key == ""
    assert s.llm_model == "deepseek-chat"
    assert s.llm_connect_timeout_s == 1.5
    assert s.llm_ttft_timeout_s == 2.0
    assert s.llm_total_timeout_npc_s == 3.0
    assert s.llm_total_timeout_tutor_s == 6.0
    assert s.llm_max_speech_chars == 200
    assert s.llm_max_scaffold_chars == 120
    assert s.llm_temperature_npc == 0.8
    assert s.llm_temperature_tutor == 0.3
    assert s.llm_max_tokens_npc == 320
    assert s.llm_max_tokens_tutor == 320
    assert s.llm_session_call_cap == 200
    assert s.llm_concurrency_limit == 2
    assert s.tutor_cache_dir == __import__("pathlib").Path("data/tutor-audio")


def test_settings_from_env_override(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat-v3")
    monkeypatch.setenv("LLM_SESSION_CALL_CAP", "42")
    s = Settings.from_env()
    assert s.llm_api_key == "sk-env"
    assert s.llm_model == "deepseek-chat-v3"
    assert s.llm_session_call_cap == 42
    assert s.llm_connect_timeout_s == 1.5  # 未覆盖的保留默认


def test_record_and_count(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    log = LlmLog(events.connection)
    log.record(session_id="s1", role="npc_actor", model="deepseek-chat",
               generation_id="g1", turn_id="t1", utterance_id="u1",
               prompt_tokens=40, completion_tokens=9, latency_ms=900, ttft_ms=120,
               finish_reason="stop", fallback_reason="none", attempt=1, ok=True)
    log.record(session_id="s1", role="npc_actor", model="deepseek-chat",
               fallback_reason="timeout", attempt=1, ok=False, error="boom")
    assert log.count_session_calls("s1") == 2
    assert log.count_session_calls("other") == 0
    rows = log.recent("s1")
    assert len(rows) == 2
    assert rows[0]["fallback_reason"] == "none" and rows[0]["ok"] == 1
    assert rows[1]["fallback_reason"] == "timeout" and rows[1]["ok"] == 0


def test_fallback_reason_enum_rejected(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    log = LlmLog(events.connection)
    assert FALLBACK_REASONS == frozenset({
        "timeout", "connect", "invalid_json", "schema_reject",
        "length_truncated", "no_key", "budget", "none",
    })
    with pytest.raises(ValueError):
        log.record(session_id="s1", role="npc_actor", model="m", fallback_reason="bogus")


def test_table_created_with_index(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    LlmLog(events.connection)
    tables = {r[0] for r in events.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "llm_calls" in tables
    idx = {r[0] for r in events.connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_llm_calls_session" in idx
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --project apps/api pytest tests/test_llm_log.py -v`
Expected: FAIL（`ModuleNotFoundError: app.llm_log`、`Settings has no attribute 'llm_base_url'`）

- [ ] **Step 3: 实现 settings + llm_log**

`apps/api/app/settings.py`（整体替换）：

```python
from dataclasses import dataclass, fields
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path("english_town.db")
    asset_root: Path = Path(__file__).resolve().parents[3] / "assets"
    asr_ws_url: str = "ws://127.0.0.1:8001/ws/asr"   # 阶段 2 使用
    tts_url: str = "http://127.0.0.1:8002/tts"       # 阶段 2 使用

    # --- 阶段 2：LLM（provider 无关，OpenAI 兼容）---
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""                            # env DEEPSEEK_API_KEY；空 → mock
    llm_model: str = "deepseek-chat"
    llm_connect_timeout_s: float = 1.5
    llm_ttft_timeout_s: float = 2.0
    llm_total_timeout_npc_s: float = 3.0
    llm_total_timeout_tutor_s: float = 6.0
    llm_max_speech_chars: int = 200
    llm_max_scaffold_chars: int = 120
    llm_temperature_npc: float = 0.8
    llm_temperature_tutor: float = 0.3
    llm_max_tokens_npc: int = 320
    llm_max_tokens_tutor: int = 320
    llm_session_call_cap: int = 200
    llm_concurrency_limit: int = 2
    tutor_cache_dir: Path = Path("data/tutor-audio")

    _ENV_FIELDS = {
        "llm_base_url": "LLM_BASE_URL",
        "llm_api_key": "DEEPSEEK_API_KEY",
        "llm_model": "LLM_MODEL",
        "llm_connect_timeout_s": "LLM_CONNECT_TIMEOUT_S",
        "llm_ttft_timeout_s": "LLM_TTFT_TIMEOUT_S",
        "llm_total_timeout_npc_s": "LLM_TOTAL_TIMEOUT_NPC_S",
        "llm_total_timeout_tutor_s": "LLM_TOTAL_TIMEOUT_TUTOR_S",
        "llm_max_speech_chars": "LLM_MAX_SPEECH_CHARS",
        "llm_max_scaffold_chars": "LLM_MAX_SCAFFOLD_CHARS",
        "llm_temperature_npc": "LLM_TEMPERATURE_NPC",
        "llm_temperature_tutor": "LLM_TEMPERATURE_TUTOR",
        "llm_max_tokens_npc": "LLM_MAX_TOKENS_NPC",
        "llm_max_tokens_tutor": "LLM_MAX_TOKENS_TUTOR",
        "llm_session_call_cap": "LLM_SESSION_CALL_CAP",
        "llm_concurrency_limit": "LLM_CONCURRENCY_LIMIT",
        "tutor_cache_dir": "TUTOR_CACHE_DIR",
    }

    @classmethod
    def from_env(cls) -> "Settings":
        kw: dict = {}
        base = cls()
        for field, env_name in cls._ENV_FIELDS.items():
            if env_name in os.environ:
                raw = os.environ[env_name]
                default = getattr(base, field)
                if isinstance(default, bool):
                    kw[field] = raw.lower() == "true"
                elif isinstance(default, int):
                    kw[field] = int(raw)
                elif isinstance(default, float):
                    kw[field] = float(raw)
                else:
                    kw[field] = Path(raw) if field == "tutor_cache_dir" else raw
        return cls(**kw)
```

注意：dataclass 里 `fields` 导入未使用——用 `getattr(base, field)` 而非 `fields`，删除 `fields` 导入。

`apps/api/app/llm_log.py`：

```python
"""llm_calls 表：LLM 调用记账（含降级原因枚举、attempt）。与 session_events 同一 SQLite 连接。"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

FALLBACK_REASONS = frozenset({
    "timeout", "connect", "invalid_json", "schema_reject",
    "length_truncated", "no_key", "budget", "none",
})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls(
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id        TEXT NOT NULL,
  generation_id     TEXT,
  turn_id           TEXT,
  utterance_id      TEXT,
  role              TEXT NOT NULL,
  model             TEXT NOT NULL,
  prompt_tokens     INTEGER,
  completion_tokens INTEGER,
  latency_ms        INTEGER,
  ttft_ms           INTEGER,
  finish_reason     TEXT,
  fallback_reason   TEXT,
  attempt           INTEGER,
  ok                INTEGER NOT NULL,
  error             TEXT,
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_session ON llm_calls(session_id, created_at);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class LlmLog:
    def __init__(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(_SCHEMA)
        connection.commit()
        self._connection = connection
        self._lock = threading.Lock()

    def record(self, *, session_id: str, role: str, model: str,
               generation_id: str | None = None, turn_id: str | None = None,
               utterance_id: str | None = None, prompt_tokens: int | None = None,
               completion_tokens: int | None = None, latency_ms: int | None = None,
               ttft_ms: int | None = None, finish_reason: str | None = None,
               fallback_reason: str = "none", attempt: int = 1,
               ok: bool = True, error: str | None = None) -> int:
        if fallback_reason not in FALLBACK_REASONS:
            raise ValueError(f"unknown fallback_reason: {fallback_reason}")
        with self._lock:
            cur = self._connection.execute(
                "INSERT INTO llm_calls(session_id, generation_id, turn_id, utterance_id, role, model,"
                " prompt_tokens, completion_tokens, latency_ms, ttft_ms, finish_reason, fallback_reason,"
                " attempt, ok, error, created_at)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, generation_id, turn_id, utterance_id, role, model,
                 prompt_tokens, completion_tokens, latency_ms, ttft_ms, finish_reason,
                 fallback_reason, attempt, 1 if ok else 0, error, _utcnow()),
            )
            self._connection.commit()
            return int(cur.lastrowid)

    def count_session_calls(self, session_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE session_id = ?", (session_id,)
        ).fetchone()
        return int(row[0])

    def recent(self, session_id: str, limit: int = 20) -> list[dict]:
        rows = self._connection.execute(
            "SELECT * FROM llm_calls WHERE session_id = ? ORDER BY id LIMIT ?",
            (session_id, limit),
        ).fetchall()
        cols = [d[0] for d in self._connection.execute("SELECT * FROM llm_calls LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]
```

`apps/api/tests/fakes.py`：

```python
"""测试共享 fake：NpcActor / CompanionTutor 的 llm_log 注入。"""
from __future__ import annotations


class FakeLlmLog:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, **kw) -> int:
        self.rows.append(kw)
        return len(self.rows)

    def count_session_calls(self, session_id: str) -> int:
        return 0

    def recent(self, session_id: str, limit: int = 20) -> list[dict]:
        return list(self.rows[-limit:])
```

`apps/api/app/main.py` 中 `create_app` 的第一行改为默认 env 覆盖：

```python
    settings = settings or Settings.from_env()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --project apps/api pytest tests/test_llm_log.py -v`
Expected: PASS（6 个用例）

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/settings.py apps/api/app/llm_log.py apps/api/tests/test_llm_log.py apps/api/tests/fakes.py apps/api/app/main.py
git commit -m "feat(api): LLM settings + llm_calls table (llm_log)"
```

---

### Task 2: LLM 适配层（client + mock，provider 无关）

**Files:**
- Create: `apps/api/app/llm/__init__.py`（空文件）
- Create: `apps/api/app/llm/client.py`
- Create: `apps/api/app/llm/mock.py`
- Modify: `apps/api/pyproject.toml`（加 `openai` 依赖）
- Create: `apps/api/tests/test_llm_client.py`

**Interfaces:**
- Consumes: `Settings`（Task 1）、`FakeLlmLog`（Task 1，Task 2 自身不用，后续用）
- Produces:
  - `TextDelta(text: str = "", finish_reason: str | None = None, usage: dict | None = None)`
  - `JsonResult(json: dict, usage: dict | None, finish_reason: str | None)`
  - `LLMConnectError(ConnectionError)`、`JsonParseError(ValueError)`
  - `LLMAdapter`（Protocol）：`stream_text(messages: list[dict], *, max_tokens: int, temperature: float) -> AsyncIterator[TextDelta]`；`complete_json(messages: list[dict], *, max_tokens: int, temperature: float) -> JsonResult`
  - `OpenAIClient(settings: Settings, http_client: httpx.AsyncClient | None = None)`（构造时拒绝 `deepseek-reasoner`；连接错误内部重试一次后抛 `LLMConnectError`；`complete_json` JSON 解析失败抛 `JsonParseError`）
  - `MockAdapter(scenario: str = "ok", stream_text_override: str | None = None)`；`SCENARIOS = {"ok","timeout","connect_error","invalid_json","bad_word_id","missing_word","too_long","truncated","empty"}`。`ok` 时流式文本按句吐字、`complete_json` 从 user 消息的 JSON 里取 `word` 回显（scaffold 含该词）；其余 scenario 触发对应故障
  - `get_client(settings: Settings) -> LLMAdapter`：`llm_api_key` 空 → `MockAdapter(os.environ.get("MOCK_LLM_SCENARIO", "ok"))`，非空 → `OpenAIClient(settings)`

- [ ] **Step 1: 加依赖**

`apps/api/pyproject.toml` 的 `dependencies` 里追加 `"openai>=1.0,<2"`。然后：

```bash
cd apps/api && uv lock && uv sync
```

- [ ] **Step 2: 写失败的测试**

`apps/api/tests/test_llm_client.py`：

```python
import json

import httpx
import pytest

from app.llm.client import JsonParseError, LLMConnectError, OpenAIClient, get_client
from app.llm.mock import MockAdapter
from app.settings import Settings


def _sse_stream(parts: list[str]) -> bytes:
    """把一段文本按字符切成分块 SSE 响应（OpenAI 流式格式）。"""
    chunks = []
    for i, ch in enumerate(parts):
        payload = json.dumps({"id": "x", "object": "chat.completion.chunk", "model": "deepseek-chat",
                              "choices": [{"index": 0, "delta": {"content": ch}, "finish_reason": None}]})
        chunks.append("data: " + payload + "\n\n")
    final = json.dumps({"id": "x", "object": "chat.completion.chunk", "model": "deepseek-chat",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
    chunks.append("data: " + final + "\n\n")
    chunks.append("data: [DONE]\n\n")
    return "".join(chunks).encode()


def _mock_openai_client(handler) -> OpenAIClient:
    transport = httpx.MockTransport(handler)
    settings = Settings(llm_api_key="sk-test")
    return OpenAIClient(settings, http_client=httpx.AsyncClient(transport=transport))


async def test_factory_mock_without_key() -> None:
    assert isinstance(get_client(Settings(llm_api_key="")), MockAdapter)


async def test_factory_openai_with_key() -> None:
    assert isinstance(get_client(Settings(llm_api_key="sk-test")), OpenAIClient)


async def test_reasoner_rejected() -> None:
    with pytest.raises(ValueError, match="reasoner"):
        OpenAIClient(Settings(llm_api_key="sk-test", llm_model="deepseek-reasoner"))


async def test_stream_text_request_shape_and_parse() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse_stream(["Hello! ", "Welcome to the bakery."]),
                              headers={"content-type": "text/event-stream"})

    client = _mock_openai_client(handler)
    deltas = [d async for d in client.stream_text(
        [{"role": "user", "content": "hi"}], max_tokens=320, temperature=0.8)]
    assert captured["url"].endswith("/chat/completions")
    body = captured["body"]
    assert body["model"] == "deepseek-chat"
    assert body["stream"] is True
    assert body["temperature"] == 0.8
    assert body["max_tokens"] == 320
    assert body["stream_options"] == {"include_usage": True}
    assert "".join(d.text for d in deltas) == "Hello! Welcome to the bakery."
    assert deltas[-1].finish_reason == "stop"
    assert deltas[-1].usage["completion_tokens"] == 2


async def test_stream_connect_error_retries_once_then_raises() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, text="unavailable")

    client = _mock_openai_client(handler)
    with pytest.raises(LLMConnectError):
        async for _ in client.stream_text([{"role": "user", "content": "hi"}],
                                          max_tokens=320, temperature=0.8):
            pass
    assert len(calls) == 2  # 连接错误只重试一次


async def test_complete_json_parses_object() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "x", "object": "chat.completion",
                                         "model": "deepseek-chat",
                                         "choices": [{"index": 0, "message": {"role": "assistant",
                                                                              "content": '{"word": "loaf"}'},
                                                      "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 3, "completion_tokens": 2}})

    client = _mock_openai_client(handler)
    res = await client.complete_json([{"role": "user", "content": "x"}], max_tokens=320, temperature=0.3)
    assert res.json == {"word": "loaf"}
    assert res.finish_reason == "stop"


async def test_complete_json_bad_json_raises_parse_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "x", "object": "chat.completion",
                                         "model": "deepseek-chat",
                                         "choices": [{"index": 0, "message": {"role": "assistant",
                                                                              "content": "not json"},
                                                      "finish_reason": "stop"}],
                                         "usage": None})

    client = _mock_openai_client(handler)
    with pytest.raises(JsonParseError):
        await client.complete_json([{"role": "user", "content": "x"}], max_tokens=320, temperature=0.3)


async def test_mock_ok_streams_sentences() -> None:
    m = MockAdapter("ok")
    deltas = [d async for d in m.stream_text([], max_tokens=320, temperature=0.8)]
    assert "".join(d.text for d in deltas) == "Hello! Welcome to the bakery. Can I help you? "
    assert deltas[-1].finish_reason == "stop"
    assert deltas[-1].usage["completion_tokens"] > 0


async def test_mock_ok_complete_json_echoes_word() -> None:
    m = MockAdapter("ok")
    res = await m.complete_json(
        [{"role": "user", "content": json.dumps({"word": "apple", "scene": "bakery"})}],
        max_tokens=320, temperature=0.3)
    assert res.json["word"] == "apple"
    assert "apple" in res.json["scaffold"]


async def test_mock_connect_error_raises() -> None:
    m = MockAdapter("connect_error")
    with pytest.raises(LLMConnectError):
        async for _ in m.stream_text([], max_tokens=320, temperature=0.8):
            pass


async def test_mock_invalid_json_raises_parse_error() -> None:
    m = MockAdapter("invalid_json")
    with pytest.raises(JsonParseError):
        await m.complete_json([], max_tokens=320, temperature=0.3)


async def test_mock_bad_scenario_rejected() -> None:
    with pytest.raises(ValueError):
        MockAdapter("nope")
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run --project apps/api pytest tests/test_llm_client.py -v`
Expected: FAIL（`ModuleNotFoundError: app.llm.client`）

- [ ] **Step 4: 实现 client + mock**

`apps/api/app/llm/client.py`：

```python
"""OpenAI 兼容 LLM 客户端（provider 无关）。DeepSeek deepseek-chat 为默认端点。
base_url/model/api_key 全部可配置；无 key 时 get_client 返回 mock。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

import httpx
from openai import AsyncOpenAI

from app.settings import Settings


class LLMConnectError(ConnectionError):
    """连接类错误（重试一次仍失败后抛）。"""


class JsonParseError(ValueError):
    """complete_json 的输出不是合法 JSON 对象。"""


@dataclass
class TextDelta:
    text: str = ""
    finish_reason: str | None = None
    usage: dict | None = None


@dataclass
class JsonResult:
    json: dict
    usage: dict | None
    finish_reason: str | None


class LLMAdapter(Protocol):
    async def stream_text(self, messages: list[dict], *, max_tokens: int,
                          temperature: float) -> AsyncIterator[TextDelta]: ...

    async def complete_json(self, messages: list[dict], *, max_tokens: int,
                            temperature: float) -> JsonResult: ...


class OpenAIClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        if settings.llm_model.startswith("deepseek-reasoner"):
            raise ValueError("deepseek-reasoner 不支持 JSON Output；请配置 llm_model=deepseek-chat")
        self._settings = settings
        self._http_client = http_client
        timeout = httpx.Timeout(settings.llm_connect_timeout_s, read=settings.llm_ttft_timeout_s)
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url, api_key=settings.llm_api_key,
            timeout=timeout, http_client=http_client,
        )

    async def stream_text(self, messages: list[dict], *, max_tokens: int,
                          temperature: float) -> AsyncIterator[TextDelta]:
        s = self._settings
        stream = await self._create_stream(messages, max_tokens, temperature)
        async for chunk in stream:
            delta = TextDelta()
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                delta.text = chunk.choices[0].delta.content
            if chunk.choices:
                delta.finish_reason = chunk.choices[0].finish_reason
            if chunk.usage:
                delta.usage = chunk.usage.model_dump()
            yield delta

    async def _create_stream(self, messages, max_tokens, temperature):
        s = self._settings
        for attempt in (1, 2):
            try:
                return await self._client.chat.completions.create(
                    model=s.llm_model, messages=messages,
                    temperature=temperature, max_tokens=max_tokens,
                    stream=True, stream_options={"include_usage": True},
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError,
                    httpx.RemoteProtocolError) as e:
                if attempt == 2:
                    raise LLMConnectError(f"LLM connect failed: {e}") from e
        raise AssertionError("unreachable")

    async def complete_json(self, messages: list[dict], *, max_tokens: int,
                            temperature: float) -> JsonResult:
        s = self._settings
        resp = await self._client.chat.completions.create(
            model=s.llm_model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or ""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            raise JsonParseError(f"LLM returned non-JSON: {content[:80]!r}") from e
        if not isinstance(parsed, dict):
            raise JsonParseError(f"LLM returned non-object: {content[:80]!r}")
        return JsonResult(
            json=parsed,
            usage=resp.usage.model_dump() if resp.usage else None,
            finish_reason=resp.choices[0].finish_reason,
        )


def get_client(settings: Settings) -> LLMAdapter:
    # mock 在函数内导入：client ↔ mock 无模块级循环依赖
    from app.llm.mock import MockAdapter
    if not settings.llm_api_key:
        return MockAdapter(os.environ.get("MOCK_LLM_SCENARIO", "ok"))
    return OpenAIClient(settings)
```

`apps/api/app/llm/mock.py`：

```python
"""确定性 mock LLM。离线测试 + 故障注入（MOCK_LLM_SCENARIO）。
scenario 枚举：ok | timeout | connect_error | invalid_json | bad_word_id
             | missing_word | too_long | truncated | empty
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from app.llm.client import JsonParseError, JsonResult, LLMConnectError, TextDelta

SCENARIOS = frozenset({
    "ok", "timeout", "connect_error", "invalid_json", "bad_word_id",
    "missing_word", "too_long", "truncated", "empty",
})


class MockAdapter:
    def __init__(self, scenario: str = "ok", stream_text_override: str | None = None) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown mock scenario: {scenario}")
        self.scenario = scenario
        self.stream_text_override = stream_text_override

    async def stream_text(self, messages: list[dict], *, max_tokens: int,
                          temperature: float) -> AsyncIterator[TextDelta]:
        if self.scenario == "timeout":
            await asyncio.sleep(60)  # 外层 total timeout 会取消它（Task 5 测降级）
            return
        if self.scenario == "connect_error":
            raise LLMConnectError("mock connect error")
        if self.scenario == "too_long":
            # 单个超长 token：chunker 不断词，validate_speech 判超长 → 降级
            yield TextDelta(text="A" * 250)
            return
        if self.scenario == "truncated":
            yield TextDelta(text="Hello! Welcome to the bakery. ")
            yield TextDelta(text="Would you like a", finish_reason="length")
            return
        if self.scenario == "empty":
            return  # 无任何 delta、无 finish_reason
        # 逐词吐流：句子切分交给 chunker（纯函数单测已覆盖），mock 不重复实现断句
        text = self.stream_text_override or "Hello! Welcome to the bakery. Can I help you? "
        for word in text.split():
            yield TextDelta(text=word + " ")
        yield TextDelta(finish_reason="stop", usage={"prompt_tokens": 40, "completion_tokens": 9})

    async def complete_json(self, messages: list[dict], *, max_tokens: int,
                            temperature: float) -> JsonResult:
        if self.scenario == "timeout":
            await asyncio.sleep(60)
        if self.scenario == "connect_error":
            raise LLMConnectError("mock connect error")
        if self.scenario == "invalid_json":
            raise JsonParseError("mock invalid json")
        word = self._word_from_messages(messages)
        if self.scenario == "bad_word_id":
            return JsonResult(json={"word": "wrongword", "scaffold": "A wrong word."},
                              usage=None, finish_reason="stop")
        if self.scenario == "missing_word":
            return JsonResult(json={"word": word, "scaffold": "A baked good you can buy."},
                              usage=None, finish_reason="stop")
        if self.scenario == "too_long":
            return JsonResult(json={"word": word, "scaffold": "word " * 40},
                              usage=None, finish_reason="stop")
        return JsonResult(json={"word": word, "scaffold": f"A {word} is a thing you can see here. Say it: {word}."},
                          usage={"prompt_tokens": 3, "completion_tokens": 6}, finish_reason="stop")

    def _word_from_messages(self, messages: list[dict]) -> str:
        for m in messages:
            if m.get("role") == "user":
                try:
                    return json.loads(m["content"]).get("word", "loaf")
                except (json.JSONDecodeError, TypeError, AttributeError):
                    continue
        return "loaf"
```

`apps/api/app/llm/__init__.py`（空内容）。

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run --project apps/api pytest tests/test_llm_client.py -v`
Expected: PASS（13 个用例）。`test_mock_ok_streams_sentences` 断言 join 后 == `"Hello! Welcome to the bakery. Can I help you? "`——mock 逐词吐流，chunker 负责断句。

- [ ] **Step 6: 提交**

```bash
git add apps/api/pyproject.toml apps/api/uv.lock apps/api/app/llm apps/api/tests/test_llm_client.py
git commit -m "feat(api): provider-agnostic LLM client + fault-injecting mock"
```

---

### Task 3: 纯函数（chunker 句子切分 + lexmatch 词法匹配）

**Files:**
- Create: `apps/api/app/llm/chunker.py`
- Create: `apps/api/app/llm/lexmatch.py`
- Create: `apps/api/tests/test_chunker.py`
- Create: `apps/api/tests/test_lexmatch.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `SentenceChunker(max_words: int = 20)`：`feed(delta: str) -> list[str]`（返回完整句子）；`finalize() -> str`（余量，可能为空）
    - 规则：句号/问号/叹号（`[.!?]`）后面跟空白或缓冲末尾 → 切句；后跟数字/字母（如 `3.5`、`U.S.`）不切；缓冲超 `max_words` 词在**词边界**强制断句（不断词）；`feed` 同时处理这两类。
  - `token_contains(text: str, lemma: str) -> bool`（text 是否含 lemma 或其屈折形式 s/es/ies/ves，小写、去标点）
  - `derive_candidate_word_ids(text: str, allowed: dict[str, str]) -> list[str]`（allowed 为 `{wordId: lemma}` 插入序；返回 text 中出现的 wordId 列表，去重）

- [ ] **Step 1: 写失败的测试**

`apps/api/tests/test_chunker.py`：

```python
from app.llm.chunker import SentenceChunker


def test_emits_complete_sentences() -> None:
    c = SentenceChunker()
    assert c.feed("Hello! ") == ["Hello!"]
    assert c.feed("Welcome to the bakery. Can I help you?") == ["Welcome to the bakery.", "Can I help you?"]


def test_fragment_waits_for_punctuation() -> None:
    c = SentenceChunker()
    assert c.feed("The loaf is ") == []
    assert c.feed("three dollars.") == ["The loaf is three dollars."]


def test_short_sentences_emit_immediately() -> None:
    c = SentenceChunker()
    assert c.feed("Hi!") == ["Hi!"]


def test_force_split_at_twenty_words() -> None:
    c = SentenceChunker()
    words = [f"w{i}" for i in range(25)]
    out = c.feed(" ".join(words) + " ")
    assert len(out) == 1
    assert out[0].split() == words[:20]
    assert c.finalize() == " ".join(words[20:])


def test_finalize_returns_remainder() -> None:
    c = SentenceChunker()
    assert c.feed("no punctuation yet") == []
    assert c.finalize() == "no punctuation yet"


def test_decimal_dot_is_not_boundary() -> None:
    c = SentenceChunker()
    assert c.feed("The price is 3.5 dollars.") == ["The price is 3.5 dollars."]


def test_no_double_space_drift() -> None:
    c = SentenceChunker()
    assert c.feed("Hi there. ") == ["Hi there."]
    assert c.finalize() == ""
```

`apps/api/tests/test_lexmatch.py`：

```python
from app.llm.lexmatch import derive_candidate_word_ids, token_contains

ALLOWED = {"word_loaf_n_1": "loaf", "word_apple_n_1": "apple", "word_receipt_n_1": "receipt"}


def test_matches_lemma() -> None:
    assert derive_candidate_word_ids("I'd like a loaf, please.", ALLOWED) == ["word_loaf_n_1"]


def test_matches_plural_irregular() -> None:
    assert derive_candidate_word_ids("Two loaves, please.", ALLOWED) == ["word_loaf_n_1"]


def test_matches_multiple_in_scene_order() -> None:
    assert derive_candidate_word_ids("An apple and a receipt.", ALLOWED) == ["word_apple_n_1", "word_receipt_n_1"]


def test_no_match_returns_empty() -> None:
    assert derive_candidate_word_ids("Goodbye!", ALLOWED) == []


def test_case_and_punctuation_insensitive() -> None:
    assert derive_candidate_word_ids("LOAF!", ALLOWED) == ["word_loaf_n_1"]


def test_dedupe_per_word() -> None:
    assert derive_candidate_word_ids("loaf and loaves", ALLOWED) == ["word_loaf_n_1"]


def test_token_contains_helper() -> None:
    assert token_contains("The loaves are fresh.", "loaf") is True
    assert token_contains("The receipt is here.", "loaf") is False
    assert token_contains("An apple!", "apple") is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --project apps/api pytest tests/test_chunker.py tests/test_lexmatch.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 chunker + lexmatch**

`apps/api/app/llm/chunker.py`：

```python
"""流式句子切分：delta 累积 → 完整句子（按 [.!?] 切；20 词强制断句、词边界不断词）。
纯函数、无 IO，直接可测。"""
from __future__ import annotations

import re

_TERMINATOR = re.compile(r"[.!?]")
_WORD = re.compile(r"\S+")


def _word_count(text: str) -> int:
    return len(_WORD.findall(text.strip()))


class SentenceChunker:
    def __init__(self, max_words: int = 20) -> None:
        self.max_words = max_words
        self._buf = ""

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        while True:
            m = _TERMINATOR.search(self._buf)
            if m is None:
                break
            end = m.end()
            # 终止符后跟数字/字母（3.5 / U.S.）不是句子边界 → 跳过该终止符
            if end < len(self._buf) and self._buf[end].isalnum():
                self._buf = self._buf[end:]
                continue
            sentence = self._buf[:end].strip()
            self._buf = self._buf[end:]
            if sentence:
                out.append(sentence)
        # 强制断句：缓冲超 max_words 词，在词边界切开
        while _word_count(self._buf) > self.max_words:
            words = _WORD.findall(self._buf)
            sentence = " ".join(words[: self.max_words])
            out.append(sentence)
            rest = " ".join(words[self.max_words :])
            self._buf = rest
        return out

    def finalize(self) -> str:
        remainder = self._buf.strip()
        self._buf = ""
        return remainder
```

`apps/api/app/llm/lexmatch.py`：

```python
"""服务端词法匹配：commit.text → candidateWordIds（lemma 屈折归一）。
这是 spec §7 的最终形态——不信任 LLM 自报 exposure，从全文派生。"""
from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z]+")


def _variants(lemma: str) -> set[str]:
    v = {lemma, lemma + "s", lemma + "es"}
    if lemma.endswith("y") and len(lemma) > 1:
        v.add(lemma[:-1] + "ies")
    if lemma.endswith("fe") and len(lemma) > 2:
        v.add(lemma[:-2] + "ves")
    if lemma.endswith("f") and len(lemma) > 1:
        v.add(lemma[:-1] + "ves")
    return v


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def token_contains(text: str, lemma: str) -> bool:
    forms = _variants(lemma)
    return any(tok in forms for tok in _tokens(text))


def derive_candidate_word_ids(text: str, allowed: dict[str, str]) -> list[str]:
    """allowed: {wordId: lemma}，返回出现过的 wordId（按 allowed 插入序）。"""
    return [word_id for word_id, lemma in allowed.items() if token_contains(text, lemma)]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --project apps/api pytest tests/test_chunker.py tests/test_lexmatch.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/llm/chunker.py apps/api/app/llm/lexmatch.py apps/api/tests/test_chunker.py apps/api/tests/test_lexmatch.py
git commit -m "feat(api): pure sentence chunker + lexmatch word-id derivation"
```

---

### Task 4: 提案校验边界（proposals：validate_speech + validate_tutor）

**Files:**
- Create: `apps/api/app/llm/proposals.py`
- Create: `apps/api/tests/test_proposals.py`

**Interfaces:**
- Consumes: `token_contains`（Task 3）
- Produces:
  - `ProposalError(ValueError)`
  - `validate_speech(text: str, max_chars: int) -> str`：通过则原样返回；否则抛 `ProposalError`。规则：非空、≤`max_chars`、纯 ASCII、无换行、无 URL/代码块标记。**绝不截断。**
  - `validate_tutor(word: str, scaffold: str, *, expected_word: str, max_scaffold_chars: int) -> None`：`word` 必须等于 `expected_word`（忽略大小写）；`scaffold` 非空、≤`max_scaffold_chars`、纯 ASCII、无换行、无 URL/代码块、**必须含目标词**（`token_contains`，允许屈折）。任一条失败抛 `ProposalError`。

- [ ] **Step 1: 写失败的测试**

`apps/api/tests/test_proposals.py`：

```python
import pytest

from app.llm.proposals import ProposalError, validate_speech, validate_tutor


def test_speech_ok() -> None:
    assert validate_speech("The loaf is three dollars.", 200) == "The loaf is three dollars."


def test_speech_empty_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_speech("", 200)


def test_speech_over_long_rejected_not_truncated() -> None:
    with pytest.raises(ProposalError):
        validate_speech("word " * 60, 200)


def test_speech_newline_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_speech("line one\nline two", 200)


def test_speech_url_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_speech("visit https://example.com now", 200)


def test_speech_code_block_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_speech("```python\nx=1\n```", 200)


def test_speech_non_ascii_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_speech("un café", 200)


def test_tutor_ok() -> None:
    validate_tutor("loaf", "A loaf is a big piece of bread. Say it: loaf.",
                   expected_word="loaf", max_scaffold_chars=120)


def test_tutor_wrong_word_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_tutor("apple", "A loaf is bread.", expected_word="loaf", max_scaffold_chars=120)


def test_tutor_scaffold_missing_target_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_tutor("loaf", "A big piece of bread you can buy.",
                       expected_word="loaf", max_scaffold_chars=120)


def test_tutor_scaffold_over_long_rejected() -> None:
    with pytest.raises(ProposalError):
        validate_tutor("loaf", "word " * 30, expected_word="loaf", max_scaffold_chars=120)


def test_tutor_case_insensitive_word() -> None:
    validate_tutor("Loaf", "A loaf is bread.", expected_word="loaf", max_scaffold_chars=120)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --project apps/api pytest tests/test_proposals.py -v`
Expected: FAIL（`ModuleNotFoundError: app.llm.proposals`）

- [ ] **Step 3: 实现 proposals**

`apps/api/app/llm/proposals.py`：

```python
"""LLM 输出校验边界。超长一律判失败降级，绝不截断（截断会让 TTS 读半句话）。"""
from __future__ import annotations

from app.llm.lexmatch import token_contains

_FORBIDDEN = ("```", "http://", "https://", "www.")


class ProposalError(ValueError):
    pass


def _check(text: str, label: str, max_chars: int) -> None:
    if not text or not text.strip():
        raise ProposalError(f"empty {label}")
    if len(text) > max_chars:
        raise ProposalError(f"{label} too long: {len(text)} > {max_chars}")
    if not text.isascii():
        raise ProposalError(f"non-ascii {label}")
    if any(c in text for c in "\r\n"):
        raise ProposalError(f"newline in {label}")
    if any(f in text.lower() for f in _FORBIDDEN):
        raise ProposalError(f"forbidden token in {label}: {next(f for f in _FORBIDDEN if f in text.lower())}")


def validate_speech(text: str, max_chars: int) -> str:
    _check(text, "speech", max_chars)
    return text


def validate_tutor(word: str, scaffold: str, *, expected_word: str, max_scaffold_chars: int) -> None:
    if word.strip().lower() != expected_word.strip().lower():
        raise ProposalError(f"tutor word mismatch: {word!r} != {expected_word!r}")
    _check(scaffold, "scaffold", max_scaffold_chars)
    if not token_contains(scaffold, expected_word):
        raise ProposalError(f"scaffold missing target word: {expected_word!r}")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --project apps/api pytest tests/test_proposals.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/llm/proposals.py apps/api/tests/test_proposals.py
git commit -m "feat(api): LLM output proposal validation boundaries"
```

---

### Task 5: NPC Actor（prompt 构造 → 流式 → 切句 → 校验 → delta/commit/metadata + 降级）

**Files:**
- Create: `apps/api/app/llm/npc_actor.py`
- Create: `apps/api/tests/test_npc_actor.py`
- Create: `apps/api/tests/test_history.py`（`build_history` 单测）

**Interfaces:**
- Consumes: `LLMAdapter`/`TextDelta`/`LLMConnectError`（Task 2）、`MockAdapter`（Task 2）、`SentenceChunker`（Task 3）、`validate_speech`/`ProposalError`（Task 4）、`derive_candidate_word_ids`（Task 3）、`LlmLog`/`FakeLlmLog`（Task 1）、`Settings`（Task 1）
- Produces:
  - `SYSTEM_PROMPT`（字符串常量，Rosa 人格 + "never mention AI / no newlines URLs code"）
  - `build_history(events, session_id: str, limit: int = 10, max_chars: int = 1200) -> list[dict]`：从 `session_events` 投影 `dialogue.turn` + `dialogue.turn.interrupted`，返回成对的 `[{"speaker": "learner", "text": ...}, {"speaker": "rosa", "text": ..., "interrupted_after_ms": ...}]`（从旧到新；超预算从旧丢弃；保持成对）
  - `NpcActor(client: LLMAdapter, settings: Settings, llm_log: LlmLog, allowed_words: dict[str, str], fallback: Callable[[str], str])`
    - `async def stream_reply(*, session_id: str, generation_id: str, turn_id: str, utterance_id: str, user_text: str, recent_turns: list[dict], budget_exceeded: bool = False) -> AsyncIterator[dict]`，产出：
      - `{"type": "npc.speech.delta", "generationId": g, "turnId": t, "text": 句子}`
      - `{"type": "npc.speech.commit", "generationId": g, "turnId": t, "text": 全文}`
      - `{"type": "npc.turn.metadata", "generationId": g, "turnId": t, "candidateWordIds": [...]}`
    - 降级（`budget_exceeded` / 超时 / 连接错误重试后 / 校验失败 / `length_truncated` / 空输出）→ 用 `fallback(user_text)` 产出一个 delta + commit + metadata，并在 `llm_calls` 记 `ok=0` + 对应 `fallback_reason`
    - exception→reason 映射：`TimeoutError→"timeout"`、`LLMConnectError→"connect"`、`ProposalError→"schema_reject"`、`finish_reason=="length"→"length_truncated"`、空输出→`"schema_reject"`、`budget_exceeded→"budget"`

- [ ] **Step 1: 写失败的测试**

`apps/api/tests/test_npc_actor.py`：

```python
import pytest

from app.llm.mock import MockAdapter
from app.llm.npc_actor import NpcActor
from app.settings import Settings
from tests.fakes import FakeLlmLog

ALLOWED = {"word_loaf_n_1": "loaf", "word_apple_n_1": "apple"}


def fallback(user_text: str) -> str:
    return "Sorry, I didn't catch that. Could you say it again?"


def make_actor(scenario: str = "ok", stream_text_override: str | None = None, **overrides):
    settings = Settings(llm_total_timeout_npc_s=0.2, **overrides)
    log = FakeLlmLog()
    actor = NpcActor(MockAdapter(scenario, stream_text_override=stream_text_override),
                     settings, log, ALLOWED, fallback)
    return actor, log


KW = dict(session_id="s1", generation_id="g1", turn_id="t1", utterance_id="u1",
          user_text="hello", recent_turns=[])


async def test_happy_path_delta_commit_metadata() -> None:
    actor, log = make_actor(stream_text_override="Hello! A loaf is three dollars. Can I help you?")
    msgs = [m async for m in actor.stream_reply(**KW)]
    types = [m["type"] for m in msgs]
    assert types == ["npc.speech.delta", "npc.speech.delta", "npc.speech.delta",
                     "npc.speech.commit", "npc.turn.metadata"]
    deltas = [m["text"] for m in msgs if m["type"] == "npc.speech.delta"]
    assert deltas == ["Hello!", "A loaf is three dollars.", "Can I help you?"]
    commit = [m for m in msgs if m["type"] == "npc.speech.commit"][0]
    assert commit["text"] == "Hello! A loaf is three dollars. Can I help you?"
    meta = [m for m in msgs if m["type"] == "npc.turn.metadata"][0]
    assert meta["candidateWordIds"] == ["word_loaf_n_1"]
    assert meta["generationId"] == "g1" and meta["turnId"] == "t1"
    # happy path 记 ok=1
    assert log.rows[-1]["ok"] is True and log.rows[-1]["fallback_reason"] == "none"


async def test_timeout_degrades() -> None:
    actor, log = make_actor("timeout")
    msgs = [m async for m in actor.stream_reply(**KW)]
    assert [m["type"] for m in msgs] == ["npc.speech.delta", "npc.speech.commit", "npc.turn.metadata"]
    assert msgs[0]["text"] == fallback("hello")
    assert log.rows[-1]["fallback_reason"] == "timeout"
    assert log.rows[-1]["ok"] is False


async def test_connect_error_degrades() -> None:
    actor, log = make_actor("connect_error")
    msgs = [m async for m in actor.stream_reply(**KW)]
    assert msgs[0]["text"] == fallback("hello")
    assert log.rows[-1]["fallback_reason"] == "connect"
    assert log.rows[-1]["attempt"] == 2  # 连接错误内部已重试一次


async def test_too_long_degrades_not_truncated() -> None:
    actor, log = make_actor("too_long")
    msgs = [m async for m in actor.stream_reply(**KW)]
    assert msgs[0]["text"] == fallback("hello")
    assert log.rows[-1]["fallback_reason"] == "schema_reject"


async def test_length_truncated_degrades() -> None:
    actor, log = make_actor("truncated")
    msgs = [m async for m in actor.stream_reply(**KW)]
    # 截断发生在流中途：已发出的真实 delta 会先到，最终 commit 覆盖为兜底文本
    commit = [m for m in msgs if m["type"] == "npc.speech.commit"][0]
    assert commit["text"] == fallback("hello")
    assert log.rows[-1]["fallback_reason"] == "length_truncated"


async def test_empty_output_degrades() -> None:
    actor, log = make_actor("empty")
    msgs = [m async for m in actor.stream_reply(**KW)]
    assert msgs[0]["text"] == fallback("hello")
    assert log.rows[-1]["fallback_reason"] == "schema_reject"


async def test_budget_exceeded_degrades() -> None:
    actor, log = make_actor()
    msgs = [m async for m in actor.stream_reply(**KW, budget_exceeded=True)]
    assert msgs[0]["text"] == fallback("hello")
    assert log.rows[-1]["fallback_reason"] == "budget"
    assert log.rows[-1]["ok"] is False


async def test_cumulative_length_degrades() -> None:
    # 累计超 max_speech_chars=200 才在流中途触发 → 需要一段 >200 字符的多句文本
    long_text = "Hello! " + "This is a long sentence with lots of words. " * 6
    actor, log = make_actor(stream_text_override=long_text, llm_max_speech_chars=200)
    msgs = [m async for m in actor.stream_reply(**KW)]
    # 中途超长 → 降级（commit 是兜底文本），不截断继续
    commit = [m for m in msgs if m["type"] == "npc.speech.commit"][0]
    assert commit["text"] == fallback("hello")
    assert log.rows[-1]["fallback_reason"] == "schema_reject"
```

`apps/api/tests/test_history.py`：

```python
from app.event_store import EventStore
from app.llm.npc_actor import build_history


def test_build_history_pairs_and_interruption(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    events.append("s1", "dialogue.turn", {"turnId": "t1", "utteranceId": "u1", "userText": "hello",
                                          "npcText": "Hi there.", "audioBytes": 10})
    events.append("s1", "dialogue.turn", {"turnId": "t2", "utteranceId": "u2", "userText": "loaf",
                                          "npcText": "A loaf!", "audioBytes": 10})
    events.append("s1", "dialogue.turn.interrupted", {"generationId": "g1", "turnId": "t2", "playedMs": 1840})
    hist = build_history(events, "s1")
    assert hist == [
        {"speaker": "learner", "text": "hello"},
        {"speaker": "rosa", "text": "Hi there."},
        {"speaker": "learner", "text": "loaf"},
        {"speaker": "rosa", "text": "A loaf!", "interrupted_after_ms": 1840},
    ]


def test_build_history_keeps_pairs_and_newest(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    for i in range(4):
        events.append("s1", "dialogue.turn",
                      {"turnId": f"t{i}", "utteranceId": f"u{i}", "userText": f"hello {i}",
                       "npcText": f"Hi from t{i}.", "audioBytes": 1})
    assert len(build_history(events, "s1")) == 8
    tight = build_history(events, "s1", limit=10, max_chars=10)
    assert len(tight) == 2          # 预算太小 → 只保留最新一对
    assert tight[0] == {"speaker": "learner", "text": "hello 3"}
    assert tight[1] == {"speaker": "rosa", "text": "Hi from t3."}


def test_build_history_limit_drops_oldest_pairs(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    for i in range(12):
        events.append("s1", "dialogue.turn",
                      {"turnId": f"t{i}", "utteranceId": f"u{i}", "userText": f"hello {i}",
                       "npcText": f"Hi from t{i}.", "audioBytes": 1})
    hist = build_history(events, "s1", limit=10, max_chars=99999)
    # 只保留最近 10 轮（20 条），从旧丢弃
    assert len(hist) == 20
    assert hist[0]["text"] == "hello 2"
    assert hist[1]["text"] == "Hi from t2."
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --project apps/api pytest tests/test_npc_actor.py tests/test_history.py -v`
Expected: FAIL（`ModuleNotFoundError: app.llm.npc_actor`）

- [ ] **Step 3: 实现 npc_actor**

`apps/api/app/llm/npc_actor.py`：

```python
"""NPC Actor：prompt 构造（trusted 系统人格 + untrusted 结构化字段）→ 流式文本
→ 按句切分 → 校验 → delta/commit/metadata；超时/失败降级 scripted_npc。"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable

from app.llm.chunker import SentenceChunker
from app.llm.client import LLMAdapter, LLMConnectError
from app.llm.lexmatch import derive_candidate_word_ids
from app.llm.proposals import ProposalError, validate_speech
from app.settings import Settings

SYSTEM_PROMPT = (
    "You are Rosa, a friendly vendor in a small English bakery. "
    "Reply in short, simple English sentences suitable for an A1-A2 English learner. "
    "Stay in character at the bakery. Never mention that you are an AI. "
    "Use only plain English text with basic punctuation: no newlines, no URLs, no code."
)


def build_history(events, session_id: str, limit: int = 10, max_chars: int = 1200) -> list[dict]:
    """投影 dialogue.turn + dialogue.turn.interrupted → 成对历史（从旧到新）。"""
    all_events = events.list_after(session_id, 0)
    turns = [e["payload"] for e in all_events if e["event_type"] == "dialogue.turn"]
    interrupted = {e["payload"]["turnId"]: e["payload"]
                   for e in all_events if e["event_type"] == "dialogue.turn.interrupted"}
    recent = turns[-limit:]  # 保留最近 N 轮
    kept: list[dict] = []
    total = 0
    for t in reversed(recent):  # 从新到旧累积，超预算丢旧的
        learner = {"speaker": "learner", "text": t["userText"]}
        rosa: dict = {"speaker": "rosa", "text": t["npcText"]}
        intr = interrupted.get(t["turnId"])
        if intr:
            rosa["interrupted_after_ms"] = int(intr.get("playedMs", 0))
        add = len(json.dumps([learner, rosa], ensure_ascii=False))
        if total + add > max_chars:
            continue  # 这条（更旧）超预算 → 丢弃，保留更新的
        kept.append(learner)
        kept.append(rosa)
        total += add
    kept.reverse()  # 回到从旧到新，且成对相邻
    return kept


class NpcActor:
    def __init__(self, client: LLMAdapter, settings: Settings, llm_log,
                 allowed_words: dict[str, str], fallback: Callable[[str], str]) -> None:
        self._client = client
        self._settings = settings
        self._llm_log = llm_log
        self._allowed_words = dict(allowed_words)
        self._fallback = fallback

    def _scene_hint(self) -> str:
        return "Items nearby: " + ", ".join(self._allowed_words.values())

    def _build_messages(self, user_text: str, recent_turns: list[dict]) -> list[dict]:
        user_payload = {
            "transcript": user_text,
            "recent_turns": recent_turns,
            "scene": self._scene_hint(),
        }
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]

    def _record(self, *, session_id, generation_id, turn_id, utterance_id,
                latency_ms, ttft_ms, reason, ok, error=None, tokens=None) -> None:
        self._llm_log.record(
            session_id=session_id, generation_id=generation_id, turn_id=turn_id,
            utterance_id=utterance_id, role="npc_actor", model=self._settings.llm_model,
            prompt_tokens=(tokens or {}).get("prompt_tokens"),
            completion_tokens=(tokens or {}).get("completion_tokens"),
            latency_ms=latency_ms, ttft_ms=ttft_ms,
            finish_reason="stop" if ok else None,
            fallback_reason=reason, attempt=2 if reason == "connect" else 1,
            ok=ok, error=error,
        )

    async def stream_reply(self, *, session_id: str, generation_id: str, turn_id: str,
                           utterance_id: str, user_text: str, recent_turns: list[dict],
                           budget_exceeded: bool = False) -> AsyncIterator[dict]:
        async def _emit(text: str, reason: str, ok: bool, error: str | None = None,
                        latency_ms: int | None = None, ttft_ms: int | None = None,
                        tokens: dict | None = None) -> None:
            self._record(session_id=session_id, generation_id=generation_id, turn_id=turn_id,
                         utterance_id=utterance_id, latency_ms=latency_ms, ttft_ms=ttft_ms,
                         reason=reason, ok=ok, error=error, tokens=tokens)
            yield {"type": "npc.speech.delta", "generationId": generation_id, "turnId": turn_id, "text": text}
            yield {"type": "npc.speech.commit", "generationId": generation_id, "turnId": turn_id, "text": text}
            yield {"type": "npc.turn.metadata", "generationId": generation_id, "turnId": turn_id,
                   "candidateWordIds": derive_candidate_word_ids(text, self._allowed_words)}

        if budget_exceeded:
            async for m in _emit(self._fallback(user_text), "budget", False,
                                 error="session call cap exceeded"):
                yield m
            return

        t0 = time.perf_counter()
        ttft_ms: int | None = None
        tokens: dict | None = None
        reason = "none"
        chunker = SentenceChunker()
        emitted_chars = 0
        sentences: list[str] = []
        messages = self._build_messages(user_text, recent_turns)
        try:
            async with asyncio.timeout(self._settings.llm_total_timeout_npc_s):
                sent_any = False
                async for delta in self._client.stream_text(
                        messages, max_tokens=self._settings.llm_max_tokens_npc,
                        temperature=self._settings.llm_temperature_npc):
                    if ttft_ms is None and delta.text:
                        ttft_ms = int((time.perf_counter() - t0) * 1000)
                    if delta.usage:
                        tokens = delta.usage
                    if delta.finish_reason == "length":
                        reason = "length_truncated"
                        break
                    for sentence in chunker.feed(delta.text):
                        validate_speech(sentence, self._settings.llm_max_speech_chars)
                        if emitted_chars + len(sentence) > self._settings.llm_max_speech_chars:
                            raise ProposalError(f"cumulative speech over {self._settings.llm_max_speech_chars} chars")
                        sentences.append(sentence)
                        emitted_chars += len(sentence) + 1
                        sent_any = True
                        yield {"type": "npc.speech.delta", "generationId": generation_id,
                               "turnId": turn_id, "text": sentence}
                remainder = chunker.finalize()
                if remainder:
                    validate_speech(remainder, self._settings.llm_max_speech_chars)
                    sentences.append(remainder)
                    emitted_chars += len(remainder) + 1
                    sent_any = True
                    yield {"type": "npc.speech.delta", "generationId": generation_id,
                           "turnId": turn_id, "text": remainder}
                if not sent_any or reason == "length_truncated":
                    raise ProposalError("empty or truncated speech") if reason != "length_truncated" else None
            # happy path 收尾
            full = " ".join(sentences).strip()
            latency_ms = int((time.perf_counter() - t0) * 1000)
            self._record(session_id=session_id, generation_id=generation_id, turn_id=turn_id,
                         utterance_id=utterance_id, latency_ms=latency_ms, ttft_ms=ttft_ms,
                         reason="none", ok=True, tokens=tokens)
            yield {"type": "npc.speech.commit", "generationId": generation_id, "turnId": turn_id, "text": full}
            yield {"type": "npc.turn.metadata", "generationId": generation_id, "turnId": turn_id,
                   "candidateWordIds": derive_candidate_word_ids(full, self._allowed_words)}
        except TimeoutError:
            reason = "timeout"
        except LLMConnectError as e:
            reason = "connect"
        except ProposalError as e:
            reason = "schema_reject"
        if reason != "none":
            async for m in _emit(self._fallback(user_text), reason, False, error=str(reason)):
                yield m
```

> 说明：`if not sent_any or reason == "length_truncated": raise ProposalError(...) if reason != "length_truncated" else None` 是让"空输出/截断"两条路径都落到下方统一降级。写成等价清晰版：若 `not sent_any` → `raise ProposalError("empty")`；若 `reason == "length_truncated"` → 不 raise，直接走 `if reason != "none"` 降级块（此时未发 commit）。

按上面的说明，把这段改为：

```python
                if not sent_any:
                    raise ProposalError("empty speech")
                if reason == "length_truncated":
                    pass  # 下方降级块处理
            # happy path 收尾（reason == "none" 才到这里）
            if reason == "none":
                full = " ".join(sentences).strip()
                latency_ms = int((time.perf_counter() - t0) * 1000)
                self._record(session_id=session_id, generation_id=generation_id, turn_id=turn_id,
                             utterance_id=utterance_id, latency_ms=latency_ms, ttft_ms=ttft_ms,
                             reason="none", ok=True, tokens=tokens)
                yield {"type": "npc.speech.commit", "generationId": generation_id, "turnId": turn_id, "text": full}
                yield {"type": "npc.turn.metadata", "generationId": generation_id, "turnId": turn_id,
                       "candidateWordIds": derive_candidate_word_ids(full, self._allowed_words)}
        except TimeoutError:
            reason = "timeout"
        except LLMConnectError as e:
            reason = "connect"
        except ProposalError as e:
            reason = "schema_reject"
        if reason != "none":
            async for m in _emit(self._fallback(user_text), reason, False, error=reason):
                yield m
```

（完整文件用后一个版本。`_emit` 里的 `async def` + `yield` 其实不能那样写——改成普通闭包返回生成器，见下。）

> 修正：`_emit` 不能在 async def 里直接 yield 后再 `async for`。改为普通函数返回一个生成器，`async for m in _emit(...): yield m` 保留。

`apps/api/app/llm/npc_actor.py` 最终完整版：

```python
"""NPC Actor：prompt 构造（trusted 系统人格 + untrusted 结构化字段）→ 流式文本
→ 按句切分 → 校验 → delta/commit/metadata；超时/失败降级 scripted_npc。"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable, Iterator

from app.llm.chunker import SentenceChunker
from app.llm.client import LLMAdapter, LLMConnectError
from app.llm.lexmatch import derive_candidate_word_ids
from app.llm.proposals import ProposalError, validate_speech
from app.settings import Settings

SYSTEM_PROMPT = (
    "You are Rosa, a friendly vendor in a small English bakery. "
    "Reply in short, simple English sentences suitable for an A1-A2 English learner. "
    "Stay in character at the bakery. Never mention that you are an AI. "
    "Use only plain English text with basic punctuation: no newlines, no URLs, no code."
)


def build_history(events, session_id: str, limit: int = 10, max_chars: int = 1200) -> list[dict]:
    """投影 dialogue.turn + dialogue.turn.interrupted → 成对历史（从旧到新）。"""
    all_events = events.list_after(session_id, 0)
    turns = [e["payload"] for e in all_events if e["event_type"] == "dialogue.turn"]
    interrupted = {e["payload"]["turnId"]: e["payload"]
                   for e in all_events if e["event_type"] == "dialogue.turn.interrupted"}
    recent = turns[-limit:]
    kept: list[dict] = []
    total = 0
    for t in reversed(recent):
        learner = {"speaker": "learner", "text": t["userText"]}
        rosa: dict = {"speaker": "rosa", "text": t["npcText"]}
        intr = interrupted.get(t["turnId"])
        if intr:
            rosa["interrupted_after_ms"] = int(intr.get("playedMs", 0))
        add = len(json.dumps([learner, rosa], ensure_ascii=False))
        if total + add > max_chars:
            continue
        kept.append(learner)
        kept.append(rosa)
        total += add
    kept.reverse()
    return kept


class NpcActor:
    def __init__(self, client: LLMAdapter, settings: Settings, llm_log,
                 allowed_words: dict[str, str], fallback: Callable[[str], str]) -> None:
        self._client = client
        self._settings = settings
        self._llm_log = llm_log
        self._allowed_words = dict(allowed_words)
        self._fallback = fallback

    def _scene_hint(self) -> str:
        return "Items nearby: " + ", ".join(self._allowed_words.values())

    def _build_messages(self, user_text: str, recent_turns: list[dict]) -> list[dict]:
        user_payload = {
            "transcript": user_text,
            "recent_turns": recent_turns,
            "scene": self._scene_hint(),
        }
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]

    def _record(self, *, session_id, generation_id, turn_id, utterance_id,
                latency_ms, ttft_ms, reason, ok, error=None, tokens=None) -> None:
        self._llm_log.record(
            session_id=session_id, generation_id=generation_id, turn_id=turn_id,
            utterance_id=utterance_id, role="npc_actor", model=self._settings.llm_model,
            prompt_tokens=(tokens or {}).get("prompt_tokens"),
            completion_tokens=(tokens or {}).get("completion_tokens"),
            latency_ms=latency_ms, ttft_ms=ttft_ms,
            finish_reason="stop" if ok else None,
            fallback_reason=reason, attempt=2 if reason == "connect" else 1,
            ok=ok, error=error,
        )

    def _degrade(self, *, generation_id: str, turn_id: str, user_text: str,
                 reason: str, error: str | None = None) -> Iterator[dict]:
        text = self._fallback(user_text)
        yield {"type": "npc.speech.delta", "generationId": generation_id, "turnId": turn_id, "text": text}
        yield {"type": "npc.speech.commit", "generationId": generation_id, "turnId": turn_id, "text": text}
        yield {"type": "npc.turn.metadata", "generationId": generation_id, "turnId": turn_id,
               "candidateWordIds": derive_candidate_word_ids(text, self._allowed_words)}

    async def stream_reply(self, *, session_id: str, generation_id: str, turn_id: str,
                           utterance_id: str, user_text: str, recent_turns: list[dict],
                           budget_exceeded: bool = False) -> AsyncIterator[dict]:
        t0 = time.perf_counter()
        ttft_ms: int | None = None
        tokens: dict | None = None
        reason = "none"
        error: str | None = None
        chunker = SentenceChunker()
        emitted_chars = 0
        sentences: list[str] = []
        messages = self._build_messages(user_text, recent_turns)

        try:
            if budget_exceeded:
                raise _BudgetExceeded()
            async with asyncio.timeout(self._settings.llm_total_timeout_npc_s):
                sent_any = False
                async for delta in self._client.stream_text(
                        messages, max_tokens=self._settings.llm_max_tokens_npc,
                        temperature=self._settings.llm_temperature_npc):
                    if ttft_ms is None and delta.text:
                        ttft_ms = int((time.perf_counter() - t0) * 1000)
                    if delta.usage:
                        tokens = delta.usage
                    if delta.finish_reason == "length":
                        reason = "length_truncated"
                        break
                    for sentence in chunker.feed(delta.text):
                        validate_speech(sentence, self._settings.llm_max_speech_chars)
                        if emitted_chars + len(sentence) > self._settings.llm_max_speech_chars:
                            raise ProposalError("cumulative speech over limit")
                        sentences.append(sentence)
                        emitted_chars += len(sentence) + 1
                        sent_any = True
                        yield {"type": "npc.speech.delta", "generationId": generation_id,
                               "turnId": turn_id, "text": sentence}
                if reason == "none":
                    # 只有正常流才 finalize 余量；length_truncated 直接整轮降级，不读半句
                    remainder = chunker.finalize()
                    if remainder:
                        validate_speech(remainder, self._settings.llm_max_speech_chars)
                        sentences.append(remainder)
                        sent_any = True
                        yield {"type": "npc.speech.delta", "generationId": generation_id,
                               "turnId": turn_id, "text": remainder}
                if not sent_any:
                    raise ProposalError("empty speech")
            if reason == "none":
                full = " ".join(sentences).strip()
                latency_ms = int((time.perf_counter() - t0) * 1000)
                self._record(session_id=session_id, generation_id=generation_id, turn_id=turn_id,
                             utterance_id=utterance_id, latency_ms=latency_ms, ttft_ms=ttft_ms,
                             reason="none", ok=True, tokens=tokens)
                yield {"type": "npc.speech.commit", "generationId": generation_id, "turnId": turn_id, "text": full}
                yield {"type": "npc.turn.metadata", "generationId": generation_id, "turnId": turn_id,
                       "candidateWordIds": derive_candidate_word_ids(full, self._allowed_words)}
        except TimeoutError:
            reason = "timeout"
            error = "total timeout"
        except LLMConnectError as e:
            reason = "connect"
            error = str(e)
        except ProposalError as e:
            reason = "schema_reject"
            error = str(e)
        except _BudgetExceeded:
            reason = "budget"
            error = "session call cap exceeded"

        if reason != "none":
            self._record(session_id=session_id, generation_id=generation_id, turn_id=turn_id,
                         utterance_id=utterance_id, latency_ms=int((time.perf_counter() - t0) * 1000),
                         ttft_ms=ttft_ms, reason=reason, ok=False, error=error)
            for m in self._degrade(generation_id=generation_id, turn_id=turn_id,
                                   user_text=user_text, reason=reason, error=error):
                yield m


class _BudgetExceeded(Exception):
    pass
```

（`_BudgetExceeded` 定义放 `NpcActor` 之前即可，模块内唯一。）

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --project apps/api pytest tests/test_npc_actor.py tests/test_history.py -v`
Expected: PASS。若 `test_cumulative_length_degrades` 失败，检查 mock 的 `stream_text_override` 总字符数是否确实超过 `llm_max_speech_chars`（mock 逐词 yield，`len(text)` 以单词加空格累加），并确认 actor 的 cumulative-length 分支在 `finish_reason != "length"` 时也触发。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/llm/npc_actor.py apps/api/tests/test_npc_actor.py apps/api/tests/test_history.py
git commit -m "feat(api): streaming NPC actor with degradation + history projection"
```

---

### Task 6: Companion Tutor（tutor + tutor_cache + 带读降级）

**Files:**
- Create: `apps/api/app/llm/tutor_cache.py`
- Create: `apps/api/app/llm/tutor.py`
- Create: `apps/api/tests/test_tutor.py`

**Interfaces:**
- Consumes: `LLMAdapter`/`JsonParseError`（Task 2）、`MockAdapter`（Task 2）、`validate_tutor`/`ProposalError`（Task 4）、`LlmLog`/`FakeLlmLog`（Task 1）、`Settings`（Task 1）
- Produces:
  - `TutorResult` dataclass：`word: str`、`scaffold: str`、`audio_base64: str | None`、`sample_rate: int | None`、`from_cache: bool`、`degraded: bool`
  - `TutorCache(connection: sqlite3.Connection, cache_dir: Path)`：
    - `get(word_id: str) -> dict | None`（含 `word_id/scaffold/model/audio_path/sample_rate/created_at`）
    - `put(word_id: str, scaffold: str, model: str, audio_path: str, sample_rate: int) -> None`（写缓存行；音频 wav 由调用方落盘到 cache_dir）
  - `CompanionTutor(client: LLMAdapter, settings: Settings, llm_log, cache: TutorCache, tts_client: Callable[[str], Awaitable[dict]])`
    - `async def reply(*, session_id: str, generation_id: str, word_id: str, word: str) -> TutorResult`
      - 命中缓存 → 读缓存音频文件 base64 → `from_cache=True`（0 LLM 0 TTS，不写 llm_calls）
      - 未命中 → `complete_json`（JSON mode，`temperature=0.3`，max_tokens 设足）→ `validate_tutor` → TTS 单词 → 音频写 `cache_dir/{word_id}.wav` → `put` → 返回
      - 任何失败（超时 / `LLMConnectError` / `JsonParseError` / `ProposalError`）→ 降级为只读单词：`scaffold=""`，仍 TTS 单词（TTS 失败则 `audio_base64=None`），记 llm_calls `ok=0` + 对应 reason；缓存写不入
      - `TUTOR_SYSTEM_PROMPT` 常量：要求仅返回 `{"word": ..., "scaffold": ...}`，scaffold 是给 A1-A2 学习者的一句简单英文解释且**必须包含目标词**

- [ ] **Step 1: 写失败的测试**

`apps/api/tests/test_tutor.py`：

```python
import base64
from pathlib import Path

import pytest

from app.event_store import EventStore
from app.llm.mock import MockAdapter
from app.llm.tutor import CompanionTutor
from app.llm.tutor_cache import TutorCache
from app.settings import Settings
from tests.fakes import FakeLlmLog

WAV_BYTES = b"RIFF____WAVEfmt "


def make_tts(tts_calls: list):
    async def tts_client(text: str):
        tts_calls.append(text)
        return {"audioBase64": base64.b64encode(WAV_BYTES).decode(), "ms": 30, "sampleRate": 24000}

    return tts_client


def make_tutor(tmp_path: Path, scenario: str = "ok", tts_calls: list | None = None):
    events = EventStore(tmp_path / "e.db")
    cache = TutorCache(events.connection, tmp_path / "audio")
    log = FakeLlmLog()
    calls = tts_calls if tts_calls is not None else []
    tutor = CompanionTutor(MockAdapter(scenario), Settings(llm_total_timeout_tutor_s=1.0),
                           log, cache, make_tts(calls))
    return tutor, log, cache


async def test_cache_miss_calls_llm_and_tts(tmp_path) -> None:
    tutor, log, cache = make_tutor(tmp_path)
    res = await tutor.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.word == "loaf"
    assert "loaf" in res.scaffold
    assert res.from_cache is False and res.degraded is False
    assert res.audio_base64 is not None and res.sample_rate == 24000
    assert log.rows[-1]["role"] == "companion_tutor" and log.rows[-1]["ok"] is True
    assert cache.get("word_loaf_n_1") is not None
    # 音频已落盘
    assert (tmp_path / "audio" / "word_loaf_n_1.wav").read_bytes() == WAV_BYTES


async def test_cache_hit_zero_llm_tts(tmp_path) -> None:
    tutor, log, cache = make_tutor(tmp_path)
    calls: list = []
    tutor2, _, _ = make_tutor(tmp_path, tts_calls=calls)
    await tutor2.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    res = await tutor2.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.from_cache is True
    assert res.audio_base64 is not None
    assert calls == ["loaf"]        # 第二次命中：0 TTS
    assert log.rows == []           # 命中缓存：0 LLM 调用（不写 llm_calls）


async def test_invalid_json_degrades_to_word_only(tmp_path) -> None:
    tutor, log, cache = make_tutor(tmp_path, "invalid_json")
    res = await tutor.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.word == "loaf" and res.scaffold == ""
    assert res.degraded is True
    assert res.audio_base64 is not None   # 仍读单词
    assert log.rows[-1]["fallback_reason"] == "invalid_json" and log.rows[-1]["ok"] is False


async def test_bad_word_degrades(tmp_path) -> None:
    tutor, log, cache = make_tutor(tmp_path, "bad_word_id")
    res = await tutor.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.word == "loaf" and res.scaffold == ""
    assert log.rows[-1]["fallback_reason"] == "schema_reject"


async def test_missing_word_degrades(tmp_path) -> None:
    tutor, log, cache = make_tutor(tmp_path, "missing_word")
    res = await tutor.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.scaffold == ""
    assert log.rows[-1]["fallback_reason"] == "schema_reject"


async def test_too_long_degrades(tmp_path) -> None:
    tutor, log, cache = make_tutor(tmp_path, "too_long")
    res = await tutor.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.scaffold == ""
    assert log.rows[-1]["fallback_reason"] == "schema_reject"


async def test_timeout_degrades(tmp_path) -> None:
    tutor, log, cache = make_tutor(tmp_path, "timeout")
    res = await tutor.reply(session_id="s1", generation_id="g1", word_id="word_loaf_n_1", word="loaf")
    assert res.degraded is True
    assert log.rows[-1]["fallback_reason"] == "timeout"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --project apps/api pytest tests/test_tutor.py -v`
Expected: FAIL（`ModuleNotFoundError: app.llm.tutor`）

- [ ] **Step 3: 实现 tutor_cache + tutor**

`apps/api/app/llm/tutor_cache.py`：

```python
"""tutor_cache 表：word_id 主键，缓存 scaffold + 已合成音频路径。"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tutor_cache(
  word_id     TEXT PRIMARY KEY,
  scaffold    TEXT NOT NULL,
  model       TEXT NOT NULL,
  audio_path  TEXT,
  sample_rate INTEGER,
  created_at  TEXT NOT NULL
);
"""


class TutorCache:
    def __init__(self, connection: sqlite3.Connection, cache_dir: Path) -> None:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(_SCHEMA)
        connection.commit()
        self._connection = connection
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def audio_path(self, word_id: str) -> Path:
        return self._cache_dir / f"{word_id}.wav"

    def get(self, word_id: str) -> dict | None:
        row = self._connection.execute(
            "SELECT word_id, scaffold, model, audio_path, sample_rate, created_at FROM tutor_cache WHERE word_id = ?",
            (word_id,),
        ).fetchone()
        if row is None:
            return None
        return {"word_id": row[0], "scaffold": row[1], "model": row[2],
                "audio_path": row[3], "sample_rate": row[4], "created_at": row[5]}

    def put(self, word_id: str, scaffold: str, model: str, audio_path: str,
            sample_rate: int) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO tutor_cache(word_id, scaffold, model, audio_path, sample_rate, created_at)"
                " VALUES(?, ?, ?, ?, ?, ?)",
                (word_id, scaffold, model, audio_path, sample_rate,
                 datetime.now(timezone.utc).isoformat()),
            )
            self._connection.commit()
```

`apps/api/app/llm/tutor.py`：

```python
"""Companion Tutor：点击实体 → {word, scaffold} 带读提案（JSON mode，不在硬实时路径）+ 缓存。"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.llm.client import LLMAdapter, LLMConnectError
from app.llm.proposals import ProposalError, validate_tutor
from app.llm.tutor_cache import TutorCache
from app.settings import Settings

TUTOR_SYSTEM_PROMPT = (
    "You help an A1-A2 English learner understand one word. "
    "Reply with ONLY a JSON object: {\"word\": <the given word>, \"scaffold\": <one simple English sentence>}. "
    "The scaffold must contain the given word and be under 120 characters. No newlines, no URLs, no code."
)


@dataclass
class TutorResult:
    word: str
    scaffold: str
    audio_base64: str | None
    sample_rate: int | None
    from_cache: bool
    degraded: bool


class CompanionTutor:
    def __init__(self, client: LLMAdapter, settings: Settings, llm_log,
                 cache: TutorCache, tts_client: Callable[[str], Awaitable[dict]]) -> None:
        self._client = client
        self._settings = settings
        self._llm_log = llm_log
        self._cache = cache
        self._tts_client = tts_client

    def _record(self, *, session_id, generation_id, reason, ok, error=None,
                latency_ms=None, ttft_ms=None, tokens=None) -> None:
        self._llm_log.record(
            session_id=session_id, generation_id=generation_id, role="companion_tutor",
            model=self._settings.llm_model,
            prompt_tokens=(tokens or {}).get("prompt_tokens"),
            completion_tokens=(tokens or {}).get("completion_tokens"),
            latency_ms=latency_ms, ttft_ms=ttft_ms,
            finish_reason="stop" if ok else None,
            fallback_reason=reason, attempt=1, ok=ok, error=error,
        )

    async def _synthesize_word(self, word: str) -> tuple[str, int]:
        tts = await self._tts_client(word)
        return tts["audioBase64"], int(tts["sampleRate"])

    async def reply(self, *, session_id: str, generation_id: str,
                    word_id: str, word: str) -> TutorResult:
        cached = self._cache.get(word_id)
        if cached is not None and cached.get("audio_path"):
            try:
                audio = base64.b64encode(Path(cached["audio_path"]).read_bytes()).decode()
                return TutorResult(word=word, scaffold=cached["scaffold"],
                                   audio_base64=audio, sample_rate=cached.get("sample_rate"),
                                   from_cache=True, degraded=False)
            except OSError:
                pass  # 缓存文件丢失 → 走重新合成

        t0 = time.perf_counter()
        reason = "none"
        error: str | None = None
        scaffold = ""
        try:
            messages = [
                {"role": "system", "content": TUTOR_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"word": word})},
            ]
            async with asyncio.timeout(self._settings.llm_total_timeout_tutor_s):
                res = await self._client.complete_json(
                    messages, max_tokens=self._settings.llm_max_tokens_tutor,
                    temperature=self._settings.llm_temperature_tutor)
            validate_tutor(res.json.get("word", ""), res.json.get("scaffold", ""),
                           expected_word=word, max_scaffold_chars=self._settings.llm_max_scaffold_chars)
            scaffold = res.json["scaffold"]
            audio_b64, sample_rate = await self._synthesize_word(word)
            audio_path = str(self._cache.audio_path(word_id))
            Path(audio_path).write_bytes(base64.b64decode(audio_b64))
            self._cache.put(word_id, scaffold, self._settings.llm_model, audio_path, sample_rate)
            self._record(session_id=session_id, generation_id=generation_id, reason="none",
                         ok=True, latency_ms=int((time.perf_counter() - t0) * 1000),
                         ttft_ms=int((time.perf_counter() - t0) * 1000), tokens=res.usage)
            return TutorResult(word=word, scaffold=scaffold, audio_base64=audio_b64,
                               sample_rate=sample_rate, from_cache=False, degraded=False)
        except TimeoutError:
            reason, error = "timeout", "tutor total timeout"
        except LLMConnectError as e:
            reason, error = "connect", str(e)
        except JsonParseError as e:
            reason, error = "invalid_json", str(e)
        except ProposalError as e:
            reason, error = "schema_reject", str(e)

        # 降级：只读单词（scaffold 缺省），仍 TTS；TTS 失败则无音频
        self._record(session_id=session_id, generation_id=generation_id, reason=reason,
                     ok=False, error=error, latency_ms=int((time.perf_counter() - t0) * 1000))
        try:
            audio_b64, sample_rate = await self._synthesize_word(word)
        except Exception:  # noqa: BLE001 —— 带读不能因 TTS 失败整条失败
            return TutorResult(word=word, scaffold="", audio_base64=None, sample_rate=None,
                               from_cache=False, degraded=True)
        return TutorResult(word=word, scaffold="", audio_base64=audio_b64,
                           sample_rate=sample_rate, from_cache=False, degraded=True)
```

`apps/api/app/llm/tutor.py` 顶部补充导入：`from pathlib import Path` 和 `from app.llm.client import JsonParseError`。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --project apps/api pytest tests/test_tutor.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/llm/tutor.py apps/api/app/llm/tutor_cache.py apps/api/tests/test_tutor.py
git commit -m "feat(api): companion tutor with tutor_cache and read-word fallback"
```

---

### Task 7: 服务端回合编排（run_round 流式 + ws.py 任务化/打断/spurious + create_app 接线 + 旧测试更新）

**Files:**
- Modify: `apps/api/app/voice_round.py`（重写为流式）
- Modify: `apps/api/app/ws.py`（重写：asyncio 任务 / SessionState / 打断 / spurious / 过期丢弃 / playbackState）
- Modify: `apps/api/app/main.py`（`create_app` 接线：llm_log / actor / scene_words / entity_words / sessions / asr_client / tts_client）
- Modify: `apps/api/tests/test_voice_round.py`（更新为先写库再发 commit、逐句音频）
- Modify: `apps/api/tests/test_ws.py`（更新 playback.interrupted 语义）
- Create: `apps/api/tests/ws_helpers.py`（FakeWS + make_app）
- Create: `apps/api/tests/test_interrupt.py`
- Create: `apps/api/tests/test_stale_drop.py`

**Interfaces:**
- Consumes: `NpcActor.stream_reply`/`build_history`（Task 5）、`Settings`/`SessionState`、`EventStore`、`workers.asr_client`/`workers.tts_client`
- Produces:
  - `run_round(session_id: str, utterance_id: str, audio_pcm16: bytes, events, asr_client, tts_client, ws_send, actor: NpcActor, state: "SessionState", *, budget_exceeded: bool = False) -> dict`
    - 流程：`turn_id = state.new_turn_id()`；`state.active_turn_id = turn_id`；ASR → 空文本直接返回；`async for msg in actor.stream_reply(..., budget_exceeded=...)`：
      - `npc.speech.delta` → 发 delta → `tts_client(句子)` → 记 `state.played_ms += ms`、`state.is_playing=True` → 发 `tts.audio.start{chunkId:s{N}}` + 音频 + `tts.audio.end` → `state.is_playing=False`
      - `npc.speech.commit` → `events.append(dialogue.turn)`（先写库，含全文 npcText + audioBytes 累计）→ 发 commit
      - `npc.turn.metadata` → 发 metadata
    - 取消（`asyncio.CancelledError`）：若未 commit 则补写一条 `dialogue.turn`（部分 npcText = 已累积 delta），再 `raise`
  - `SessionState(settings: Settings)`：`generation_id`、`round_task`、`active_turn_id`、`is_playing`、`played_ms`、`utterance_id`、`frames`、`audio_start_armed`、`barge_in_armed`、`pending_asks`、`semaphore`、`spurious_window_s = 0.5`、`new_turn_id() -> str`
  - `ws_session(ws)` 重写；`create_app` 增加可注入 `asr_client/tts_client/llm_client`，`app.state` 增加 `llm_log/actor/scene_words/entity_words/sessions/asr_client/tts_client/tutor_cache`
  - 消息路由：`audio.start`（装守卫）→ `audio.end`（截帧 → `asyncio.create_task(_run_round(...))`）→ `playback.interrupted`（`_cancel_and_interrupt`）→ 二进制帧（首帧触发 barge-in 取消）→ `companion.ask`（Task 8 接）
  - `_cancel_and_interrupt`：`is_playing=False`；取消 `round_task`；append `dialogue.turn.interrupted{generationId, turnId, playedMs}`

- [ ] **Step 1: 写失败的测试（ws_helpers + interrupt + stale_drop + 更新 voice_round/ws 测试）**

`apps/api/tests/ws_helpers.py`：

```python
"""WS 级测试工具：FakeWS（可控消息源）+ make_app（注入 mock asr/tts/llm）。"""
from __future__ import annotations

import asyncio
import base64
import json

from app.event_store import EventStore
from app.llm.mock import MockAdapter
from app.main import create_app


class FakeWS:
    """最小 WebSocket 替身：脚本化 receive + 记录 send。receive 消费完挂起。
    支持 `{"type": "sleep", "seconds": N}` 标记消息：让 ws_session 让出事件循环
    （回合任务才能真正启动/推进），用于打断类测试。"""

    def __init__(self, messages: list[dict], app, session_id: str = "sess-x") -> None:
        self._in = list(messages)
        self.app = app
        self.path_params = {"session_id": session_id}
        self.sent: list = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict:
        while self._in:
            msg = self._in.pop(0)
            if isinstance(msg, dict) and msg.get("type") == "sleep":
                await asyncio.sleep(msg.get("seconds", 0.1))
                continue
            return msg
        await asyncio.sleep(999)  # 挂起直到被取消

    async def send_json(self, payload) -> None:
        self.sent.append(payload)

    async def send_bytes(self, payload) -> None:
        self.sent.append(payload)


def audio_start(utterance_id: str) -> dict:
    return {"type": "websocket.receive",
            "text": json.dumps({"type": "audio.start", "utteranceId": utterance_id})}


def audio_end(utterance_id: str) -> dict:
    return {"type": "websocket.receive",
            "text": json.dumps({"type": "audio.end", "utteranceId": utterance_id})}


def audio_frame() -> dict:
    return {"type": "websocket.receive", "bytes": b"\x00\x00" * 400}


def make_app(tmp_path, scenario: str = "ok", slow_delta_s: float = 0.0,
             stream_text_override: str | None = None):
    events = EventStore(tmp_path / "e.db")

    class SlowActor:
        def __init__(self, inner):
            self._inner = inner

        async def stream_reply(self, **kw):
            async for m in self._inner.stream_reply(**kw):
                if m["type"] == "npc.speech.delta" and slow_delta_s:
                    await asyncio.sleep(slow_delta_s)
                yield m

    async def fake_asr(samples: bytes):
        return {"finalText": "hello", "segments": [], "language": "en", "confidence": -0.3}

    async def fake_tts(text: str):
        return {"audioBase64": base64.b64encode(b"\x00\x00\x00\x00").decode(), "ms": 30, "sampleRate": 16000}

    app = create_app(events, asr_client=fake_asr, tts_client=fake_tts,
                     llm_client=MockAdapter(scenario, stream_text_override=stream_text_override))
    app.state.actor = SlowActor(app.state.actor)
    return events, app


def cancel_round(app, session_id: str = "sess-x") -> None:
    st = app.state.sessions.get(session_id)
    if st and st.round_task and not st.round_task.done():
        st.round_task.cancel()
```

`apps/api/tests/test_interrupt.py`：

```python
import asyncio
import json

import pytest

from app.ws import ws_session
from tests.ws_helpers import FakeWS, audio_end, audio_frame, audio_start, cancel_round, make_app


def _sent_types(ws: FakeWS) -> list[str]:
    return [m["type"] for m in ws.sent if isinstance(m, dict)]


async def test_barge_in_cancels_round_and_appends_interrupted(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok", slow_delta_s=0.3)
    ws = FakeWS([
        audio_start("u1"), audio_frame(), audio_end("u1"),
        {"type": "sleep", "seconds": 0.4},   # 让 u1 回合真正跑起来（发出第一个 delta、set active_turn_id）
        audio_start("u2"), audio_frame(), audio_end("u2"),
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.8)   # u1 回合已在逐句播放中，u2 首帧已打断
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancel_round(app)

    evs = events.list_after("sess-x", 0)
    types = [e["event_type"] for e in evs]
    assert "dialogue.turn.interrupted" in types
    intr = [e for e in evs if e["event_type"] == "dialogue.turn.interrupted"][0]
    assert intr["payload"]["turnId"]
    assert intr["payload"]["playedMs"] >= 0
    assert intr["payload"]["generationId"] == app.state.sessions["sess-x"].generation_id


async def test_explicit_interrupt_without_turn_writes_nothing(tmp_path) -> None:
    events, app = make_app(tmp_path)
    ws = FakeWS([{"type": "websocket.receive",
                  "text": json.dumps({"type": "playback.interrupted"})}], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0)   # 让 ws_session 真正处理 playback.interrupted
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events.list_after("sess-x", 0) == []


async def test_spurious_audio_start_without_frames_ignored(tmp_path) -> None:
    # 1 句短回复 + slow_delta：u1 回合在 sleep 标记内跑完并 commit
    events, app = make_app(tmp_path, scenario="ok", slow_delta_s=0.3,
                           stream_text_override="Hi there. ")
    ws = FakeWS([
        audio_start("u1"), audio_frame(), audio_end("u1"),
        {"type": "sleep", "seconds": 0.5},   # u1 回合完成（delta + commit）
        audio_start("u2"), audio_end("u2"),  # u2 无任何帧
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0)   # 让 ws_session 初始化 session state
    app.state.sessions["sess-x"].spurious_window_s = 0.02
    await asyncio.sleep(0.8)   # u2 的 start 因无帧被 spurious 守卫忽略
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancel_round(app)

    types = [e["event_type"] for e in events.list_after("sess-x", 0)]
    assert "dialogue.turn" in types       # u1 正常完成
    assert "dialogue.turn.interrupted" not in types
```

`apps/api/tests/test_stale_drop.py`：

```python
import asyncio

import pytest

from app.ws import ws_session
from tests.ws_helpers import FakeWS, audio_end, audio_frame, audio_start, cancel_round, make_app


async def test_old_round_sends_nothing_after_cancel(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok", slow_delta_s=0.3)
    ws = FakeWS([
        audio_start("u1"), audio_frame(), audio_end("u1"),
        {"type": "sleep", "seconds": 0.4},   # u1 回合已启动并发出第一个 delta
        audio_start("u2"), audio_frame(), audio_end("u2"),
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.5)   # u1 逐句播放中 → u2 首帧到达取消 u1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    if st.round_task and not st.round_task.done():
        st.round_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await st.round_task

    msgs = [m for m in ws.sent if isinstance(m, dict)]
    assert "npc.speech.commit" not in [m.get("type") for m in msgs]   # u1 未 commit 即被打断
    assert "dialogue.turn.interrupted" in [e["event_type"] for e in events.list_after("sess-x", 0)]
```

更新 `apps/api/tests/test_voice_round.py`（整体替换为流式版本）：

```python
import pytest

from app.event_store import EventStore
from app.llm.mock import MockAdapter
from app.llm.npc_actor import NpcActor
from app.settings import Settings
from app.voice_round import run_round
from app.ws import SessionState
from tests.fakes import FakeLlmLog

SILENCE = bytes(1600)  # 50ms @16k 静音

ALLOWED = {"word_loaf_n_1": "loaf"}


def make_asr(asr_final: str = "hello"):
    async def asr_client(samples):
        return {"finalText": asr_final, "segments": [], "language": "en", "confidence": -0.3}

    return asr_client


def make_tts(tts_calls: list):
    async def tts_client(text):
        tts_calls.append(text)
        return {"audioBase64": "AA==", "ms": 40, "sampleRate": 16000}

    return tts_client


def make_spy(events):
    sent: list = []

    async def ws_send(payload):
        sent.append({"payload": payload, "eventsVisible": len(events.list_after("s1", 0))})

    return sent, ws_send


def make_actor(scenario="ok", override=None):
    actor = NpcActor(MockAdapter(scenario, stream_text_override=override),
                     Settings(llm_total_timeout_npc_s=1.0), FakeLlmLog(),
                     ALLOWED, lambda u: "Sorry, I didn't catch that.")
    return actor


async def test_round_streams_delta_then_commit_then_metadata(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    sent, ws_send = make_spy(events)
    tts_calls: list = []
    state = SessionState(Settings())
    actor = make_actor(override="Hello! A loaf is three dollars. Can I help you?")
    result = await run_round("s1", "u1", SILENCE, events, make_asr(), make_tts(tts_calls),
                             ws_send, actor, state)

    assert result["finalText"] == "hello" and result["replied"] is True
    types = [f["payload"]["type"] for f in sent if isinstance(f["payload"], dict)]
    assert types.count("npc.speech.delta") == 3
    assert "npc.speech.commit" in types and "npc.turn.metadata" in types
    meta = [f["payload"] for f in sent if isinstance(f["payload"], dict) and f["payload"]["type"] == "npc.turn.metadata"][0]
    assert meta["candidateWordIds"] == ["word_loaf_n_1"]
    # 先写库再发 commit
    commit_send = [f for f in sent if isinstance(f["payload"], dict) and f["payload"]["type"] == "npc.speech.commit"][0]
    assert commit_send["eventsVisible"] >= 1
    # 逐句 TTS
    assert tts_calls == ["Hello!", "A loaf is three dollars.", "Can I help you?"]
    # 每句都有 audio.start/end
    assert types.count("tts.audio.start") == 3 and types.count("tts.audio.end") == 3


async def test_empty_text_sends_nothing_and_writes_nothing(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    sent, ws_send = make_spy(events)
    tts_calls: list = []
    state = SessionState(Settings())
    result = await run_round("s1", "u1", SILENCE, events, make_asr(asr_final=""), make_tts(tts_calls),
                             ws_send, make_actor(), state)
    assert result["replied"] is False
    assert tts_calls == [] and sent == []
    assert events.list_after("s1", 0) == []


async def test_budget_exceeded_forces_scripted(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    sent, ws_send = make_spy(events)
    state = SessionState(Settings())
    result = await run_round("s1", "u1", SILENCE, events, make_asr(), make_tts([]),
                             ws_send, make_actor(), state, budget_exceeded=True)
    assert result["replied"] is True
    commit = [f["payload"] for f in sent if isinstance(f["payload"], dict) and f["payload"]["type"] == "npc.speech.commit"][0]
    assert commit["text"] == "Sorry, I didn't catch that."
```

更新 `apps/api/tests/test_ws.py`（整体替换）：

```python
from pathlib import Path

from fastapi.testclient import TestClient

from app.event_store import EventStore
from app.main import create_app


def test_ws_mounted_and_control_appends_nothing_without_turn(tmp_path: Path) -> None:
    events = EventStore(tmp_path / "e.db")
    app = create_app(events)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/sessions/sess-smoke") as ws:
            ws.send_json({"type": "playback.interrupted", "utteranceId": None})
    # 无活跃回合时，显式打断不写任何事件（phase-2 append-only：interrupted 需配对 dialogue.turn）
    assert events.list_after("sess-smoke", 0) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --project apps/api pytest tests/test_voice_round.py tests/test_ws.py tests/test_interrupt.py tests/test_stale_drop.py -v`
Expected: FAIL（import/断言错误——voice_round 还是旧的单块实现）

- [ ] **Step 3: 实现 run_round + ws.py + create_app**

`apps/api/app/voice_round.py`（整体替换）：

```python
"""语音回合（流式）：PCM → ASR final → NpcActor 流式回复 → 逐句 TTS → 音频回传。
回合跑在独立 asyncio 任务里（ws.py 用 create_task 调度），可被取消。
asr_client/tts_client/ws_send/actor/state 可注入（测试用 mock/可控状态）。"""
from __future__ import annotations

import asyncio
import base64
import uuid
from typing import Awaitable, Callable

from app.llm.npc_actor import NpcActor, build_history

ASRClient = Callable[[bytes], Awaitable[dict]]
TTSClient = Callable[[str], Awaitable[dict]]
WsSend = Callable[[object], Awaitable[None]]


async def run_round(
    session_id: str,
    utterance_id: str,
    audio_pcm16: bytes,
    events,
    asr_client: ASRClient,
    tts_client: TTSClient,
    ws_send: WsSend,
    actor: NpcActor,
    state,  # SessionState（Task 7 定义）
    *,
    budget_exceeded: bool = False,
) -> dict:
    turn_id = state.new_turn_id()
    state.active_turn_id = turn_id
    asr_result = await asr_client(audio_pcm16)
    final_text = asr_result["finalText"].strip()
    if not final_text:
        state.active_turn_id = None
        return {"finalText": "", "turnId": turn_id, "replied": False}

    committed = False
    audio_bytes = 0
    chunk_index = 0
    accumulated = ""
    try:
        async for msg in actor.stream_reply(
                session_id=session_id, generation_id=state.generation_id, turn_id=turn_id,
                utterance_id=utterance_id, user_text=final_text,
                recent_turns=build_history(events, session_id),
                budget_exceeded=budget_exceeded):
            mtype = msg["type"]
            if mtype == "npc.speech.delta":
                accumulated += msg["text"] + " "
                await ws_send(msg)
                tts = await tts_client(msg["text"])
                audio = base64.b64decode(tts["audioBase64"])
                audio_bytes += len(audio)
                state.played_ms += int(tts.get("ms", 0))
                chunk_index += 1
                state.is_playing = True
                await ws_send({"type": "tts.audio.start", "generationId": state.generation_id,
                               "turnId": turn_id, "chunkId": f"s{chunk_index}",
                               "sampleRate": tts["sampleRate"]})
                await ws_send(audio)
                await ws_send({"type": "tts.audio.end", "generationId": state.generation_id,
                               "turnId": turn_id, "chunkId": f"s{chunk_index}"})
                state.is_playing = False
            elif mtype == "npc.speech.commit":
                # 先写库（含全文 npcText），再对外确认
                events.append(session_id, "dialogue.turn", {
                    "turnId": turn_id, "utteranceId": utterance_id,
                    "userText": final_text, "npcText": msg["text"], "audioBytes": audio_bytes,
                })
                committed = True
                await ws_send(msg)
            elif mtype == "npc.turn.metadata":
                await ws_send(msg)
        state.active_turn_id = None
        return {"finalText": final_text, "turnId": turn_id, "replied": True}
    except asyncio.CancelledError:
        # 未 commit 就被打断 → 补写部分轮次（用户输入 + 已产生的 npcText），保持证据不丢
        if not committed:
            events.append(session_id, "dialogue.turn", {
                "turnId": turn_id, "utteranceId": utterance_id,
                "userText": final_text, "npcText": accumulated.strip(), "audioBytes": audio_bytes,
            })
        raise
```

`apps/api/app/ws.py`（整体替换）：

```python
"""浏览器实时连接：音频二进制 + 控制 JSON。回合跑独立 asyncio 任务（可取消）；
playbackState + barge-in + spurious 守卫 + append-only 打断 + 双端过期丢弃（服务端侧）。"""
from __future__ import annotations

import asyncio
import base64
import json
import uuid

from fastapi import APIRouter, WebSocket

from app.settings import Settings
from app.voice_round import run_round

router = APIRouter()


class SessionState:
    def __init__(self, settings: Settings) -> None:
        self.generation_id = f"gen_{uuid.uuid4().hex[:8]}"
        self.round_task: asyncio.Task | None = None
        self.active_turn_id: str | None = None
        self.is_playing = False
        self.played_ms = 0
        self.utterance_id: str | None = None
        self.frames: list[bytes] = []
        self.audio_start_armed = False
        self.barge_in_armed = False
        self.pending_asks: dict[str, asyncio.Task] = {}
        self.semaphore = asyncio.Semaphore(settings.llm_concurrency_limit)
        self.spurious_window_s = 0.5
        self._turn_seq = 0

    def new_turn_id(self) -> str:
        self._turn_seq += 1
        return f"turn_{uuid.uuid4().hex[:8]}_{self._turn_seq}"


@router.websocket("/ws/sessions/{session_id}")
async def ws_session(ws: WebSocket) -> None:
    await ws.accept()
    app = ws.app
    events = app.state.events
    settings = app.state.settings
    session_id = ws.path_params["session_id"]
    sessions: dict[str, SessionState] = app.state.sessions
    state = sessions.get(session_id) or SessionState(settings)
    sessions[session_id] = state

    async def send(payload: object) -> None:
        if isinstance(payload, bytes):
            await ws.send_bytes(payload)
        else:
            await ws.send_json(payload)

    async def _spurious_guard() -> None:
        await asyncio.sleep(state.spurious_window_s)
        if state.audio_start_armed:
            # 无任何帧到达 → 忽略这次 audio.start（不取消回合、不记打断）
            state.audio_start_armed = False
            state.barge_in_armed = False

    async def _cancel_and_interrupt() -> None:
        state.is_playing = False
        task = state.round_task
        turn_id = state.active_turn_id
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if turn_id:
            events.append(session_id, "dialogue.turn.interrupted", {
                "generationId": state.generation_id, "turnId": turn_id,
                "playedMs": state.played_ms,
            })
        state.round_task = None
        state.active_turn_id = None

    async def _run_round(payload: bytes, utterance_id: str) -> None:
        budget_exceeded = app.state.llm_log.count_session_calls(session_id) >= settings.llm_session_call_cap
        try:
            async with state.semaphore:
                await run_round(session_id, utterance_id, payload, events,
                                app.state.asr_client, app.state.tts_client, send,
                                app.state.actor, state, budget_exceeded=budget_exceeded)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 回合失败不杀连接
            await send({"type": "round.error", "turnId": state.active_turn_id, "error": str(e)})
        finally:
            state.round_task = None

    while True:
        msg = await ws.receive()
        if msg["type"] == "websocket.disconnect":
            if state.round_task and not state.round_task.done():
                state.round_task.cancel()
            return
        if msg.get("text"):
            ctrl = json.loads(msg["text"])
            t = ctrl["type"]
            if t == "audio.start":
                state.utterance_id = ctrl.get("utteranceId")
                state.frames = []
                state.audio_start_armed = True
                state.barge_in_armed = state.is_playing or (
                    state.round_task is not None and not state.round_task.done())
                asyncio.create_task(_spurious_guard())
            elif t == "audio.end":
                if state.utterance_id is not None and state.frames:
                    payload = b"".join(state.frames)
                    state.round_task = asyncio.create_task(_run_round(payload, state.utterance_id))
                state.utterance_id = None
                state.audio_start_armed = False
                state.barge_in_armed = False
            elif t == "playback.interrupted":
                await _cancel_and_interrupt()
            # companion.ask 分支由 Task 8 加入（ws_session 路由 + _handle_companion_ask 实现一起落）
        else:
            raw = msg.get("bytes")
            if raw:
                if state.audio_start_armed:
                    state.audio_start_armed = False
                    if state.barge_in_armed:
                        await _cancel_and_interrupt()
                    state.barge_in_armed = False
                if state.utterance_id is not None:
                    state.frames.append(raw)
```

（`companion.ask` 的 ws_session 路由分支与 `_handle_companion_ask` 实现都只在 Task 8 一次落地，Task 7 不引入未接线分支。）

`apps/api/app/main.py`（`create_app` 整体替换）：

```python
from pathlib import Path

from fastapi import FastAPI, HTTPException

from app.event_store import EventStore
from app.llm.client import get_client
from app.llm.npc_actor import NpcActor
from app.llm.tutor_cache import TutorCache
from app.llm_log import LlmLog
from app.scene_store import SceneStore
from app.scripted_npc import reply as scripted_reply
from app.settings import Settings
from app.workers import asr_client as worker_asr, tts_client as worker_tts

SCENE_ID = "scene_bakery_001"
ASR_URL = "http://127.0.0.1:8001"
TTS_BASE = "http://127.0.0.1:8002"


def create_app(events: EventStore | None = None, settings: Settings | None = None, *,
               asr_client=None, tts_client=None, llm_client=None) -> FastAPI:
    settings = settings or Settings.from_env()
    events = events or EventStore(settings.db_path)
    scenes = SceneStore(settings.asset_root)
    scene = scenes.get_compiled_scene(SCENE_ID)
    scene_words = {
        e["semantics"]["wordId"]: e["semantics"]["name"]
        for e in scene["entities"] if e.get("semantics", {}).get("wordId")
    }
    entity_words = {
        e["id"]: (e["semantics"]["wordId"], e["semantics"]["name"])
        for e in scene["entities"] if e.get("semantics", {}).get("wordId")
    }
    llm_log = LlmLog(events.connection)
    cache = TutorCache(events.connection, settings.tutor_cache_dir)
    client = llm_client or get_client(settings)
    actor = NpcActor(client, settings, llm_log, scene_words,
                     lambda u: scripted_reply(u)["speech"])

    app = FastAPI(title="english-town-api", version="0.2.0")
    app.state.events = events
    app.state.settings = settings
    app.state.scenes = scenes
    app.state.scene_words = scene_words
    app.state.entity_words = entity_words
    app.state.llm_log = llm_log
    app.state.tutor_cache = cache
    app.state.actor = actor
    app.state.asr_client = asr_client or (lambda audio: worker_asr(audio, f"{ASR_URL}/transcribe"))
    app.state.tts_client = tts_client or (lambda text: worker_tts(text, TTS_BASE))
    app.state.sessions = {}

    from app.ws import router as ws_router
    app.include_router(ws_router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "archetypeCount": len(scenes.list_archetype_ids())}

    @app.get("/api/archetypes")
    def archetypes() -> dict:
        return {"ids": scenes.list_archetype_ids()}

    @app.get("/api/scenes/{scene_id}")
    def scene(scene_id: str) -> dict:
        try:
            return scenes.get_compiled_scene(scene_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown scene: {scene_id}")

    return app


app = create_app()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --project apps/api pytest tests/test_voice_round.py tests/test_ws.py tests/test_interrupt.py tests/test_stale_drop.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/voice_round.py apps/api/app/ws.py apps/api/app/main.py \
  apps/api/tests/test_voice_round.py apps/api/tests/test_ws.py \
  apps/api/tests/ws_helpers.py apps/api/tests/test_interrupt.py apps/api/tests/test_stale_drop.py
git commit -m "feat(api): cancellable streaming round task + barge-in/spurious/append-only interrupt"
```

---

### Task 8: companion.ask 接线 + 成本护栏（ws.py 补全 + create_app 补 tutor + 集成测试）

**Files:**
- Modify: `apps/api/app/main.py`（`create_app` 补 `CompanionTutor` → `app.state.tutor`）
- Modify: `apps/api/app/ws.py`（`ws_session` 路由加 `companion.ask` 分支 + `_handle_companion_ask` 实现：word 查表 → tutor 任务（in-flight 合并）→ `companion.reply` + 音频）
- Create: `apps/api/tests/test_companion.py`
- Create: `apps/api/tests/test_budget.py`

**Interfaces:**
- Consumes: `CompanionTutor.reply`/`TutorResult`（Task 6）、`app.state.entity_words`/`tutor`/`llm_log`/`sessions`（Task 7）
- Produces:
  - ws.py `_handle_companion_ask(entity_id)`：
    - `entity_id` 不在 `app.state.entity_words` → `companion.reply{error:"unknown_entity"}` 并返回
    - `word_id, word = entity_words[entity_id]`；若 `word_id` 已在 `state.pending_asks`（in-flight）→ 复用，不再 spawn（用户连点不放大调用）
    - 否则 `task = asyncio.create_task(_run_tutor(word_id, word))`，存 `state.pending_asks[word_id]`
    - `_run_tutor`：`async with state.semaphore` → `await app.state.tutor.reply(...)` → 发 `companion.reply{turnId: comp_turn, word, scaffold, degraded}` → 若有 `audio_base64` → `tts.audio.start{chunkId:"c1"}` + 音频 + `tts.audio.end`；`finally: state.pending_asks.pop(word_id, None)`
  - `test_budget.py`：`llm_calls` 满 cap → 回合走 scripted（`fallback_reason=budget` 行 + dialogue.turn.npcText == 兜底文本）

- [ ] **Step 1: 写失败的测试**

`apps/api/tests/test_companion.py`：

```python
import asyncio
import json

import pytest

from app.ws import ws_session
from tests.ws_helpers import FakeWS, make_app


def _companion_ask(entity_id: str) -> dict:
    return {"type": "websocket.receive",
            "text": json.dumps({"type": "companion.ask", "entityId": entity_id})}


async def test_companion_ask_returns_reply_and_audio(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    ws = FakeWS([_companion_ask("loaf-1")], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.4)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    replies = [m for m in ws.sent if isinstance(m, dict) and m.get("type") == "companion.reply"]
    assert len(replies) == 1
    assert replies[0]["word"] == "loaf"
    assert "loaf" in replies[0]["scaffold"]
    assert replies[0]["degraded"] is False
    # 音频消息（start + bytes + end）都在
    types = [m["type"] for m in ws.sent if isinstance(m, dict)]
    assert types.count("tts.audio.start") == 1 and types.count("tts.audio.end") == 1
    assert any(isinstance(m, bytes) for m in ws.sent)
    # 缓存已写入
    assert app.state.tutor_cache.get("word_loaf_n_1") is not None


async def test_companion_ask_unknown_entity(tmp_path) -> None:
    events, app = make_app(tmp_path)
    ws = FakeWS([_companion_ask("no-such-entity")], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0)   # 让 ws_session 处理 companion.ask
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    replies = [m for m in ws.sent if isinstance(m, dict) and m.get("type") == "companion.reply"]
    assert replies and replies[0]["error"] == "unknown_entity"


async def test_same_entity_inflight_merged(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    ws = FakeWS([_companion_ask("loaf-1"), _companion_ask("loaf-1")], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    replies = [m for m in ws.sent if isinstance(m, dict) and m.get("type") == "companion.reply"]
    assert len(replies) == 1          # 连点同一实体只回一次（复用同一结果）
    rows = app.state.llm_log.recent("sess-x")
    assert len([r for r in rows if r["role"] == "companion_tutor"]) == 1
```

`apps/api/tests/test_budget.py`：

```python
import asyncio

import pytest

from app.scripted_npc import reply as scripted_reply
from app.ws import ws_session
from tests.ws_helpers import FakeWS, audio_end, audio_frame, audio_start, cancel_round, make_app


async def test_call_cap_forces_scripted(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    # 先把 session 的 llm_calls 写满 cap
    cap = app.state.settings.llm_session_call_cap
    for _ in range(cap):
        app.state.llm_log.record(session_id="sess-x", role="npc_actor", model="deepseek-chat")

    ws = FakeWS([audio_start("u1"), audio_frame(), audio_end("u1")], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancel_round(app)

    rows = app.state.llm_log.recent("sess-x")
    assert any(r["fallback_reason"] == "budget" and r["ok"] == 0 for r in rows)
    # dialogue.turn 的 npcText == scripted 兜底（对同一 userText 的 scripted_npc 输出）
    turns = [e["payload"] for e in events.list_after("sess-x", 0) if e["event_type"] == "dialogue.turn"]
    assert turns and turns[-1]["npcText"] == scripted_reply("hello")["speech"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --project apps/api pytest tests/test_companion.py tests/test_budget.py -v`
Expected: FAIL（`companion.reply` 占位返回 `error="not_implemented"`，断言不匹配）

- [ ] **Step 3: 实现 companion 接线**

`apps/api/app/main.py` 的 `create_app` 增加 tutor 构建（在 `actor = NpcActor(...)` 之后）：

```python
    from app.llm.tutor import CompanionTutor
    tutor = CompanionTutor(client, settings, llm_log, cache, app.state.tts_client)
```

（注意：`app.state.tts_client` 赋值在 tutor 构建之后才能引用——把 `app.state.tts_client = ...` 移到 `client = ...` 之前，或先算 `tts_impl = tts_client or (lambda text: worker_tts(text, TTS_BASE))`，tutor 用 `tts_impl`，再 `app.state.tts_client = tts_impl`。）

修正后的顺序：

```python
    llm_log = LlmLog(events.connection)
    cache = TutorCache(events.connection, settings.tutor_cache_dir)
    client = llm_client or get_client(settings)
    asr_impl = asr_client or (lambda audio: worker_asr(audio, f"{ASR_URL}/transcribe"))
    tts_impl = tts_client or (lambda text: worker_tts(text, TTS_BASE))
    actor = NpcActor(client, settings, llm_log, scene_words,
                     lambda u: scripted_reply(u)["speech"])
    tutor = CompanionTutor(client, settings, llm_log, cache, tts_impl)
    ...
    app.state.asr_client = asr_impl
    app.state.tts_client = tts_impl
    app.state.tutor = tutor
```

`apps/api/app/ws.py` 的 `_handle_companion_ask` 实现，并在 `ws_session` 路由的 `playback.interrupted` 分支后加入 `elif t == "companion.ask": await _handle_companion_ask(ctrl.get("entityId"))`：

```python
    async def _handle_companion_ask(entity_id: str) -> None:
        entry = app.state.entity_words.get(entity_id)
        if entry is None:
            await send({"type": "companion.reply", "turnId": f"comp_{uuid.uuid4().hex[:8]}",
                        "word": "", "scaffold": "", "degraded": True, "error": "unknown_entity"})
            return
        word_id, word = entry

        async def _run_tutor() -> None:
            try:
                async with state.semaphore:
                    res = await app.state.tutor.reply(
                        session_id=session_id, generation_id=state.generation_id,
                        word_id=word_id, word=word)
                turn_id = f"comp_{uuid.uuid4().hex[:8]}"
                await send({"type": "companion.reply", "turnId": turn_id,
                            "word": res.word, "scaffold": res.scaffold,
                            "degraded": res.degraded})
                if res.audio_base64 is not None:
                    state.is_playing = True
                    await send({"type": "tts.audio.start", "generationId": state.generation_id,
                                "turnId": turn_id, "chunkId": "c1",
                                "sampleRate": res.sample_rate})
                    await send(base64.b64decode(res.audio_base64))
                    await send({"type": "tts.audio.end", "generationId": state.generation_id,
                                "turnId": turn_id, "chunkId": "c1"})
                    state.is_playing = False
            finally:
                state.pending_asks.pop(word_id, None)

        if word_id in state.pending_asks:
            return  # in-flight 合并：连点同一实体不放大调用
        state.pending_asks[word_id] = asyncio.create_task(_run_tutor())
```

（ws.py 顶部 import 块补 `import base64`——Task 7 的 ws.py 完整文件已含：`import asyncio / import json / import uuid / import base64`。）

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run --project apps/api pytest tests/test_companion.py tests/test_budget.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/main.py apps/api/app/ws.py apps/api/tests/test_companion.py apps/api/tests/test_budget.py
git commit -m "feat(api): companion.ask wiring (entityId→word) + tutor in-flight merge + budget cap"
```

---

### Task 9: 前端音频健壮性（rms-gate ducking/barge-in hold + mic autoGainControl）

**Files:**
- Modify: `apps/web/src/audio/rms-gate.ts`
- Modify: `apps/web/src/audio/mic.ts`
- Create: `apps/web/tests/rms-gate.test.ts`

**Interfaces:**
- Consumes: 无
- Produces:
  - `RmsGate` 新构造器：`constructor(threshold = 0.008, silenceFrames = 11, holdMs = 300, duckFactor = 2, frameMs = 20)`
    - `setDucking(on: boolean)`：播放开始/结束都启动一个 300ms 恢复窗口（`this.duckUntil = performance.now() + 300`）
    - `feed(frame)`：有效阈值 = ducking 窗口内 `threshold * duckFactor`；RMS 连续超阈累计 `speechMs`，达到 `holdMs` 才算 `'speech'`（否则 `'silence'`）；低于阈值清零 `speechMs`，连续 `silenceFrames` 帧静音返回 `'end'`
  - `Mic.start()` 的约束加 `autoGainControl: true`

- [ ] **Step 1: 写失败的测试**

`apps/web/tests/rms-gate.test.ts`：

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RmsGate } from '../src/audio/rms-gate';

function loudFrame(amp = 0.5, samples = 320): ArrayBuffer {
  const buf = new ArrayBuffer(samples * 2);
  const v = new DataView(buf);
  for (let i = 0; i < samples; i++) v.setInt16(i * 2, amp * 32767, true);
  return buf;
}

function silentFrame(samples = 320): ArrayBuffer {
  return new ArrayBuffer(samples * 2);
}

describe('RmsGate ducking + barge-in hold', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('reports speech only after 300ms of above-threshold RMS', () => {
    const g = new RmsGate(holdMs: 300);  // frameMs 默认 20 → 需连续 15 帧
    for (let i = 0; i < 14; i++) expect(g.feed(loudFrame())).toBe('silence');
    expect(g.feed(loudFrame())).toBe('speech');
  });

  it('a single spike does not count as speech', () => {
    const g = new RmsGate();
    expect(g.feed(loudFrame())).toBe('silence');
    expect(g.feed(silentFrame())).toBe('silence');
  });

  it('ducking raises the threshold during playback', () => {
    const g = new RmsGate(holdMs: 300, duckFactor: 2);
    g.setDucking(true);
    // 半幅度的"扬声器回声"在 ducking 下低于阈值（0.5 < 0.008*2*?）——用低于阈值*2 的幅度
    const medium = 0.01;
    for (let i = 0; i < 20; i++) {
      expect(g.feed(loudFrame(medium))).not.toBe('speech');
    }
    // 300ms 后恢复阈值 → 同一幅度可触发
    vi.advanceTimersByTime(400);
    for (let i = 0; i < 20; i++) {
      expect(g.feed(loudFrame(medium))).toBe('speech');
    }
  });

  it('silence ends the utterance after silenceFrames', () => {
    const g = new RmsGate();
    for (let i = 0; i < 15; i++) g.feed(loudFrame());  // 先触发 speech
    for (let i = 0; i < 10; i++) g.feed(silentFrame());
    expect(g.feed(silentFrame())).toBe('end');
  });
});
```

> 注意：上面测试写法有误——TS 构造器是 `new RmsGate({ holdMs })` 还是位置参数？本计划定义**位置参数** `new RmsGate(threshold, silenceFrames, holdMs, duckFactor, frameMs)`。修正测试：

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RmsGate } from '../src/audio/rms-gate';

function loudFrame(amp = 0.5, samples = 320): ArrayBuffer {
  const buf = new ArrayBuffer(samples * 2);
  const v = new DataView(buf);
  for (let i = 0; i < samples; i++) v.setInt16(i * 2, amp * 32767, true);
  return buf;
}

function silentFrame(samples = 320): ArrayBuffer {
  return new ArrayBuffer(samples * 2);
}

describe('RmsGate ducking + barge-in hold', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('reports speech only after 300ms of above-threshold RMS', () => {
    const g = new RmsGate(0.008, 11, 300, 2, 20);  // frameMs 20 → 连续 15 帧
    for (let i = 0; i < 14; i++) expect(g.feed(loudFrame())).toBe('silence');
    expect(g.feed(loudFrame())).toBe('speech');
  });

  it('a single spike does not count as speech', () => {
    const g = new RmsGate();
    expect(g.feed(loudFrame())).toBe('silence');
    expect(g.feed(silentFrame())).toBe('silence');
  });

  it('ducking raises the threshold during playback and restores after 300ms', () => {
    const g = new RmsGate(0.008, 11, 300, 2, 20);
    g.setDucking(true);
    // 幅度 0.01：常态阈值 0.008 之下 → 未 ducking 会 speech；ducking 阈值 0.016 之上 → 不 speech
    for (let i = 0; i < 20; i++) {
      expect(g.feed(loudFrame(0.01))).toBe('silence');
    }
    vi.advanceTimersByTime(400);  // 恢复窗口结束
    for (let i = 0; i < 20; i++) {
      expect(g.feed(loudFrame(0.01))).toBe('speech');
    }
  });

  it('silence ends the utterance after silenceFrames', () => {
    const g = new RmsGate();
    for (let i = 0; i < 15; i++) g.feed(loudFrame());
    for (let i = 0; i < 10; i++) g.feed(silentFrame());
    expect(g.feed(silentFrame())).toBe('end');
  });
});
```

> 注意：`0.01` 的 RMS 在阈值 0.008（未 ducking）时确实 >0.008 会 speech；ducking 阈值 0.016 时 0.01 < 0.016 不 speech。两个分支都靠 `holdMs=300`（连续 15 帧）累积。正确。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/web && npx vitest run tests/rms-gate.test.ts`
Expected: FAIL（`RmsGate` 构造器参数不匹配 / 无 ducking）

- [ ] **Step 3: 实现 rms-gate + mic**

`apps/web/src/audio/rms-gate.ts`（整体替换）：

```ts
export class RmsGate {
  private threshold: number;
  private silenceFrames: number;
  private holdMs: number;
  private duckFactor: number;
  private frameMs: number;
  private silence = 0;
  private speechMs = 0;
  private duckUntil = 0;

  constructor(threshold = 0.008, silenceFrames = 11, holdMs = 300, duckFactor = 2, frameMs = 20) {
    this.threshold = threshold;
    this.silenceFrames = silenceFrames;
    this.holdMs = holdMs;
    this.duckFactor = duckFactor;
    this.frameMs = frameMs;
  }

  /** 播放开始/结束都启动 300ms 恢复窗口（播放结束后的回声尾巴也被压住）。 */
  setDucking(_on: boolean): void {
    this.duckUntil = performance.now() + 300;
  }

  private effectiveThreshold(): number {
    return performance.now() < this.duckUntil ? this.threshold * this.duckFactor : this.threshold;
  }

  feed(frame: ArrayBuffer): 'speech' | 'silence' | 'end' {
    const view = new DataView(frame);
    let sum = 0;
    for (let i = 0; i < view.byteLength; i += 2) {
      const s = view.getInt16(i, true) / 32768;
      sum += s * s;
    }
    const rms = Math.sqrt(sum / (view.byteLength / 2));
    if (rms >= this.effectiveThreshold()) {
      this.speechMs += this.frameMs;
      this.silence = 0;
      return this.speechMs >= this.holdMs ? 'speech' : 'silence';
    }
    this.speechMs = 0;
    this.silence++;
    return this.silence >= this.silenceFrames ? 'end' : 'silence';
  }
}
```

`apps/web/src/audio/mic.ts` 的 `start()` 一行改为：

```ts
const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
```

- [ ] **Step 4: 运行测试确认通过 + 回归旧套件**

Run: `cd apps/web && npx vitest run`
Expected: PASS（含新 rms-gate 用例 + 既有全部）

- [ ] **Step 5: 提交**

```bash
git add apps/web/src/audio/rms-gate.ts apps/web/src/audio/mic.ts apps/web/tests/rms-gate.test.ts
git commit -m "feat(web): RMS ducking + barge-in hold; mic autoGainControl"
```

---

### Task 10: 前端协议接入（turnGate + useVoiceRound delta/commit/turnId + CompanionPopover + App）

**Files:**
- Create: `apps/web/src/audio/turnGate.ts`
- Modify: `apps/web/src/useVoiceRound.ts`
- Modify: `apps/web/src/CompanionPopover.tsx`
- Modify: `apps/web/src/App.tsx`
- Create: `apps/web/tests/turnGate.test.ts`
- Create: `apps/web/tests/useVoiceRound.test.ts`

**Interfaces:**
- Consumes: `VoiceSocket`（on/sendControl/sendAudioChunk）、`Mic`、`RmsGate`（Task 9）、`AudioQueue`、`createWavPlayer`（Task 9 已就绪的既有件）
- Produces:
  - `turnGate.ts`：`isAcceptedTurn(currentTurnId: string | null, companionTurnId: string | null, turnId: string | null | undefined) -> boolean`（turnId 为空 → false；等于 current 或 companion → true；否则 false）；`isSpeechMessage(type)` 辅助（delta/commit/metadata）
  - `useVoiceRound` 新增：`companion: { word: string; scaffold: string } | null` state；`askCompanion(entityId: string)` 发 `{type:"companion.ask", entityId}`；`interrupt()` 停止播放 + 清队 + 发 `playback.interrupted`（保持）；`beginUtterance()` 用 `listeningRef` 防重复；`start()` 接新消息：`npc.speech.delta`（累积到当前 turn）、`npc.speech.commit`（覆盖全文 + 存 `candidateWordIds`）、`npc.turn.metadata`（存 `candidateWordIds`）、`companion.reply`（设 `companion` state + `companionTurnIdRef`）、`tts.audio.start/end`（多 chunk：turnId 门控 + ducking 开/关 + 停止前一个播放再播下一个）
  - `Turn` 类型扩展：`{ role, text, candidateWordIds?: string[] }`
  - `CompanionPopover({ entity, companion, onAsk, onClose })`：显示 `entity.semantics.name` + companion 的 scaffold；`onAsk(entityId)`
  - `App.tsx`：`onEntityClick={(e) => setFocusEntity(e)}`；`<CompanionPopover entity={focusEntity} companion={companion} ...>`

- [ ] **Step 1: 写失败的测试**

`apps/web/tests/turnGate.test.ts`：

```ts
import { describe, expect, it } from 'vitest';
import { isAcceptedTurn } from '../src/audio/turnGate';

describe('turnGate', () => {
  it('rejects null turnId', () => {
    expect(isAcceptedTurn('t1', null, null)).toBe(false);
    expect(isAcceptedTurn(null, null, undefined)).toBe(false);
  });

  it('accepts the current dialogue turnId', () => {
    expect(isAcceptedTurn('t1', null, 't1')).toBe(true);
  });

  it('accepts the pending companion turnId', () => {
    expect(isAcceptedTurn('t1', 'c9', 'c9')).toBe(true);
  });

  it('rejects a stale turnId (old round late arrival)', () => {
    expect(isAcceptedTurn('t2', null, 't1')).toBe(false);
  });
});
```

`apps/web/tests/useVoiceRound.test.ts`：

```ts
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useVoiceRound } from '../src/useVoiceRound';

vi.mock('../src/audio/ws-client', () => {
  class FakeVoiceSocket {
    handlers = new Map<string, Set<(m: any) => void>>();
    sent: any[] = [];
    static last: FakeVoiceSocket | null = null;
    constructor(public sessionId: string) { FakeVoiceSocket.last = this; }
    async connect(_url: string) {}
    sendControl(msg: any) { this.sent.push(msg); }
    sendAudioChunk() {}
    on(type: string, h: any) {
      if (!this.handlers.has(type)) this.handlers.set(type, new Set());
      this.handlers.get(type)!.add(h);
    }
    close() {}
  }
  return { VoiceSocket: FakeVoiceSocket };
});

vi.mock('../src/audio/mic', () => {
  class FakeMic {
    onChunk: ((c: ArrayBuffer) => void) | null = null;
    async start() {}
    stop() {}
  }
  return { Mic: FakeMic };
});

vi.mock('../src/audio/rms-gate', () => {
  const { RmsGate } = vi.importActual<typeof import('../src/audio/rms-gate')>('../src/audio/rms-gate');
  return { RmsGate };
});

afterEach(() => { vi.restoreAllMocks(); });

/** start() 里才 new VoiceSocket()——socket 实例只能在 start 之后从 mock 取。 */
async function startHook() {
  const { VoiceSocket } = await import('../src/audio/ws-client');
  const { result } = renderHook(() => useVoiceRound('s', 'ws://x'));
  await act(async () => { await result.current.start(); });
  const sock = VoiceSocket.last!;
  const emit = (type: string, payload: any) => {
    for (const h of sock.handlers.get(type) ?? []) h(payload);
  };
  return { result, sock, emit };
}

describe('useVoiceRound protocol', () => {
  it('delta 累积 → commit 覆盖 + candidateWordIds', async () => {
    const { result, emit } = await startHook();
    act(() => result.current.beginUtterance());
    act(() => {
      emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g', turnId: 't1', text: 'Hello!' });
      emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g', turnId: 't1', text: 'Welcome.' });
      emit('npc.turn.metadata', { type: 'npc.turn.metadata', generationId: 'g', turnId: 't1', candidateWordIds: ['word_loaf_n_1'] });
      emit('npc.speech.commit', { type: 'npc.speech.commit', generationId: 'g', turnId: 't1', text: 'Hello! Welcome.' });
    });
    const last = result.current.turns[result.current.turns.length - 1];
    expect(last).toEqual({ role: 'npc', text: 'Hello! Welcome.', candidateWordIds: ['word_loaf_n_1'] });
  });

  it('stale turnId delta 被丢弃（不入字幕）', async () => {
    const { result, emit } = await startHook();
    act(() => result.current.beginUtterance());
    act(() => {
      emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g', turnId: 't1', text: 'A loaf.' });
      emit('npc.speech.commit', { type: 'npc.speech.commit', generationId: 'g', turnId: 't1', text: 'A loaf.' });
    });
    const before = result.current.turns.length;
    act(() => emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g', turnId: 't2', text: 'Late junk.' }));
    expect(result.current.turns.length).toBe(before);
    expect(result.current.turns[result.current.turns.length - 1].text).toBe('A loaf.');
  });

  it('companion.ask 只发 entityId；reply 写入 companion state', async () => {
    const { result, sock, emit } = await startHook();
    act(() => result.current.askCompanion('loaf-1'));
    expect(sock.sent.some((m: any) => m.type === 'companion.ask' && m.entityId === 'loaf-1')).toBe(true);
    act(() => emit('companion.reply', { type: 'companion.reply', turnId: 'c1', word: 'loaf', scaffold: 'A loaf is bread.', degraded: false }));
    expect(result.current.companion).toEqual({ word: 'loaf', scaffold: 'A loaf is bread.' });
  });
});
```

> 注：`startHook` 里 `start()` 会 `await sock.connect(...)`（FakeVoiceSocket.connect 是 async 空函数）；`new Mic()` 走 mock；jsdom 有 `performance.now`，真实 RmsGate 可用。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd apps/web && npx vitest run tests/turnGate.test.ts tests/useVoiceRound.test.ts`
Expected: FAIL（`turnGate` 不存在；`useVoiceRound` 无 companion state）

- [ ] **Step 3: 实现 turnGate + useVoiceRound + CompanionPopover + App**

`apps/web/src/audio/turnGate.ts`：

```ts
/** 双端过期丢弃（前端侧）：非当前 turnId 的 delta/commit/metadata/tts.audio.start 一律丢弃。 */
export function isAcceptedTurn(
  currentTurnId: string | null,
  companionTurnId: string | null,
  turnId: string | null | undefined,
): boolean {
  if (!turnId) return false;
  return turnId === currentTurnId || turnId === companionTurnId;
}
```

`apps/web/src/useVoiceRound.ts`（整体替换）：

```ts
import { useRef, useState } from 'react';
import { Mic } from './audio/mic';
import { RmsGate } from './audio/rms-gate';
import { VoiceSocket } from './audio/ws-client';
import { AudioQueue } from './audio/playback-queue';
import { createWavPlayer, type PlaybackHandle, type WavPlayer } from './audio/playback';
import { isAcceptedTurn } from './audio/turnGate';
import type { Turn } from './DialogueDock';

interface CompanionState { word: string; scaffold: string; }

export function useVoiceRound(sessionId: string, wsUrl: string) {
  const [micOn, setMicOn] = useState(false);
  const [status, setStatus] = useState('idle');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [companion, setCompanion] = useState<CompanionState | null>(null);
  const socketRef = useRef<VoiceSocket | null>(null);
  const queueRef = useRef(new AudioQueue());
  const micRef = useRef<Mic | null>(null);
  const gateRef = useRef(new RmsGate());
  const playerRef = useRef<WavPlayer | null>(null);
  const activePlaybackRef = useRef<PlaybackHandle | null>(null);
  const uttRef = useRef(0);
  const listeningRef = useRef(false);
  const currentTurnIdRef = useRef<string | null>(null);
  const companionTurnIdRef = useRef<string | null>(null);
  const audioTurnIdRef = useRef<string | null>(null);

  const ensurePlayer = () => {
    if (!playerRef.current) playerRef.current = createWavPlayer();
    return playerRef.current;
  };

  const stopPlayback = () => {
    activePlaybackRef.current?.stop();
    activePlaybackRef.current = null;
  };

  const nextUtterance = () => `u${++uttRef.current}`;

  /** delta 累积：同一 turnId 的句子追加到字幕（空格拼接）。 */
  const appendDelta = (turnId: string, sentence: string) => {
    setTurns((t) => {
      const copy = [...t];
      const last = copy[copy.length - 1];
      if (last && last.role === 'npc' && last.turnId === turnId) {
        copy[copy.length - 1] = { ...last, text: last.text ? `${last.text} ${sentence}` : sentence };
      } else {
        copy.push({ role: 'npc', turnId, text: sentence });
      }
      return copy;
    });
  };

  /** commit 是权威全文：覆盖已累积的 delta 字幕。 */
  const applyCommit = (turnId: string, text: string) => {
    setTurns((t) => {
      const copy = [...t];
      const last = copy[copy.length - 1];
      if (last && last.role === 'npc' && last.turnId === turnId) {
        copy[copy.length - 1] = { ...last, text };
      } else {
        copy.push({ role: 'npc', turnId, text });
      }
      return copy;
    });
  };

  const applyMetadata = (turnId: string, candidateWordIds: string[]) => {
    setTurns((t) => {
      const copy = [...t];
      const last = copy[copy.length - 1];
      if (last && last.role === 'npc' && last.turnId === turnId) {
        copy[copy.length - 1] = { ...last, candidateWordIds };
      }
      return copy;
    });
  };

  const start = async () => {
    const sock = new VoiceSocket(sessionId);
    await sock.connect(wsUrl);
    socketRef.current = sock;

    sock.on('npc.speech.delta', (m: any) => {
      if (!isAcceptedTurn(currentTurnIdRef.current, companionTurnIdRef.current, m.turnId)) return;
      if (currentTurnIdRef.current === null) currentTurnIdRef.current = m.turnId;
      appendDelta(m.turnId, (m.text ?? '').trim());
    });
    sock.on('npc.speech.commit', (m: any) => {
      if (!isAcceptedTurn(currentTurnIdRef.current, companionTurnIdRef.current, m.turnId)) return;
      if (currentTurnIdRef.current === null) currentTurnIdRef.current = m.turnId;
      applyCommit(m.turnId, m.text);
    });
    sock.on('npc.turn.metadata', (m: any) => {
      if (!isAcceptedTurn(currentTurnIdRef.current, companionTurnIdRef.current, m.turnId)) return;
      applyMetadata(m.turnId, m.candidateWordIds ?? []);
    });
    sock.on('companion.reply', (m: any) => {
      if (m.error) return;
      companionTurnIdRef.current = m.turnId;
      setCompanion({ word: m.word, scaffold: m.scaffold });
    });
    sock.on('tts.audio.start', (m: any) => {
      if (!isAcceptedTurn(currentTurnIdRef.current, companionTurnIdRef.current, m.turnId)) {
        queueRef.current.clear();
        audioTurnIdRef.current = null;
        return;
      }
      audioTurnIdRef.current = m.turnId;
      gateRef.current.setDucking(true);
      setStatus('speaking');
    });
    sock.on('tts.audio.end', (m: any) => {
      if (audioTurnIdRef.current !== m.turnId) return;
      audioTurnIdRef.current = null;
      gateRef.current.setDucking(false);
      stopPlayback();
      const item = queueRef.current.next();
      if (item) activePlaybackRef.current = ensurePlayer().play(item.buffer);
      setStatus('idle');
    });
    sock.on('audio.binary', (chunk) => {
      if (audioTurnIdRef.current !== null) queueRef.current.enqueue(audioTurnIdRef.current, chunk as ArrayBuffer);
    });

    const mic = new Mic();
    mic.onChunk = (chunk) => {
      sock.sendAudioChunk(chunk);
      const tag = gateRef.current.feed(chunk);
      if (tag === 'speech' && !listeningRef.current) {
        // 语音触发开始；若正在播放 → 本地立即停播（barge-in 本地清理），audio.start 即打断信号
        stopPlayback();
        listeningRef.current = true;
        sock.sendControl({ type: 'audio.start', utteranceId: nextUtterance(), languageMode: 'en' });
        setStatus('listening');
      } else if (tag === 'end' && listeningRef.current) {
        sock.sendControl({ type: 'audio.end', utteranceId: `u${uttRef.current}` });
        listeningRef.current = false;
        setStatus('idle');
      }
    };
    await mic.start();
    micRef.current = mic;
    setMicOn(true);
  };

  const beginUtterance = () => {
    if (listeningRef.current) return;
    listeningRef.current = true;
    socketRef.current?.sendControl({ type: 'audio.start', utteranceId: nextUtterance(), languageMode: 'en' });
  };

  const stop = () => {
    stopPlayback();
    micRef.current?.stop();
    socketRef.current?.close();
    setMicOn(false);
  };

  const interrupt = () => {
    stopPlayback();
    listeningRef.current = false;
    socketRef.current?.sendControl({ type: 'playback.interrupted' });
    queueRef.current.clear();
  };

  const askCompanion = (entityId: string) => {
    socketRef.current?.sendControl({ type: 'companion.ask', entityId });
  };

  return { micOn, status, turns, companion, start, stop, beginUtterance, interrupt, askCompanion };
}
```

`apps/web/src/DialogueDock.tsx` 的 `Turn` 类型扩展：

```ts
export interface Turn { role: 'user' | 'npc'; text: string; turnId?: string; candidateWordIds?: string[]; }
```

`apps/web/src/CompanionPopover.tsx`（整体替换）：

```tsx
import type { Entity } from './types';

interface Props {
  entity: Entity;
  companion: { word: string; scaffold: string } | null;
  onAsk: (entityId: string) => void;
  onClose: () => void;
}

export function CompanionPopover({ entity, companion, onAsk, onClose }: Props) {
  const word = entity.semantics?.name ?? '';
  return (
    <div data-testid="companion-popover" style={{ position: 'fixed', right: 16, bottom: 120, background: '#fff', border: '1px solid #ccc', borderRadius: 12, padding: 12, width: 280 }}>
      <strong>伴学者</strong>
      <p>这个词：<b>{word}</b></p>
      {companion && companion.word === word && (
        <p data-testid="companion-scaffold" style={{ color: '#333' }}>{companion.scaffold}</p>
      )}
      <button onClick={() => onAsk(entity.id)}>读给我听</button>
      <button onClick={onClose}>关闭</button>
    </div>
  );
}
```

`apps/web/src/App.tsx`（修改 `onEntityClick` 与 popover 渲染）：

```tsx
const [focusEntity, setFocusEntity] = useState<any>(null);
const { micOn, status, turns, companion, start, stop, beginUtterance, interrupt, askCompanion } =
  useVoiceRound('sess-1', `ws://${location.hostname}:8000/ws/sessions/sess-1`);
...
<SceneViewport scene={scene} onEntityClick={(e) => setFocusEntity(e)} />
...
{focusEntity && <CompanionPopover entity={focusEntity} companion={companion} onAsk={askCompanion} onClose={() => setFocusEntity(null)} />}
```

- [ ] **Step 4: 运行测试确认通过 + 全量前端回归**

Run: `cd apps/web && npx vitest run`
Expected: PASS。若有 `Turn` 类型不兼容（DialogueDock 使用），确认扩展是可选字段，旧代码不受影响。

- [ ] **Step 5: 提交**

```bash
git add apps/web/src/audio/turnGate.ts apps/web/src/useVoiceRound.ts apps/web/src/CompanionPopover.tsx apps/web/src/App.tsx apps/web/src/DialogueDock.tsx apps/web/tests/turnGate.test.ts apps/web/tests/useVoiceRound.test.ts
git commit -m "feat(web): streaming protocol (delta/commit/metadata) + companion popover + turnId gate"
```

---

### Task 11: llm-smoke golden + 启动自检探活

**Files:**
- Create: `apps/api/app/llm/probe.py`
- Create: `scripts/llm-smoke.py`
- Create: `tests/fixtures/llm-golden/README.md`（占位说明）
- Modify: `scripts/startup-selfcheck.py`（加 LLM 探活步骤）

**Interfaces:**
- Consumes: `get_client`/`LLMAdapter`（Task 2）、`Settings.from_env`（Task 1）
- Produces:
  - `app/llm/probe.py`：`async def run(settings: Settings) -> dict`——无 key 返回 `{ok:false, scenario:"no_key"}`；有 key 用 `OpenAIClient` 流式 `"hi"`，测 `ttft_ms`/`latency_ms`，返回 `{ok, scenario, model, ttft_ms, latency_ms}`；失败返回 `{ok:false, scenario:"connect", error}`。模块入口 `if __name__ == "__main__": asyncio.run(...)` 打印 JSON
  - `scripts/llm-smoke.py`：跑 **20 次** NPC 流式 + 20 次 tutor JSON，记录 `ttft_ms`/`latency_ms`/token；写 `tests/fixtures/llm-golden/npc-<ISOdate>.json` 与 `tutor-<ISOdate>.json`，并打印汇总（min/p50/p95/max）。需真 key（`DEEPSEEK_API_KEY`）；无 key 直接报错退出

- [ ] **Step 1: 写探活实现 + smoke 脚本**

`apps/api/app/llm/probe.py`：

```python
"""启动自检用的 LLM 探活：极短 completion。"""
from __future__ import annotations

import asyncio
import time

from app.llm.client import OpenAIClient
from app.settings import Settings


async def run(settings: Settings) -> dict:
    if not settings.llm_api_key:
        return {"ok": False, "scenario": "no_key", "model": settings.llm_model,
                "message": "DEEPSEEK_API_KEY 未设置，运行时将落 mock"}
    client = OpenAIClient(settings)
    t0 = time.perf_counter()
    try:
        ttft_ms = None
        async for delta in client.stream_text(
                [{"role": "user", "content": "hi"}],
                max_tokens=settings.llm_max_tokens_npc,
                temperature=settings.llm_temperature_npc):
            if ttft_ms is None and delta.text:
                ttft_ms = int((time.perf_counter() - t0) * 1000)
        return {"ok": True, "scenario": "ok", "model": settings.llm_model,
                "ttft_ms": ttft_ms, "latency_ms": int((time.perf_counter() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "scenario": "connect", "model": settings.llm_model,
                "error": str(e)[:300]}


async def main() -> None:
    import json
    print(json.dumps(await run(Settings.from_env()), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
```

`scripts/llm-smoke.py`：

```python
"""LLM golden 测量：真 key 跑 20 次 NPC 流式 + 20 次 Tutor JSON，
记录 TTFT / 总延迟 / token 分布 → tests/fixtures/llm-golden/。"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.llm.client import get_client  # noqa: E402
from app.settings import Settings  # noqa: E402

RUNS = 20


async def sample_npc(client, settings) -> dict:
    t0 = time.perf_counter()
    ttft_ms = None
    tokens = None
    async for delta in client.stream_text(
            [{"role": "user", "content": json.dumps({"transcript": "hello", "recent_turns": [], "scene": "bakery"})}],
            max_tokens=settings.llm_max_tokens_npc, temperature=settings.llm_temperature_npc):
        if ttft_ms is None and delta.text:
            ttft_ms = int((time.perf_counter() - t0) * 1000)
        if delta.usage:
            tokens = delta.usage
    return {"ttft_ms": ttft_ms, "latency_ms": int((time.perf_counter() - t0) * 1000),
            "completion_tokens": (tokens or {}).get("completion_tokens")}


async def sample_tutor(client, settings) -> dict:
    t0 = time.perf_counter()
    res = await client.complete_json(
        [{"role": "user", "content": json.dumps({"word": "loaf"})}],
        max_tokens=settings.llm_max_tokens_tutor, temperature=settings.llm_temperature_tutor)
    return {"latency_ms": int((time.perf_counter() - t0) * 1000),
            "completion_tokens": (res.usage or {}).get("completion_tokens")}


def summarize(samples: list[dict], key: str) -> dict:
    vals = sorted(s for s in samples if s.get(key) is not None)
    if not vals:
        return {}
    return {"min": vals[0], "p50": statistics.median(vals),
            "p95": vals[int(len(vals) * 0.95) - 1], "max": vals[-1], "n": len(vals)}


async def main() -> None:
    settings = Settings.from_env()
    if not settings.llm_api_key:
        raise SystemExit("llm-smoke 需要 DEEPSEEK_API_KEY（真 key），无 key 用 mock 无意义")
    client = get_client(settings)
    npc = [await sample_npc(client, settings) for _ in range(RUNS)]
    tutor = [await sample_tutor(client, settings) for _ in range(RUNS)]
    today = date.today().isoformat()
    out_dir = ROOT / "tests" / "fixtures" / "llm-golden"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"npc-{today}.json").write_text(
        json.dumps({"meta": {"model": settings.llm_model, "runs": RUNS}, "samples": npc}, indent=2),
        encoding="utf-8")
    (out_dir / f"tutor-{today}.json").write_text(
        json.dumps({"meta": {"model": settings.llm_model, "runs": RUNS}, "samples": tutor}, indent=2),
        encoding="utf-8")
    print(json.dumps({
        "npc": {"ttft_ms": summarize(npc, "ttft_ms"), "latency_ms": summarize(npc, "latency_ms")},
        "tutor": {"latency_ms": summarize(tutor, "latency_ms")},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
```

`tests/fixtures/llm-golden/README.md`：

```markdown
# llm-golden

`llm-smoke.py` 用真 key 跑 20 次后落盘的实际测量（TTFT / 总延迟 / token），
作为 `llm_total_timeout_*` / `llm_max_tokens_*` 定值依据 + mock 文本模板来源。
文件名带日期；不入 CI。
```

`scripts/startup-selfcheck.py` 的 `main()` 加一段（放在 `report["asr"]` 之后）：

```python
    report["llm"] = run_in("apps/api", "app.llm.probe")
```

- [ ] **Step 2: 运行验证**

Run: `uv run --project apps/api -m app.llm.probe`（无 key → 输出 `{"ok": false, "scenario": "no_key", ...}`）
Run: `uv run --project apps/api python scripts/llm-smoke.py`（无 key → 报错退出；有 key → 写 golden + 汇总）
Run: `python scripts/startup-selfcheck.py`（含 `llm` 段）

- [ ] **Step 3: 提交**

```bash
git add apps/api/app/llm/probe.py scripts/llm-smoke.py scripts/startup-selfcheck.py tests/fixtures/llm-golden/README.md
git commit -m "feat: llm-smoke golden measurement + startup LLM probe"
```

---

### Task 12: 全量回归 + 无状态越权写实断言 + README

**Files:**
- Create: `apps/api/tests/test_state_audit.py`
- Modify: `docs/README.md`（或仓库根 README）——补阶段 2 环境变量与运行说明
- Modify: `docs/superpowers/specs/2026-08-06-english-town-phase2-design.md`（若需把状态标记从 "待用户复核" 改为 "已批准/已实现"）

**Interfaces:**
- Consumes: 全部前序任务产物
- Produces: 无新接口——回归 + 审计

- [ ] **Step 1: 写无状态越权写实断言测试**

`apps/api/tests/test_state_audit.py`：

```python
"""写实断言：任何合法操作只允许 3 张表变化（session_events / llm_calls / tutor_cache）。
对未来新增表（如 mastery_states）越权写入会立刻失败。"""
from __future__ import annotations

import asyncio
import hashlib
import json

from app.event_store import EventStore
from app.ws import ws_session
from tests.ws_helpers import FakeWS, audio_end, audio_frame, audio_start, make_app

ALLOWED_TABLES = {"session_events", "llm_calls", "tutor_cache"}


def _snapshot(events: EventStore) -> dict[str, tuple[int, str]]:
    """所有表 count + 行级 checksum。"""
    snap: dict[str, tuple[int, str]] = {}
    tables = {r[0] for r in events.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    for table in sorted(tables):
        count = events.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        digest = hashlib.sha1()
        for (row,) in events.connection.execute(f"SELECT quote(*) FROM {table}"):
            digest.update(str(row).encode())
        snap[table] = (int(count), digest.hexdigest())
    return snap


def _changed(before: dict, after: dict) -> set[str]:
    return {t for t in before if before[t] != after.get(t)}


async def test_full_round_only_touches_allowed_tables(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    before = _snapshot(events)
    ws = FakeWS([
        audio_start("u1"), audio_frame(), audio_end("u1"),
        {"type": "websocket.receive", "text": json.dumps({"type": "companion.ask", "entityId": "loaf-1"})},
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.6)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    if st.round_task and not st.round_task.done():
        st.round_task.cancel()

    changed = _changed(before, _snapshot(events))
    assert changed <= ALLOWED_TABLES, f"越权写入了表: {changed - ALLOWED_TABLES}"
    assert changed == ALLOWED_TABLES   # 一个合法回合确实动了这三张（含 tutor 缓存）
```

（顶部补 `import pytest`。）

- [ ] **Step 2: 全量回归**

Run: `uv run --project apps/api pytest -v`
Expected: 阶段 1 + 阶段 2 全部通过

Run: `cd apps/web && npx vitest run`
Expected: 前端全部通过

Run: `cd apps/web && npm run build`（tsc -b && vite build）
Expected: 构建通过

- [ ] **Step 3: README 补充**

在 README 的 API 部分补：

```markdown
### 阶段 2：LLM（DeepSeek / OpenAI 兼容）

- 环境变量：`DEEPSEEK_API_KEY`（必填才走真模型；不填自动落 mock）、`LLM_BASE_URL`（默认 https://api.deepseek.com）、`LLM_MODEL`（默认 deepseek-chat；拒绝 deepseek-reasoner）
- 离线测试：`MOCK_LLM_SCENARIO=ok|timeout|connect_error|invalid_json|bad_word_id|missing_word|too_long|truncated|empty`（默认 ok）
- 测量：`uv run --project apps/api python scripts/llm-smoke.py`（真 key，20 次 → tests/fixtures/llm-golden/）
- 启动自检：`python scripts/startup-selfcheck.py`（含 LLM 探活）
- 成本护栏默认值：单 session 200 次 LLM 调用 / 并发 2 / tutor 同词 in-flight 合并
```

- [ ] **Step 4: 提交**

```bash
git add apps/api/tests/test_state_audit.py docs/README.md
git commit -m "test: state audit (write-real assertions) + README phase-2 docs"
```

---

## Self-Review（writing-plans 要求）

**1. Spec 覆盖核对**（逐节 → 任务）：

| spec 节 | 计划任务 |
|---|---|
| §2 settings 字段表 / llm_calls 表 / client / mock | Task 1 / Task 2 |
| §3 流式管线 / chunker / lexmatch / validate_speech | Task 3 / Task 4 / Task 5 |
| §4 双通道协议 + 双端过期丢弃 | Task 5（消息带 generationId+turnId）/ Task 7（服务端比对丢弃）/ Task 10（前端 turnId 门） |
| §5 AEC / ducking / barge-in / playbackState / spurious | Task 9（前端）/ Task 7（服务端） |
| §6 回合任务 + append-only 打断 | Task 7 |
| §7 Tutor + 校验 + tutor_cache | Task 6 / Task 8 |
| §8 历史裁剪 + 打断标注 | Task 5（build_history） |
| §9 安全（结构化字段 / 模型守卫 / 白名单） | Task 2（reasoner 拒绝）/ Task 4（校验）/ Task 5（prompt 结构） |
| §10 成本护栏（cap / 并发 / in-flight 合并） | Task 7（semaphore + cap 检查）/ Task 8（合并 + budget 测试） |
| §11 分角色超时 + temperature | Task 5 / Task 6（settings 默认值） |
| §12.1 单测矩阵 | Task 3-6（各纯函数）+ Task 7（打断/过期丢弃）+ Task 8（tutor/budget） |
| §12.2 集成 + 写实断言 + 回归 | Task 7-8（WS 集成）+ Task 12（test_state_audit + 全量回归） |
| §12.3 启动自检 | Task 11（probe） |
| §14 交付物形态 | 全部任务（目录/文件与 spec 一一对应；新增 tutor_cache.py / probe.py / turnGate.ts） |

**2. 占位符扫描**：无 TBD/“类似 Task N”占位；每步含可执行代码与验证命令。`companion.ask` 路由分支与 `_handle_companion_ask` 只在 Task 8 一次落地（Task 7 不引入未接线分支），无悬空占位。

**3. 类型一致性抽查**：
- `LlmLog.record` kwargs（Task 1）↔ `NpcActor._record` / `CompanionTutor._record` / `_run_round` 的调用（Task 5/6/7）一致。
- `SentenceChunker.feed/finalize`（Task 3）↔ `npc_actor` 消费（Task 5）一致。
- `derive_candidate_word_ids(text, allowed)`（Task 3）↔ npc_actor metadata / `validate_tutor` 的 `token_contains`（Task 4/5）一致。
- `stream_reply(**kw, budget_exceeded=)` 签名（Task 5）↔ `run_round` 转发（Task 7）↔ `SlowActor` 包装（ws_helpers）一致。
- `TutorResult(word, scaffold, audio_base64, sample_rate, from_cache, degraded)`（Task 6）↔ `_handle_companion_ask` 消费（Task 8）一致。
- `SessionState` 字段（Task 7）↔ `run_round` / `_cancel_and_interrupt` / 测试读取（`app.state.sessions["sess-x"].round_task` 等）一致。
- 前端 `isAcceptedTurn(current, companion, turnId)`（Task 10）↔ `useVoiceRound` 内调用一致；`RmsGate` 位置参数构造器（Task 9）↔ 测试一致。

**偏差记录**（相对已批准 spec 的可执行化决策，均为补全/精度修正）：
1. spec §3 chunker 规则精度修正（“≥8 词才发” → “句号/叹号处切句、完整短句照发”），原因：硬性 ≥8 词门槛会让“Hello! Welcome to the bakery. Can I help you?” 这类多短句 NPC 回复合并成一块、破坏“delta 按句”语义；已同步改 spec 两处（§3、§12.1）。Task 3 即按修正后规则实现。
2. 打断发生在 LLM 生成中（commit 前）：Task 7 规定 `run_round` 捕获 `CancelledError` 时补写一条 `dialogue.turn`（部分 npcText），避免用户输入与已产出的内容丢失——与“append-only 不 UPDATE”一致（仍是插入）。
3. `playedMs` 由服务端从逐句 TTS 的 `ms` 累计（`state.played_ms`），前端不传——单一来源，避免双端口径分歧。
4. `tutor_cache_dir`、`spurious_window_s`（默认 0.5s）为计划新增的可配值，不属于 spec 的 settings 表；`spurious_window_s` 保持在 `SessionState`（非 Settings），测试可注入更小值。
5. `llm-smoke` 的 `stream_text_override`/`stream_text` 需要 `tests/fixtures/llm-golden/` 目录占位（README）——不入 CI，只做真 key 人工测量。
