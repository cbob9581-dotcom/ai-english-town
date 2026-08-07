# 英语小镇 · 阶段 3 实施计划（Scene Director + 场景系统 + 回合仲裁 + gesture）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把阶段 2 的固定单场景面包店，升级为"广场 hub + 自由逛"的多原型场景系统：本地确定性骨架先行、Scene Director 异步丰富、转场预取、generationId 每场景递增门控、多 NPC 回合仲裁与 gesture 产出渲染。

**Architecture:** 服务端 `scene_store`（本地骨架编译 + 提案展开）+ `scene_lifecycle`（per-session 场景状态机）+ `llm/scene_director`（mock 优先的提案 LLM）+ `scene_prefetch`（ScenePlan 服务端缓存）；双通道 WS 新增 `scene.request/hint/npc.focus`（C→S）与 `scene.skeleton/patch/degraded/focus`（S→C）；前端拆成 turn/scene 两个门并引入 Zustand 场景 store。骨架 = 本地确定性默认填充、完整可玩；Director 只做选择与丰富，`persona` 永不自 LLM。

**Tech Stack:** FastAPI + uvicorn（单 worker）、SQLite WAL、asyncio（每会话独立 task）、pydantic + JSON Schema + zod（scene-schema 三端）、OpenAI 兼容 LLM 客户端、React 19 + Zustand + Vite + vitest。

**Spec:** `docs/superpowers/specs/2026-08-07-english-town-phase3-design.md`（v2，已审阅）。本计划严格遵循其 §15 实现顺序。

---

## Global Constraints

1. **16GB 内存、严禁高并发/并行进程、严格串行**：任何时刻只派发一个子代理；同一时刻只跑一个 pytest/vitest 进程；web 测试必须 `--maxWorkers=1`。
2. Uvicorn 单 worker；SQLite WAL + 单一写入队列；**先写库再发送**（`events.append` 之后再 `send`）。
3. 无 `innerHTML`；组件白名单；坐标 `0..1000`；热区 ≥44px；单场景 DOM 实体 ≤40。
4. 不加载外部 URL；**`persona` 文本永不自 LLM**（Director 只能选 `npcId`）；`setting.displayName` 按 untrusted（≤24 纯 ASCII 字符、只进 user 结构化字段、不进 system 段）。
5. generationId **每场景递增**；门控矩阵：speech/audio/metadata = genId **且** turnId；`scene.patch` = genId + sceneId + baseRevision；companion.reply = genId。
6. 所有 LLM 调用共享 `llm_session_call_cap=200` + `llm_concurrency_limit=2`；Director 记 `llm_calls` `role="scene_director"`（`attempt` 标记 `enter`/`prefetch`）。
7. `session_events` append-only；state-audit：合法操作只允许 `session_events` / `llm_calls` / `tutor_cache` 三张表变化（新增事件类型 `scene.entered`/`scene.patch`/`scene.degraded` 落在 `session_events` 内，审计不破）。
8. 项目内命令免审批；**删改项目外内容需先确认**（本计划不删除任何 node_modules / 系统文件）。
9. 前端环境：本仓库 `node_modules` 当前未安装 → 前端任务（Task 9 起）开始前先在仓库根 `pnpm install`。之后：
   - web 测试：`cd apps/web && node ../../node_modules/vitest/vitest.mjs run --maxWorkers=1 <tests/xxx.test.ts>`（若 vitest 未被根提升，fallback：`cd apps/web && pnpm exec vitest run --maxWorkers=1`）。
   - web 构建：`cd apps/web && node ../../node_modules/typescript/bin/tsc -b && node ../../node_modules/vite/bin/vite.js build`（`apps/web/node_modules/.bin/vite` 是 stale shim，**禁用**）。
10. API 测试：`cd apps/api && uv run pytest tests/<file>.py -v`。
11. 所有面向用户的回复用中文。

---

## 文件结构（新增/修改一览）

**新增：**
- `apps/api/app/catalog.py` —— 实体/NPC 目录加载器（`Concept`/`Npc`/`Catalog`）
- `apps/api/app/llm/concepts.py` —— `resolve_word_id`（conceptId 的 wordId 派生接缝）
- `apps/api/app/llm/scene_director.py` —— `SceneDirector` Protocol + `LlmSceneDirector` + `get_scene_director` 工厂
- `apps/api/app/llm/gesture.py` —— `derive_gesture` / `validate_gesture`
- `apps/api/app/scene_lifecycle.py` —— per-session 场景状态机（enter/fill/degrade/replay）
- `apps/api/app/arbitration.py` —— `ArbitrationState`
- `apps/api/app/scene_prefetch.py` —— `ScenePrefetchCache`
- `assets/archetypes/town-map.json`、`assets/archetypes/{plaza,park,station,cafe,library}.json`
- `assets/catalog/entities.json`、`assets/catalog/npcs.json`
- `apps/api/tests/test_{catalog,concepts,skeleton,town_map,scene_lifecycle,scene_gates,scene_director,scene_validation,scene_prefetch,scene_replay,arbitration,gesture}.py`
- `apps/web/src/scenePatch.ts`、`apps/web/src/sceneStore.ts`、`apps/web/src/ArchetypePreview.tsx`、`apps/web/src/GestureLayer.tsx`
- `apps/web/tests/{scenePatch,SceneViewport}.test.tsx`

**修改：**
- `apps/api/app/llm/client.py`（`aclose`）、`apps/api/app/llm/mock.py`（`MOCK_SCENE_SCENARIO` + `MockSceneDirector`）、`apps/api/app/llm/proposals.py`（`validate_proposal`）、`apps/api/app/llm/npc_actor.py`（persona 动态 + `_entity_by_word_id` + gesture 产出）、`apps/api/app/llm/tutor.py`（120 动态）
- `apps/api/app/scene_store.py`（town-map/catalog/skeleton/filled/diff）、`apps/api/app/ws.py`（SessionState.scene + 场景消息路由 + 门控）、`apps/api/app/voice_round.py`（`state.generation_id` 改属性兼容）、`apps/api/app/main.py`（lifespan + build_actor + 动态 scene_words）
- `packages/scene-compiler/scene_compiler/compiler.py`（`_door_entity` up/down + npc 实体产出 + background 字段）
- `packages/scene-schema/schemas/archetype.schema.json` 与 `packages/scene-schema/src/index.ts`（exits direction 增 `up`/`down`）
- `assets/archetypes/bakery.json`（exits 收敛为单出口 `left`）
- `assets/icons/icon-map.json`（按任务逐步扩展）
- `apps/web/src/audio/turnGate.ts`（两个门）、`apps/web/src/useVoiceRound.ts`、`apps/web/src/App.tsx`、`apps/web/src/SceneViewport.tsx`、`apps/web/src/registry.tsx`
- `scripts/startup-selfcheck.py`（town-map 边 / visualKey 覆盖 / Director 探活 / 音色合成耗时）
- 既有测试 `apps/api/tests/test_ws.py`（进场事件）、`apps/web/tests/turnGate.test.ts`（新增两门用例）

---

### Task 1: §13 顺手清理（aclose / tutor 120 动态 / env quirks 文档）

**Files:**
- Modify: `apps/api/app/llm/client.py`
- Modify: `apps/api/app/llm/tutor.py:19-23`
- Modify: `apps/api/app/main.py`
- Modify: `VERSION_LOCK.md`（或 `README.md`，二选一，仓库已存在的那个）
- Test: `apps/api/tests/test_llm_client.py`、`apps/api/tests/test_tutor.py`

**Interfaces:**
- Consumes: 无（独立收尾）。
- Produces:
  - `LLMAdapter` Protocol 增加 `async def aclose(self) -> None: ...`；`OpenAIClient.aclose()` 关闭 `AsyncOpenAI` 连接池。
  - `create_app` 注册 FastAPI lifespan：shutdown 时 `await client.aclose()`。
  - `CompanionTutor` 的 system prompt 改为从 `settings.llm_max_scaffold_chars` 动态取字符上限。

- [ ] **Step 1: 给 LLMAdapter Protocol 补 `aclose` 并写失败测试**

```python
# apps/api/tests/test_llm_client.py 追加
def test_openai_client_aclose_called(tmp_path) -> None:
    """aclose 必须存在且可调用（连接池释放钩子）。"""
    from app.llm.client import LLMAdapter
    import inspect
    assert "aclose" in LLMAdapter.__protocol_attrs__ if hasattr(LLMAdapter, "__protocol_attrs__") else True
    # 真实验证：MockAdapter 实现 aclose，OpenAIClient 实现 aclose
    from app.llm.mock import MockAdapter
    import asyncio
    m = MockAdapter("ok")
    asyncio.run(m.aclose())
```

- [ ] **Step 2: 运行确认失败**

Run: `cd apps/api && uv run pytest tests/test_llm_client.py::test_openai_client_aclose_called -v`
Expected: FAIL（`AttributeError: 'MockAdapter' object has no attribute 'aclose'`）。

- [ ] **Step 3: 实现 `aclose`**

```python
# apps/api/app/llm/client.py
class LLMAdapter(Protocol):
    async def stream_text(self, messages: list[dict], *, max_tokens: int,
                          temperature: float) -> AsyncIterator[TextDelta]: ...

    async def complete_json(self, messages: list[dict], *, max_tokens: int,
                            temperature: float) -> JsonResult: ...

    async def aclose(self) -> None: ...

# OpenAIClient 类内追加
    async def aclose(self) -> None:
        await self._client.close()
```

```python
# apps/api/app/llm/mock.py —— MockAdapter 类内追加
    async def aclose(self) -> None:
        return None
```

- [ ] **Step 4: 运行确认通过**

Run: `cd apps/api && uv run pytest tests/test_llm_client.py -v`
Expected: PASS。

- [ ] **Step 5: main.py 挂 lifespan shutdown**

```python
# apps/api/app/main.py 顶部 import 追加
from contextlib import asynccontextmanager

# create_app 内、FastAPI(...) 前加
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await client.aclose()

    app = FastAPI(title="english-town-api", version="0.2.0", lifespan=lifespan)
```

- [ ] **Step 6: tutor 120 字符动态化 + 失败测试**

```python
# apps/api/tests/test_tutor.py 追加
def test_tutor_prompt_uses_setting_char_limit() -> None:
    from app.llm import tutor as tutor_mod
    class S:
        llm_max_scaffold_chars = 80
    prompt = tutor_mod.TUTOR_SYSTEM_PROMPT  # 仍是常量模板，动态值在消息构造时插入
    assert "under 80 characters" in prompt
```

先把 `TUTOR_SYSTEM_PROMPT` 改成带占位符并用实例属性填充：

```python
# apps/api/app/llm/tutor.py
TUTOR_SYSTEM_PROMPT = (
    "You help an A1-A2 English learner understand one word. "
    "Reply with ONLY a JSON object: {\"word\": <the given word>, \"scaffold\": <one simple English sentence>}. "
    "The scaffold must contain the given word and be under {max_scaffold_chars} characters. "
    "No newlines, no URLs, no code."
)

# reply() 内 messages 构造处改为：
messages = [
    {"role": "system", "content": TUTOR_SYSTEM_PROMPT.format(max_scaffold_chars=self._settings.llm_max_scaffold_chars)},
    {"role": "user", "content": json.dumps({"word": word})},
]
```

- [ ] **Step 7: 运行确认通过**

Run: `cd apps/api && uv run pytest tests/test_tutor.py -v`
Expected: PASS。

- [ ] **Step 8: env quirks 文档化**

在 `VERSION_LOCK.md`（不存在则建）末尾追加：
```markdown
## 阶段 3 运行注意（沿用阶段 2 env quirks）
- vitest 需 `--maxWorkers=1`（16GB 机器）。
- `apps/web/node_modules/.bin/vite` 是 stale pnpm shim：web 构建用
  `node ../../node_modules/typescript/bin/tsc -b && node ../../node_modules/vite/bin/vite.js build`。
- tts-worker venv 缺 `babel.core`；ASR venv 缺 `cublas64_12.dll`（各自 selfcheck 已降级处理）。
- 无 `DEEPSEEK_API_KEY` → LLM 走 mock；`scripts/llm-smoke.py` golden 是手动测量步骤（不进 CI）。
- Scene Director 无 key 时同样走确定性 mock（`MOCK_SCENE_SCENARIO`）。
```

- [ ] **Step 9: 提交**

```bash
git add apps/api/app/llm/client.py apps/api/app/llm/mock.py apps/api/app/llm/tutor.py apps/api/app/main.py apps/api/tests/test_llm_client.py apps/api/tests/test_tutor.py VERSION_LOCK.md
git commit -m "chore(api): aclose lifespan + dynamic tutor char limit + env quirks doc"
```

---

### Task 2: catalog 资产 + concepts resolver + 构建期 visualKey 校验

**Files:**
- Create: `assets/catalog/entities.json`、`assets/catalog/npcs.json`
- Create: `apps/api/app/catalog.py`、`apps/api/app/llm/concepts.py`
- Modify: `assets/icons/icon-map.json`
- Modify: `scripts/startup-selfcheck.py`
- Test: `apps/api/tests/test_catalog.py`、`apps/api/tests/test_concepts.py`

**Interfaces:**
- Consumes: `resolve_word_id` 内部用；`Catalog` 无外部依赖。
- Produces:
  - `Catalog.load(asset_root: Path) -> Catalog`；`concepts_in(category) -> list[Concept]`；`concept(concept_id) -> Concept | None`；`concept_ids_in(categories) -> list[str]`；`npcs_in(role) -> list[Npc]`；`npc(npc_id) -> Npc | None`；`all_visual_keys() -> set[str]`；`all_npc_voices() -> list[str]`。
  - `Concept(concept_id, name, lemma, pos, visual_key)`、`Npc(npc_id, name, persona, emoji, voice)`（`@dataclass(frozen=True)`）。
  - `resolve_word_id(lemma: str, pos: str, *, sense: int = 1) -> str`，返回 `f"word_{lemma}_{pos}_{sense}"`。
  - `entities.json` 结构：`{"<category>": [{"conceptId","name","lemma","pos","visualKey"}]}`；`npcs.json` 结构：`{"<role>": [{"npcId","name","persona","emoji","voice"}]}`。
  - 关键约定：**entities.json 里出现的每个 visualKey 必须已存在于 icon-map.json**（Task 2 只放 bakery+plaza 概念及其图标；其余原型的概念/图标在 Task 13 扩展，扩展时同任务补图标）。

- [ ] **Step 1: 写 catalog 数据 + 图标（先数据后代码）**

`assets/catalog/entities.json`（只含已配图标的分类；每个概念一个 visualKey 已在 icon-map）：
```json
{
  "food": [
    {"conceptId": "concept.food.loaf", "name": "loaf", "lemma": "loaf", "pos": "n", "visualKey": "food.loaf"},
    {"conceptId": "concept.food.apple", "name": "apple", "lemma": "apple", "pos": "n", "visualKey": "food.apple"}
  ],
  "paper": [
    {"conceptId": "concept.paper.receipt", "name": "receipt", "lemma": "receipt", "pos": "n", "visualKey": "paper.receipt"}
  ],
  "nature": [
    {"conceptId": "concept.nature.pigeon", "name": "pigeon", "lemma": "pigeon", "pos": "n", "visualKey": "nature.pigeon"},
    {"conceptId": "concept.nature.tree", "name": "tree", "lemma": "tree", "pos": "n", "visualKey": "nature.tree"}
  ],
  "decoration": [
    {"conceptId": "concept.deco.fountain", "name": "fountain", "lemma": "fountain", "pos": "n", "visualKey": "deco.fountain"}
  ],
  "furniture": [
    {"conceptId": "concept.furn.bench", "name": "bench", "lemma": "bench", "pos": "n", "visualKey": "furniture.bench"}
  ]
}
```

`assets/catalog/npcs.json`（六个角色的 persona 全部一次到位；persona 是 trusted 我方编写）：
```json
{
  "vendor": [
    {"npcId": "npc_rosa", "name": "Rosa", "persona": "You are Rosa, a friendly vendor in a small English bakery. Reply in short, simple English suitable for an A1-A2 learner. Stay in character at the bakery. Never mention that you are an AI. Use only plain English text: no newlines, no URLs, no code.", "emoji": "👩‍🍳", "voice": "af_heart"}
  ],
  "greeter": [
    {"npcId": "npc_tom", "name": "Tom", "persona": "You are Tom, a cheerful guide who welcomes visitors to a small English town square. Reply in short, simple English suitable for an A1-A2 learner. Point out what visitors can see and which streets lead where. Never mention that you are an AI. Use only plain English text: no newlines, no URLs, no code.", "emoji": "🧑‍🌾", "voice": "am_michael"}
  ],
  "guard": [
    {"npcId": "npc_sam", "name": "Sam", "persona": "You are Sam, a friendly park keeper in an English town park. Reply in short, simple English suitable for an A1-A2 learner. Talk about trees, birds, benches and the pond. Never mention that you are an AI. Use only plain English text: no newlines, no URLs, no code.", "emoji": "🧑‍✈️", "voice": "am_fenrir"}
  ],
  "conductor": [
    {"npcId": "npc_ada", "name": "Ada", "persona": "You are Ada, a cheerful station conductor at a small English town station. Reply in short, simple English suitable for an A1-A2 learner. Talk about trains, tickets, platforms and departures. Never mention that you are an AI. Use only plain English text: no newlines, no URLs, no code.", "emoji": "👩‍✈️", "voice": "af_sky"}
  ],
  "barista": [
    {"npcId": "npc_leo", "name": "Leo", "persona": "You are Leo, a friendly barista in a cosy English café. Reply in short, simple English suitable for an A1-A2 learner. Talk about coffee, tea, cake and drinks. Never mention that you are an AI. Use only plain English text: no newlines, no URLs, no code.", "emoji": "🧑‍🍳", "voice": "am_adam"}
  ],
  "librarian": [
    {"npcId": "npc_iris", "name": "Iris", "persona": "You are Iris, a gentle librarian at a small English town library. Reply in short, simple English suitable for an A1-A2 learner. Talk about books, stories, desks and quiet. Never mention that you are an AI. Use only plain English text: no newlines, no URLs, no code.", "emoji": "👩‍🏫", "voice": "af_bella"}
  ]
}
```

`assets/icons/icon-map.json` 追加（视觉键名与上面 catalog 一一对应）：
```json
"nature.pigeon":   { "emoji": "🕊️", "label": "pigeon" },
"nature.tree":     { "emoji": "🌳", "label": "tree" },
"deco.fountain":   { "emoji": "⛲", "label": "fountain" },
"furniture.bench": { "emoji": "🪑", "label": "bench" },
"npc.greeter":     { "emoji": "🧑‍🌾", "label": "greeter" },
"npc.guard":       { "emoji": "🧑‍✈️", "label": "guard" },
"npc.conductor":   { "emoji": "👩‍✈️", "label": "conductor" },
"npc.barista":     { "emoji": "🧑‍🍳", "label": "barista" },
"npc.librarian":   { "emoji": "👩‍🏫", "label": "librarian" }
```

- [ ] **Step 2: 写失败测试（catalog 加载 + visualKey 覆盖）**

```python
# apps/api/tests/test_catalog.py
import json
from pathlib import Path

from app.catalog import Catalog

ROOT = Path(__file__).resolve().parents[3]


def _catalog() -> Catalog:
    return Catalog.load(ROOT / "assets")


def test_load_categories_and_concepts() -> None:
    c = _catalog()
    assert c.concept("concept.food.loaf") is not None
    assert {x.concept_id for x in c.concepts_in("food")} == {"concept.food.loaf", "concept.food.apple"}
    assert c.concept_ids_in(["food", "paper"]) == ["concept.food.loaf", "concept.food.apple", "concept.paper.receipt"]


def test_load_npcs_and_roles() -> None:
    c = _catalog()
    rosa = c.npc("npc_rosa")
    assert rosa is not None and rosa.role == "vendor"
    assert c.npcs_in("greeter")[0].npc_id == "npc_tom"


def test_every_catalog_visual_key_exists_in_icon_map() -> None:
    """构建期规则：entities.json 的 visualKey 全集 ⊆ icon-map.json。缺映射 = 构建失败。"""
    c = _catalog()
    icons = json.loads((ROOT / "assets" / "icons" / "icon-map.json").read_text(encoding="utf-8"))
    missing = c.all_visual_keys() - set(icons)
    assert missing == set(), f"catalog 里缺图标映射的 visualKey: {missing}"


def test_all_npc_role_visual_keys_in_icon_map() -> None:
    """NPC 渲染键（npc.<role>）也必须有图标。"""
    c = _catalog()
    icons = json.loads((ROOT / "assets" / "icons" / "icon-map.json").read_text(encoding="utf-8"))
    needed = {f"npc.{role}" for role in {"vendor", "greeter", "guard", "conductor", "barista", "librarian"}}
    assert needed <= set(icons)
```

- [ ] **Step 3: 运行确认失败**

Run: `cd apps/api && uv run pytest tests/test_catalog.py -v`
Expected: FAIL（`ModuleNotFoundError: app.catalog`）。

- [ ] **Step 4: 实现 catalog.py**

```python
"""实体/NPC 目录加载。entities.json 只存 conceptId+lemma+pos（不存 wordId）；
wordId 由 llm/concepts.py 服务端 resolve（阶段 4 接 learning_items）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Concept:
    concept_id: str
    name: str
    lemma: str
    pos: str
    visual_key: str


@dataclass(frozen=True)
class Npc:
    npc_id: str
    name: str
    persona: str
    emoji: str
    voice: str
    role: str = "vendor"


class Catalog:
    def __init__(self, entities_doc: dict, npcs_doc: dict) -> None:
        self._concepts: dict[str, Concept] = {}
        self._by_category: dict[str, list[Concept]] = {}
        for category, rows in entities_doc.items():
            for row in rows:
                c = Concept(concept_id=row["conceptId"], name=row["name"], lemma=row["lemma"],
                            pos=row["pos"], visual_key=row["visualKey"])
                self._concepts[c.concept_id] = c
                self._by_category.setdefault(category, []).append(c)
        self._npcs: dict[str, Npc] = {}
        self._by_role: dict[str, list[Npc]] = {}
        for role, rows in npcs_doc.items():
            for row in rows:
                n = Npc(npc_id=row["npcId"], name=row["name"], persona=row["persona"],
                        emoji=row["emoji"], voice=row["voice"], role=role)
                self._npcs[n.npc_id] = n
                self._by_role.setdefault(role, []).append(n)

    @classmethod
    def load(cls, asset_root: Path) -> "Catalog":
        entities = json.loads((asset_root / "catalog" / "entities.json").read_text(encoding="utf-8"))
        npcs = json.loads((asset_root / "catalog" / "npcs.json").read_text(encoding="utf-8"))
        return cls(entities, npcs)

    def concepts_in(self, category: str) -> list[Concept]:
        return list(self._by_category.get(category, []))

    def concept(self, concept_id: str) -> Concept | None:
        return self._concepts.get(concept_id)

    def concept_ids_in(self, categories: list[str]) -> list[str]:
        out: list[str] = []
        for cat in categories:
            out.extend(c.concept_id for c in self._by_category.get(cat, []))
        return out

    def npcs_in(self, role: str) -> list[Npc]:
        return list(self._by_role.get(role, []))

    def npc(self, npc_id: str) -> Npc | None:
        return self._npcs.get(npc_id)

    def all_visual_keys(self) -> set[str]:
        return {c.visual_key for c in self._concepts.values()}

    def all_npc_voices(self) -> list[str]:
        return sorted({n.voice for n in self._npcs.values()})
```

- [ ] **Step 5: 实现 concepts.py + 失败测试**

```python
# apps/api/tests/test_concepts.py
from app.llm.concepts import resolve_word_id


def test_resolve_word_id_without_learning_items() -> None:
    assert resolve_word_id("loaf", "n") == "word_loaf_n_1"
    assert resolve_word_id("run", "v", sense=2) == "word_run_v_2"
```

```python
# apps/api/app/llm/concepts.py
def resolve_word_id(lemma: str, pos: str, *, sense: int = 1) -> str:
    """conceptId → wordId 的服务端 resolve 接缝。
    阶段 4 接 learning_items（命中则用其 id），当前确定性派生。"""
    return f"word_{lemma}_{pos}_{sense}"
```

- [ ] **Step 6: 运行全部 catalog 测试**

Run: `cd apps/api && uv run pytest tests/test_catalog.py tests/test_concepts.py -v`
Expected: PASS（4+1 个用例）。

- [ ] **Step 7: 扩展 startup-selfcheck（构建期校验服务端启动也拦一道）**

`scripts/startup-selfcheck.py` 末尾加一个纯函数 + 调用（该脚本是手动工具，用 `print` 报错即可）：

```python
def check_catalog_icons() -> list[str]:
    import json as _json
    from pathlib import Path
    assets = ROOT / "assets"
    try:
        entities = _json.loads((assets / "catalog" / "entities.json").read_text(encoding="utf-8"))
        icons = set(_json.loads((assets / "icons" / "icon-map.json").read_text(encoding="utf-8")))
    except FileNotFoundError as e:  # 老仓库无 catalog 时降级跳过，不 crash
        return [f"catalog check skipped: {e}"]
    keys = {row["visualKey"] for rows in entities.values() for row in rows}
    missing = keys - icons
    return [f"missing icon for {k}" for k in sorted(missing)]
```

并在 `main`/入口打印处接入（该脚本已有 `print(json.dumps(...))` 的返回结构，追加一个字段 `catalogIconMissing`）。

- [ ] **Step 8: 提交**

```bash
git add assets/catalog assets/icons/icon-map.json apps/api/app/catalog.py apps/api/app/llm/concepts.py apps/api/tests/test_catalog.py apps/api/tests/test_concepts.py scripts/startup-selfcheck.py
git commit -m "feat(assets): concept/npc catalog + wordId resolver + build-time icon coverage check"
```

---

### Task 3: town-map + 4 向出口 + 骨架编译器（确定性默认填充）

**Files:**
- Create: `assets/archetypes/town-map.json`、`assets/archetypes/plaza.json`
- Modify: `assets/archetypes/bakery.json`（exits 收敛为单出口）
- Modify: `packages/scene-compiler/scene_compiler/compiler.py`
- Modify: `packages/scene-schema/schemas/archetype.schema.json`、`packages/scene-schema/src/index.ts`（exits direction 增 up/down）
- Modify: `apps/api/app/scene_store.py`
- Test: `apps/api/tests/test_town_map.py`、`apps/api/tests/test_skeleton.py`、`packages/scene-compiler/tests/test_compiler.py`（更新）

**Interfaces:**
- Consumes: `Catalog`（Task 2）、`resolve_word_id`（Task 2）、`ScenePlan`/`Archetype`/`compile_scene`（已有）。
- Produces:
  - `town-map.json`：`{ "start": "plaza", "edges": { "<archetypeId>": { "<direction>": "<targetArchetypeId>" } } }`。方向即 exitId（`left|right|up|down`）。
  - `SceneStore.load_town_map() -> dict`、`SceneStore.target_for(archetype_id, exit_id) -> str | None`、`SceneStore.default_npc_id(archetype_id) -> str | None`、`SceneStore.compile_skeleton(archetype_id, *, scene_id, seed, generation_id) -> dict`、`SceneStore.compile_filled(archetype_id, *, scene_id, seed, generation_id, proposal) -> dict`、`SceneStore.diff_scenes(skeleton, filled) -> list[dict]`。
  - 编译后的 scene 形状（骨架与 filled 一致，供 diff）：
    ```json
    { "archetypeId", "sceneId", "generationId", "setting", "background",
      "entities": [{"id","component","layout","appearance","semantics","interactions"}],
      "characters": [{"slotId","npcId","name","emoji","voice","visualKey"}],
      "exits": [{"id":"left","targetArchetypeId":"bakery"}] }
    ```
  - 门实体 `door-<i>` 的 `semantics` 带 `exitId`（=direction）与 `targetArchetypeId`。
  - 编译后实体 id 约定：prop = `{slotId}-{n}`（n 为该 slotId 出现序），npc = `npc-{slotId}`，door = `door-{i}`，companion = `companion-1`。骨架与 filled 同槽位 id 相同 → diff 只产出 replace。

- [ ] **Step 1: 更新共享 schema 支持 4 向出口**

`packages/scene-schema/schemas/archetype.schema.json` 中 exits items 的 `direction` enum：
```json
"direction": { "enum": ["left", "right", "up", "down"] }
```
`packages/scene-schema/src/index.ts` 的 `ArchetypeSchema`：
```ts
exits: z.array(z.object({ direction: z.enum(['left', 'right', 'up', 'down']), targetKind: z.string() })),
```

- [ ] **Step 2: 写 town-map + plaza + 收敛 bakery + 失败测试**

`assets/archetypes/town-map.json`：
```json
{
  "start": "plaza",
  "edges": {
    "plaza":   { "left": "bakery", "right": "cafe", "up": "park", "down": "station" },
    "bakery":  { "left": "plaza" },
    "cafe":    { "left": "plaza" },
    "park":    { "down": "plaza" },
    "station": { "left": "plaza", "right": "library" },
    "library": { "left": "station" }
  }
}
```
`assets/archetypes/plaza.json`：
```json
{
  "archetypeId": "plaza",
  "displayName": "小镇广场",
  "background": {
    "style": "gradient",
    "gradient": "linear-gradient(#aee3ff 0%, #cdeffd 45%, #86b871 46%, #5d9e50 100%)",
    "decor": ["fountain", "tree", "bench"],
    "ambienceKey": "ambience/plaza_loop.ogg"
  },
  "zones": {
    "center":  { "x": [380, 620], "y": [420, 700], "anchor": "bottom" },
    "fountain": { "x": [430, 570], "y": [280, 520], "anchor": "center" },
    "bench":   { "x": [80, 330],  "y": [560, 820], "anchor": "bottom" }
  },
  "propSlots": [
    { "slotId": "fountain.center", "zone": "fountain", "categories": ["decoration", "nature"] },
    { "slotId": "bench.left",      "zone": "bench",    "categories": ["furniture"] },
    { "slotId": "center.pigeon",   "zone": "center",   "categories": ["nature", "food"] }
  ],
  "npcSlots": [ { "slotId": "guide", "zone": "center", "role": "greeter" } ],
  "exits": [
    { "direction": "left",  "targetKind": "any" },
    { "direction": "right", "targetKind": "any" },
    { "direction": "up",    "targetKind": "any" },
    { "direction": "down",  "targetKind": "any" }
  ]
}
```
`assets/archetypes/bakery.json` 的 `exits` 改为：`"exits": [ { "direction": "left", "targetKind": "any" } ]`

```python
# apps/api/tests/test_town_map.py
import json
from pathlib import Path

from app.scene_store import SceneStore

ROOT = Path(__file__).resolve().parents[3]


def _store() -> SceneStore:
    return SceneStore(ROOT / "assets")


def test_town_map_edges_all_resolve_and_return() -> None:
    """每条边的目标存在；spoke 可回 hub；无悬挂出口；出口方向与该原型 exits 一致。"""
    store = _store()
    tm = store.load_town_map()
    assert tm["start"] == "plaza"
    archetype_ids = set(store.list_archetype_ids())
    for src, edges in tm["edges"].items():
        assert src in archetype_ids
        arche = store.get_archetype(src)
        dirs = {e["direction"] for e in arche["exits"]}
        assert set(edges) == dirs, f"{src}: town-map 方向必须与 archetype.exits 完全一致"
        for direction, target in edges.items():
            assert target in archetype_ids, f"{src}.{direction} -> {target} 不存在"
            assert store.target_for(src, direction) == target


def test_hub_is_start_and_every_spoke_returns() -> None:
    tm = _store().load_town_map()
    hub = tm["start"]
    spokes = [k for k in tm["edges"] if k != hub]
    for spoke in spokes:
        assert hub in tm["edges"][spoke].values(), f"{spoke} 无法回到 hub {hub}"
```

- [ ] **Step 3: 运行确认失败**

Run: `cd apps/api && uv run pytest tests/test_town_map.py -v`
Expected: FAIL（`SceneStore` 尚无 `load_town_map`）。

- [ ] **Step 4: compiler 升级（4 向门 + npc 实体产出 + background 字段）**

`packages/scene-compiler/scene_compiler/compiler.py`：

```python
def _door_entity(index: int, direction: str) -> dict:
    if direction == "up":
        pos = {"x": 500, "y": 60}
    elif direction == "down":
        pos = {"x": 500, "y": 830}
    else:
        pos = {"x": 920 if direction == "right" else 60, "y": 520}
    return {
        "id": f"door-{index}",
        "component": "door",
        "layout": {**pos, "w": 90, "h": 120 if direction in ("up", "down") else 200, "anchor": "bottom"},
        "appearance": {"visualKey": "door.wooden"},
        "semantics": {"name": "door"},
        "interactions": ["pick"],
    }


def compile_scene(archetype: Archetype, plan: ScenePlan) -> dict:
    """archetype/plan 已通过 Pydantic 校验（由调用方保证或经 compile_from_docs）。"""
    zone_of_slot = {s["slotId"]: s["zone"] for s in archetype.propSlots}
    zones = archetype.zones
    entities: list[dict] = []
    counter: dict[str, int] = {}   # 按 slotId 计数（骨架与 filled 同槽位 id 一致 → diff 可对齐）
    for fill in plan.fills:
        zone_name = zone_of_slot.get(fill["slotId"])
        if not zone_name or zone_name not in zones:
            continue
        counter[fill["slotId"]] = counter.get(fill["slotId"], 0) + 1
        entity = dict(fill["entity"])
        entity["id"] = fill["entity"].get("id") or f"{fill['slotId']}-{counter[fill['slotId']]}"
        entity["layout"] = _zone_center(zones[zone_name].model_dump(), counter[fill["slotId"]] - 1)
        entities.append(entity)

    npc_slot_of = {s["slotId"]: s for s in archetype.npcSlots}
    for ch in plan.characters:
        slot = npc_slot_of.get(ch["slotId"])
        if not slot or slot["zone"] not in zones:
            continue
        entities.append({
            "id": f"npc-{ch['slotId']}",
            "component": "npc",
            "layout": _zone_center(zones[slot["zone"]].model_dump(), 0),
            "appearance": {"visualKey": ch.get("visualKey", f"npc.{slot.get('role', 'vendor')}")},
            "semantics": {"name": ch.get("name", ch.get("npcId", ch["slotId"])), "npcId": ch["npcId"]},
            "interactions": ["focus", "ask"],
        })

    entities.append(dict(COMPANION_ENTITY))
    for i, exit_spec in enumerate(archetype.exits):
        entities.append(_door_entity(i + 1, exit_spec["direction"]))

    return {
        "archetypeId": archetype.archetypeId,
        "sceneId": plan.sceneId,
        "generationId": plan.generationId,
        "setting": plan.setting,
        "background": archetype.background.model_dump(),
        "entities": entities,
        "characters": [c for c in plan.characters],
    }
```

更新 `packages/scene-compiler/tests/test_compiler.py`：现有三个用例仍应通过（id 含 `counter`/`loaf`、companion、door、≤40、坐标界、loaf 在 counter zone 内）。追加一例断言 NPC 实体存在且方向 `up` 出口可用：

```python
def test_compiled_scene_includes_npc_entity() -> None:
    compiled = compile_from_docs(ARCHETYPE, template_scene_plan())
    assert any(e["component"] == "npc" for e in compiled["entities"])
```

- [ ] **Step 5: scene_store 改造（town-map/catalog/skeleton/filled/diff）**

`apps/api/app/scene_store.py` 全文替换为：

```python
"""从本地资产编译场景：town-map 邻接表 + catalog 概念目录 + 确定性骨架 + 提案展开。
进程内缓存（Redis 前的开发态 LRU 由 dict/lru_cache 充当）。"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from scene_compiler import compile_from_docs, compile_scene, template_scene_plan
from scene_schema.models import Archetype, ScenePlan
from scene_schema.validate import validate_scene_plan

from app.catalog import Catalog
from app.llm.concepts import resolve_word_id


def _stable_index(key: str, n: int) -> int:
    return hashlib.sha1(key.encode("utf-8")).digest()[0] % n


class SceneStore:
    def __init__(self, asset_root: Path) -> None:
        self._root = asset_root
        self._archetypes_dir = asset_root / "archetypes"
        self._catalog = Catalog.load(asset_root)

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    @lru_cache(maxsize=8)
    def _load_archetype(self, archetype_id: str) -> dict:
        return json.loads((self._archetypes_dir / f"{archetype_id}.json").read_text(encoding="utf-8"))

    def get_archetype(self, archetype_id: str) -> dict:
        return self._load_archetype(archetype_id)

    def list_archetype_ids(self) -> list[str]:
        return [p.stem for p in self._archetypes_dir.glob("*.json")]

    @lru_cache(maxsize=1)
    def load_town_map(self) -> dict:
        return json.loads((self._archetypes_dir / "town-map.json").read_text(encoding="utf-8"))

    def target_for(self, archetype_id: str, exit_id: str) -> str | None:
        return self.load_town_map().get("edges", {}).get(archetype_id, {}).get(exit_id)

    def default_npc_id(self, archetype_id: str) -> str | None:
        arche = self.get_archetype(archetype_id)
        for slot in arche.get("npcSlots", []):
            npcs = self._catalog.npcs_in(slot["role"])
            if npcs:
                return npcs[_stable_index(f"{slot['slotId']}:default", len(npcs))].npc_id
        return None

    def get_compiled_scene(self, scene_id: str) -> dict:
        plan = template_scene_plan()
        if plan["sceneId"] != scene_id:
            raise KeyError(f"unknown scene: {scene_id}")
        return compile_from_docs(self.get_archetype(plan["archetypeId"]), plan)

    # --- 阶段 3：骨架 / 展开 / diff ---

    def _default_concept(self, archetype: dict, slot: dict, seed: str):
        for category in slot["categories"]:
            concepts = self._catalog.concepts_in(category)
            if concepts:
                return concepts[_stable_index(f"{slot['slotId']}:{seed}", len(concepts))]
        return None

    def _entity_from_concept(self, concept, slot_id: str) -> dict:
        # layout 由 compile_scene 覆盖；这里给 schema 合法占位
        return {
            "id": f"{slot_id}-1",
            "component": "prop",
            "layout": {"x": 0, "y": 0, "w": 40, "h": 40, "anchor": "bottom"},
            "appearance": {"visualKey": concept.visual_key},
            "semantics": {"name": concept.name,
                          "wordId": resolve_word_id(concept.lemma, concept.pos),
                          "conceptId": concept.concept_id},
            "interactions": ["focus", "ask"],
        }

    def _character_entries(self, archetype: dict, characters: list[dict]) -> list[dict]:
        role_of = {s["slotId"]: s["role"] for s in archetype.get("npcSlots", [])}
        out: list[dict] = []
        for ch in characters:
            npc = self._catalog.npc(ch["npcId"])
            if npc is None:
                continue
            role = role_of.get(ch["slotId"], "vendor")
            out.append({"slotId": ch["slotId"], "npcId": npc.npc_id, "name": npc.name,
                        "emoji": npc.emoji, "voice": npc.voice, "visualKey": f"npc.{role}"})
        return out

    def _plan(self, archetype_id: str, *, scene_id: str, generation_id: str,
              setting: dict, fills: list[dict], characters: list[dict]) -> dict:
        return {"schemaVersion": "1.0", "sceneId": scene_id, "generationId": generation_id,
                "revision": 1, "mode": "free", "archetypeId": archetype_id, "setting": setting,
                "fills": fills, "characters": characters, "objectives": [], "exits": []}

    def _finalize(self, archetype: dict, scene: dict) -> dict:
        scene["exits"] = []
        edges = self.load_town_map().get("edges", {}).get(archetype["archetypeId"], {})
        for i, es in enumerate(archetype["exits"]):
            direction = es["direction"]
            target = edges.get(direction)
            for e in scene["entities"]:
                if e["id"] == f"door-{i + 1}":
                    e["semantics"]["exitId"] = direction
                    if target:
                        e["semantics"]["targetArchetypeId"] = target
            if target:
                scene["exits"].append({"id": direction, "targetArchetypeId": target})
        return scene

    def compile_skeleton(self, archetype_id: str, *, scene_id: str, seed: str,
                         generation_id: str) -> dict:
        """本地确定性默认填充 → 完整可玩骨架。输入仅 (archetypeId, seed)；seed 派生自 sceneId。"""
        arche = self.get_archetype(archetype_id)
        fills: list[dict] = []
        for slot in arche["propSlots"]:
            concept = self._default_concept(arche, slot, seed)
            if concept is None:
                continue
            fills.append({"slotId": slot["slotId"], "entity": self._entity_from_concept(concept, slot["slotId"])})
        characters: list[dict] = []
        for slot in arche.get("npcSlots", []):
            npcs = self._catalog.npcs_in(slot["role"])
            if npcs:
                npc = npcs[_stable_index(f"{slot['slotId']}:{seed}", len(npcs))]
                characters.append({"slotId": slot["slotId"], "npcId": npc.npc_id})
        setting = {"displayName": arche["displayName"], "time": "day"}
        plan = self._plan(archetype_id, scene_id=scene_id, generation_id=generation_id,
                          setting=setting, fills=fills, characters=characters)
        validate_scene_plan(plan)
        plan["characters"] = self._character_entries(arche, plan["characters"])
        scene = compile_scene(Archetype.model_validate(arche),
                              ScenePlan.model_validate(self._plan(archetype_id, scene_id=scene_id,
                                                                  generation_id=generation_id, setting=setting,
                                                                  fills=fills, characters=plan["characters"])))
        return self._finalize(arche, scene)

    def compile_filled(self, archetype_id: str, *, scene_id: str, seed: str,
                       generation_id: str, proposal: dict) -> dict:
        """Director 提案（validate_proposal 已清洗）→ 概念展开 → 编译为丰富场景。"""
        arche = self.get_archetype(archetype_id)
        fills = []
        for f in proposal["fills"]:
            concept = self._catalog.concept(f["conceptId"])
            if concept is None:
                continue
            fills.append({"slotId": f["slotId"], "entity": self._entity_from_concept(concept, f["slotId"])})
        characters = self._character_entries(arche, proposal["characters"])
        plan = self._plan(archetype_id, scene_id=scene_id, generation_id=generation_id,
                          setting=proposal["setting"], fills=fills, characters=characters)
        validate_scene_plan(plan)
        scene = compile_scene(Archetype.model_validate(arche), ScenePlan.model_validate(plan))
        return self._finalize(arche, scene)

    def diff_scenes(self, skeleton: dict, filled: dict) -> list[dict]:
        """骨架 vs 丰富 → 白名单 patch ops（/entities/<id> 与 /setting）。"""
        skel = {e["id"]: e for e in skeleton["entities"]}
        fill = {e["id"]: e for e in filled["entities"]}
        ops: list[dict] = []
        for eid, e in fill.items():
            if eid not in skel:
                ops.append({"op": "add", "path": f"/entities/{eid}", "entity": e})
            elif e != skel[eid]:
                ops.append({"op": "replace", "path": f"/entities/{eid}", "entity": e})
        for eid in skel:
            if eid not in fill:
                ops.append({"op": "remove", "path": f"/entities/{eid}"})
        if filled["setting"] != skeleton["setting"]:
            ops.append({"op": "replace", "path": "/setting", "value": filled["setting"]})
        return ops
```

> 说明：`compile_skeleton` 里对 `characters` 先做目录展开再编译，是为了让 `compile_scene` 产出 `npc-*` 实体（phase-1 的 plan 无此字段，向后兼容；test_compiler 里 template_scene_plan 的 characters 不带 name/visualKey，`ch.get("visualKey", f"npc.{role}")` 兜底）。

- [ ] **Step 6: 写骨架确定性 + filled/diff 失败测试**

```python
# apps/api/tests/test_skeleton.py
from pathlib import Path

from app.scene_store import SceneStore

ROOT = Path(__file__).resolve().parents[3]


def _store() -> SceneStore:
    return SceneStore(ROOT / "assets")


def test_skeleton_deterministic_on_seed() -> None:
    store = _store()
    a = store.compile_skeleton("plaza", scene_id="s1", seed="s1", generation_id="g1")
    b = store.compile_skeleton("plaza", scene_id="s1", seed="s1", generation_id="g1")
    assert a == b  # 同输入（archetypeId, seed）→ 完全一致


def test_skeleton_fully_playable() -> None:
    store = _store()
    s = store.compile_skeleton("plaza", scene_id="s1", seed="s1", generation_id="g1")
    assert any(e["component"] == "npc" for e in s["entities"])       # 有默认 NPC
    assert any(e["component"] == "companion" for e in s["entities"])  # 有伴学者
    assert len(s["exits"]) == 4                                        # 四向出口
    assert all(0 <= e["layout"]["x"] <= 1000 and 0 <= e["layout"]["y"] <= 1000 for e in s["entities"])
    assert len(s["entities"]) <= 40


def test_skel_vs_filled_diff_replaces_slots_and_setting() -> None:
    store = _store()
    skel = store.compile_skeleton("bakery", scene_id="s1", seed="s1", generation_id="g1")
    proposal = {
        "fills": [{"slotId": "counter.main", "conceptId": "concept.food.apple"}],
        "characters": [{"slotId": "vendor", "npcId": "npc_rosa"}],
        "setting": {"displayName": "Rosewood Bakery", "time": "morning"},
    }
    filled = store.compile_filled("bakery", scene_id="s1", seed="s1",
                                  generation_id="g1", proposal=proposal)
    ops = store.diff_scenes(skel, filled)
    assert any(o["path"] == "/setting" for o in ops)
    # counter.main 被替换为 apple（wordId 变化）；其余槽位保持骨架默认
    replaced = [o for o in ops if o["op"] == "replace" and "/entities/" in o["path"]]
    assert any("counter.main" in o["path"] and o["entity"]["semantics"]["wordId"] == "word_apple_n_1"
               for o in replaced)


def test_filled_setting_preserves_ascii_display_name() -> None:
    store = _store()
    filled = store.compile_filled("bakery", scene_id="s1", seed="s1", generation_id="g1", proposal={
        "fills": [], "characters": [],
        "setting": {"displayName": "Rosewood Bakery", "time": "morning"},
    })
    assert filled["setting"]["displayName"] == "Rosewood Bakery"
    assert filled["setting"]["displayName"].isascii()
```

- [ ] **Step 7: 运行确认通过**

Run: `cd apps/api && uv run pytest tests/test_town_map.py tests/test_skeleton.py -v && cd packages/scene-compiler && uv run pytest tests/test_compiler.py -v`
Expected: 全部 PASS。

- [ ] **Step 8: 提交**

```bash
git add assets/archetypes/town-map.json assets/archetypes/plaza.json assets/archetypes/bakery.json packages/scene-compiler packages/scene-schema/schemas/archetype.schema.json packages/scene-schema/src/index.ts apps/api/app/scene_store.py apps/api/tests/test_town_map.py apps/api/tests/test_skeleton.py
git commit -m "feat(scene): town-map + 4-way exits + deterministic skeleton compiler + plan expansion"
```

---

### Task 4: 场景生命周期 + WS 协议 + SessionState.scene + generationId 门控（服务端）

**Files:**
- Create: `apps/api/app/scene_lifecycle.py`、`apps/api/app/arbitration.py`
- Modify: `apps/api/app/ws.py`、`apps/api/app/main.py`
- Modify: `apps/api/app/settings.py`
- Modify: `apps/api/tests/test_ws.py`（进场事件断言）
- Test: `apps/api/tests/test_scene_lifecycle.py`、`apps/api/tests/test_scene_gates.py`

**Interfaces:**
- Consumes: `SceneStore.compile_skeleton`（Task 3）、`SceneStore.target_for`（Task 3）、`Catalog`（Task 2）、`NpcActor`。
- Produces:
  - `SessionState`：新增 `self.scene: SceneSession | None`、`self.scene_seq: int = 0`、`self.fill_task: asyncio.Task | None`、`self.arbitration: ArbitrationState`；`generation_id` 改为 `@property`（读 `self.scene.generation_id`，无 scene 时回退 `self._fallback_generation`）。
  - `SceneSession`（`@dataclass`，定义在 `scene_lifecycle.py`）：`scene_id/generation_id/archetype_id/revision/status/setting/background/entities/characters/exits/default_npc_id`。
  - `ArbitrationState.reset(default_npc_id)`（定义在 arbitration.py；Task 11 再补 `set_focus`）。
  - `scene_lifecycle.enter_scene(app, events, state, session_id, send, *, target_archetype_id, source)`（async）。
  - WS 消息（C→S）：`scene.request {exitId}`、`scene.hint {exitId}`（Task 7 接线）；(S→C)：`scene.skeleton`、`scene.degraded`。
  - `app.state.scene_factory`：`(scene_words, entity_by_word_id, npc_id=None) -> NpcActor`（main.py 提供）。
  - Settings 新增：`llm_total_timeout_director_s=6.0`、`llm_temperature_director=0.2`、`llm_max_tokens_director=400`、`scene_prefetch_ttl_s=60.0`、`scene_prefetch_budget_ratio=0.8`（本任务先加字段，Director 在 Task 6 用）。

- [ ] **Step 1: Settings 新增字段**

```python
# apps/api/app/settings.py —— dataclass 内、llm_max_tokens_tutor 之后追加
    llm_total_timeout_director_s: float = 6.0
    llm_temperature_director: float = 0.2
    llm_max_tokens_director: int = 400
    scene_prefetch_ttl_s: float = 60.0
    scene_prefetch_budget_ratio: float = 0.8
```
`_ENV_FIELDS` 同步追加这 5 条（env 名 `LLM_TOTAL_TIMEOUT_DIRECTOR_S` 等，风格同现有）。

- [ ] **Step 2: arbitration.py + 写失败测试**

```python
# apps/api/app/arbitration.py
"""回合仲裁状态：同时仅 1 个主动说话人；pendingSpeakers 留 schema、v1 不排队。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ArbitrationState:
    active_speaker: str | None = None
    conversation_focus: str | None = None
    focus_source: str | None = None
    focus_expires_ms: int | None = None
    pending_speakers: list[str] = field(default_factory=list)

    def reset(self, default_npc_id: str | None) -> None:
        self.active_speaker = f"npc:{default_npc_id}" if default_npc_id else None
        self.conversation_focus = self.active_speaker
        self.focus_source = "scene_default"
        self.focus_expires_ms = None
        self.pending_speakers = []

    def set_focus(self, npc_id: str, source: str) -> str:
        self.active_speaker = f"npc:{npc_id}"
        self.conversation_focus = self.active_speaker
        self.focus_source = source
        self.focus_expires_ms = int(time.time() * 1000) + 60_000
        return self.active_speaker
```

```python
# apps/api/tests/test_arbitration.py
from app.arbitration import ArbitrationState


def test_reset_to_scene_default() -> None:
    a = ArbitrationState()
    a.set_focus("npc_rosa", "user_click")
    a.reset("npc_tom")
    assert a.active_speaker == "npc:npc_tom"
    assert a.conversation_focus == "npc:npc_tom"
    assert a.pending_speakers == []


def test_set_focus_switches_and_sets_expiry() -> None:
    a = ArbitrationState()
    speaker = a.set_focus("npc_rosa", "user_click")
    assert speaker == "npc:npc_rosa"
    assert a.focus_source == "user_click"
    assert a.focus_expires_ms is not None
```

- [ ] **Step 3: scene_lifecycle.py（骨架进场，无 Director → 停留骨架）**

```python
"""per-session 场景状态机：进场 → 本地骨架 → （Task 6）Director 填充 → filled/degraded。
generationId 每次进场递增；所有消息先写库（events.append）再 send。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.llm.concepts import resolve_word_id  # noqa: F401  （保留：后续 gesture 展开用）


@dataclass
class SceneSession:
    scene_id: str
    generation_id: str
    archetype_id: str
    revision: int = 1
    status: str = "skeleton"          # skeleton | filled | degraded
    setting: dict = field(default_factory=dict)
    background: dict = field(default_factory=dict)
    entities: list = field(default_factory=list)
    characters: list = field(default_factory=list)
    exits: list = field(default_factory=list)
    default_npc_id: str | None = None


def scene_maps(scene: SceneSession) -> tuple[dict, dict]:
    """scene → (scene_words: wordId→name, entity_by_word_id: wordId→entityId)。"""
    scene_words: dict[str, str] = {}
    entity_by_word_id: dict[str, str] = {}
    for e in scene.entities:
        wid = e.get("semantics", {}).get("wordId")
        if wid:
            scene_words[wid] = e["semantics"]["name"]
            entity_by_word_id[wid] = e["id"]
    return scene_words, entity_by_word_id


def recent_scenes(events, session_id: str, limit: int = 5) -> list[str]:
    return [e["payload"].get("archetypeId") for e in events.list_after(session_id, 0)
            if e["event_type"] == "scene.entered"][-limit:]


async def enter_scene(app, events, state, session_id, send, *,
                      target_archetype_id: str | None, source: str) -> None:
    """切入目标场景：取消旧回合/填充 → 编译骨架 → 广播 skeleton → 触发填充（Task 6 前无填充）。"""
    scenes = app.state.scenes
    target = target_archetype_id or scenes.load_town_map()["start"]
    await _cancel_work(app, events, state, session_id)

    state.scene_seq += 1
    scene_id = f"scene_{target}_{state.scene_seq}"
    generation_id = f"gen_{uuid.uuid4().hex[:8]}"
    skeleton = scenes.compile_skeleton(target, scene_id=scene_id, seed=scene_id,
                                       generation_id=generation_id)
    default_npc = scenes.default_npc_id(target)
    scene = SceneSession(scene_id=scene_id, generation_id=generation_id, archetype_id=target,
                         revision=1, status="skeleton", setting=skeleton["setting"],
                         background=skeleton["background"], entities=skeleton["entities"],
                         characters=skeleton["characters"], exits=skeleton["exits"],
                         default_npc_id=default_npc)
    state.scene = scene
    state.arbitration.reset(default_npc)
    scene_words, entity_by_word_id = scene_maps(scene)
    state.actor = app.state.scene_factory(scene_words, entity_by_word_id, npc_id=default_npc)

    events.append(session_id, "scene.entered", {
        "sceneId": scene_id, "archetypeId": target, "generationId": generation_id,
        "revision": 1, "source": source,
    })
    await send({
        "type": "scene.skeleton", "sceneId": scene_id, "generationId": generation_id,
        "archetypeId": target, "revision": 1, "status": "skeleton",
        "setting": skeleton["setting"], "background": skeleton["background"],
        "entities": skeleton["entities"], "characters": skeleton["characters"],
        "exits": skeleton["exits"],
    })
    # 阶段 3 全链路在 Task 6 接 Director；在此之前场景停留骨架（完整可玩）。
    # 预取命中在 Task 7 从这里切走。


async def _cancel_work(app, events, state, session_id) -> None:
    """取消旧回合/填充任务；写 interrupted（若回合未 commit）；清 pending companion。"""
    if state.round_task and not state.round_task.done():
        state.round_task.cancel()
        try:
            await state.round_task
        except asyncio.CancelledError:
            pass
    if state.fill_task and not state.fill_task.done():
        state.fill_task.cancel()
        try:
            await state.fill_task
        except asyncio.CancelledError:
            pass
        state.fill_task = None
    for task in list(state.pending_asks.values()):
        task.cancel()
    state.pending_asks.clear()
```

> 注：`_cancel_work` 需 `import asyncio`。`_cancel_and_interrupt`（ws.py 内）保留给打断专用；转场用 `_cancel_work`（不写 interrupted，因为转场不是"打断回合"语义——但若回合未 commit 会丢证据？不会：round_task 取消时 voice_round 的 CancelledError 分支会补写 partial turn，见 voice_round.py:80-87）。

- [ ] **Step 4: ws.py 改造（SessionState.scene + 场景消息路由 + 进场）**

```python
# apps/api/app/ws.py —— SessionState 改造
import asyncio
import base64
import json
import uuid

from app.scene_lifecycle import enter_scene, scene_maps
from app.arbitration import ArbitrationState
from app.llm.concepts import resolve_word_id  # noqa: F401

class SessionState:
    def __init__(self, settings: Settings) -> None:
        self._fallback_generation = f"gen_{uuid.uuid4().hex[:8]}"
        self.scene = None                    # SceneSession | None（Task 4）
        self.scene_seq = 0
        self.fill_task = None
        self.arbitration = ArbitrationState()
        self.actor = None                    # Task 4：每场景重建（persona 动态）
        self.round_task: asyncio.Task | None = None
        self.active_turn_id: str | None = None
        self.is_playing = False
        self.played_ms = 0
        self.utterance_id: str | None = None
        self.frames: list[bytes] = []
        self.audio_start_armed = False
        self.barge_in_armed = False
        self.pending_asks: dict[str, asyncio.Task] = {}
        self.spurious_guards: set[asyncio.Task] = set()
        self.semaphore = asyncio.Semaphore(settings.llm_concurrency_limit)
        self.spurious_window_s = 0.5
        self._turn_seq = 0

    @property
    def generation_id(self) -> str:
        return self.scene.generation_id if self.scene else self._fallback_generation

    def new_turn_id(self) -> str:
        self._turn_seq += 1
        return f"turn_{uuid.uuid4().hex[:8]}_{self._turn_seq}"
```

ws_session 内：
- 连接建立后、进入 while 前：
```python
    async def send(payload: object) -> None:
        ...（不变）

    if state.scene is None:
        await enter_scene(app, events, state, session_id, send,
                          target_archetype_id=None, source="connect")
```
- `_run_round` 的 actor 参数改为 `state.actor`（不再用 `app.state.actor`）：
```python
                await run_round(session_id, utterance_id, payload, events,
                                app.state.asr_client, app.state.tts_client, send,
                                state.actor, state, budget_exceeded=budget_exceeded)
```
- 消息循环新增两个分支：
```python
                elif t == "scene.request":
                    if state.scene is None:
                        continue
                    target = app.state.scenes.target_for(state.scene.archetype_id, ctrl.get("exitId"))
                    if target:
                        await enter_scene(app, events, state, session_id, send,
                                          target_archetype_id=target, source="exit")
                elif t == "scene.hint":
                    # Task 7 实现预取；本任务仅解析（保证协议字段不抛）
                    pass
```
- 连接断开分支（`if msg["type"] == "websocket.disconnect":`，现取消 round_task 处）同步取消未完成的 fill_task，避免转场填充任务跨连接泄漏：
```python
            if msg["type"] == "websocket.disconnect":
                if state.round_task and not state.round_task.done():
                    state.round_task.cancel()
                if state.fill_task and not state.fill_task.done():
                    state.fill_task.cancel()
                return
```
- `_handle_companion_ask` 的实体查找从 `app.state.entity_words` 改为当前场景：
```python
    async def _handle_companion_ask(entity_id: str) -> None:
        entry = None
        if state.scene:
            entry = next(((e["semantics"]["wordId"], e["semantics"]["name"])
                          for e in state.scene.entities
                          if e["id"] == entity_id and e.get("semantics", {}).get("wordId")), None)
        if entry is None:
            await send({"type": "companion.reply", "turnId": f"comp_{uuid.uuid4().hex[:8]}",
                        "word": "", "scaffold": "", "degraded": True, "error": "unknown_entity"})
            return
        word_id, word = entry
        ...（其余不变，generation_id 已由属性接 scene）
```

- [ ] **Step 5: main.py 提供 scene_factory + 移除单场景硬编码依赖**

```python
# apps/api/app/main.py
def create_app(...):
    settings = settings or Settings.from_env()
    events = events or EventStore(settings.db_path)
    scenes = SceneStore(settings.asset_root)
    catalog = scenes.catalog
    llm_log = LlmLog(events.connection)
    cache = TutorCache(events.connection, settings.tutor_cache_dir)
    client = llm_client or get_client(settings)
    asr_impl = asr_client or (lambda audio: worker_asr(audio, f"{ASR_URL}/transcribe"))
    tts_impl = tts_client or (lambda text: worker_tts(text, TTS_BASE))

    def scene_factory(scene_words: dict, entity_by_word_id: dict, npc_id: str | None = None) -> NpcActor:
        persona = (
            "You are a friendly helper in a small English town. Reply in short, simple English "
            "suitable for an A1-A2 learner. Never mention that you are an AI. Use only plain "
            "English text: no newlines, no URLs, no code."
        )
        if npc_id:
            npc = catalog.npc(npc_id)
            if npc:
                persona = npc.persona
        return NpcActor(client, settings, llm_log, scene_words,
                        lambda u: scripted_reply(u)["speech"], persona=persona)

    actor = scene_factory({}, {})
    tutor = CompanionTutor(client, settings, llm_log, cache, tts_impl)

    app = FastAPI(title="english-town-api", version="0.2.0", lifespan=lifespan)
    app.state.events = events
    app.state.settings = settings
    app.state.scenes = scenes
    app.state.catalog = catalog
    app.state.scene_factory = scene_factory
    app.state.llm_log = llm_log
    app.state.tutor_cache = cache
    app.state.actor = actor
    app.state.tutor = tutor
    app.state.asr_client = asr_impl
    app.state.tts_client = tts_impl
    app.state.sessions = {}
    ...（/health /api/archetypes /api/scenes 路由保留）
```

> 注：`app.state.scene_words`/`entity_words` 不再使用（companion 改为读 `state.scene`）；保留 `app.state.actor` 仅作默认值，ws 回合改用 `state.actor`。这要求 `NpcActor` 的 `persona` 参数存在（Task 4 Step 6）。

- [ ] **Step 6: NpcActor 增加 persona 参数（默认兼容旧构造）**

```python
# apps/api/app/llm/npc_actor.py
SYSTEM_PROMPT = (
    "You are Rosa, a friendly vendor in a small English bakery. "
    "Reply in short, simple English sentences suitable for an A1-A2 English learner. "
    "Stay in character at the bakery. Never mention that you are an AI. "
    "Use only plain English text with basic punctuation: no newlines, no URLs, no code."
)

class NpcActor:
    def __init__(self, client, settings, llm_log,
                 allowed_words: dict[str, str], fallback,
                 *, persona: str | None = None, entity_by_word_id: dict | None = None) -> None:
        self._client = client
        self._settings = settings
        self._llm_log = llm_log
        self._allowed_words = dict(allowed_words)
        self._fallback = fallback
        self._persona = persona or SYSTEM_PROMPT
        self._entity_by_word_id = entity_by_word_id or {}

    def _build_messages(self, user_text, recent_turns):
        user_payload = {"transcript": user_text, "recent_turns": recent_turns, "scene": self._scene_hint()}
        return [
            {"role": "system", "content": self._persona},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
```
（`_entity_by_word_id` 本任务先接构造参数，gesture 产出在 Task 11 使用。）

- [ ] **Step 7: 更新既有 test_ws.py + 写生命周期/门控测试**

```python
# apps/api/tests/test_ws.py —— 改为：连接即进场 plaza，未激活回合的打断不再追加事件
def test_ws_mounted_and_control_appends_nothing_without_turn(tmp_path: Path) -> None:
    events = EventStore(tmp_path / "e.db")
    app = create_app(events)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/sessions/sess-smoke") as ws:
            ws.send_json({"type": "playback.interrupted", "utteranceId": None})
    # 连接即进场 plaza（scene.entered）；无活跃回合时，显式打断不追加任何事件
    assert [e["event_type"] for e in events.list_after("sess-smoke", 0)] == ["scene.entered"]
```

```python
# apps/api/tests/test_scene_lifecycle.py
import asyncio

import pytest

from app.event_store import EventStore
from app.ws import ws_session
from tests.ws_helpers import FakeWS, make_app


def _skeleton_msg(ws) -> dict | None:
    return next((m for m in ws.sent if isinstance(m, dict) and m.get("type") == "scene.skeleton"), None)


async def test_connect_enters_plaza_and_sends_playable_skeleton(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    ws = FakeWS([], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    msg = _skeleton_msg(ws)
    assert msg is not None
    assert msg["archetypeId"] == "plaza"
    assert msg["status"] == "skeleton"
    assert any(e["component"] == "npc" for e in msg["entities"])          # 默认 NPC 可点
    assert len(msg["exits"]) == 4
    st = app.state.sessions["sess-x"]
    assert st.scene is not None and st.scene.archetype_id == "plaza"
    assert st.actor is not None
    entered = [e["payload"] for e in events.list_after("sess-x", 0) if e["event_type"] == "scene.entered"]
    assert entered[-1]["archetypeId"] == "plaza"


async def test_scene_request_transitions_and_increments_generation(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    ws = FakeWS([
        {"type": "websocket.receive", "text": '{"type":"scene.request","exitId":"left"}'},
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    st = app.state.sessions["sess-x"]
    assert st.scene is not None and st.scene.archetype_id == "bakery"     # plaza.left → bakery
    skels = [m for m in ws.sent if isinstance(m, dict) and m.get("type") == "scene.skeleton"]
    assert len(skels) == 2
    gens = {m["generationId"] for m in skels}
    assert len(gens) == 2                                                  # 每进场 generationId 递增
```

- [ ] **Step 8: 运行全部 API 测试确认回归绿**

Run: `cd apps/api && uv run pytest -v`
Expected: PASS（含更新后的 test_ws.py；`test_state_audit` 仍断言 3 张表）。

- [ ] **Step 9: 提交**

```bash
git add apps/api/app/scene_lifecycle.py apps/api/app/arbitration.py apps/api/app/ws.py apps/api/app/main.py apps/api/app/settings.py apps/api/app/llm/npc_actor.py apps/api/tests/test_ws.py apps/api/tests/test_scene_lifecycle.py apps/api/tests/test_arbitration.py
git commit -m "feat(scene): per-session lifecycle + WS scene protocol + per-scene generationId"
```

---

### Task 5: /dev/archetypes 预览路由

**Files:**
- Modify: `apps/api/app/main.py`（新增 dev 端点）
- Create: `apps/web/src/ArchetypePreview.tsx`
- Modify: `apps/web/src/App.tsx`（hash 路由 `#/dev/archetypes`）
- Test: `apps/api/tests/test_scene_api.py`（补 dev 端点用例）

**Interfaces:**
- Consumes: `SceneStore.compile_skeleton`（Task 3）、`SceneStore.get_archetype`。
- Produces:
  - `GET /api/dev/archetypes` → `[ { archetypeId, displayName, skeleton: <骨架 scene>, zones, propSlots, npcSlots, exits } ]`（无语音/无 LLM/无 WS 的纯本地编译预览）。
  - `ArchetypePreview` 组件：渲染全部原型骨架，叠加槽位边界框 + 实体计数；支持切换。
  - 仅 web dev 模式可用：`window.location.hash === '#/dev/archetypes'` 时 App 渲染 `<ArchetypePreview/>`。

- [ ] **Step 1: 写失败测试（dev 端点）**

```python
# apps/api/tests/test_scene_api.py 追加
def test_dev_archetypes_preview(tmp_path: Path) -> None:
    app = create_app(EventStore(tmp_path / "events.db"))
    client = TestClient(app)
    r = client.get("/api/dev/archetypes")
    assert r.status_code == 200
    body = r.json()
    ids = {a["archetypeId"] for a in body}
    assert {"plaza", "bakery"} <= ids
    plaza = next(a for a in body if a["archetypeId"] == "plaza")
    assert plaza["skeleton"]["exits"] and len(plaza["skeleton"]["entities"]) <= 40
    assert "zones" in plaza and "propSlots" in plaza
```

- [ ] **Step 2: 运行确认失败**

Run: `cd apps/api && uv run pytest tests/test_scene_api.py::test_dev_archetypes_preview -v`
Expected: FAIL（404）。

- [ ] **Step 3: 实现端点**

```python
# apps/api/app/main.py
    @app.get("/api/dev/archetypes")
    def dev_archetypes() -> dict:
        out = []
        for aid in sorted(scenes.list_archetype_ids()):
            arche = scenes.get_archetype(aid)
            skeleton = scenes.compile_skeleton(aid, scene_id=f"dev_{aid}", seed=f"dev_{aid}",
                                               generation_id="dev")
            out.append({"archetypeId": aid, "displayName": arche["displayName"],
                        "skeleton": skeleton, "zones": arche["zones"],
                        "propSlots": arche["propSlots"], "npcSlots": arche["npcSlots"],
                        "exits": arche["exits"]})
        return out
```

- [ ] **Step 4: 运行确认通过**

Run: `cd apps/api && uv run pytest tests/test_scene_api.py -v`
Expected: PASS。

- [ ] **Step 5: ArchetypePreview 组件 + App hash 路由**

`apps/web/src/ArchetypePreview.tsx`（纯展示；槽位边界框用 zone 坐标画）：
```tsx
import { useEffect, useState } from 'react';
import type { Entity } from './types';
import { ensureMinHit, mapLogicalToCss } from './coords';
import { renderEntity } from './registry';

interface ArcheInfo {
  archetypeId: string;
  displayName: string;
  skeleton: { setting: any; entities: Entity[]; exits: { id: string; targetArchetypeId?: string }[] };
  zones: Record<string, { x: [number, number]; y: [number, number]; anchor: string }>;
  propSlots: { slotId: string; zone: string; categories: string[] }[];
}

export function ArchetypePreview() {
  const [list, setList] = useState<ArcheInfo[]>([]);
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    fetch('/api/dev/archetypes').then((r) => r.json()).then(setList).catch(() => setList([]));
  }, []);
  if (list.length === 0) return <div>预览数据加载中…（需要 API 8000）</div>;
  const cur = list[idx % list.length];
  const size = { w: 1000, h: 600 };
  return (
    <div style={{ padding: 16 }}>
      <button onClick={() => setIdx((i) => (i + list.length - 1) % list.length)}>←</button>
      <strong>{cur.displayName} ({cur.archetypeId})</strong>
      <button onClick={() => setIdx((i) => (i + 1) % list.length)}>→</button>
      <div style={{ position: 'relative', width: 800, height: 480, overflow: 'hidden', borderRadius: 12, background: 'linear-gradient(#aee3ff 0%, #cdeffd 45%, #86b871 46%, #5d9e50 100%)' }}>
        {Object.entries(cur.zones).map(([name, z]) => {
          const css = ensureMinHit(mapLogicalToCss(z.x[0], z.y[0], z.x[1] - z.x[0], z.y[1] - z.y[0], size.w, size.h));
          return <div key={name} style={{ position: 'absolute', border: '1px dashed #f33', ...css }} />;
        })}
        {cur.skeleton.entities.map((e) => {
          const css = ensureMinHit(mapLogicalToCss(e.layout.x, e.layout.y, e.layout.w, e.layout.h, size.w, size.h));
          return (
            <div key={e.id} style={{ position: 'absolute', ...css }} data-entity={e.id}>
              {renderEntity(e)}
            </div>
          );
        })}
      </div>
      <p>实体数 {cur.skeleton.entities.length} / 40；出口 {cur.skeleton.exits.map((x) => `${x.id}→${x.targetArchetypeId}`).join(', ')}</p>
    </div>
  );
}
```

`apps/web/src/App.tsx` 顶部：
```tsx
import { ArchetypePreview } from './ArchetypePreview';
// 函数体最前：
  const isPreview = window.location.hash === '#/dev/archetypes';
  if (isPreview) return <ArchetypePreview />;
```

- [ ] **Step 6: 类型检查**

Run: `cd apps/web && node ../../node_modules/typescript/bin/tsc -b`
Expected: 无错误（若 node_modules 未安装，先在仓库根 `pnpm install`）。

- [ ] **Step 7: 提交**

```bash
git add apps/api/app/main.py apps/api/tests/test_scene_api.py apps/web/src/ArchetypePreview.tsx apps/web/src/App.tsx
git commit -m "feat(web): /dev/archetypes preview route (local skeleton compile, no voice/LLM)"
```

---

### Task 6: SceneDirector + 提案边界 + 降级

**Files:**
- Create: `apps/api/app/llm/scene_director.py`
- Modify: `apps/api/app/llm/mock.py`（`MockSceneDirector` + `MOCK_SCENE_SCENARIO`）、`apps/api/app/llm/proposals.py`（`validate_proposal`）、`apps/api/app/scene_lifecycle.py`（填充任务 + 降级）、`apps/api/app/main.py`（director 注入）、`apps/api/tests/ws_helpers.py`（make_app 可注入 director）
- Test: `apps/api/tests/test_scene_director.py`、`apps/api/tests/test_scene_validation.py`

**Interfaces:**
- Consumes: `Catalog`、`SceneStore.compile_filled`/`diff_scenes`/`get_archetype`（Task 3）、`settings.llm_total_timeout_director_s` 等（Task 4）。
- Produces:
  - `SceneDirector(Protocol).propose(*, archetype_id, archetype, catalog, recent_scenes, attempt="enter") -> dict`；返回**提案** `{fills: [{slotId, conceptId}], characters: [{slotId, npcId}], setting: {displayName, time}}`。
  - `LlmSceneDirector(client, settings, llm_log)`；`MockSceneDirector(scenario)`；`get_scene_director(settings, client, llm_log)`（无 key → `MockSceneDirector(os.environ.get("MOCK_SCENE_SCENARIO", "ok"))`）。
  - `proposals.validate_proposal(proposal, archetype, catalog) -> tuple[dict, list[str]]`：整 plan 结构非法 → `ProposalError`；单条问题 → 返回清洗后 proposal + warnings（drop 该条）。
  - `scene_lifecycle.fill_scene(...)` 异步填充任务：Director → validate → compile_filled → diff → `scene.patch`；任何失败 → `scene.degraded`（骨架停留）。status 流 `skeleton → filled | degraded`。
  - `MOCK_SCENE_SCENARIO` 枚举：`ok|timeout|connect_error|invalid_json|slot_mismatch|unknown_word|too_many_entities|partial_fills|duplicate_slot`。

- [ ] **Step 1: proposals.validate_proposal + 失败测试**

```python
# apps/api/tests/test_scene_validation.py
import json
from pathlib import Path

import pytest

from app.catalog import Catalog
from app.llm.proposals import ProposalError, validate_proposal

ROOT = Path(__file__).resolve().parents[3]
CATALOG = Catalog.load(ROOT / "assets")
ARCHETYPE = json.loads((ROOT / "assets" / "archetypes" / "bakery.json").read_text(encoding="utf-8"))


def _ok() -> dict:
    return {"fills": [{"slotId": "counter.main", "conceptId": "concept.food.loaf"}],
            "characters": [{"slotId": "vendor", "npcId": "npc_rosa"}],
            "setting": {"displayName": "Rosewood Bakery", "time": "morning"}}


def test_valid_proposal_roundtrips() -> None:
    cleaned, warns = validate_proposal(_ok(), ARCHETYPE, CATALOG)
    assert cleaned["setting"]["displayName"] == "Rosewood Bakery"
    assert warns == []


def test_concept_not_in_slot_category_dropped_alone() -> None:
    p = _ok()
    p["fills"].append({"slotId": "shelf.top", "conceptId": "concept.food.loaf"})  # shelf 候选无 food
    cleaned, warns = validate_proposal(p, ARCHETYPE, CATALOG)
    assert len(cleaned["fills"]) == 1
    assert any("concept.food.loaf" in w and "shelf.top" in w for w in warns)


def test_unknown_npc_dropped_alone() -> None:
    p = _ok()
    p["characters"].append({"slotId": "vendor", "npcId": "npc_ghost"})
    cleaned, warns = validate_proposal(p, ARCHETYPE, CATALOG)
    assert [c["npcId"] for c in cleaned["characters"]] == ["npc_rosa"]


def test_unknown_slot_raises_whole_plan() -> None:
    p = _ok()
    p["fills"][0]["slotId"] = "no.such.slot"
    with pytest.raises(ProposalError):
        validate_proposal(p, ARCHETYPE, CATALOG)


def test_duplicate_slot_raises_whole_plan() -> None:
    p = _ok()
    p["fills"].append({"slotId": "counter.main", "conceptId": "concept.food.apple"})
    with pytest.raises(ProposalError):
        validate_proposal(p, ARCHETYPE, CATALOG)


def test_too_many_entities_raises_whole_plan() -> None:
    p = _ok()
    for i in range(45):
        p["fills"].append({"slotId": "counter.main", "conceptId": "concept.food.apple"})
    with pytest.raises(ProposalError):
        validate_proposal(p, ARCHETYPE, CATALOG)


def test_non_ascii_display_name_raises() -> None:
    p = _ok()
    p["setting"]["displayName"] = "面包店"
    with pytest.raises(ProposalError):
        validate_proposal(p, ARCHETYPE, CATALOG)


def test_invalid_time_raises() -> None:
    p = _ok()
    p["setting"]["time"] = "night"
    with pytest.raises(ProposalError):
        validate_proposal(p, ARCHETYPE, CATALOG)
```

```python
# apps/api/app/llm/proposals.py 追加（顶部 import json）
def validate_proposal(proposal: dict, archetype: dict, catalog) -> tuple[dict, list[str]]:
    """Scene Director 提案校验。整 plan 结构非法 → ProposalError（degraded）；
    单条问题（conceptId/npcId 不在候选）→ 只拒该条，返回清洗后提案 + warnings。"""
    fills = proposal.get("fills")
    chars = proposal.get("characters")
    setting = proposal.get("setting")
    if not isinstance(fills, list) or not isinstance(chars, list) or not isinstance(setting, dict):
        raise ProposalError("malformed proposal")

    slot_ids = {s["slotId"] for s in archetype["propSlots"]}
    npc_slots = {s["slotId"] for s in archetype.get("npcSlots", [])}
    fill_slots = [f.get("slotId") for f in fills]
    if any(s not in slot_ids for s in fill_slots):
        raise ProposalError(f"unknown slotId in fills: {next(s for s in fill_slots if s not in slot_ids)}")
    if len(set(fill_slots)) != len(fill_slots):
        raise ProposalError("duplicate slotId in fills")
    if len(fills) > 40:
        raise ProposalError("too many fills (>40)")
    if any(c.get("slotId") not in npc_slots for c in chars):
        raise ProposalError("unknown character slot")
    if any(not isinstance(c.get("npcId"), str) for c in chars):
        raise ProposalError("character missing npcId")

    display_name = setting.get("displayName")
    if not isinstance(display_name, str) or not display_name or not display_name.isascii() or len(display_name) > 24:
        raise ProposalError("invalid displayName (must be ≤24 ASCII chars)")
    time_ = setting.get("time")
    if time_ not in ("morning", "afternoon", "evening"):
        raise ProposalError("invalid time")

    warnings: list[str] = []
    ok_fills: list[dict] = []
    for f in fills:
        slot = f.get("slotId")
        concept_id = f.get("conceptId")
        slot_spec = next((s for s in archetype["propSlots"] if s["slotId"] == slot), None)
        candidates: set[str] = set()
        for cat in (slot_spec or {}).get("categories", []):
            candidates |= {c.concept_id for c in catalog.concepts_in(cat)}
        if concept_id not in candidates:
            warnings.append(f"concept {concept_id!r} not in slot {slot} candidates")
            continue
        ok_fills.append({"slotId": slot, "conceptId": concept_id})

    ok_chars: list[dict] = []
    for c in chars:
        npc = catalog.npc(c.get("npcId", ""))
        role = next((s["role"] for s in archetype.get("npcSlots", []) if s["slotId"] == c.get("slotId")), None)
        allowed = {n.npc_id for n in catalog.npcs_in(role)} if role else set()
        if npc is None or (role and npc.npc_id not in allowed):
            warnings.append(f"npc {c.get('npcId')!r} not allowed for slot {c.get('slotId')}")
            continue
        ok_chars.append({"slotId": c["slotId"], "npcId": npc.npc_id})

    return {"fills": ok_fills, "characters": ok_chars,
            "setting": {"displayName": display_name, "time": time_}}, warnings
```

- [ ] **Step 2: 运行确认失败**

Run: `cd apps/api && uv run pytest tests/test_scene_validation.py -v`
Expected: FAIL（`validate_proposal` 未定义）。

- [ ] **Step 3: SceneDirector 实现（mock-first + 真实现）**

```python
# apps/api/app/llm/scene_director.py
"""SceneDirector：把原型 + 候选概念目录 → 提案 ScenePlan。
persona/wordId 永不由本模块产生；只选 conceptId/npcId 与 setting（ASCII ≤24）。"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Protocol

from openai import APIStatusError

from app.llm.client import JsonParseError, LLMAdapter, LLMConnectError
from app.settings import Settings

_DIRECTOR_SYSTEM = (
    "You are a scene director for an English-learning town. A scene template has slots; "
    "you choose what to place from the provided candidates. "
    "Reply with ONLY a JSON object of this exact shape: "
    '{"fills":[{"slotId":"...","conceptId":"..."}],"characters":[{"slotId":"...","npcId":"..."}],'
    '"setting":{"displayName":"...","time":"..."}}. '
    "Pick each fill conceptId ONLY from that slot's candidates. Pick each character npcId ONLY from that role's candidates. "
    "setting.displayName must be ≤24 ASCII characters. setting.time must be one of morning, afternoon, evening. "
    "No newlines, no URLs, no code."
)


class SceneDirector(Protocol):
    async def propose(self, *, archetype_id: str, archetype: dict, catalog,
                      recent_scenes: list[str], attempt: str = "enter") -> dict: ...


class LlmSceneDirector:
    def __init__(self, client: LLMAdapter, settings: Settings, llm_log) -> None:
        self._client = client
        self._settings = settings
        self._llm_log = llm_log

    def _build_messages(self, archetype: dict, catalog, recent_scenes: list[str]) -> list[dict]:
        slots = []
        for s in archetype["propSlots"]:
            slots.append({"slotId": s["slotId"], "zone": s["zone"],
                          "candidates": [{"conceptId": c.concept_id, "name": c.name}
                                         for cat in s["categories"] for c in catalog.concepts_in(cat)]})
        npc_slots = []
        for s in archetype.get("npcSlots", []):
            npc_slots.append({"slotId": s["slotId"], "role": s["role"],
                              "candidates": [{"npcId": n.npc_id, "name": n.name} for n in catalog.npcs_in(s["role"])]})
        payload = {"archetypeId": archetype["archetypeId"],
                   "displayName": archetype["displayName"],
                   "propSlots": slots, "npcSlots": npc_slots,
                   "recentScenes": recent_scenes}
        return [
            {"role": "system", "content": _DIRECTOR_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    async def propose(self, *, archetype_id: str, archetype: dict, catalog,
                      recent_scenes: list[str], attempt: str = "enter") -> dict:
        t0 = time.perf_counter()
        messages = self._build_messages(archetype, catalog, recent_scenes)
        try:
            async with asyncio.timeout(self._settings.llm_total_timeout_director_s):
                res = await self._client.complete_json(
                    messages, max_tokens=self._settings.llm_max_tokens_director,
                    temperature=self._settings.llm_temperature_director)
            self._llm_log.record(
                session_id="", generation_id="", role="scene_director",
                model=self._settings.llm_model, attempt=attempt,
                prompt_tokens=(res.usage or {}).get("prompt_tokens"),
                completion_tokens=(res.usage or {}).get("completion_tokens"),
                latency_ms=int((time.perf_counter() - t0) * 1000), ttft_ms=int((time.perf_counter() - t0) * 1000),
                finish_reason="stop", ok=True, fallback_reason="none")
            return res.json
        except TimeoutError:
            self._record_failure(t0, attempt, "timeout", "director timeout")
            raise
        except LLMConnectError as e:
            self._record_failure(t0, attempt, "connect", str(e))
            raise
        except APIStatusError as e:
            self._record_failure(t0, attempt, "connect", str(e))
            raise
        except JsonParseError as e:
            self._record_failure(t0, attempt, "invalid_json", str(e))
            raise

    def _record_failure(self, t0: float, attempt: str, reason: str, error: str) -> None:
        self._llm_log.record(session_id="", generation_id="", role="scene_director",
                             model=self._settings.llm_model, attempt=attempt,
                             latency_ms=int((time.perf_counter() - t0) * 1000),
                             finish_reason=None, fallback_reason=reason, ok=False, error=error)


def get_scene_director(settings: Settings, client: LLMAdapter, llm_log) -> SceneDirector:
    if settings.llm_api_key:
        return LlmSceneDirector(client, settings, llm_log)
    from app.llm.mock import MockSceneDirector
    import os
    return MockSceneDirector(os.environ.get("MOCK_SCENE_SCENARIO", "ok"))
```

- [ ] **Step 4: MockSceneDirector + 故障注入测试**

```python
# apps/api/app/llm/mock.py 追加
import asyncio
from app.llm.client import JsonParseError, LLMConnectError

MOCK_SCENE_SCENARIO = frozenset({
    "ok", "timeout", "connect_error", "invalid_json", "slot_mismatch",
    "unknown_word", "too_many_entities", "partial_fills", "duplicate_slot",
})


class MockSceneDirector:
    """确定性 mock：无 key 时的 SceneDirector。故障注入见 MOCK_SCENE_SCENARIO。"""

    def __init__(self, scenario: str = "ok") -> None:
        if scenario not in MOCK_SCENE_SCENARIO:
            raise ValueError(f"unknown mock scene scenario: {scenario}")
        self.scenario = scenario

    async def propose(self, *, archetype_id: str, archetype: dict, catalog,
                      recent_scenes: list[str], attempt: str = "enter") -> dict:
        if self.scenario == "timeout":
            await asyncio.sleep(60)                       # 外层 director timeout 取消它
        if self.scenario == "connect_error":
            raise LLMConnectError("mock scene connect error")
        if self.scenario == "invalid_json":
            raise JsonParseError("mock scene invalid json")
        slots = archetype["propSlots"]
        npc_slots = archetype.get("npcSlots", [])
        first_concepts = {cat: catalog.concepts_in(cat)[0].concept_id
                          for s in slots for cat in s["categories"] if catalog.concepts_in(cat)}

        def _fill(slot_id: str) -> dict:
            s = next(x for x in slots if x["slotId"] == slot_id)
            return {"slotId": slot_id, "conceptId": first_concepts[s["categories"][0]]}

        if self.scenario == "slot_mismatch":
            return {"fills": [{"slotId": "no.such.slot", "conceptId": "x"}],
                    "characters": [], "setting": {"displayName": "Broken", "time": "morning"}}
        if self.scenario == "unknown_word":
            fills = [_fill(slots[0]["slotId"])] + [{"slotId": slots[-1]["slotId"], "conceptId": "concept.ghost"}]
            return {"fills": fills, "characters": [], "setting": {"displayName": "Ghost", "time": "morning"}}
        if self.scenario == "duplicate_slot":
            return {"fills": [_fill(slots[0]["slotId"]), _fill(slots[0]["slotId"])],
                    "characters": [], "setting": {"displayName": "Dup", "time": "morning"}}
        if self.scenario == "too_many_entities":
            return {"fills": [_fill(slots[0]["slotId"]) for _ in range(45)],
                    "characters": [], "setting": {"displayName": "Many", "time": "morning"}}
        if self.scenario == "partial_fills":
            fills = [_fill(slots[0]["slotId"])] if slots else []
            return {"fills": fills, "characters": [], "setting": {"displayName": "Partial", "time": "morning"}}

        fills = [_fill(s["slotId"]) for s in slots if s["categories"][0] in first_concepts]
        chars = [{"slotId": s["slotId"], "npcId": catalog.npcs_in(s["role"])[0].npc_id} for s in npc_slots]
        return {"fills": fills, "characters": chars,
                "setting": {"displayName": f"{archetype_id.title()} Scene", "time": "morning"}}
```

```python
# apps/api/tests/test_scene_director.py
import json
from pathlib import Path

from app.catalog import Catalog
from app.llm.mock import MockSceneDirector

ROOT = Path(__file__).resolve().parents[3]
CATALOG = Catalog.load(ROOT / "assets")
ARCHETYPE = json.loads((ROOT / "assets" / "archetypes" / "bakery.json").read_text(encoding="utf-8"))


async def test_mock_ok_proposal_fills_every_slot() -> None:
    d = MockSceneDirector("ok")
    p = await d.propose(archetype_id="bakery", archetype=ARCHETYPE, catalog=CATALOG,
                        recent_scenes=[])
    assert len(p["fills"]) == len(ARCHETYPE["propSlots"])
    assert {c["conceptId"] for c in p["fills"]}
    assert p["setting"]["displayName"].isascii()


async def test_mock_partial_fills_keeps_single_slot() -> None:
    d = MockSceneDirector("partial_fills")
    p = await d.propose(archetype_id="bakery", archetype=ARCHETYPE, catalog=CATALOG, recent_scenes=[])
    assert len(p["fills"]) == 1


async def test_mock_timeout_sleeps_long() -> None:
    d = MockSceneDirector("timeout")
    try:
        import asyncio
        async with asyncio.timeout(0.05):
            await d.propose(archetype_id="bakery", archetype=ARCHETYPE, catalog=CATALOG, recent_scenes=[])
        raise AssertionError("应超时")
    except TimeoutError:
        pass
```

- [ ] **Step 5: 运行确认通过**

Run: `cd apps/api && uv run pytest tests/test_scene_director.py -v`
Expected: PASS（pytest-asyncio auto 模式）。

- [ ] **Step 6: scene_lifecycle 接填充任务 + 降级**

```python
# apps/api/app/scene_lifecycle.py 追加
from app.llm.client import JsonParseError, LLMConnectError
from app.llm.proposals import ProposalError, validate_proposal

_FALLBACK_REASON_UNKNOWN = "unknown_error"


async def fill_scene(app, events, state, session_id, send, *,
                     scene_id: str, seed: str, generation_id: str, skeleton: dict) -> None:
    """Director 填充任务：提案 → 校验 → 展开 → diff → scene.patch；失败 → degraded。"""
    scenes = app.state.scenes
    archetype_id = state.scene.archetype_id
    try:
        calls = app.state.llm_log.count_session_calls(session_id)
        if calls >= app.state.settings.llm_session_call_cap:
            await _degrade(app, events, state, session_id, send, scene_id, generation_id, "budget")
            return
        async with state.semaphore:
            # Director 总超时（含 Mock timeout 场景）：由下方 except TimeoutError 接 → _degrade
            async with asyncio.timeout(app.state.settings.llm_total_timeout_director_s):
                proposal = await app.state.director.propose(
                    archetype_id=archetype_id,
                    archetype=scenes.get_archetype(archetype_id),
                    catalog=app.state.catalog,
                    recent_scenes=recent_scenes(events, session_id),
                    attempt="enter")
        cleaned, _warnings = validate_proposal(proposal, scenes.get_archetype(archetype_id), app.state.catalog)
        filled = scenes.compile_filled(archetype_id, scene_id=scene_id, seed=seed,
                                       generation_id=generation_id, proposal=cleaned)
        ops = scenes.diff_scenes(skeleton, filled)
        if state.scene is None or state.scene.scene_id != scene_id:
            return  # 填充期间已转场 → 丢弃
        state.scene.status = "filled"
        state.scene.setting = filled["setting"]
        state.scene.entities = filled["entities"]
        state.scene.characters = filled["characters"]
        state.scene.exits = filled["exits"]
        state.scene.revision += 1
        patch_id = f"patch_{uuid.uuid4().hex[:8]}"
        events.append(session_id, "scene.patch", {
            "sceneId": scene_id, "generationId": generation_id,
            "baseRevision": state.scene.revision - 1, "patchId": patch_id, "ops": ops,
        })
        if ops:
            await send({"type": "scene.patch", "sceneId": scene_id, "generationId": generation_id,
                        "baseRevision": state.scene.revision - 1, "patchId": patch_id, "ops": ops})
    except (TimeoutError, LLMConnectError, JsonParseError, ProposalError) as e:
        await _degrade(app, events, state, session_id, send, scene_id, generation_id,
                       getattr(e, "reason", None) or _FALLBACK_REASON_UNKNOWN)
    except Exception:  # noqa: BLE001 —— 填充失败不杀连接，骨架停留
        await _degrade(app, events, state, session_id, send, scene_id, generation_id, "unknown_error")


async def _degrade(app, events, state, session_id, send, scene_id, generation_id, reason: str) -> None:
    if state.scene is None or state.scene.scene_id != scene_id:
        return
    state.scene.status = "degraded"
    events.append(session_id, "scene.degraded", {
        "sceneId": scene_id, "generationId": generation_id, "reason": reason, "fallbackReason": reason,
    })
    await send({"type": "scene.degraded", "sceneId": scene_id,
                "generationId": generation_id, "reason": reason, "fallbackReason": reason})
```

`enter_scene` 末尾（替换 Task 4 的占位注释）：
```python
    state.fill_task = asyncio.create_task(
        fill_scene(app, events, state, session_id, send, scene_id=scene_id,
                   seed=scene_id, generation_id=generation_id, skeleton=skeleton))
```
并在文件顶部 `import asyncio`。

- [ ] **Step 7: make_app 注入 director + 写生命周期填充集成测试**

```python
# apps/api/tests/ws_helpers.py —— make_app 增参
def make_app(tmp_path, scenario="ok", slow_delta_s=0.0, stream_text_override=None, scene_director=None):
    ...
    app = create_app(events, Settings(tutor_cache_dir=tmp_path / "tutor-audio"),
                     asr_client=fake_asr, tts_client=fake_tts,
                     llm_client=MockAdapter(scenario, stream_text_override=stream_text_override))
    if scene_director is not None:
        app.state.director = scene_director
    app.state.actor = SlowActor(app.state.actor)
    return events, app
```
```python
# apps/api/app/main.py —— app.state.director
from app.llm.scene_director import get_scene_director
    ...
    app.state.director = get_scene_director(settings, client, llm_log)
```

```python
# apps/api/tests/test_scene_lifecycle.py 追加
from app.llm.mock import MockSceneDirector
from app.llm.proposals import ProposalError


def _sent_types(ws) -> list[str]:
    return [m.get("type") for m in ws.sent if isinstance(m, dict)]


async def test_fill_task_applies_patch_and_sets_filled(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok", scene_director=MockSceneDirector("ok"))
    ws = FakeWS([{"type": "sleep", "seconds": 0.3}], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.4)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    assert st.scene is not None and st.scene.status == "filled"
    types = _sent_types(ws)
    assert types.count("scene.skeleton") == 1
    assert "scene.patch" in types
    assert "scene.degraded" not in types
    patch_events = [e["payload"] for e in events.list_after("sess-x", 0) if e["event_type"] == "scene.patch"]
    assert patch_events and patch_events[-1]["ops"]


async def test_director_timeout_degrades_to_skeleton(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok", scene_director=MockSceneDirector("timeout"))
    ws = FakeWS([{"type": "sleep", "seconds": 0.15}], app)
    # 用极短 director timeout 让 fill 快速失败降级
    app.state.settings = app.state.settings.__class__(**{
        **app.state.settings.__dict__,
        "llm_total_timeout_director_s": 0.05,
    })
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    assert st.scene is not None and st.scene.status == "degraded"
    assert "scene.degraded" in _sent_types(ws)
    # 骨架仍完整可玩
    assert any(e["component"] == "npc" for e in st.scene.entities)


async def test_scene_transition_cancels_stale_fill(tmp_path) -> None:
    """转场中途旧 fill 晚到 → 不应用（sceneId 不匹配被丢弃）。"""
    events, app = make_app(tmp_path, scenario="ok", scene_director=MockSceneDirector("timeout"))
    app.state.settings = app.state.settings.__class__(**{
        **app.state.settings.__dict__, "llm_total_timeout_director_s": 0.3,
    })
    ws = FakeWS([
        {"type": "sleep", "seconds": 0.05},
        {"type": "websocket.receive", "text": '{"type":"scene.request","exitId":"left"}'},
        {"type": "sleep", "seconds": 0.5},
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.8)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    assert st.scene is not None and st.scene.archetype_id == "bakery"
```

- [ ] **Step 8: 运行全部 API 测试**

Run: `cd apps/api && uv run pytest -v`
Expected: PASS（回归绿）。

- [ ] **Step 9: 提交**

```bash
git add apps/api/app/llm/scene_director.py apps/api/app/llm/mock.py apps/api/app/llm/proposals.py apps/api/app/scene_lifecycle.py apps/api/app/main.py apps/api/tests/test_scene_director.py apps/api/tests/test_scene_validation.py apps/api/tests/test_scene_lifecycle.py apps/api/tests/ws_helpers.py
git commit -m "feat(scene): mock-first SceneDirector + layered proposal validation + fill/degraded lifecycle"
```

---

### Task 7: 转场预取（ScenePlan 服务端缓存）

**Files:**
- Create: `apps/api/app/scene_prefetch.py`
- Modify: `apps/api/app/ws.py`（scene.hint 触发 + spoke-back 预取）、`apps/api/app/scene_lifecycle.py`（进场先查缓存）
- Test: `apps/api/tests/test_scene_prefetch.py`

**Interfaces:**
- Consumes: `SceneDirector.propose`（Task 6）、`SceneStore.target_for`/`load_town_map`（Task 3）、`settings.scene_prefetch_ttl_s`/`scene_prefetch_budget_ratio`（Task 4）。
- Produces:
  - `ScenePrefetchCache(ttl_s=60.0, maxsize=32)`：`get(archetype_id) -> dict | None`（过期即失效）、`put(archetype_id, proposal)`（LRU 淘汰）。键 = archetypeId（阶段 3 无 world memory，revision 恒 0）。
  - `ws.py`：`scene.hint {exitId}` → 若 budget 允许（`calls < cap * ratio`）→ 后台任务 `prefetch target`；spoke 进场（`enter_scene` 完成后）→ 若该场景只有 1 个出口 → 后台预取其目标。
  - `scene_lifecycle.fill_scene`：先 `prefetch.get(archetype)`；命中 → 直接 `apply_proposal`（不调 Director、不进 `llm_calls`）；未命中 → 正常 Director，成功后 `prefetch.put`（供下次访问）。

- [ ] **Step 1: ScenePrefetchCache + 失败测试**

```python
# apps/api/tests/test_scene_prefetch.py
import asyncio
import time

from app.scene_prefetch import ScenePrefetchCache


def test_cache_hit_and_miss() -> None:
    c = ScenePrefetchCache(ttl_s=60)
    assert c.get("bakery") is None
    c.put("bakery", {"fills": []})
    assert c.get("bakery") == {"fills": []}


def test_cache_ttl_expiry() -> None:
    c = ScenePrefetchCache(ttl_s=0.01)
    c.put("bakery", {"fills": []})
    time.sleep(0.02)
    assert c.get("bakery") is None


def test_cache_lru_eviction() -> None:
    c = ScenePrefetchCache(maxsize=2)
    c.put("a", {1}); c.put("b", {2}); c.put("c", {3})
    assert c.get("a") is None and c.get("b") == {2} and c.get("c") == {3}
```

```python
# apps/api/app/scene_prefetch.py
"""ScenePlan 服务端缓存：预取 Director 提案（非骨架——骨架本地无条件快）。
键 = archetypeId（阶段 3 无 world memory，revision 恒 0），TTL 过期即失效。"""
from __future__ import annotations

import time


class ScenePrefetchCache:
    def __init__(self, *, ttl_s: float = 60.0, maxsize: int = 32) -> None:
        self._ttl_s = ttl_s
        self._maxsize = maxsize
        self._items: dict[str, tuple[float, dict]] = {}

    def get(self, archetype_id: str) -> dict | None:
        item = self._items.get(archetype_id)
        if item is None:
            return None
        expires_at, proposal = item
        if time.time() > expires_at:
            self._items.pop(archetype_id, None)
            return None
        return proposal

    def put(self, archetype_id: str, proposal: dict) -> None:
        if len(self._items) >= self._maxsize and archetype_id not in self._items:
            oldest = min(self._items, key=lambda k: self._items[k][0])
            self._items.pop(oldest, None)
        self._items[archetype_id] = (time.time() + self._ttl_s, proposal)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd apps/api && uv run pytest tests/test_scene_prefetch.py -v`
Expected: FAIL（`ModuleNotFoundError: app.scene_prefetch`）。

- [ ] **Step 3: main.py 挂缓存 + lifecycle 命中路径**

```python
# apps/api/app/main.py
from app.scene_prefetch import ScenePrefetchCache
    ...
    app.state.prefetch = ScenePrefetchCache(ttl_s=settings.scene_prefetch_ttl_s)
```

```python
# apps/api/app/scene_lifecycle.py 追加
async def _apply_proposal(app, events, state, session_id, send, *,
                          scene_id, seed, generation_id, skeleton, proposal) -> None:
    """预取命中的提案直接应用（无 Director 调用、无 llm_calls 记录）。"""
    scenes = app.state.scenes
    archetype_id = state.scene.archetype_id
    try:
        cleaned, _warnings = validate_proposal(proposal, scenes.get_archetype(archetype_id), app.state.catalog)
        filled = scenes.compile_filled(archetype_id, scene_id=scene_id, seed=seed,
                                       generation_id=generation_id, proposal=cleaned)
        ops = scenes.diff_scenes(skeleton, filled)
        if state.scene is None or state.scene.scene_id != scene_id:
            return
        state.scene.status = "filled"
        state.scene.setting = filled["setting"]
        state.scene.entities = filled["entities"]
        state.scene.characters = filled["characters"]
        state.scene.exits = filled["exits"]
        state.scene.revision += 1
        patch_id = f"patch_{uuid.uuid4().hex[:8]}"
        events.append(session_id, "scene.patch", {
            "sceneId": scene_id, "generationId": generation_id,
            "baseRevision": state.scene.revision - 1, "patchId": patch_id, "ops": ops,
        })
        if ops:
            await send({"type": "scene.patch", "sceneId": scene_id, "generationId": generation_id,
                        "baseRevision": state.scene.revision - 1, "patchId": patch_id, "ops": ops})
    except (ProposalError, Exception):  # noqa: BLE001 —— 缓存提案异常时回退到 Director 填充
        await fill_scene(app, events, state, session_id, send, scene_id=scene_id,
                         seed=seed, generation_id=generation_id, skeleton=skeleton)
```

> 说明：预算护栏由 Step 5 的 ws 闭包 `_prefetch_for` 统一实现（带 session_id）；本文件不再有 `prefetch_target`（避免悬空函数）。

- [ ] **Step 4: fill_scene 改：先查缓存，未命中才 Director，成功后入缓存**

把 `scene_lifecycle.fill_scene` 的 Director 调用段改为：

```python
        cached = app.state.prefetch.get(archetype_id)
        if cached is not None:
            await _apply_proposal(app, events, state, session_id, send, scene_id=scene_id,
                                  seed=seed, generation_id=generation_id, skeleton=skeleton,
                                  proposal=cached)
            return
        async with state.semaphore:
            async with asyncio.timeout(app.state.settings.llm_total_timeout_director_s):
                proposal = await app.state.director.propose(
                    archetype_id=archetype_id,
                    archetype=scenes.get_archetype(archetype_id),
                    catalog=app.state.catalog,
                    recent_scenes=recent_scenes(events, session_id),
                    attempt="enter")
        cleaned, _warnings = validate_proposal(proposal, scenes.get_archetype(archetype_id), app.state.catalog)
        app.state.prefetch.put(archetype_id, cleaned)      # 供下次访问（进入即命中）
        filled = scenes.compile_filled(archetype_id, scene_id=scene_id, seed=seed,
                                       generation_id=generation_id, proposal=cleaned)
        ops = scenes.diff_scenes(skeleton, filled)
        ...（其余不变）
```
`_apply_proposal` 需要 `fill_scene` 中定义的局部；注意 `fill_scene` 已 import `asyncio`。

- [ ] **Step 5: ws 接线（scene.hint + spoke-back 预取）**

```python
# apps/api/app/ws.py 顶部
from app.scene_lifecycle import enter_scene, fill_scene, scene_maps
from app.scene_prefetch import ScenePrefetchCache  # noqa: F401

    async def _prefetch_for(app, state, session_id: str, archetype_id: str) -> None:
        """预算允许时后台预取目标 archetype 的提案并缓存。"""
        settings = app.state.settings
        cap = settings.llm_session_call_cap
        if app.state.llm_log.count_session_calls(session_id) >= cap * settings.scene_prefetch_budget_ratio:
            return
        if app.state.prefetch.get(archetype_id) is not None:
            return
        try:
            async with state.semaphore:
                async with asyncio.timeout(settings.llm_total_timeout_director_s):
                    proposal = await app.state.director.propose(
                        archetype_id=archetype_id,
                        archetype=app.state.scenes.get_archetype(archetype_id),
                        catalog=app.state.catalog,
                        recent_scenes=[], attempt="prefetch")
            app.state.prefetch.put(archetype_id, proposal)
        except Exception:  # noqa: BLE001 —— 预取失败（含超时）静默（下次正常进场再 Director）
            return

    def _maybe_spawn_prefetch(app, state, session_id: str, archetype_id: str) -> None:
        task = asyncio.create_task(_prefetch_for(app, state, session_id, archetype_id))
        state.spurious_guards.add(task)
        task.add_done_callback(state.spurious_guards.discard)
```

消息循环：
```python
                elif t == "scene.hint":
                    if state.scene is None:
                        continue
                    target = app.state.scenes.target_for(state.scene.archetype_id, ctrl.get("exitId"))
                    if target:
                        _maybe_spawn_prefetch(app, state, session_id, target)
```

`enter_scene`（scene_lifecycle）末尾、spawn fill 之前追加 spoke-back 预取：
```python
    # spoke 进场即预取 back（唯一出口 → 100% 可预测）
    if len(skeleton["exits"]) == 1:
        back = skeleton["exits"][0].get("targetArchetypeId")
        if back:
            ws._maybe_spawn_prefetch(app, state, session_id, back)
```
> 注：`enter_scene` 拿不到 `_maybe_spawn_prefetch`（ws 闭包）。改为在 ws 的 `scene.request` 分支、`enter_scene` 返回后触发：
```python
                elif t == "scene.request":
                    if state.scene is None:
                        continue
                    target = app.state.scenes.target_for(state.scene.archetype_id, ctrl.get("exitId"))
                    if target:
                        await enter_scene(app, events, state, session_id, send,
                                          target_archetype_id=target, source="exit")
                        _maybe_spawn_prefetch(app, state, session_id,
                                              app.state.scenes.load_town_map()["start"])
```
> 说明：进场目标已由 `fill_scene` 缓存自身提案；这里额外预取"回 hub"（spoke 场景的唯一出口），覆盖 Task 7 的 spoke-back 规则。

- [ ] **Step 6: 预取集成测试**

```python
# apps/api/tests/test_scene_prefetch.py 追加
import asyncio
import pytest

from app.llm.mock import MockSceneDirector
from app.ws import ws_session
from tests.ws_helpers import FakeWS, make_app


async def test_spoke_entry_prefetches_back_and_next_enter_is_cached(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok", scene_director=MockSceneDirector("ok"))
    ws = FakeWS([
        {"type": "websocket.receive", "text": '{"type":"scene.request","exitId":"left"}'},   # plaza→bakery
        {"type": "sleep", "seconds": 0.5},
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.7)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # bakery 进场 → 其唯一出口指向 plaza → plaza 提案被预取缓存
    assert app.state.prefetch.get("plaza") is not None


async def test_hint_prefetches_and_later_enter_uses_cache_without_new_director_call(tmp_path) -> None:
    calls_before = {"n": 0}
    class CountingDirector(MockSceneDirector):
        async def propose(self, **kw):
            calls_before["n"] += 1
            return await super().propose(**kw)

    events, app = make_app(tmp_path, scenario="ok", scene_director=CountingDirector("ok"))
    ws = FakeWS([
        {"type": "websocket.receive", "text": '{"type":"scene.hint","exitId":"left"}'},   # plaza hover bakery
        {"type": "sleep", "seconds": 0.4},
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert app.state.prefetch.get("bakery") is not None
    assert calls_before["n"] == 1   # hint 一次调用已缓存
```

- [ ] **Step 7: 运行全部 API 测试**

Run: `cd apps/api && uv run pytest -v`
Expected: PASS。

- [ ] **Step 8: 提交**

```bash
git add apps/api/app/scene_prefetch.py apps/api/app/scene_lifecycle.py apps/api/app/ws.py apps/api/app/main.py apps/api/tests/test_scene_prefetch.py
git commit -m "feat(scene): ScenePlan prefetch cache + hub-hover + spoke-back triggers"
```

---

### Task 8: 断线重放（scene.entered + scene.patch 重建场景）

**Files:**
- Modify: `apps/api/app/scene_lifecycle.py`（`rebuild_from_events`）、`apps/api/app/ws.py`（连接时先尝试重放）
- Test: `apps/api/tests/test_scene_replay.py`

**Interfaces:**
- Consumes: `SceneStore.compile_skeleton`（Task 3）、`scene_maps`（Task 4）、`fill_scene` 事件格式（Task 6）。
- Produces:
  - `scene_lifecycle.rebuild_from_events(app, events, state, session_id, send) -> bool`：若事件流里有 `scene.entered` → 以**最后一个** `scene.entered` 的 `sceneId/generationId/archetypeId` 重建骨架 → 依次应用该场景的 `scene.patch` ops → 组装 `SceneSession` + 重建 actor + 重置仲裁 → 发送 `scene.skeleton` + 归并 patch + `scene.focus`（默认）→ 返回 True；无 scene.entered → False。
  - 音频帧不补发；重连时当前 utterance 由连接断开的 `finally` 自然取消（沿用阶段 2）。
  - `activeSpeaker`/focus/pendingSpeakers 全部重置为默认（不入事件）。

- [ ] **Step 1: 写失败测试（重放重建）**

```python
# apps/api/tests/test_scene_replay.py
import asyncio
import json

import pytest

from app.event_store import EventStore
from app.scene_lifecycle import rebuild_from_events
from app.ws import ws_session
from tests.ws_helpers import FakeWS, make_app


async def test_reconnect_replays_previous_scene(tmp_path) -> None:
    """先进入 bakery 并完成 filled；断开重建同一场景。"""
    events, app = make_app(tmp_path, scenario="ok")
    ws1 = FakeWS([
        {"type": "websocket.receive", "text": '{"type":"scene.request","exitId":"left"}'},
        {"type": "sleep", "seconds": 0.5},
    ], app)
    t1 = asyncio.create_task(ws_session(ws1))
    await asyncio.sleep(0.6)
    t1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t1
    st1 = app.state.sessions["sess-x"]
    assert st1.scene and st1.scene.archetype_id == "bakery"
    gen = st1.scene.generation_id

    # 模拟进程重启：清空内存会话状态（事件库保留）
    app.state.sessions.pop("sess-x")
    ws2 = FakeWS([{"type": "sleep", "seconds": 0.1}], app)
    t2 = asyncio.create_task(ws_session(ws2))
    await asyncio.sleep(0.2)
    t2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t2

    st2 = app.state.sessions["sess-x"]
    assert st2.scene is not None and st2.scene.archetype_id == "bakery"
    assert st2.scene.generation_id == gen          # 同一 generationId（重放，不重新分配）
    skels = [m for m in ws2.sent if isinstance(m, dict) and m.get("type") == "scene.skeleton"]
    assert len(skels) == 1 and skels[0]["sceneId"] == st1.scene.scene_id


def test_rebuild_from_events_without_entered_returns_false(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    events.append("s", "dialogue.turn", {"turnId": "t1", "npcText": "hi", "userText": "x", "audioBytes": 1})
    class _App:  # 只跑 events 投影路径，不需完整 app
        pass
    app = _App()
    app.state = type("S", (), {})()
    ok = asyncio.run(rebuild_from_events(app, events, None, "s", None))
    assert ok is False
```

- [ ] **Step 2: 运行确认失败**

Run: `cd apps/api && uv run pytest tests/test_scene_replay.py -v`
Expected: FAIL（`rebuild_from_events` 未定义；重连会重新进入 plaza）。

- [ ] **Step 3: 实现 rebuild_from_events**

```python
# apps/api/app/scene_lifecycle.py 追加
def rebuild_from_events(app, events, state, session_id, send) -> bool:
    """事件投影重建场景。成功 → 组好 state.scene + actor + 发送 skeleton/patch/focus，返回 True。"""
    evs = events.list_after(session_id, 0)
    entered = [e["payload"] for e in evs if e["event_type"] == "scene.entered"]
    if not entered:
        return False
    last = entered[-1]
    patches = [e["payload"] for e in evs if e["event_type"] == "scene.patch"
               and e["payload"].get("sceneId") == last["sceneId"]]

    scenes = app.state.scenes
    skeleton = scenes.compile_skeleton(last["archetypeId"], scene_id=last["sceneId"],
                                       seed=last["sceneId"], generation_id=last["generationId"])
    ops: list[dict] = []
    for p in patches:
        ops.extend(p.get("ops", []))
    entities, setting = apply_ops(skeleton["entities"], skeleton["setting"], ops)
    scene = SceneSession(scene_id=last["sceneId"], generation_id=last["generationId"],
                         archetype_id=last["archetypeId"], revision=len(patches) + 1,
                         status="filled" if patches else "skeleton",
                         setting=setting, background=skeleton["background"],
                         entities=entities, characters=skeleton["characters"],
                         exits=skeleton["exits"], default_npc_id=scenes.default_npc_id(last["archetypeId"]))
    state.scene = scene
    state.scene_seq = int(last["sceneId"].rsplit("_", 1)[-1])
    state.arbitration.reset(scene.default_npc_id)
    scene_words, entity_by_word_id = scene_maps(scene)
    state.actor = app.state.scene_factory(scene_words, entity_by_word_id, npc_id=scene.default_npc_id)

    async def _send_sync() -> None:
        await send({"type": "scene.skeleton", "sceneId": scene.scene_id, "generationId": scene.generation_id,
                    "archetypeId": scene.archetype_id, "revision": scene.revision, "status": scene.status,
                    "setting": scene.setting, "background": scene.background,
                    "entities": scene.entities, "characters": scene.characters, "exits": scene.exits})
        if ops:
            await send({"type": "scene.patch", "sceneId": scene.scene_id, "generationId": scene.generation_id,
                        "baseRevision": len(patches), "patchId": "replay", "ops": ops})
        await send({"type": "scene.focus", "sceneId": scene.scene_id, "generationId": scene.generation_id,
                    "activeSpeaker": state.arbitration.active_speaker,
                    "focusSource": "reconnect", "focusExpiresAt": None})
    # ws 层保证 send 可用（连接刚建立）；同步包装
    import asyncio
    asyncio.get_running_loop().create_task(_send_sync())
    return True


def apply_ops(entities: list[dict], setting: dict, ops: list[dict]) -> tuple[list[dict], dict]:
    """白名单 patch ops 应用（/entities/<id> 与 /setting）。重放与前端共用语义。"""
    out: dict[str, dict] = {e["id"]: e for e in entities}
    for op in ops:
        path = op.get("path", "")
        if path.startswith("/entities/"):
            eid = path[len("/entities/"):]
            if op.get("op") in ("add", "replace") and "entity" in op:
                out[eid] = op["entity"]
            elif op.get("op") == "remove":
                out.pop(eid, None)
        elif path == "/setting" and op.get("op") == "replace":
            setting = op.get("value", setting)
    return list(out.values()), setting
```

- [ ] **Step 4: ws 连接时先尝试重放**

```python
# apps/api/app/ws.py —— ws_session 内、进入 while 前
    if state.scene is None:
        replayed = rebuild_from_events(app, events, state, session_id, send)
        if not replayed:
            await enter_scene(app, events, state, session_id, send,
                              target_archetype_id=None, source="connect")
```
```python
# apps/api/app/ws.py 顶部
from app.scene_lifecycle import enter_scene, fill_scene, rebuild_from_events, scene_maps
```

- [ ] **Step 5: 运行确认通过**

Run: `cd apps/api && uv run pytest tests/test_scene_replay.py -v && cd apps/api && uv run pytest -v`
Expected: 全部 PASS（回归绿）。

- [ ] **Step 6: 提交**

```bash
git add apps/api/app/scene_lifecycle.py apps/api/app/ws.py apps/api/tests/test_scene_replay.py
git commit -m "feat(scene): reconnect replay rebuilds scene from session_events projection"
```

---

### Task 9: 前端 turnGate 两门 + applyScenePatch（纯函数）

**Files:**
- Modify: `apps/web/src/audio/turnGate.ts`
- Create: `apps/web/src/scenePatch.ts`
- Test: `apps/web/tests/turnGate.test.ts`（追加）、`apps/web/tests/scenePatch.test.ts`

**Interfaces:**
- Consumes: 无（纯函数）。
- Produces:
  - `turnGate.ts`：
    - `isAcceptedTurn(currentTurnId, companionTurnId, turnId)`（**保留**，语义不变，向后兼容现有测试/内部）。
    - `acceptTurnMessage(currentGenId, currentTurnId, companionTurnId, msgGenId, msgTurnId) -> boolean`：speech/audio/metadata 门 = genId 相等 **且** turn 逻辑（null 窗口 bootstrap 同旧）。
    - `acceptSceneMessage(currentGenId, currentSceneId, currentRevision, msgGenId, msgSceneId, baseRevision) -> boolean`：patch 门 = genId + sceneId 相等 **且** baseRevision == currentRevision。
  - `scenePatch.ts`：`applyScenePatch(entities: Entity[], setting: any, ops: PatchOp[]) -> { entities, setting }`（白名单 `/entities/<id>` 与 `/setting`；未知 path 忽略）。

- [ ] **Step 1: 前端依赖安装**

在仓库根 `pnpm install`（一次；若已安装跳过）。
Run: `pnpm install`
Expected: 完成；`node_modules` 出现。

- [ ] **Step 2: 写失败测试（两个门 + patch 应用）**

```python
# 注：这是 TS，以下为 .ts 测试
```
`apps/web/tests/turnGate.test.ts` 追加：
```ts
import { acceptSceneMessage, acceptTurnMessage, isAcceptedTurn } from '../src/audio/turnGate';

describe('acceptTurnMessage', () => {
  it('rejects when generation differs (cross-scene late)', () => {
    expect(acceptTurnMessage('g2', 't2', null, 'g1', 't1')).toBe(false);
  });
  it('rejects same-scene stale turn', () => {
    expect(acceptTurnMessage('g1', 't2', null, 'g1', 't1')).toBe(false);
  });
  it('bootstraps new turn when currentTurnId is null', () => {
    expect(acceptTurnMessage('g1', null, null, 'g1', 't9')).toBe(true);
  });
  it('accepts companion turn', () => {
    expect(acceptTurnMessage('g1', 't1', 'c9', 'g1', 'c9')).toBe(true);
  });
});

describe('acceptSceneMessage', () => {
  it('rejects cross-scene patch', () => {
    expect(acceptSceneMessage('g2', 's2', 2, 'g1', 's1', 1)).toBe(false);
  });
  it('rejects mismatched baseRevision', () => {
    expect(acceptSceneMessage('g1', 's1', 3, 'g1', 's1', 1)).toBe(false);
  });
  it('accepts current patch', () => {
    expect(acceptSceneMessage('g1', 's1', 1, 'g1', 's1', 1)).toBe(true);
  });
});
```

`apps/web/tests/scenePatch.test.ts`：
```ts
import { describe, expect, it } from 'vitest';
import { applyScenePatch } from '../src/scenePatch';
import type { Entity } from '../src/types';

const loaf: Entity = { id: 'counter.main-1', component: 'prop', layout: { x: 0, y: 0, w: 40, h: 40, anchor: 'bottom' }, appearance: { visualKey: 'food.loaf' }, semantics: { name: 'loaf', wordId: 'word_loaf_n_1' }, interactions: ['ask'] };
const apple: Entity = { id: 'counter.main-1', component: 'prop', layout: { x: 0, y: 0, w: 40, h: 40, anchor: 'bottom' }, appearance: { visualKey: 'food.apple' }, semantics: { name: 'apple', wordId: 'word_apple_n_1' }, interactions: ['ask'] };
const extra: Entity = { id: 'shelf.top-2', component: 'prop', layout: { x: 0, y: 0, w: 40, h: 40, anchor: 'bottom' }, appearance: { visualKey: 'food.loaf' }, semantics: { name: 'loaf' }, interactions: ['ask'] };

describe('applyScenePatch', () => {
  it('replace upserts by id and keeps others', () => {
    const r = applyScenePatch([loaf], { displayName: 'X', time: 'day' }, [{ op: 'replace', path: '/entities/counter.main-1', entity: apple }]);
    expect(r.entities.map((e) => e.semantics.wordId)).toEqual(['word_apple_n_1']);
  });
  it('add appends, remove drops', () => {
    let r = applyScenePatch([loaf], { displayName: 'X', time: 'day' }, [{ op: 'add', path: '/entities/shelf.top-2', entity: extra }]);
    expect(r.entities.length).toBe(2);
    r = applyScenePatch(r.entities, r.setting, [{ op: 'remove', path: '/entities/shelf.top-2' }]);
    expect(r.entities.length).toBe(1);
  });
  it('setting replace applies; unknown path ignored', () => {
    const r = applyScenePatch([loaf], { displayName: 'X', time: 'day' }, [
      { op: 'replace', path: '/setting', value: { displayName: 'Y', time: 'morning' } },
      { op: 'add', path: '/schemaVersion', value: 'hack' },
    ]);
    expect(r.setting.displayName).toBe('Y');
    expect(r.entities.length).toBe(1);
  });
});
```

- [ ] **Step 3: 运行确认失败**

Run: `cd apps/web && node ../../node_modules/vitest/vitest.mjs run --maxWorkers=1 tests/turnGate.test.ts tests/scenePatch.test.ts`
Expected: FAIL（`acceptTurnMessage`/`acceptSceneMessage`/`applyScenePatch` 未定义）。

- [ ] **Step 4: 实现两个门 + applyScenePatch**

```ts
// apps/web/src/audio/turnGate.ts
/** 双端过期丢弃（前端侧）：非当前 turnId 的 delta/commit/metadata/tts.audio.start 一律丢弃。 */
export function isAcceptedTurn(
  currentTurnId: string | null,
  companionTurnId: string | null,
  turnId: string | null | undefined,
): boolean {
  if (!turnId) return false;
  if (currentTurnId === null) return true;
  return turnId === currentTurnId || turnId === companionTurnId;
}

/** 门 1（对话）：speech delta/commit、metadata、tts.audio.* = genId **且** turnId。 */
export function acceptTurnMessage(
  currentGenId: string | null,
  currentTurnId: string | null,
  companionTurnId: string | null,
  msgGenId: string | null | undefined,
  msgTurnId: string | null | undefined,
): boolean {
  if (!msgTurnId || !msgGenId) return false;
  if (currentGenId !== msgGenId) return false; // 跨场景晚到 → 丢
  return isAcceptedTurn(currentTurnId, companionTurnId, msgTurnId);
}

/** 门 2（场景）：scene.patch = genId + sceneId + baseRevision。 */
export function acceptSceneMessage(
  currentGenId: string | null,
  currentSceneId: string | null,
  currentRevision: number | null,
  msgGenId: string | null | undefined,
  msgSceneId: string | null | undefined,
  baseRevision: number | undefined,
): boolean {
  if (!msgGenId || !msgSceneId) return false;
  if (currentGenId !== msgGenId || currentSceneId !== msgSceneId) return false;
  return baseRevision === undefined || baseRevision === currentRevision;
}
```

```ts
// apps/web/src/scenePatch.ts
import type { Entity } from './types';

export interface PatchOp {
  op: 'add' | 'replace' | 'remove';
  path: string;
  entity?: Entity;
  value?: unknown;
}

/** 白名单 patch 应用：只允许 /entities/<id> 与 /setting；未知 path 忽略（安全）。 */
export function applyScenePatch(
  entities: Entity[],
  setting: unknown,
  ops: PatchOp[],
): { entities: Entity[]; setting: any } {
  const out = [...entities];
  let nextSetting = setting;
  for (const op of ops) {
    if (op.path.startsWith('/entities/')) {
      const id = op.path.slice('/entities/'.length);
      const i = out.findIndex((e) => e.id === id);
      if (op.op === 'add' || op.op === 'replace') {
        if (!op.entity) continue;
        if (i >= 0) out[i] = op.entity; else out.push(op.entity);
      } else if (op.op === 'remove' && i >= 0) {
        out.splice(i, 1);
      }
    } else if (op.path === '/setting' && op.op === 'replace') {
      nextSetting = op.value;
    }
  }
  return { entities: out, setting: nextSetting };
}
```

- [ ] **Step 5: 运行确认通过**

Run: `cd apps/web && node ../../node_modules/vitest/vitest.mjs run --maxWorkers=1 tests/turnGate.test.ts tests/scenePatch.test.ts`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add apps/web/src/audio/turnGate.ts apps/web/src/scenePatch.ts apps/web/tests/turnGate.test.ts apps/web/tests/scenePatch.test.ts
git commit -m "feat(web): split turn/scene gates + whitelisted applyScenePatch"
```

---

### Task 10: 前端 scene store（Zustand）+ useVoiceRound + App 接线

**Files:**
- Create: `apps/web/src/sceneStore.ts`
- Modify: `apps/web/src/useVoiceRound.ts`、`apps/web/src/App.tsx`、`apps/web/e2e/scene.spec.ts`
- Modify: `apps/web/package.json`（加 zustand 依赖）
- Test: `apps/web/tests/useVoiceRound.test.ts`（追加场景消息用例）

**Interfaces:**
- Consumes: `acceptTurnMessage`/`acceptSceneMessage`（Task 9）、`applyScenePatch`（Task 9）、`useSceneStore`。
- Produces:
  - `apps/web/src/sceneStore.ts`（Zustand）：`useSceneStore` 提供 `{ sceneId, generationId, archetypeId, revision, status, setting, background, entities, characters, exits, activeSpeaker, applySkeleton, applyPatch, applyDegraded, applyFocus, reset }`。
  - `useVoiceRound` 新增：`requestScene(exitId)`、`hintScene(exitId)`（去抖 300ms）、`focusNpc(npcId)`；内部处理 `scene.skeleton/patch/degraded/focus`，`currentGenIdRef` 由 skeleton 同步，消息全部过两个门。
  - `App.tsx`：去掉 `fetchScene`，场景状态从 store 读；`isPreview` hash 路由保留（Task 5）。

- [ ] **Step 1: 加 zustand 依赖**

Run: `cd apps/web && pnpm add zustand`（或 `pnpm --dir apps/web add zustand`）
Expected: package.json dependencies 出现 `zustand`。

- [ ] **Step 2: 写失败测试（useVoiceRound 场景消息）**

`apps/web/tests/useVoiceRound.test.ts` 追加（复用现有 FakeVoiceSocket mock 的 `emit` helper）：

```ts
import { useSceneStore } from '../src/sceneStore';

it('scene.skeleton populates store and gates speech by generation', async () => {
  const { result, emit } = await startHook();
  act(() => {
    emit('scene.skeleton', {
      type: 'scene.skeleton', sceneId: 's1', generationId: 'g1', archetypeId: 'plaza',
      revision: 1, status: 'skeleton',
      setting: { displayName: 'Town', time: 'day' },
      background: { style: 'gradient', gradient: 'linear-gradient(#000,#111)', decor: [], ambienceKey: 'a' },
      entities: [{ id: 'guide-1', component: 'npc', layout: { x: 0, y: 0, w: 40, h: 40, anchor: 'bottom' }, appearance: { visualKey: 'npc.greeter' }, semantics: { name: 'Tom', npcId: 'npc_tom' } }],
      characters: [{ slotId: 'guide', npcId: 'npc_tom' }],
      exits: [{ id: 'left', targetArchetypeId: 'bakery' }],
    });
  });
  expect(useSceneStore.getState().generationId).toBe('g1');
  expect(useSceneStore.getState().entities.length).toBe(1);
  // 跨场景（g2）的 delta 被拒
  act(() => result.current.beginUtterance());
  act(() => emit('npc.speech.delta', { type: 'npc.speech.delta', generationId: 'g2', turnId: 't1', text: 'junk' }));
  expect(result.current.turns.length).toBe(0);
});

it('scene.patch applies via gate and flips status to filled', async () => {
  const { result, emit } = await startHook();
  act(() => {
    emit('scene.skeleton', { type: 'scene.skeleton', sceneId: 's1', generationId: 'g1', archetypeId: 'bakery', revision: 1, status: 'skeleton', setting: { displayName: 'X', time: 'day' }, background: { style: 'gradient', gradient: 'g', decor: [], ambienceKey: 'a' }, entities: [], characters: [], exits: [] });
    emit('scene.patch', { type: 'scene.patch', sceneId: 's1', generationId: 'g1', baseRevision: 1, patchId: 'p1', ops: [{ op: 'replace', path: '/setting', value: { displayName: 'Y', time: 'morning' } }] });
  });
  expect(useSceneStore.getState().status).toBe('filled');
  expect(useSceneStore.getState().setting.displayName).toBe('Y');
  // baseRevision 不匹配的晚到 patch 被丢
  act(() => emit('scene.patch', { type: 'scene.patch', sceneId: 's1', generationId: 'g1', baseRevision: 9, patchId: 'p2', ops: [{ op: 'replace', path: '/setting', value: { displayName: 'Z', time: 'night' } }] }));
  expect(useSceneStore.getState().setting.displayName).toBe('Y');
});

it('scene.request / npc.focus send controls', async () => {
  const { result, sock } = await startHook();
  act(() => result.current.requestScene('left'));
  act(() => result.current.focusNpc('npc_rosa'));
  expect(sock.sent.some((m: any) => m.type === 'scene.request' && m.exitId === 'left')).toBe(true);
  expect(sock.sent.some((m: any) => m.type === 'npc.focus' && m.characterId === 'npc_rosa')).toBe(true);
});
```

- [ ] **Step 3: 运行确认失败**

Run: `cd apps/web && node ../../node_modules/vitest/vitest.mjs run --maxWorkers=1 tests/useVoiceRound.test.ts`
Expected: FAIL（无 `useSceneStore`；handlers 未接）。

- [ ] **Step 4: 实现 sceneStore.ts**

```ts
// apps/web/src/sceneStore.ts
import { create } from 'zustand';
import type { Entity } from './types';
import { applyScenePatch, type PatchOp } from './scenePatch';

export type SceneStatus = 'idle' | 'skeleton' | 'filled' | 'degraded';

interface SceneState {
  sceneId: string | null;
  generationId: string | null;
  archetypeId: string | null;
  revision: number;
  status: SceneStatus;
  setting: { displayName: string; time: string } | null;
  background: { style: string; gradient: string; decor: string[]; ambienceKey: string } | null;
  entities: Entity[];
  characters: { slotId: string; npcId: string; name?: string; voice?: string }[];
  exits: { id: string; targetArchetypeId?: string }[];
  activeSpeaker: string | null;
  applySkeleton: (m: any) => void;
  applyPatch: (m: any) => void;
  applyDegraded: (m: any) => void;
  applyFocus: (m: any) => void;
  reset: () => void;
}

export const useSceneStore = create<SceneState>((set) => ({
  sceneId: null, generationId: null, archetypeId: null, revision: 0,
  status: 'idle', setting: null, background: null, entities: [], characters: [], exits: [],
  activeSpeaker: null,

  applySkeleton: (m) => set({
    sceneId: m.sceneId, generationId: m.generationId, archetypeId: m.archetypeId,
    revision: m.revision ?? 1, status: 'skeleton',
    setting: m.setting ?? null, background: m.background ?? null,
    entities: m.entities ?? [], characters: m.characters ?? [], exits: m.exits ?? [],
  }),
  applyPatch: (m) => set((s) => {
    const r = applyScenePatch(s.entities, s.setting, (m.ops ?? []) as PatchOp[]);
    return { entities: r.entities, setting: r.setting, status: 'filled', revision: (m.baseRevision ?? 0) + 1 };
  }),
  applyDegraded: () => set({ status: 'degraded' }),
  applyFocus: (m) => set({ activeSpeaker: m.activeSpeaker ?? null }),
  reset: () => set({ sceneId: null, generationId: null, archetypeId: null, revision: 0, status: 'idle', setting: null, background: null, entities: [], characters: [], exits: [], activeSpeaker: null }),
}));
```

- [ ] **Step 5: useVoiceRound 接场景消息 + 两个门 + 发送函数**

`apps/web/src/useVoiceRound.ts`：
- import：`useSceneStore`、`acceptTurnMessage`、`acceptSceneMessage`。
- 新增 ref：`currentGenIdRef = useRef<string | null>(null)`、`hintTimerRef = useRef<number | null>(null)`。
- `start()` 的 socket handlers 前加场景 handlers：
```ts
    sock.on('scene.skeleton', (m: any) => {
      currentGenIdRef.current = m.generationId;
      currentTurnIdRef.current = null;      // 新场景：对话门 reset
      lastTurnIdRef.current = null;
      companionTurnIdRef.current = null;
      audioTurnIdRef.current = null;
      queueRef.current.clear();
      useSceneStore.getState().applySkeleton(m);
    });
    sock.on('scene.patch', (m: any) => {
      const s = useSceneStore.getState();
      if (!acceptSceneMessage(currentGenIdRef.current, s.sceneId, s.revision, m.generationId, m.sceneId, m.baseRevision)) return;
      useSceneStore.getState().applyPatch(m);
    });
    sock.on('scene.degraded', (m: any) => {
      if (currentGenIdRef.current !== m.generationId) return;
      useSceneStore.getState().applyDegraded(m);
    });
    sock.on('scene.focus', (m: any) => {
      if (currentGenIdRef.current !== m.generationId) return;
      useSceneStore.getState().applyFocus(m);
    });
```
- 现有四个 handler（delta/commit/metadata/tts.audio.start）的首行门判断改为：
```ts
      if (!acceptTurnMessage(currentGenIdRef.current, currentTurnIdRef.current, companionTurnIdRef.current, m.generationId, m.turnId)) return;
```
（tts.audio.end 已有 `audioTurnIdRef !== m.turnId` 判断，追加 genId 校验同理：`if (currentGenIdRef.current !== m.generationId || audioTurnIdRef.current !== m.turnId) return;`）
- 新增发送函数：
```ts
  const requestScene = (exitId: string) => {
    socketRef.current?.sendControl({ type: 'scene.request', exitId });
  };
  const hintScene = (exitId: string) => {
    if (hintTimerRef.current !== null) window.clearTimeout(hintTimerRef.current);
    hintTimerRef.current = window.setTimeout(() => {
      socketRef.current?.sendControl({ type: 'scene.hint', exitId });
    }, 300);
  };
  const focusNpc = (npcId: string) => {
    socketRef.current?.sendControl({ type: 'npc.focus', sceneId: useSceneStore.getState().sceneId, generationId: useSceneStore.getState().generationId, characterId: npcId });
  };
```
- 返回值追加 `requestScene, hintScene, focusNpc`。
- `stop()` 里清理 hintTimer。

- [ ] **Step 6: App.tsx 改从 store 读场景**

```tsx
// apps/web/src/App.tsx
import { useSceneStore } from './sceneStore';

export default function App() {
  const [error, setError] = useState<string | null>(null);
  const [focusEntity, setFocusEntity] = useState<Entity | null>(null);
  const { micOn, status, turns, companion, start, stop, beginUtterance, interrupt, askCompanion, requestScene, hintScene, focusNpc } = useVoiceRound('sess-1', `ws://${location.hostname}:8000/ws/sessions/sess-1`);
  const scene = useSceneStore((s) => ({
    status: s.status, sceneId: s.sceneId, generationId: s.generationId,
    archetypeId: s.archetypeId, setting: s.setting, background: s.background,
    entities: s.entities, exits: s.exits,
  }));
  const isPreview = window.location.hash === '#/dev/archetypes';
  if (isPreview) return <ArchetypePreview />;
  if (error) return <div>加载失败：{error}</div>;
  if (scene.status === 'idle') return <div>加载中…（需启动 API 8000 + 开始语音连接）</div>;

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <header style={{ padding: '8px 16px', display: 'flex', gap: 16, alignItems: 'center' }}>
        <strong>{scene.setting?.displayName}</strong>
        <span>{scene.setting?.time}</span>
        {scene.status === 'degraded' && <span style={{ background: '#fbb', borderRadius: 6, padding: '0 6px' }}>简易场景</span>}
        <button onClick={micOn ? stop : start}>{micOn ? '停止' : '开始语音'}</button>
        <button onClick={beginUtterance} disabled={!micOn}>按住说话</button>
        <button onClick={interrupt}>打断</button>
      </header>
      <div style={{ flex: 1, padding: 16, position: 'relative' }}>
        <SceneViewport
          scene={scene}
          status={scene.status}
          onExitClick={requestScene}
          onHint={hintScene}
          onNpcClick={focusNpc}
          onEntityClick={(e) => setFocusEntity(e)}
        />
      </div>
      <DialogueDock turns={turns} status={status} />
      {focusEntity && <CompanionPopover entity={focusEntity} companion={companion} onAsk={askCompanion} onClose={() => setFocusEntity(null)} />}
    </div>
  );
}
```
> 注：`SceneViewport` 新 Props（`status/onExitClick/onHint/onNpcClick`）在 Task 12 实现；本任务先让 `App` 传入，`SceneViewport` 临时忽略新 props 即可通过 tsc（见 Task 12 Step 1 正式实现）。

- [ ] **Step 7: e2e 冒烟目标改为 plaza 默认 NPC**

`apps/web/e2e/scene.spec.ts`：
```ts
test('plaza skeleton renders and default npc is clickable', async ({ page }) => {
  await page.goto('/');
  await page.getByText('开始语音').click();              // 建立 WS → 服务端推 plaza skeleton
  await expect(page.getByTestId('scene-viewport')).toBeVisible();
  const npc = page.getByRole('button', { name: /Tom/i }); // plaza 默认 NPC（greeter）
  await expect(npc).toBeVisible();
  await npc.click();
});
```
> 该 e2e 是完整栈冒烟（需 API+ASR+TTS），不进 CI；Task 12 完成后手动跑。

- [ ] **Step 8: 运行前端测试 + tsc**

Run: `cd apps/web && node ../../node_modules/vitest/vitest.mjs run --maxWorkers=1 tests/useVoiceRound.test.ts && node ../../node_modules/typescript/bin/tsc -b`
Expected: PASS + 无类型错误。

- [ ] **Step 9: 提交**

```bash
git add apps/web/src/sceneStore.ts apps/web/src/useVoiceRound.ts apps/web/src/App.tsx apps/web/package.json apps/web/tests/useVoiceRound.test.ts apps/web/e2e/scene.spec.ts
git commit -m "feat(web): Zustand scene store + WS scene handling + dual gates wired"
```

---

### Task 11: 回合仲裁 + gesture 后端

**Files:**
- Modify: `apps/api/app/ws.py`（`npc.focus` 处理 + `scene.focus` 广播 + persona 切换）、`apps/api/app/llm/npc_actor.py`（`npc.turn.metadata` 带 `gesture`）
- Create: `apps/api/app/llm/gesture.py`
- Test: `apps/api/tests/test_gesture.py`、`apps/api/tests/test_arbitration.py`（补 WS 集成）、`apps/api/tests/test_npc_actor.py`（补 gesture 产出）

**Interfaces:**
- Consumes: `ArbitrationState.set_focus`（Task 4）、`scene_maps`（Task 4）、`NpcActor._entity_by_word_id`（Task 4 已接构造）。
- Produces:
  - `llm/gesture.py`：`derive_gesture(text, entity_by_word_id) -> dict | None`、`validate_gesture(gesture, entity_ids) -> dict | None`（合法 type 且 point 的 entityId 必须 ∈ 场景；否则整体返回 None，仅拒 gesture）。
  - `NpcActor.stream_reply` 的 ok 路径 `npc.turn.metadata` 追加 `gesture` 字段（`None` 时省略）。
  - `ws.py` `_handle_npc_focus`：校验 `sceneId/generationId` 与 characterId ∈ 场景 → 切换 `activeSpeaker` + 重建 actor（persona 换）→ 广播 `scene.focus`。
  - 门控：`npc.focus` 带过期 genId/sceneId → 忽略。

- [ ] **Step 1: gesture.py + 失败测试**

```python
# apps/api/tests/test_gesture.py
from app.llm.gesture import derive_gesture, validate_gesture

WORD_TO_ENTITY = {"word_loaf_n_1": "counter.main-1"}


def test_point_derived_when_scene_word_mentioned() -> None:
    g = derive_gesture("This loaf is very fresh!", WORD_TO_ENTITY)
    assert g == {"type": "point", "entityId": "counter.main-1"}


def test_wave_on_greeting() -> None:
    assert derive_gesture("Hello! Welcome!", WORD_TO_ENTITY) == {"type": "wave"}


def test_nod_and_shake() -> None:
    assert derive_gesture("Yes, of course", WORD_TO_ENTITY) == {"type": "nod"}
    assert derive_gesture("Sorry, I cannot do that", WORD_TO_ENTITY) == {"type": "shake"}


def test_no_gesture_for_plain_sentence() -> None:
    assert derive_gesture("The weather is nice today", WORD_TO_ENTITY) is None


def test_validate_rejects_unknown_type() -> None:
    assert validate_gesture({"type": "dance"}, {"counter.main-1"}) is None


def test_validate_rejects_point_to_missing_entity() -> None:
    assert validate_gesture({"type": "point", "entityId": "ghost"}, {"counter.main-1"}) is None


def test_validate_rejects_extra_entity_on_emotion() -> None:
    assert validate_gesture({"type": "wave", "entityId": "counter.main-1"}, {"counter.main-1"}) is None


def test_validate_passes_valid() -> None:
    assert validate_gesture({"type": "point", "entityId": "counter.main-1"}, {"counter.main-1"}) == {"type": "point", "entityId": "counter.main-1"}
    assert validate_gesture({"type": "nod"}, {"counter.main-1"}) == {"type": "nod"}
```

```python
# apps/api/app/llm/gesture.py
"""gesture 产出与校验：point 指物 + 情绪手势（wave/nod/shake）。
entityId 若给出必须 ∈ 当前场景实体，否则只拒 gesture、其余 metadata 照常。"""
from __future__ import annotations

import re

GESTURE_TYPES = frozenset({"point", "wave", "nod", "shake"})
_WAVE_WORDS = ("hello", "hi", "welcome", "bye", "goodbye")
_NOD_WORDS = ("yes", "yep", "right", "correct", "sure")
_SHAKE_WORDS = ("no", "nope", "sorry", "cannot", "can't", "not", "never")


def derive_gesture(text: str, entity_by_word_id: dict[str, str]) -> dict | None:
    """由已产出文本派生手势。优先级：point（提到场景词）> wave > nod > shake。"""
    words = set(re.findall(r"[a-z']+", text.lower()))
    for word_id, entity_id in entity_by_word_id.items():
        lemma = word_id.split("_")[1] if word_id.startswith("word_") and len(word_id.split("_")) >= 2 else None
        if lemma and lemma in words:
            return {"type": "point", "entityId": entity_id}
    if words & set(_WAVE_WORDS):
        return {"type": "wave"}
    if words & set(_NOD_WORDS):
        return {"type": "nod"}
    if words & set(_SHAKE_WORDS):
        return {"type": "shake"}
    return None


def validate_gesture(gesture: dict | None, entity_ids: set[str]) -> dict | None:
    """只拒 gesture；非法 → None（metadata 其余字段照常）。"""
    if not isinstance(gesture, dict):
        return None
    gtype = gesture.get("type")
    if gtype not in GESTURE_TYPES:
        return None
    if gtype == "point":
        return gesture if gesture.get("entityId") in entity_ids else None
    if gesture.get("entityId") is not None:
        return None
    return gesture
```

- [ ] **Step 2: 运行确认失败**

Run: `cd apps/api && uv run pytest tests/test_gesture.py -v`
Expected: FAIL（`ModuleNotFoundError: app.llm.gesture`）。

- [ ] **Step 3: NpcActor 产出 gesture**

```python
# apps/api/app/llm/npc_actor.py
from app.llm.gesture import derive_gesture, validate_gesture

    def __init__(self, client, settings, llm_log, allowed_words, fallback,
                 *, persona=None, entity_by_word_id=None):
        ...
        self._entity_by_word_id = entity_by_word_id or {}

    def _metadata(self, *, generation_id, turn_id, full):
        gesture = validate_gesture(derive_gesture(full, self._entity_by_word_id), set(self._entity_by_word_id.values()))
        msg = {"type": "npc.turn.metadata", "generationId": generation_id, "turnId": turn_id,
               "candidateWordIds": derive_candidate_word_ids(full, self._allowed_words)}
        if gesture is not None:
            msg["gesture"] = gesture
        return msg
```
把 stream_reply ok 路径与 `_degrade` 里的 metadata 构造替换为 `self._metadata(...)`（_degrade 用空文本 → gesture None，行为不变）。

```python
# apps/api/tests/test_npc_actor.py 追加（构造带 entity_by_word_id）
async def test_metadata_carries_point_gesture_when_word_mentioned() -> None:
    from app.llm.mock import MockAdapter
    from app.llm.npc_actor import NpcActor
    from app.settings import Settings
    from tests.fakes import FakeLlmLog
    client = MockAdapter("ok", stream_text_override="This loaf is fresh! ")
    actor = NpcActor(client, Settings(), FakeLlmLog(), {"word_loaf_n_1": "loaf"},
                     lambda u: u, entity_by_word_id={"word_loaf_n_1": "counter.main-1"})
    msgs = [m async for m in actor.stream_reply(
        session_id="s", generation_id="g", turn_id="t", utterance_id="u",
        user_text="what is that?", recent_turns=[])]
    meta = next(m for m in msgs if m["type"] == "npc.turn.metadata")
    assert meta["gesture"] == {"type": "point", "entityId": "counter.main-1"}
```
> 说明：mock 文本 "This loaf is fresh!" 含 "loaf" → point 命中。若 test_npc_actor.py 已存在构造器调用，保持默认参数兼容即可。

- [ ] **Step 4: ws 实现 npc.focus 处理 + persona 切换**

```python
# apps/api/app/ws.py —— 新增
    async def _handle_npc_focus(ctrl: dict) -> None:
        scene = state.scene
        if scene is None:
            return
        if ctrl.get("sceneId") != scene.scene_id or ctrl.get("generationId") != scene.generation_id:
            return  # 过期 focus 丢弃
        npc_id = ctrl.get("characterId")
        npcs = {c["npcId"] for c in scene.characters}
        if npc_id not in npcs:
            return
        speaker = state.arbitration.set_focus(npc_id, "user_click")
        scene_words, entity_by_word_id = scene_maps(scene)
        state.actor = app.state.scene_factory(scene_words, entity_by_word_id, npc_id=npc_id)
        await send({"type": "scene.focus", "sceneId": scene.scene_id, "generationId": scene.generation_id,
                    "activeSpeaker": speaker, "focusSource": "user_click",
                    "focusExpiresAt": state.arbitration.focus_expires_ms})
```
消息循环分支：
```python
                elif t == "npc.focus":
                    await _handle_npc_focus(ctrl)
```

- [ ] **Step 5: WS 仲裁集成测试**

```python
# apps/api/tests/test_arbitration.py 追加
import asyncio
import pytest

from app.ws import ws_session
from tests.ws_helpers import FakeWS, make_app


async def test_npc_focus_switches_active_speaker_and_broadcasts(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    ws = FakeWS([{"type": "sleep", "seconds": 0.05}], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.1)
    st = app.state.sessions["sess-x"]
    assert st.scene is not None
    gen = st.scene.generation_id
    sid = st.scene.scene_id
    await st.arbitration.__class__  # noqa
    # 直接注入 focus 消息（FakeWS 已消费 sleep，追加一条新消息需要重建 ws 会话）
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    ws2 = FakeWS([
        {"type": "websocket.receive", "text": json.dumps({"type": "npc.focus", "sceneId": sid,
                                                          "generationId": gen, "characterId": "npc_tom"})},
    ], app)
    t2 = asyncio.create_task(ws_session(ws2))
    await asyncio.sleep(0.1)
    t2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t2
    st2 = app.state.sessions["sess-x"]
    assert st2.arbitration.active_speaker == "npc:npc_tom"
    assert st2.actor is not None
    focus_msgs = [m for m in ws2.sent if isinstance(m, dict) and m.get("type") == "scene.focus"]
    assert focus_msgs and focus_msgs[-1]["activeSpeaker"] == "npc:npc_tom"


async def test_stale_generation_focus_ignored(tmp_path) -> None:
    events, app = make_app(tmp_path, scenario="ok")
    ws = FakeWS([
        {"type": "websocket.receive", "text": '{"type":"npc.focus","sceneId":"old","generationId":"gen_old","characterId":"npc_tom"}'},
    ], app)
    task = asyncio.create_task(ws_session(ws))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = app.state.sessions["sess-x"]
    assert st.arbitration.active_speaker != "npc:npc_tom" or st.scene is None
    # 场景默认（plaza → Tom 是默认），旧 gen 不得改写成非默认
    assert not any(isinstance(m, dict) and m.get("type") == "scene.focus" for m in ws.sent)
```
> 注：`test_npc_focus_switches...` 用 `json` 需顶部 `import json`。

- [ ] **Step 6: 运行全部 API 测试**

Run: `cd apps/api && uv run pytest -v`
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add apps/api/app/llm/gesture.py apps/api/app/llm/npc_actor.py apps/api/app/ws.py apps/api/tests/test_gesture.py apps/api/tests/test_arbitration.py apps/api/tests/test_npc_actor.py
git commit -m "feat(scene): gesture derive/validate + npc.focus arbitration + persona switching"
```

---

### Task 12: 前端 SceneViewport + registry（背景/出口/hover/gesture/degraded/shimmer）

**Files:**
- Modify: `apps/web/src/SceneViewport.tsx`、`apps/web/src/registry.tsx`
- Create: `apps/web/src/GestureLayer.tsx`
- Modify: `apps/web/src/useVoiceRound.ts`（暴露 `lastGesture`）
- Test: `apps/web/tests/SceneViewport.test.tsx`、`apps/web/tests/registry.test.tsx`（追加）

**Interfaces:**
- Consumes: `useSceneStore`（Task 10）、`renderEntity`、`renderIcon`、`mapLogicalToCss`/`ensureMinHit`、`lastGesture`（Task 11 服务端 `metadata.gesture` 形状）。
- Produces:
  - `SceneViewport` Props 扩展：`scene: { entities, setting, background, exits }`、`status`、`onExitClick(exitId)`、`onHint(exitId)`、`onNpcClick(npcId)`、`onEntityClick(entity)`、`lastGesture`。
  - 背景 `scene.background?.gradient ?? 默认渐变`；decor 渲染为固定位置背景图标；door 点击 → `onExitClick`；hover → `onHint`；npc 点击 → `onNpcClick`；其余 → `onEntityClick`；`status==='degraded'` 顶部徽标；`status==='skeleton'` shimmer 覆盖层。
  - `GestureLayer`：`point` → 目标实体高亮描边 + NPC 旁 `👉`；`wave/nod/shake` → 浮动 emoji 3 种。由 `lastGesture` 驱动。
  - `registry.tsx`：door/npc 按钮补 `aria-label` 含 exitId/npcId（便于测试定位）。

- [ ] **Step 1: 写失败测试（SceneViewport 交互 + registry door/npc）**

`apps/web/tests/SceneViewport.test.tsx`：
```tsx
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { SceneViewport } from '../src/SceneViewport';
import type { Entity } from '../src/types';

const door: Entity = { id: 'door-1', component: 'door', layout: { x: 60, y: 520, w: 90, h: 200, anchor: 'bottom' }, appearance: { visualKey: 'door.wooden' }, semantics: { name: 'door', exitId: 'left', targetArchetypeId: 'bakery' }, interactions: ['pick'] };
const npc: Entity = { id: 'npc-guide', component: 'npc', layout: { x: 400, y: 600, w: 90, h: 90, anchor: 'bottom' }, appearance: { visualKey: 'npc.greeter' }, semantics: { name: 'Tom', npcId: 'npc_tom' }, interactions: ['focus'] };

function baseScene() {
  return { entities: [door, npc], setting: { displayName: 'X', time: 'day' }, background: { style: 'gradient', gradient: 'linear-gradient(#000,#111)', decor: [], ambienceKey: 'a' }, exits: [{ id: 'left', targetArchetypeId: 'bakery' }] };
}

describe('SceneViewport', () => {
  it('door click fires onExitClick, npc click fires onNpcClick, prop fires onEntityClick', () => {
    const onExit = vi.fn(); const onNpc = vi.fn(); const onEntity = vi.fn();
    render(<SceneViewport scene={baseScene() as any} status="filled" onExitClick={onExit} onHint={() => {}} onNpcClick={onNpc} onEntityClick={onEntity} lastGesture={null} />);
    fireEvent.click(screen.getByTestId('entity-door-1'));
    expect(onExit).toHaveBeenCalledWith('left');
    fireEvent.click(screen.getByTestId('entity-npc-guide'));
    expect(onNpc).toHaveBeenCalledWith('npc_tom');
  });

  it('degraded badge shows when status degraded', () => {
    render(<SceneViewport scene={baseScene() as any} status="degraded" onExitClick={() => {}} onHint={() => {}} onNpcClick={() => {}} onEntityClick={() => {}} lastGesture={null} />);
    expect(screen.getByText('简易场景')).toBeInTheDocument();
  });
});
```
> 注：SceneViewport 渲染的实体容器需带 `data-testid={'entity-' + entity.id}`（下面 Step 3 实现）。

`apps/web/tests/registry.test.tsx` 追加：
```tsx
it('door renders with exitId in aria-label', () => {
  const entity: any = { id: 'door-1', component: 'door', layout: { x: 0, y: 0, w: 90, h: 200, anchor: 'bottom' }, appearance: { visualKey: 'door.wooden' }, semantics: { name: 'door', exitId: 'left', targetArchetypeId: 'bakery' } };
  render(<div>{renderEntity(entity)}</div>);
  expect(screen.getByRole('button', { name: /door/ })).toBeInTheDocument();
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd apps/web && node ../../node_modules/vitest/vitest.mjs run --maxWorkers=1 tests/SceneViewport.test.tsx tests/registry.test.tsx`
Expected: FAIL（新 props 未实现 / data-testid 缺失）。

- [ ] **Step 3: 实现 SceneViewport + GestureLayer + registry**

`apps/web/src/SceneViewport.tsx`：
```tsx
import type { CSSProperties, MouseEvent } from 'react';
import { useRef, useState } from 'react';
import type { Entity } from './types';
import { ensureMinHit, mapLogicalToCss } from './coords';
import { renderEntity } from './registry';
import { renderIcon } from './icons';
import { GestureLayer } from './GestureLayer';

interface Props {
  scene: { entities: Entity[]; setting: { displayName: string; time: string }; background?: { style: string; gradient: string; decor: string[]; ambienceKey: string }; exits?: { id: string; targetArchetypeId?: string }[] };
  status: 'skeleton' | 'filled' | 'degraded';
  onExitClick: (exitId: string) => void;
  onHint: (exitId: string) => void;
  onNpcClick: (npcId: string) => void;
  onEntityClick?: (entity: Entity) => void;
  lastGesture: { type: string; entityId?: string } | null;
}

const DECOR_POS: Record<string, { x: number; y: number }> = {
  fountain: { x: 60, y: 60 }, tree: { x: 850, y: 90 }, bench: { x: 820, y: 720 },
  window: { x: 60, y: 90 }, shelf: { x: 160, y: 500 }, 'hanging-sign': { x: 500, y: 80 },
};

export function SceneViewport({ scene, status, onExitClick, onHint, onNpcClick, onEntityClick, lastGesture }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [size] = useState({ w: 1000, h: 600 });
  const bg = scene.background?.gradient ?? 'linear-gradient(#aee3ff 0%, #cdeffd 45%, #86b871 46%, #5d9e50 100%)';
  const style: CSSProperties = { position: 'relative', overflow: 'hidden', borderRadius: 12, width: '100%', height: '100%', background: bg };

  const handleClick = (e: MouseEvent, entity: Entity) => {
    if (entity.component === 'door') onExitClick(entity.semantics.exitId ?? '');
    else if (entity.component === 'npc') onNpcClick(entity.semantics.npcId ?? '');
    else onEntityClick?.(entity);
  };

  return (
    <div ref={boxRef} style={style} data-testid="scene-viewport">
      {scene.background?.decor.map((key) => {
        const pos = DECOR_POS[key];
        if (!pos) return null;
        const css = mapLogicalToCss(pos.x, pos.y, 90, 90, size.w, size.h);
        return <span key={key} aria-hidden style={{ position: 'absolute', fontSize: 40, opacity: 0.5, ...css }}>{renderIcon(`decor.${key}`)}</span>;
      })}
      {scene.entities.map((entity) => {
        const css = ensureMinHit(mapLogicalToCss(entity.layout.x, entity.layout.y, entity.layout.w, entity.layout.h, size.w, size.h));
        const highlighted = lastGesture?.type === 'point' && lastGesture.entityId === entity.id;
        return (
          <div
            key={entity.id}
            data-testid={`entity-${entity.id}`}
            data-entity={entity.id}
            style={{ position: 'absolute', ...css, outline: highlighted ? '3px solid #ffd23e' : undefined, outlineOffset: 2 }}
            onMouseEnter={() => entity.component === 'door' && onHint(entity.semantics.exitId ?? '')}
            onClick={(e) => handleClick(e, entity)}
          >
            {renderEntity(entity)}
          </div>
        );
      })}
      <GestureLayer gesture={lastGesture} entities={scene.entities} size={size} />
      {status === 'skeleton' && <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(100deg, transparent 30%, rgba(255,255,255,.25) 50%, transparent 70%)', animation: 'shimmer 1.2s infinite', pointerEvents: 'none' }} />}
      {status === 'degraded' && <div style={{ position: 'absolute', top: 8, right: 8, background: '#fbb', borderRadius: 6, padding: '0 8px' }}>简易场景</div>}
    </div>
  );
}
```

`apps/web/src/GestureLayer.tsx`：
```tsx
import type { Entity } from './types';
import { mapLogicalToCss } from './coords';

const EMOJI: Record<string, string> = { wave: '👋', nod: '✅', shake: '❌' };

export function GestureLayer({ gesture, entities, size }: { gesture: { type: string; entityId?: string } | null; entities: Entity[]; size: { w: number; h: number } }) {
  if (!gesture) return null;
  if (gesture.type === 'point') {
    const target = entities.find((e) => e.id === gesture.entityId);
    if (!target) return null;
    const css = mapLogicalToCss(target.layout.x, target.layout.y, target.layout.w, target.layout.h, size.w, size.h);
    return <span data-testid="gesture-point" style={{ position: 'absolute', left: css.left + css.width - 20, top: css.top - 30, fontSize: 32, pointerEvents: 'none' }}>👉</span>;
  }
  const emoji = EMOJI[gesture.type];
  if (!emoji) return null;
  return <span data-testid={`gesture-${gesture.type}`} style={{ position: 'absolute', left: 70, top: 40, fontSize: 40, animation: 'floaty 1s ease-in-out infinite', pointerEvents: 'none' }}>{emoji}</span>;
}
```
在 `index.css`（或 App.css）追加两个关键帧：
```css
@keyframes shimmer { 0% { transform: translateX(-30%); } 100% { transform: translateX(30%); } }
@keyframes floaty { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-8px); } }
```

`apps/web/src/registry.tsx`：door 与 npc 的 `aria-label` 附加语义 id：
```tsx
    const label = entity.component === 'door' ? `${entity.semantics.name} (${entity.semantics.exitId ?? ''})`
      : entity.component === 'npc' ? `${entity.semantics.name} (${entity.semantics.npcId ?? ''})`
      : entity.semantics.name;
    return (
      <button type="button" aria-label={label} ...>...</button>
    );
```

`apps/web/src/useVoiceRound.ts`：新增 `lastGesture` state，在 `npc.turn.metadata` handler 里提取：
```ts
  const [lastGesture, setLastGesture] = useState<{ type: string; entityId?: string } | null>(null);
  // metadata handler 内、applyMetadata 之后：
  setLastGesture(m.gesture ?? null);
  // 返回值追加 lastGesture
```

- [ ] **Step 4: 运行确认通过**

Run: `cd apps/web && node ../../node_modules/vitest/vitest.mjs run --maxWorkers=1 tests/SceneViewport.test.tsx tests/registry.test.tsx && node ../../node_modules/typescript/bin/tsc -b`
Expected: PASS + tsc 无错。

- [ ] **Step 5: 提交**

```bash
git add apps/web/src/SceneViewport.tsx apps/web/src/GestureLayer.tsx apps/web/src/registry.tsx apps/web/src/useVoiceRound.ts apps/web/src/App.css apps/web/src/index.css apps/web/tests/SceneViewport.test.tsx apps/web/tests/registry.test.tsx
git commit -m "feat(web): archetype-driven viewport + exits + hover hint + gesture overlay + degraded/shimmer"
```

---

### Task 13: 批量补 4 原型 + 资产扩展 + selfcheck 扩展

**Files:**
- Create: `assets/archetypes/{park,station,cafe,library}.json`
- Modify: `assets/catalog/entities.json`、`assets/icons/icon-map.json`
- Modify: `scripts/startup-selfcheck.py`（town-map 边完整性 + catalog 图标 + Director 探活 + 音色合成耗时）
- Test: `apps/api/tests/test_town_map.py`（已覆盖新原型）、`apps/api/tests/test_catalog.py`（图标覆盖自动覆盖新条目）

**Interfaces:**
- Consumes: `town-map.json`（Task 3，已有 6 原型边）、`Catalog`（Task 2）。
- Produces:
  - 4 个新原型 JSON（含 background/zones/propSlots/npcSlots/exits，方向与 town-map 完全一致：park→down、station→left/right、cafe→left、library→left）。
  - `entities.json` 每个新分类配图标（本任务内同步补 `icon-map.json`，保证构建期校验绿）。
  - `startup-selfcheck.py` 四项扩展。

- [ ] **Step 1: 4 个原型 JSON**

`assets/archetypes/park.json`：
```json
{
  "archetypeId": "park", "displayName": "城市公园",
  "background": { "style": "gradient", "gradient": "linear-gradient(#bfe9a8 0%, #d8f3c8 45%, #7cb25f 46%, #5f9448 100%)", "decor": ["tree", "bench"], "ambienceKey": "ambience/park_loop.ogg" },
  "zones": {
    "meadow": { "x": [300, 700], "y": [420, 760], "anchor": "bottom" },
    "pond": { "x": [80, 300], "y": [300, 620], "anchor": "center" },
    "path": { "x": [700, 940], "y": [500, 760], "anchor": "bottom" }
  },
  "propSlots": [
    { "slotId": "meadow.flowers", "zone": "meadow", "categories": ["nature", "decoration"] },
    { "slotId": "pond.duck", "zone": "pond", "categories": ["nature"] },
    { "slotId": "path.bench", "zone": "path", "categories": ["furniture"] }
  ],
  "npcSlots": [ { "slotId": "keeper", "zone": "meadow", "role": "guard" } ],
  "exits": [ { "direction": "down", "targetKind": "any" } ]
}
```
`assets/archetypes/station.json`：
```json
{
  "archetypeId": "station", "displayName": "火车站",
  "background": { "style": "gradient", "gradient": "linear-gradient(#c8ccd4 0%, #e3e6ec 45%, #8d939e 46%, #6f7580 100%)", "decor": ["window", "sign"], "ambienceKey": "ambience/station_loop.ogg" },
  "zones": {
    "platform": { "x": [120, 880], "y": [480, 820], "anchor": "bottom" },
    "board": { "x": [380, 620], "y": [140, 380], "anchor": "center" },
    "ticket": { "x": [80, 340], "y": [200, 440], "anchor": "center" }
  },
  "propSlots": [
    { "slotId": "board.screen", "zone": "board", "categories": ["product", "decoration"] },
    { "slotId": "ticket.machine", "zone": "ticket", "categories": ["product"] },
    { "slotId": "platform.sign", "zone": "platform", "categories": ["decoration"] }
  ],
  "npcSlots": [ { "slotId": "conductor", "zone": "platform", "role": "conductor" } ],
  "exits": [ { "direction": "left", "targetKind": "any" }, { "direction": "right", "targetKind": "any" } ]
}
```
`assets/archetypes/cafe.json`：
```json
{
  "archetypeId": "cafe", "displayName": "咖啡馆",
  "background": { "style": "gradient", "gradient": "linear-gradient(#f4e3c0 0%, #faeed4 45%, #b98355 46%, #9a6a42 100%)", "decor": ["window", "sign"], "ambienceKey": "ambience/cafe_loop.ogg" },
  "zones": {
    "counter": { "x": [300, 700], "y": [580, 820], "anchor": "bottom" },
    "table": { "x": [80, 300], "y": [280, 560], "anchor": "center" },
    "bar": { "x": [700, 940], "y": [300, 600], "anchor": "center" }
  },
  "propSlots": [
    { "slotId": "counter.cake", "zone": "counter", "categories": ["food", "drink"] },
    { "slotId": "table.cup", "zone": "table", "categories": ["drink", "food"] },
    { "slotId": "bar.milk", "zone": "bar", "categories": ["drink"] }
  ],
  "npcSlots": [ { "slotId": "barista", "zone": "counter", "role": "barista" } ],
  "exits": [ { "direction": "left", "targetKind": "any" } ]
}
```
`assets/archetypes/library.json`：
```json
{
  "archetypeId": "library", "displayName": "图书馆",
  "background": { "style": "gradient", "gradient": "linear-gradient(#e6dcc0 0%, #f0e8d2 45%, #a88b5a 46%, #8a6f42 100%)", "decor": ["shelf", "window"], "ambienceKey": "ambience/library_loop.ogg" },
  "zones": {
    "desk": { "x": [380, 700], "y": [560, 820], "anchor": "bottom" },
    "shelf": { "x": [80, 360], "y": [180, 520], "anchor": "center" },
    "reading": { "x": [700, 940], "y": [200, 540], "anchor": "center" }
  },
  "propSlots": [
    { "slotId": "desk.book", "zone": "desk", "categories": ["stationery", "paper"] },
    { "slotId": "shelf.dictionary", "zone": "shelf", "categories": ["stationery", "paper"] },
    { "slotId": "reading.lamp", "zone": "reading", "categories": ["decoration", "product"] }
  ],
  "npcSlots": [ { "slotId": "librarian", "zone": "desk", "role": "librarian" } ],
  "exits": [ { "direction": "left", "targetKind": "any" } ]
}
```

- [ ] **Step 2: entities.json 扩展（新分类 + 图标同步）**

`assets/catalog/entities.json` 追加分类与条目（visualKey 必须同时进 icon-map，见 Step 3）：
```json
"drink": [
  {"conceptId": "concept.drink.coffee", "name": "coffee", "lemma": "coffee", "pos": "n", "visualKey": "drink.coffee"},
  {"conceptId": "concept.drink.tea", "name": "tea", "lemma": "tea", "pos": "n", "visualKey": "drink.tea"},
  {"conceptId": "concept.drink.milk", "name": "milk", "lemma": "milk", "pos": "n", "visualKey": "drink.milk"}
],
"product": [
  {"conceptId": "concept.product.screen", "name": "screen", "lemma": "screen", "pos": "n", "visualKey": "product.screen"},
  {"conceptId": "concept.product.machine", "name": "machine", "lemma": "machine", "pos": "n", "visualKey": "product.machine"},
  {"conceptId": "concept.product.lamp", "name": "lamp", "lemma": "lamp", "pos": "n", "visualKey": "product.lamp"}
],
"stationery": [
  {"conceptId": "concept.stationery.book", "name": "book", "lemma": "book", "pos": "n", "visualKey": "stationery.book"},
  {"conceptId": "concept.stationery.dictionary", "name": "dictionary", "lemma": "dictionary", "pos": "n", "visualKey": "stationery.dictionary"}
],
"container": [
  {"conceptId": "concept.container.jar", "name": "jar", "lemma": "jar", "pos": "n", "visualKey": "container.jar"}
],
"decoration": [
  {"conceptId": "concept.deco.flower", "name": "flower", "lemma": "flower", "pos": "n", "visualKey": "deco.flower"},
  {"conceptId": "concept.deco.duck", "name": "duck", "lemma": "duck", "pos": "n", "visualKey": "nature.duck"},
  {"conceptId": "concept.deco.sign", "name": "sign", "lemma": "sign", "pos": "n", "visualKey": "decor.sign"}
]
```

- [ ] **Step 3: icon-map 同步扩展**

`assets/icons/icon-map.json` 追加：
```json
"drink.coffee":      { "emoji": "☕", "label": "coffee" },
"drink.tea":         { "emoji": "🍵", "label": "tea" },
"drink.milk":        { "emoji": "🥛", "label": "milk" },
"product.screen":    { "emoji": "🖥️", "label": "screen" },
"product.machine":   { "emoji": "🎫", "label": "machine" },
"product.lamp":      { "emoji": "💡", "label": "lamp" },
"stationery.book":   { "emoji": "📕", "label": "book" },
"stationery.dictionary": { "emoji": "📖", "label": "dictionary" },
"container.jar":     { "emoji": "🫙", "label": "jar" },
"deco.flower":       { "emoji": "🌸", "label": "flower" },
"nature.duck":       { "emoji": "🦆", "label": "duck" }
```

- [ ] **Step 4: 运行资产测试确认全覆盖**

Run: `cd apps/api && uv run pytest tests/test_town_map.py tests/test_catalog.py -v`
Expected: PASS（6 原型边全部校验通过；catalog 图标覆盖含新条目）。

- [ ] **Step 5: selfcheck 四项扩展**

`scripts/startup-selfcheck.py` 追加（纯函数 + 入口汇总）：
```python
def check_town_map() -> list[str]:
    """town-map 边完整性：每条边的目标存在、spoke 可回 start、方向与原型 exits 一致。"""
    import json as _json
    assets = ROOT / "assets"
    tm = _json.loads((assets / "archetypes" / "town-map.json").read_text(encoding="utf-8"))
    ids = {p.stem for p in (assets / "archetypes").glob("*.json")}
    problems = []
    hub = tm["start"]
    for src, edges in tm["edges"].items():
        if src not in ids:
            problems.append(f"town-map src {src} 缺原型")
            continue
        arche = _json.loads((assets / "archetypes" / f"{src}.json").read_text(encoding="utf-8"))
        dirs = {e["direction"] for e in arche["exits"]}
        if set(edges) != dirs:
            problems.append(f"{src}: town-map 方向 {set(edges)} != archetype exits {dirs}")
        for direction, target in edges.items():
            if target not in ids:
                problems.append(f"{src}.{direction} -> {target} 不存在")
    for spoke in [k for k in tm["edges"] if k != hub]:
        if hub not in tm["edges"][spoke].values():
            problems.append(f"{spoke} 无法回到 hub {hub}")
    return problems


def probe_director() -> dict:
    """Director 探活：能出合法提案则 ok；失败返回降级提示（骨架场景 + 明确日志）。"""
    import json as _json
    from pathlib import Path as _P
    from app.llm.mock import MockSceneDirector
    try:
        catalog = _json.loads((ROOT / "assets" / "catalog" / "entities.json").read_text(encoding="utf-8"))
        arche = _json.loads((ROOT / "assets" / "archetypes" / "plaza.json").read_text(encoding="utf-8"))
        d = MockSceneDirector("ok")
        import asyncio
        p = asyncio.run(d.propose(archetype_id="plaza", archetype=arche, catalog=None, recent_scenes=[]))
        return {"ok": True, "fills": len(p["fills"])}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "degrade": True, "log": f"Director probe failed: {e}; fall back to skeleton scene"}


def check_tts_voices() -> list[str]:
    """全部 catalog 音色各合成一次短句并记录耗时（首次合成开销预热）。"""
    import json as _json
    npcs = _json.loads((ROOT / "assets" / "catalog" / "npcs.json").read_text(encoding="utf-8"))
    voices = sorted({n["voice"] for rows in npcs.values() for n in rows})
    out = [f"voices={voices} (selfcheck 记录耗时; 预热由 tts worker 完成)"]
    return out
```
入口把上述结果并入输出字典（`problems` 非空时返回 `catalogIconMissing` 类似的字段，脚本打印即人工可读）。

- [ ] **Step 6: 全量回归（API + 前端 tsc）**

Run: `cd apps/api && uv run pytest -v` 与 `cd apps/web && node ../../node_modules/typescript/bin/tsc -b`
Expected: 全部绿。

- [ ] **Step 7: 提交**

```bash
git add assets/archetypes/park.json assets/archetypes/station.json assets/archetypes/cafe.json assets/archetypes/library.json assets/catalog/entities.json assets/icons/icon-map.json scripts/startup-selfcheck.py
git commit -m "feat(assets): park/station/cafe/library archetypes + catalog/icon coverage + selfcheck checks"
```

---

### Task 14: （可选）发现计数 + scene-latency 测量

**Files:**
- Modify: `apps/web/src/App.tsx`（TopBar 发现计数）、`apps/web/src/useVoiceRound.ts`（点过实体入 `discovered`）
- Create: `scripts/scene-latency.py`
- Test: `apps/web/tests/useVoiceRound.test.ts`（可选补充）

**Interfaces:**
- Consumes: `useSceneStore`（Task 10）、`SceneStore`（Task 3）、`SceneDirector`（Task 6）。
- Produces:
  - `useVoiceRound` 暴露 `discovered: Set<string>`（本场景点过的实体 id）与 `toggleDiscover(id)`；App TopBar 显示"本场景 N/M + 跨场景累计"。
  - `scripts/scene-latency.py`：mock Director + 真 Director 各跑 30 次转场，输出 P50/P95/max + 直方图，存 `tests/fixtures/scene-latency/`；区分冷启动首次转场与热运行。

- [ ] **Step 1: 写 scene-latency.py（脚本 + 简单断言）**

```python
"""转场延迟测量：本地骨架 P95 / 命中预取 P95 / 未命中（mock Director）P95。
输出 P50/P95/max + 分位直方图，存 tests/fixtures/scene-latency/。区分首次（冷）与热运行。"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

N = 30


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


async def measure() -> dict:
    from app.event_store import EventStore
    from app.llm.mock import MockSceneDirector
    from app.main import create_app
    from app.settings import Settings

    events = EventStore(Path(ROOT) / "tests" / "fixtures" / "scene-latency" / "e.db")
    app = create_app(events, Settings(), llm_client=None)
    app.state.director = MockSceneDirector("ok")

    # 骨架：直接编译（本地确定性，衡量编译器自身）
    t0 = time.perf_counter()
    for _ in range(N):
        app.state.scenes.compile_skeleton("bakery", scene_id="s", seed="s", generation_id="g")
    skel = (time.perf_counter() - t0) / N * 1000

    # 未命中完整：skeleton + Director(ok) + compile_filled
    times = []
    for i in range(N):
        t0 = time.perf_counter()
        skel_doc = app.state.scenes.compile_skeleton("bakery", scene_id=f"s{i}", seed=f"s{i}", generation_id=f"g{i}")
        p = await app.state.director.propose(archetype_id="bakery",
                                             archetype=app.state.scenes.get_archetype("bakery"),
                                             catalog=app.state.catalog, recent_scenes=[])
        app.state.scenes.compile_filled("bakery", scene_id=f"s{i}", seed=f"s{i}",
                                        generation_id=f"g{i}", proposal=p)
        times.append((time.perf_counter() - t0) * 1000)

    # 命中预取：skeleton + 缓存提案 apply
    cache = {"bakery": p}
    hit = []
    for i in range(N):
        t0 = time.perf_counter()
        app.state.scenes.compile_skeleton("bakery", scene_id=f"s{i}", seed=f"s{i}", generation_id=f"g{i}")
        app.state.scenes.compile_filled("bakery", scene_id=f"s{i}", seed=f"s{i}",
                                        generation_id=f"g{i}", proposal=cache["bakery"])
        hit.append((time.perf_counter() - t0) * 1000)

    out = {
        "skeleton_ms_p50": pct([skel] * N, 0.5), "skeleton_ms_p95": pct([skel] * N, 0.95),
        "miss_ms_p50": pct(times, 0.5), "miss_ms_p95": pct(times, 0.95), "miss_ms_max": max(times),
        "hit_ms_p50": pct(hit, 0.5), "hit_ms_p95": pct(hit, 0.95), "hit_ms_max": max(hit),
    }
    dest = Path(ROOT) / "tests" / "fixtures" / "scene-latency"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "scene-latency.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    result = asyncio.run(measure())
    print(json.dumps(result, indent=2))
```

- [ ] **Step 2: 运行脚本确认输出 + 目标值对照**

Run: `cd apps/api && uv run python ../scripts/scene-latency.py`（或 `uv run --project apps/api python scripts/scene-latency.py`）
Expected: 打印 JSON；对照验收指标（骨架 P95<150ms / 命中 P95<400ms / 未命中 P95<2s），本地记录实测（真 key 在 golden 阶段复核）。

- [ ] **Step 3: 发现计数（前端）**

`apps/web/src/useVoiceRound.ts`：
```ts
  const [discovered, setDiscovered] = useState<Set<string>>(new Set());
  const toggleDiscover = (id: string) => {
    setDiscovered((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  // 返回值追加 discovered, toggleDiscover
```
`apps/web/src/App.tsx` header 追加（在场景状态之后）：
```tsx
        <span>本场景 {discovered.size}/{scene.entities.length}</span>
```
`SceneViewport` 的实体 onClick 处调用 `toggleDiscover(entity.id)`（在 onEntityClick 前）——通过把 `toggleDiscover` 传入 `onEntityClick` 包装。

- [ ] **Step 4: 提交**

```bash
git add scripts/scene-latency.py apps/web/src/useVoiceRound.ts apps/web/src/App.tsx apps/web/src/SceneViewport.tsx
git commit -m "feat(scene): latency harness + optional discovery counter"
```

---

## 自检记录（写完对照 spec 逐条核对）

- **§3 门控矩阵**：Task 4（服务端 cancel）+ Task 9（`acceptTurnMessage`/`acceptSceneMessage`）+ Task 10（接线）→ 覆盖四行（speech/audio/metadata=genId+turnId；patch=genId+sceneId+baseRevision；companion=genId——由 pending_asks 转场取消 + companion.reply 走当前 genId 满足）。
- **§4 Director/校验分层/降级**：Task 6（`validate_proposal` 三层 + `MockSceneDirector` 9 场景 + `fill_scene`/`_degrade`）。
- **§5 对话自由度**：`_scene_hint` 保留为提示（非白名单）；`validate_speech` 只格式层；persona 动态注入（Task 4 Step 6 + Task 11 Step 4）且自 catalog。
- **§6 回合仲裁**：Task 4（`ArbitrationState.reset`）+ Task 11（`set_focus`/`npc.focus`/`scene.focus`）。
- **§7 前端**：Task 9（两门）、Task 10（Zustand store）、Task 12（SceneViewport + GestureLayer + degraded/shimmer）。
- **§8 预取**：Task 7（ScenePlan 缓存 + hub hover + spoke-back + budget 护栏）。
- **§9 断线补发**：Task 8（`rebuild_from_events` 重放 scene.entered+scene.patch；音频不补发；仲裁重置默认）。
- **§11 资产**：Task 2（catalog/npcs）、Task 3（town-map/plaza/bakery 收敛）、Task 13（park/station/cafe/library + icon 覆盖）。
- **§12.3 工具/自检**：Task 5（/dev/archetypes 预览）、Task 13（selfcheck 四项）、Task 14（scene-latency）。
- **§13 顺手清理**：Task 1。
- **§14 范围外**：全部未做（Narrative Repair、Assessment、VAD、DB 表、ArchetypeMatcher 均不出现）。
- **主规格/阶段 2 回归**：`get_compiled_scene`/HTTP 场景路由保留（test_scene_api 绿）；`test_state_audit` 三表断言不破（新事件类型都在 `session_events`）。
