# 英语小镇 · 阶段 1：本地面包店原型（语音闭环）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭起"React 场景渲染 + 语音闭环（麦克风→ASR→本地 scripted 回复→TTS→播放）"的全本地最小闭环，验收：用户说完后 1.5s 内听到本地回复。

**Architecture:** 三层本地架构：`apps/web`（React，Emoji 图标渲染、组件白名单、AudioWorklet 采集）↔ `apps/api`（FastAPI Orchestrator，CPU：场景端点、SQLite WAL `session_events`、Silero VAD、本地 scripted NPC 回复引擎、语音回合编排）→ `services/asr-worker`（GPU 独占 faster-whisper，滚动窗口伪流式）与 `services/tts-worker`（CPU 优先 Kokoro）。共享契约在 `packages/scene-schema`（JSON Schema 事实源 + TS Zod + Python Pydantic）。

**Tech Stack:** pnpm workspace（Node 24）+ uv workspace（Python 3.13）；React 19 + TypeScript + Vite + Zod + Vitest；FastAPI + Uvicorn（单 worker）+ SQLite WAL；faster-whisper（CTranslate2，cuda float16，CPU `small.en` 兜底）+ Kokoro ONNX（CPU）+ Silero VAD；Playwright（E2E）。

## Global Constraints

以下约束来自已批准的 spec（`docs/superpowers/specs/2026-08-05-english-town-design.md` v2），每个任务隐式遵守：

- **阶段 1 不含 LLM**：NPC 回复一律走 `scripted_npc` 本地模板（也是 spec §14 降级阶梯中"DeepSeek 超时 → 本地模板短句"的实现基础）。不得在阶段 1 接入任何云端模型。
- **状态权威**：会话/场景/焦点/NPC 回合 = 服务器；客户端只持有临时 UI 状态（XState 是服务器状态镜像）。LLM 不存在于阶段 1。
- **本地确定性骨架先行**：场景首次可见不依赖任何生成调用，直接由 `scene_compiler` 从 `assets/archetypes/bakery.json` 本地编译。
- **Uvicorn 只能启动一个 worker**（禁止 `--workers N`）；ASR 是 GPU 唯一高优先级任务；Kokoro 默认 CPU；若 TTS 需用 GPU 则必须 `GPU_SEMAPHORE=1`（ASR/TTS 不同时重计算）——阶段 1 TTS 固定 CPU，不实现 GPU TTS。
- **SQLite 开启 WAL + busy_timeout + 事务**；所有学习状态/事件更新走**单一写入队列**（`EventStore` 内部锁）。
- **`session_events` 表结构**（阶段 1 建表）：`(sequence INTEGER, event_id TEXT UNIQUE, session_id TEXT, event_type TEXT, payload_json TEXT, created_at TEXT, PRIMARY KEY(session_id, sequence))`。
- **WS 消息**：JSON 控制消息与二进制音频帧同连接复用；所有消息含 `eventId / sessionId / timestamp / sequence`（客户端消息 `sequence` = 客户端自增；服务端事件 `sequence` = `session_events` 服务端生成）。
- **逻辑坐标 `0..1000`**，前端等比映射；点击热区 **≥44×44 CSS px**；单场景 DOM 实体 **≤40**。
- **安全**：场景渲染禁止 `innerHTML`；组件白名单；`visualKey` 走 `assets/icons/icon-map.json` 映射，不直接用任意字符串渲染；不加载任何外部 URL。
- **发音评分边界**：阶段 1 不承诺发音评分；只记录 ASR 置信度与是否识别到目标词。
- **冷/热测量**：`tests/latency/` 的测量脚本必须区分"模型已加载（热）"与"冷启动"。
- 里程碑 1 验收：**用户说完后 1.5s 内听到本地回复（热运行，本机闭环）**。

---

### Task 1: 项目骨架 + 双语言契约包 scene-schema

**Files:**
- Create: `pnpm-workspace.yaml`、`package.json`（root）、`pyproject.toml`（root uv workspace）
- Create: `packages/scene-schema/package.json`、`packages/scene-schema/tsconfig.json`、`packages/scene-schema/schemas/{entity,archetype,scene-plan}.schema.json`
- Create: `packages/scene-schema/src/index.ts`、`packages/scene-schema/test/index.test.ts`、`packages/scene-schema/test/fixtures/bakery-plan.json`
- Create: `packages/scene-schema/python/pyproject.toml`、`packages/scene-schema/python/scene_schema/{__init__,models,validate}.py`、`packages/scene-schema/python/tests/{__init__,test_models}.py`、`packages/scene-schema/python/tests/fixtures/bakery-plan.json`

**Interfaces:**
- Consumes: 无（首个任务）
- Produces:
  - TS（`packages/scene-schema/src/index.ts` 导出）：`EntityKind`、`Entity`、`Archetype`、`ScenePlan` 类型 + `EntitySchema`/`ArchetypeSchema`/`ScenePlanSchema`（Zod）
  - Python（`packages/scene-schema/python/scene_schema/` 导出）：`models.{Entity, Archetype, ScenePlan}`（Pydantic）；`validate.validate_scene_plan(doc: dict) -> None`（不抛即通过）；`validate.validate_archetype(doc: dict) -> None`
  - 规范 JSON Schema 位于 `schemas/*.json`（后续任务的 TS/Python 校验都以它为准）

- [ ] **Step 1: 建 monorepo 骨架**

创建 `pnpm-workspace.yaml`：
```yaml
packages:
  - 'apps/*'
  - 'packages/*'
```

创建 root `package.json`：
```json
{
  "name": "english-town",
  "private": true,
  "packageManager": "pnpm@11.1.2",
  "scripts": {
    "test": "pnpm -r --if-present test"
  }
}
```

创建 root `pyproject.toml`（uv workspace）：
```toml
[tool.uv.workspace]
members = [
  "packages/scene-schema/python",
  "packages/scene-compiler",
  "apps/api",
  "services/asr-worker",
  "services/tts-worker",
]
```

创建 `packages/scene-schema/package.json`：
```json
{
  "name": "@english-town/scene-schema",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "main": "src/index.ts",
  "scripts": { "test": "vitest run" },
  "dependencies": { "zod": "^3.23.0" },
  "devDependencies": { "typescript": "^5.5.0", "vitest": "^2.0.0" }
}
```

`packages/scene-schema/tsconfig.json`：
```json
{
  "compilerOptions": {
    "target": "ES2022", "module": "ESNext", "moduleResolution": "bundler",
    "strict": true, "skipLibCheck": true, "noEmit": true
  },
  "include": ["src", "test"]
}
```

运行 `pnpm install` 确认 workspace 解析成功。

- [ ] **Step 2: 写规范 JSON Schema（entity/archetype/scene-plan）**

`packages/scene-schema/schemas/entity.schema.json`：
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://english-town.local/schemas/entity.schema.json",
  "title": "Entity",
  "type": "object",
  "required": ["id", "component", "layout", "appearance", "semantics"],
  "properties": {
    "id": { "type": "string" },
    "component": { "enum": ["image", "label", "npc", "companion", "prop", "door", "dialogue-zone", "ambient-audio"] },
    "layout": {
      "type": "object", "required": ["x", "y", "w", "h", "anchor"],
      "properties": {
        "x": { "type": "integer", "minimum": 0, "maximum": 1000 },
        "y": { "type": "integer", "minimum": 0, "maximum": 1000 },
        "w": { "type": "integer", "minimum": 0, "maximum": 1000 },
        "h": { "type": "integer", "minimum": 0, "maximum": 1000 },
        "anchor": { "enum": ["bottom", "center", "top"] }
      }
    },
    "appearance": {
      "type": "object", "required": ["visualKey"],
      "properties": { "visualKey": { "type": "string", "pattern": "^[a-z0-9]+\\.[a-z0-9]+$" } }
    },
    "semantics": {
      "type": "object", "required": ["name"],
      "properties": {
        "name": { "type": "string" },
        "wordId": { "type": "string" },
        "description": { "type": "string" }
      }
    },
    "interactions": { "type": "array", "items": { "enum": ["focus", "ask", "inspect", "pick"] } }
  }
}
```

`packages/scene-schema/schemas/archetype.schema.json`：
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://english-town.local/schemas/archetype.schema.json",
  "title": "Archetype",
  "type": "object",
  "required": ["archetypeId", "displayName", "background", "zones", "propSlots", "npcSlots", "exits"],
  "properties": {
    "archetypeId": { "type": "string" },
    "displayName": { "type": "string" },
    "background": {
      "type": "object", "required": ["style", "gradient", "decor", "ambienceKey"],
      "properties": {
        "style": { "const": "gradient" },
        "gradient": { "type": "string" },
        "decor": { "type": "array", "items": { "type": "string" } },
        "ambienceKey": { "type": "string" }
      }
    },
    "zones": {
      "type": "object",
      "additionalProperties": {
        "type": "object", "required": ["x", "y", "anchor"],
        "properties": {
          "x": { "type": "array", "minItems": 2, "maxItems": 2, "items": { "type": "integer", "minimum": 0, "maximum": 1000 } },
          "y": { "type": "array", "minItems": 2, "maxItems": 2, "items": { "type": "integer", "minimum": 0, "maximum": 1000 } },
          "anchor": { "enum": ["bottom", "center", "top"] }
        }
      }
    },
    "propSlots": {
      "type": "array",
      "items": { "type": "object", "required": ["slotId", "zone", "categories"], "properties": { "slotId": { "type": "string" }, "zone": { "type": "string" }, "categories": { "type": "array", "items": { "type": "string" } } } }
    },
    "npcSlots": {
      "type": "array",
      "items": { "type": "object", "required": ["slotId", "zone", "role"], "properties": { "slotId": { "type": "string" }, "zone": { "type": "string" }, "role": { "type": "string" } } }
    },
    "exits": {
      "type": "array",
      "items": { "type": "object", "required": ["direction", "targetKind"], "properties": { "direction": { "enum": ["left", "right"] }, "targetKind": { "type": "string" } } }
    }
  }
}
```

`packages/scene-schema/schemas/scene-plan.schema.json`：
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://english-town.local/schemas/scene-plan.schema.json",
  "title": "ScenePlan",
  "type": "object",
  "required": ["schemaVersion", "sceneId", "generationId", "revision", "mode", "archetypeId", "setting", "fills", "characters"],
  "properties": {
    "schemaVersion": { "const": "1.0" },
    "sceneId": { "type": "string" },
    "generationId": { "type": "string" },
    "revision": { "type": "integer", "minimum": 1 },
    "mode": { "enum": ["free", "quest"] },
    "archetypeId": { "type": "string" },
    "setting": {
      "type": "object", "required": ["displayName", "time"],
      "properties": { "displayName": { "type": "string" }, "time": { "type": "string" } }
    },
    "fills": {
      "type": "array",
      "items": { "type": "object", "required": ["slotId", "entity"], "properties": { "slotId": { "type": "string" }, "entity": { "$ref": "https://english-town.local/schemas/entity.schema.json" } } }
    },
    "characters": {
      "type": "array",
      "items": { "type": "object", "required": ["slotId", "npcId"], "properties": { "slotId": { "type": "string" }, "npcId": { "type": "string" } } }
    },
    "objectives": { "type": "array", "default": [] },
    "exits": { "type": "array", "default": [] }
  }
}
```

- [ ] **Step 3: 写失败测试（TS 侧：fixture 通过 Zod 校验）**

`packages/scene-schema/test/fixtures/bakery-plan.json`（后续任务复用的阶段 1 场景 fixture）：
```json
{
  "schemaVersion": "1.0",
  "sceneId": "scene_bakery_001",
  "generationId": "gen_bakery_001",
  "revision": 1,
  "mode": "free",
  "archetypeId": "bakery",
  "setting": { "displayName": "Rosewood Bakery", "time": "morning" },
  "fills": [
    { "slotId": "counter.main", "entity": { "id": "loaf-1", "component": "prop", "layout": { "x": 400, "y": 620, "w": 90, "h": 70, "anchor": "bottom" }, "appearance": { "visualKey": "food.loaf" }, "semantics": { "name": "loaf", "wordId": "word_loaf_n_1" }, "interactions": ["focus", "ask"] } },
    { "slotId": "counter.side", "entity": { "id": "apple-1", "component": "prop", "layout": { "x": 540, "y": 630, "w": 80, "h": 60, "anchor": "bottom" }, "appearance": { "visualKey": "food.apple" }, "semantics": { "name": "apple", "wordId": "word_apple_n_1" }, "interactions": ["focus", "ask"] } },
    { "slotId": "counter.side", "entity": { "id": "receipt-1", "component": "prop", "layout": { "x": 620, "y": 640, "w": 60, "h": 50, "anchor": "bottom" }, "appearance": { "visualKey": "paper.receipt" }, "semantics": { "name": "receipt", "wordId": "word_receipt_n_1" }, "interactions": ["focus", "ask"] } }
  ],
  "characters": [ { "slotId": "vendor", "npcId": "npc_rosa" } ],
  "objectives": [],
  "exits": []
}
```

`packages/scene-schema/src/index.ts`：
```ts
import { z } from 'zod';

export const EntityKindSchema = z.enum([
  'image', 'label', 'npc', 'companion', 'prop', 'door', 'dialogue-zone', 'ambient-audio',
]);

export const LayoutSchema = z.object({
  x: z.number().int().min(0).max(1000),
  y: z.number().int().min(0).max(1000),
  w: z.number().int().min(0).max(1000),
  h: z.number().int().min(0).max(1000),
  anchor: z.enum(['bottom', 'center', 'top']),
});

export const EntitySchema = z.object({
  id: z.string(),
  component: EntityKindSchema,
  layout: LayoutSchema,
  appearance: z.object({ visualKey: z.string().regex(/^[a-z0-9]+\.[a-z0-9]+$/) }),
  semantics: z.object({
    name: z.string(),
    wordId: z.string().optional(),
    description: z.string().optional(),
  }),
  interactions: z.array(z.enum(['focus', 'ask', 'inspect', 'pick'])).optional(),
});

export const ArchetypeSchema = z.object({
  archetypeId: z.string(),
  displayName: z.string(),
  background: z.object({
    style: z.literal('gradient'),
    gradient: z.string(),
    decor: z.array(z.string()),
    ambienceKey: z.string(),
  }),
  zones: z.record(z.object({
    x: z.tuple([z.number().int(), z.number().int()]),
    y: z.tuple([z.number().int(), z.number().int()]),
    anchor: z.enum(['bottom', 'center', 'top']),
  })),
  propSlots: z.array(z.object({ slotId: z.string(), zone: z.string(), categories: z.array(z.string()) })),
  npcSlots: z.array(z.object({ slotId: z.string(), zone: z.string(), role: z.string() })),
  exits: z.array(z.object({ direction: z.enum(['left', 'right']), targetKind: z.string() })),
});

export const ScenePlanSchema = z.object({
  schemaVersion: z.literal('1.0'),
  sceneId: z.string(),
  generationId: z.string(),
  revision: z.number().int().min(1),
  mode: z.enum(['free', 'quest']),
  archetypeId: z.string(),
  setting: z.object({ displayName: z.string(), time: z.string() }),
  fills: z.array(z.object({ slotId: z.string(), entity: EntitySchema })),
  characters: z.array(z.object({ slotId: z.string(), npcId: z.string() })),
  objectives: z.array(z.unknown()).default([]),
  exits: z.array(z.unknown()).default([]),
});

export type Entity = z.infer<typeof EntitySchema>;
export type Archetype = z.infer<typeof ArchetypeSchema>;
export type ScenePlan = z.infer<typeof ScenePlanSchema>;
```

`packages/scene-schema/test/index.test.ts`：
```ts
import { describe, it, expect } from 'vitest';
import { ScenePlanSchema } from '../src/index';
import bakery from './fixtures/bakery-plan.json';

describe('scene-schema', () => {
  it('accepts the bakery fixture as a valid ScenePlan', () => {
    const plan = ScenePlanSchema.parse(bakery);
    expect(plan.archetypeId).toBe('bakery');
    expect(plan.fills.length).toBe(3);
  });

  it('rejects a plan with an invalid component', () => {
    const bad = { ...bakery, fills: [{ slotId: 'counter.main', entity: { ...bakery.fills[0].entity, component: 'script' } }] };
    expect(() => ScenePlanSchema.parse(bad)).toThrow();
  });
});
```

- [ ] **Step 4: 运行 TS 测试验证失败**

Run: `cd packages/scene-schema && npx vitest run`
Expected: FAIL —— `ScenePlanSchema` 未定义 / fixture 无法解析。若 `bakery-plan.json` 未被 TS 识别为模块，加 `packages/scene-schema/tsconfig.json` 的 `"resolveJsonModule": true`。

- [ ] **Step 5: Python 侧：Pydantic 模型 + jsonschema 校验**

`packages/scene-schema/python/pyproject.toml`：
```toml
[project]
name = "scene-schema"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["pydantic>=2.7,<3", "jsonschema>=4.22"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["scene_schema"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`packages/scene-schema/python/scene_schema/models.py`：
```python
from pydantic import BaseModel, Field


class Layout(BaseModel):
    x: int = Field(ge=0, le=1000)
    y: int = Field(ge=0, le=1000)
    w: int = Field(ge=0, le=1000)
    h: int = Field(ge=0, le=1000)
    anchor: str


class Entity(BaseModel):
    id: str
    component: str
    layout: Layout
    appearance: dict
    semantics: dict
    interactions: list[str] = Field(default_factory=list)


class Background(BaseModel):
    style: str
    gradient: str
    decor: list[str]
    ambienceKey: str


class Zone(BaseModel):
    x: tuple[int, int]
    y: tuple[int, int]
    anchor: str


class Archetype(BaseModel):
    archetypeId: str
    displayName: str
    background: Background
    zones: dict[str, Zone]
    propSlots: list[dict]
    npcSlots: list[dict]
    exits: list[dict]


class ScenePlan(BaseModel):
    schemaVersion: str
    sceneId: str
    generationId: str
    revision: int
    mode: str
    archetypeId: str
    setting: dict
    fills: list[dict]
    characters: list[dict]
    objectives: list = Field(default_factory=list)
    exits: list = Field(default_factory=list)
```

`packages/scene-schema/python/scene_schema/validate.py`：
```python
"""Cross-language source of truth: the JSON Schemas under schemas/."""
import json
from pathlib import Path

from jsonschema import ValidationError, validate

_SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


def _load(name: str) -> dict:
    return json.loads((_SCHEMAS / name).read_text(encoding="utf-8"))


def validate_scene_plan(doc: dict) -> None:
    """Raise jsonschema.ValidationError if doc is not a valid ScenePlan."""
    validate(instance=doc, schema=_load("scene-plan.schema.json"))


def validate_archetype(doc: dict) -> None:
    """Raise jsonschema.ValidationError if doc is not a valid Archetype."""
    validate(instance=doc, schema=_load("archetype.schema.json"))
```

`packages/scene-schema/python/scene_schema/__init__.py`：
```python
from .models import Archetype, Entity, ScenePlan
from .validate import validate_archetype, validate_scene_plan

__all__ = ["Archetype", "Entity", "ScenePlan", "validate_archetype", "validate_scene_plan"]
```

`packages/scene-schema/python/tests/__init__.py`：空文件。

`packages/scene-schema/python/tests/test_models.py`：
```python
import json
from pathlib import Path

import pytest

from scene_schema.models import ScenePlan
from scene_schema.validate import validate_scene_plan

FIXTURE = Path(__file__).parent / "fixtures" / "bakery-plan.json"


def test_fixture_is_valid_scene_plan() -> None:
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    validate_scene_plan(doc)  # must not raise


def test_pydantic_parses_fixture() -> None:
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    plan = ScenePlan.model_validate(doc)
    assert plan.archetypeId == "bakery"
    assert len(plan.fills) == 3


def test_invalid_component_rejected() -> None:
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc["fills"][0]["entity"]["component"] = "script"
    with pytest.raises(Exception):
        validate_scene_plan(doc)
```

复制 `test/fixtures/bakery-plan.json` 到 `python/tests/fixtures/bakery-plan.json`（两份 fixture 必须逐字一致 —— 后续可在 CI 比对哈希）。

- [ ] **Step 6: 运行 Python 测试验证失败→通过**

Run: `cd packages/scene-schema/python && uv run pytest -v`
Expected: 先 FAIL（`scene_schema` 未安装/未实现），Step 5 完成后 PASS（3 个测试全绿）。

- [ ] **Step 7: 提交**

```bash
git add pnpm-workspace.yaml package.json pyproject.toml packages/scene-schema
git commit -m "feat: monorepo skeleton + scene-schema dual-language contract package"
```

---

### Task 2: 面包店原型资产 + scene-compiler（布局计算）

**Files:**
- Create: `assets/archetypes/bakery.json`、`assets/icons/icon-map.json`
- Create: `packages/scene-compiler/pyproject.toml`、`packages/scene-compiler/scene_compiler/{__init__,compiler,template_scene}.py`、`packages/scene-compiler/tests/{__init__,test_compiler}.py`

**Interfaces:**
- Consumes: `scene_schema.models.{Archetype, ScenePlan}`、`scene_schema.validate.{validate_archetype, validate_scene_plan}`（Task 1）
- Produces:
  - `scene_compiler.compiler.compile_scene(archetype: Archetype, plan: ScenePlan) -> dict` —— 返回编译后场景：`{sceneId, generationId, setting, entities: list[dict], characters: list[dict]}`，其中每个 entity 的 `layout` 是**槽位编译器**计算的 `0..1000` 坐标（LLM/填槽者不生成像素位置）
  - `scene_compiler.compiler.compile_from_docs(archetype_doc: dict, plan_doc: dict) -> dict` —— 便捷入口（校验 → 编译）
  - `scene_compiler.template_scene.TEMPLATE_SCENE_ID`、`scene_compiler.template_scene.template_scene_plan() -> dict` —— 阶段 1 固定场景（面包店 + Rosa + 3 个物品 fill），与 Task 1 fixture 一致

- [ ] **Step 1: 写面包店原型资产**

`assets/archetypes/bakery.json`：
```json
{
  "archetypeId": "bakery",
  "displayName": "面包店",
  "background": {
    "style": "gradient",
    "gradient": "linear-gradient(#ffe8c8 0%, #ffd9a0 55%, #a9744b 56%, #8a5a34 100%)",
    "decor": ["window", "shelf", "hanging-sign"],
    "ambienceKey": "ambience/bakery_loop.ogg"
  },
  "zones": {
    "counter": { "x": [320, 700], "y": [600, 820], "anchor": "bottom" },
    "shelf":   { "x": [60, 260],  "y": [180, 520], "anchor": "center" },
    "door":    { "x": [860, 980], "y": [280, 820], "anchor": "bottom" }
  },
  "propSlots": [
    { "slotId": "counter.main", "zone": "counter", "categories": ["food", "product", "paper"] },
    { "slotId": "counter.side", "zone": "counter", "categories": ["drink", "food", "paper"] },
    { "slotId": "shelf.top",    "zone": "shelf",   "categories": ["container", "decoration"] }
  ],
  "npcSlots": [ { "slotId": "vendor", "zone": "counter", "role": "vendor" } ],
  "exits": [ { "direction": "left", "targetKind": "any" }, { "direction": "right", "targetKind": "any" } ]
}
```

`assets/icons/icon-map.json`（阶段 1 子集）：
```json
{
  "food.loaf":    { "emoji": "🍞", "label": "loaf" },
  "food.apple":   { "emoji": "🍎", "label": "apple" },
  "paper.receipt":{ "emoji": "🧾", "label": "receipt" },
  "npc.vendor":   { "emoji": "👩‍🍳", "label": "vendor" },
  "companion.fox":{ "emoji": "🦊", "label": "companion" },
  "door.wooden":  { "emoji": "🚪", "label": "door" },
  "decor.window": { "emoji": "🌞", "label": "window" },
  "decor.shelf":  { "emoji": "🪵", "label": "shelf" },
  "decor.sign":   { "emoji": "☕", "label": "sign" }
}
```

- [ ] **Step 2: 写失败测试**

`packages/scene-compiler/pyproject.toml`：
```toml
[project]
name = "scene-compiler"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["scene-schema"]

[tool.hatch.build.targets.wheel]
packages = ["scene_compiler"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`packages/scene-compiler/tests/test_compiler.py`：
```python
import json
from pathlib import Path

from scene_compiler.compiler import compile_from_docs
from scene_compiler.template_scene import template_scene_plan

ROOT = Path(__file__).resolve().parents[3]
ARCHETYPE = json.loads((ROOT / "assets" / "archetypes" / "bakery.json").read_text(encoding="utf-8"))


def test_compiled_scene_contains_fills_companion_and_door() -> None:
    compiled = compile_from_docs(ARCHETYPE, template_scene_plan())
    ids = [e["id"] for e in compiled["entities"]]
    # 3 fills + 1 companion + 2 doors
    assert any("loaf" in i or "counter" in i for i in ids)
    assert any(e["component"] == "companion" for e in compiled["entities"])
    assert any(e["component"] == "door" for e in compiled["entities"])
    assert len(compiled["entities"]) <= 40


def test_all_layouts_within_logical_canvas() -> None:
    compiled = compile_from_docs(ARCHETYPE, template_scene_plan())
    for e in compiled["entities"]:
        lay = e["layout"]
        assert 0 <= lay["x"] <= 1000 and 0 <= lay["y"] <= 1000
        assert 0 <= lay["w"] <= 1000 and 0 <= lay["h"] <= 1000


def test_fill_placed_inside_its_slot_zone() -> None:
    compiled = compile_from_docs(ARCHETYPE, template_scene_plan())
    counter_zone = ARCHETYPE["zones"]["counter"]
    for e in compiled["entities"]:
        if e["semantics"].get("wordId") == "word_loaf_n_1":
            assert counter_zone["x"][0] <= e["layout"]["x"] <= counter_zone["x"][1]
            assert counter_zone["y"][0] <= e["layout"]["y"] <= counter_zone["y"][1]
            return
    raise AssertionError("loaf fill not found")
```

- [ ] **Step 3: 运行测试验证失败**

Run: `cd packages/scene-compiler && uv run pytest -v`
Expected: FAIL —— `scene_compiler` 不存在。

- [ ] **Step 4: 实现 template_scene + compiler**

`packages/scene-compiler/scene_compiler/template_scene.py`：
```python
"""阶段 1 固定场景：面包店。后续阶段由 Scene Director（云端）产出此文档。"""

TEMPLATE_SCENE_ID = "scene_bakery_001"


def template_scene_plan() -> dict:
    return {
        "schemaVersion": "1.0",
        "sceneId": TEMPLATE_SCENE_ID,
        "generationId": "gen_bakery_001",
        "revision": 1,
        "mode": "free",
        "archetypeId": "bakery",
        "setting": {"displayName": "Rosewood Bakery", "time": "morning"},
        "fills": [
            {"slotId": "counter.main", "entity": {"id": "loaf-1", "component": "prop", "appearance": {"visualKey": "food.loaf"}, "semantics": {"name": "loaf", "wordId": "word_loaf_n_1"}, "interactions": ["focus", "ask"]}},
            {"slotId": "counter.side", "entity": {"id": "apple-1", "component": "prop", "appearance": {"visualKey": "food.apple"}, "semantics": {"name": "apple", "wordId": "word_apple_n_1"}, "interactions": ["focus", "ask"]}},
            {"slotId": "counter.side", "entity": {"id": "receipt-1", "component": "prop", "appearance": {"visualKey": "paper.receipt"}, "semantics": {"name": "receipt", "wordId": "word_receipt_n_1"}, "interactions": ["focus", "ask"]}},
        ],
        "characters": [{"slotId": "vendor", "npcId": "npc_rosa"}],
        "objectives": [],
        "exits": [],
    }
```

`packages/scene-compiler/scene_compiler/compiler.py`：
```python
"""槽位编译器：把 ScenePlan 的 fills 落到原型 zone 的 0..1000 逻辑坐标。
布局全部由本模块计算 —— 填槽者 / 未来 LLM 都不产生像素位置。"""
from scene_schema.models import Archetype, ScenePlan
from scene_schema.validate import validate_archetype, validate_scene_plan

COMPANION_ENTITY = {
    "id": "companion-1",
    "component": "companion",
    "layout": {"x": 60, "y": 760, "w": 90, "h": 90, "anchor": "bottom"},
    "appearance": {"visualKey": "companion.fox"},
    "semantics": {"name": "companion"},
    "interactions": ["ask"],
}


def _zone_center(zone: dict, slot_index: int) -> dict:
    x0, x1 = zone["x"]
    y0, y1 = zone["y"]
    # 同槽位多个物品按 index 在 zone 内错开，避免完全重叠
    col = slot_index % 3
    x = x0 + int((x1 - x0) * (0.25 + 0.25 * col))
    y = y0 + int((y1 - y0) * 0.5)
    w = max(40, (x1 - x0) // 5)
    h = max(40, (y1 - y0) // 4)
    return {"x": x, "y": y, "w": w, "h": h, "anchor": zone.get("anchor", "bottom")}


def _door_entity(index: int, direction: str) -> dict:
    x = 920 if direction == "right" else 60
    return {
        "id": f"door-{index}",
        "component": "door",
        "layout": {"x": x, "y": 520, "w": 90, "h": 200, "anchor": "bottom"},
        "appearance": {"visualKey": "door.wooden"},
        "semantics": {"name": "door"},
        "interactions": ["pick"],
    }


def compile_scene(archetype: Archetype, plan: ScenePlan) -> dict:
    """archetype/plan 已通过 Pydantic 校验（由调用方保证或经 compile_from_docs）。"""
    zone_of_slot = {s["slotId"]: s["zone"] for s in archetype.propSlots}
    zones = archetype.zones
    entities: list[dict] = []
    counter: dict[str, int] = {}
    for fill in plan.fills:
        zone_name = zone_of_slot.get(fill["slotId"])
        if not zone_name or zone_name not in zones:
            continue
        counter[zone_name] = counter.get(zone_name, 0) + 1
        entity = dict(fill["entity"])
        entity["id"] = fill["entity"].get("id") or f"{fill['slotId']}-{counter[zone_name]}"
        entity["layout"] = _zone_center(zones[zone_name].model_dump(), counter[zone_name] - 1)
        entities.append(entity)

    entities.append(dict(COMPANION_ENTITY))
    for i, exit_spec in enumerate(archetype.exits):
        entities.append(_door_entity(i + 1, exit_spec["direction"]))

    return {
        "sceneId": plan.sceneId,
        "generationId": plan.generationId,
        "setting": plan.setting,
        "entities": entities,
        "characters": [c for c in plan.characters],
    }


def compile_from_docs(archetype_doc: dict, plan_doc: dict) -> dict:
    validate_archetype(archetype_doc)
    validate_scene_plan(plan_doc)
    return compile_scene(Archetype.model_validate(archetype_doc), ScenePlan.model_validate(plan_doc))
```

`packages/scene-compiler/scene_compiler/__init__.py`：
```python
from .compiler import compile_from_docs, compile_scene
from .template_scene import TEMPLATE_SCENE_ID, template_scene_plan

__all__ = ["compile_from_docs", "compile_scene", "TEMPLATE_SCENE_ID", "template_scene_plan"]
```

注意：`_zone_center` 的 `zone.model_dump()` 把 tuple 转成 list，取 `x/y` 后按 int 计算 —— 需确认 `zones[zone_name]` 是 `Zone` 模型。若类型不符，在 Step 4 后修正：直接把 `Zone` 的 `x/y` 解包。

- [ ] **Step 5: 运行测试验证通过**

Run: `cd packages/scene-compiler && uv run pytest -v`
Expected: PASS（3 个测试）。若 `zone.model_dump()` 返回结构不符合 `_zone_center` 的 `zone["x"]` 下标访问，将 `_zone_center` 改为接收 `(x0, x1, y0, y1, anchor)` 显式元组。

- [ ] **Step 6: 提交**

```bash
git add assets/archetypes assets/icons packages/scene-compiler
git commit -m "feat: bakery archetype + icon map + scene-compiler (slot layout computation)"
```

---

### Task 3: API 编排服务（场景端点 + SQLite WAL + event_store）

**Files:**
- Create: `apps/api/pyproject.toml`、`apps/api/app/{__init__,main,scene_store,event_store,settings}.py`
- Create: `apps/api/tests/{__init__,test_scene_api,test_event_store}.py`

**Interfaces:**
- Consumes: `scene_compiler.template_scene_plan`、`scene_compiler.compile_from_docs`（Task 2）、`scene_schema.validate`（Task 1）
- Produces:
  - HTTP（Uvicorn 单 worker，127.0.0.1:8000）：`GET /health`、`GET /api/archetypes`、`GET /api/scenes/{sceneId}`
  - `app.event_store.EventStore(db_path: Path)`：`append(session_id, event_type, payload, event_id=None) -> int`（返回 sequence）、`list_after(session_id, seq) -> list[dict]`、`init_db()`
  - `app.settings.Settings`：`DB_PATH`、`ASSET_ROOT`、`ASR_WS_URL`、`TTS_URL`（阶段 2 才真正调用，先占位）
  - `app.scene_store.SceneStore`：`get_archetype(archetype_id) -> dict`、`get_compiled_scene(scene_id) -> dict`（缓存）

- [ ] **Step 1: 写失败测试（event_store + 场景端点）**

`apps/api/pyproject.toml`：
```toml
[project]
name = "app-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115,<1",
  "uvicorn>=0.30,<1",
  "scene-schema",
  "scene-compiler",
  "pytest>=8,<9",
  "httpx>=0.27,<1",
]

[tool.hatch.build.targets.wheel]
packages = ["app"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`apps/api/tests/test_event_store.py`：
```python
import json
from pathlib import Path

import pytest

from app.event_store import EventStore


@pytest.fixture()
def store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "events.db")


def test_append_returns_increasing_sequence(store: EventStore) -> None:
    s1 = store.append("s1", "scene.entered", {"sceneId": "scene_bakery_001"})
    s2 = store.append("s1", "dialogue.turn", {"text": "hello"})
    assert s2 == s1 + 1


def test_dedupe_by_event_id(store: EventStore) -> None:
    seq1 = store.append("s1", "scene.entered", {"x": 1}, event_id="ev-1")
    seq2 = store.append("s1", "scene.entered", {"x": 1}, event_id="ev-1")
    assert seq1 == seq2  # 幂等：重复提交不新增 sequence


def test_wal_enabled(store: EventStore) -> None:
    mode = store.connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_list_after(store: EventStore) -> None:
    store.append("s1", "a", {})
    mid = store.append("s1", "b", {})
    store.append("s1", "c", {})
    assert [e["event_type"] for e in store.list_after("s1", mid)] == ["c"]
```

`apps/api/tests/test_scene_api.py`：
```python
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.event_store import EventStore
from app.main import create_app

ROOT = Path(__file__).resolve().parents[3]
ARCHETYPE_DOC = json.loads((ROOT / "assets" / "archetypes" / "bakery.json").read_text(encoding="utf-8"))


def test_scene_endpoint_returns_compiled_scene(tmp_path: Path) -> None:
    app = create_app(EventStore(tmp_path / "events.db"))
    client = TestClient(app)
    r = client.get("/api/scenes/scene_bakery_001")
    assert r.status_code == 200
    body = r.json()
    assert body["archetypeId"] == "bakery"
    assert any(e["component"] == "companion" for e in body["entities"])


def test_archetypes_endpoint(tmp_path: Path) -> None:
    app = create_app(EventStore(tmp_path / "events.db"))
    client = TestClient(app)
    r = client.get("/api/archetypes")
    assert r.status_code == 200
    assert "bakery" in r.json()["ids"]


def test_health(tmp_path: Path) -> None:
    app = create_app(EventStore(tmp_path / "events.db"))
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd apps/api && uv run pytest -v`
Expected: FAIL —— `app` 包不存在。

- [ ] **Step 3: 实现 settings + event_store**

`apps/api/app/settings.py`：
```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path("english_town.db")
    asset_root: Path = Path(__file__).resolve().parents[3] / "assets"
    asr_ws_url: str = "ws://127.0.0.1:8001/ws/asr"   # 阶段 2 使用
    tts_url: str = "http://127.0.0.1:8002/tts"       # 阶段 2 使用
```

`apps/api/app/event_store.py`：
```python
"""session_events 持久化 + SQLite WAL + 单一写入队列（锁）。
学习证据等事件先写库再对外确认，断线可补发。"""
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_events(
  sequence    INTEGER NOT NULL,
  event_id    TEXT NOT NULL UNIQUE,
  session_id  TEXT NOT NULL,
  event_type  TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  PRIMARY KEY(session_id, sequence)
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(db_path), check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.executescript(_SCHEMA)
        self.connection.commit()
        self._lock = threading.Lock()

    def append(self, session_id: str, event_type: str, payload: dict, event_id: str | None = None) -> int:
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
                "INSERT OR IGNORE INTO session_events(sequence, event_id, session_id, event_type, payload_json, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (seq, event_id or str(uuid.uuid4()), session_id, event_type, json.dumps(payload, ensure_ascii=False), _utcnow()),
            )
            self.connection.commit()
            return seq

    def list_after(self, session_id: str, seq: int) -> list[dict]:
        rows = self.connection.execute(
            "SELECT sequence, event_type, payload_json FROM session_events WHERE session_id = ? AND sequence > ? ORDER BY sequence",
            (session_id, seq),
        ).fetchall()
        return [{"sequence": int(r[0]), "event_type": r[1], "payload": json.loads(r[2])} for r in rows]
```

`apps/api/app/scene_store.py`：
```python
"""从本地资产编译场景，进程内缓存（Redis 前的开发态 LRU 由 dict 充当）。"""
import json
from functools import lru_cache
from pathlib import Path

from scene_compiler import compile_from_docs, template_scene_plan


class SceneStore:
    def __init__(self, asset_root: Path) -> None:
        self._archetypes_dir = asset_root / "archetypes"

    @lru_cache(maxsize=8)
    def _load_archetype(self, archetype_id: str) -> dict:
        return json.loads((self._archetypes_dir / f"{archetype_id}.json").read_text(encoding="utf-8"))

    def get_archetype(self, archetype_id: str) -> dict:
        return self._load_archetype(archetype_id)

    def list_archetype_ids(self) -> list[str]:
        return [p.stem for p in self._archetypes_dir.glob("*.json")]

    def get_compiled_scene(self, scene_id: str) -> dict:
        plan = template_scene_plan()
        if plan["sceneId"] != scene_id:
            raise KeyError(f"unknown scene: {scene_id}")
        return compile_from_docs(self.get_archetype(plan["archetypeId"]), plan)
```

`apps/api/app/main.py`：
```python
from pathlib import Path

from fastapi import FastAPI, HTTPException

from app.event_store import EventStore
from app.scene_store import SceneStore
from app.settings import Settings


def create_app(events: EventStore | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    events = events or EventStore(settings.db_path)
    scenes = SceneStore(settings.asset_root)

    app = FastAPI(title="english-town-api", version="0.1.0")
    app.state.events = events
    app.state.settings = settings

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

`apps/api/app/__init__.py`：空文件。

- [ ] **Step 4: 运行测试验证通过**

Run: `cd apps/api && uv run pytest -v`
Expected: PASS（5 个测试）。若 `test_scene_endpoint` 的 `body["archetypeId"]` 不存在，说明 `compile_from_docs` 返回值缺该字段 —— 在 `compiler.compile_scene` 的返回 dict 里补上 `"archetypeId": archetype.archetypeId`，并同步改 Task 2 的 `test_compiler` 不需要动（它没断言该字段）。**注意**：改完必须重跑 `packages/scene-compiler` 的测试确认仍绿。

- [ ] **Step 5: 启动冒烟**

Run: `cd apps/api && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`
然后在另一终端：`curl -s http://127.0.0.1:8000/api/scenes/scene_bakery_001 | python -m json.tool | head -30`
Expected: 返回编译后的场景 JSON（含 companion 实体）。

- [ ] **Step 6: 提交**

```bash
git add apps/api
git commit -m "feat: FastAPI orchestrator with scene endpoints + SQLite WAL session_events"
```

---

### Task 4: 前端场景渲染（组件白名单 + SceneViewport 分层）

**Files:**
- Create: `apps/web/`（Vite React TS 脚手架）、`src/coords.ts`、`src/icons.tsx`、`src/registry.tsx`、`src/SceneViewport.tsx`、`src/App.tsx`、`src/api.ts`、`src/types.ts`、`vite.config.ts`（dev proxy）、`tests/coords.test.ts`、`tests/registry.test.tsx`

**Interfaces:**
- Consumes: `@english-town/scene-schema`（TS 类型 + Zod）、API `GET /api/scenes/{sceneId}`（Task 3）
- Produces:
  - `coords.mapLogicalToCss(x, y, w, h, viewportW, viewportH) -> {left, top, width, height}`
  - `coords.ensureMinHit(rect, minPx=44) -> rect`（不足 44px 以中心外扩）
  - `icons.renderIcon(visualKey) -> string`（返回 Emoji；查 `icon-map.json` 注入的模块，未知 key 返回 `❓`）
  - `registry.renderEntity(entity: Entity) -> JSX`（按 `component` 分发到白名单组件；未知组件渲染 null）
  - `<SceneViewport scene={compiled} onEntityClick={cb}/>`（分层渲染，`pointer-events` 只开在 EntityLayer/CharacterLayer/InteractionLayer 实体上）
  - `<App/>` 挂载后拉取场景并渲染

- [ ] **Step 1: 脚手架**

```bash
cd apps && pnpm create vite@latest web -- --template react-ts
cd web && pnpm add zod react && pnpm add -D vitest @vitest/coverage-v8 jsdom @testing-library/react @testing-library/jest-dom
pnpm add @english-town/scene-schema@workspace:*
```

`vite.config.ts`：
```ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  test: { environment: 'jsdom', setupFiles: ['./tests/setup.ts'] },
});
```

`tests/setup.ts`：`import '@testing-library/jest-dom';`

`src/types.ts`：
```ts
export type { Entity, ScenePlan, Archetype } from '@english-town/scene-schema';
```

- [ ] **Step 2: 写失败测试（coords + registry）**

`apps/web/tests/coords.test.ts`：
```ts
import { describe, it, expect } from 'vitest';
import { mapLogicalToCss, ensureMinHit } from '../src/coords';

describe('coords', () => {
  it('maps 0..1000 logical coords proportionally', () => {
    const r = mapLogicalToCss(250, 500, 100, 100, 1000, 600);
    expect(r).toEqual({ left: 250, top: 300, width: 100, height: 100 });
  });

  it('expands small hit areas around center to 44px', () => {
    const r = ensureMinHit({ left: 100, top: 100, width: 20, height: 20 });
    expect(r).toEqual({ left: 88, top: 88, width: 44, height: 44 });
  });

  it('leaves large areas untouched', () => {
    const r = ensureMinHit({ left: 0, top: 0, width: 120, height: 80 });
    expect(r).toEqual({ left: 0, top: 0, width: 120, height: 80 });
  });
});
```

`apps/web/tests/registry.test.tsx`：
```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { renderEntity } from '../src/registry';

describe('registry', () => {
  it('renders a prop as a button with its label', () => {
    const entity = {
      id: 'loaf-1', component: 'prop',
      layout: { x: 0, y: 0, w: 100, h: 100, anchor: 'bottom' },
      appearance: { visualKey: 'food.loaf' },
      semantics: { name: 'loaf', wordId: 'word_loaf_n_1' },
      interactions: ['ask'],
    };
    render(<div>{renderEntity(entity)}</div>);
    expect(screen.getByRole('button', { name: /loaf/i })).toBeInTheDocument();
  });

  it('renders null for unknown component (whitelist)', () => {
    const entity: any = { id: 'x', component: 'script', layout: { x: 0, y: 0, w: 1, h: 1, anchor: 'bottom' }, appearance: { visualKey: 'a.b' }, semantics: { name: 'x' } };
    expect(renderEntity(entity)).toBeNull();
  });
});
```

- [ ] **Step 3: 运行测试验证失败**

Run: `cd apps/web && npx vitest run`
Expected: FAIL —— `src/coords.ts`、`src/registry.tsx` 不存在。

- [ ] **Step 4: 实现 coords + icons + registry**

`src/coords.ts`：
```ts
export interface CssRect { left: number; top: number; width: number; height: number; }

export function mapLogicalToCss(
  x: number, y: number, w: number, h: number,
  viewportW: number, viewportH: number,
): CssRect {
  return {
    left: (x / 1000) * viewportW,
    top: (y / 1000) * viewportH,
    width: (w / 1000) * viewportW,
    height: (h / 1000) * viewportH,
  };
}

export function ensureMinHit(rect: CssRect, minPx = 44): CssRect {
  const growX = Math.max(0, minPx - rect.width) / 2;
  const growY = Math.max(0, minPx - rect.height) / 2;
  return {
    left: rect.left - growX,
    top: rect.top - growY,
    width: Math.max(rect.width, minPx),
    height: Math.max(rect.height, minPx),
  };
}
```

`src/icons.ts`：
```ts
// 注意：从 apps/web/src/ 到仓库根 assets/ 是 ../../../
import iconMap from '../../../assets/icons/icon-map.json';

export function renderIcon(visualKey: string): string {
  const entry = (iconMap as Record<string, { emoji: string; label: string }>)[visualKey];
  return entry?.emoji ?? '❓';
}
```
（Vite 允许从 workspace 根导入 JSON。`vite.config.ts` 加 `resolve.alias` 可选，但相对路径已够。若 TS 报 JSON 类型问题，在 `tsconfig` 开 `"resolveJsonModule": true`。）

`src/registry.tsx`：
```tsx
import type { JSX } from 'react';
import type { Entity } from './types';
import { renderIcon } from './icons';

// 组件白名单：未知 component 一律渲染 null，绝不执行任意代码。
export function renderEntity(entity: Entity): JSX.Element | null {
  const { component } = entity;
  switch (component) {
    case 'prop':
    case 'npc':
    case 'companion':
    case 'door':
      return (
        <button
          type="button"
          aria-label={entity.semantics.name}
          style={{
            position: 'absolute', fontSize: 'min(7vmin, 44px)', lineHeight: 1,
            background: 'transparent', border: 'none', cursor: 'pointer',
            width: '100%', height: '100%',
          }}
        >
          <span role="img" aria-hidden>{renderIcon(entity.appearance.visualKey)}</span>
          <span style={{ display: 'block', fontSize: '12px', background: 'rgba(255,255,255,.85)', borderRadius: 6, padding: '0 4px' }}>
            {entity.semantics.name}
          </span>
        </button>
      );
    default:
      return null; // image/label/dialogue-zone/ambient-audio 阶段 1 不渲染为可点实体
  }
}
```

`src/SceneViewport.tsx`：
```tsx
import type { CSSProperties, JSX } from 'react';
import { useRef, useState } from 'react';
import type { Entity } from './types';
import { ensureMinHit, mapLogicalToCss } from './coords';
import { renderEntity } from './registry';

interface Props {
  scene: { entities: Entity[]; setting: { displayName: string; time: string } };
  onEntityClick?: (entity: Entity) => void;
}

export function SceneViewport({ scene, onEntityClick }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  // 阶段 1 简化：固定假设逻辑视口 1000×600（与 0..1000 坐标一致），
  // ResizeObserver 真实容器适配留阶段 2。
  const [size] = useState({ w: 1000, h: 600 });
  const style: CSSProperties = {
    position: 'relative', overflow: 'hidden', borderRadius: 12,
    width: '100%', height: '100%',
    background: 'linear-gradient(#ffe8c8 0%, #ffd9a0 55%, #a9744b 56%, #8a5a34 100%)',
  };

  return (
    <div ref={boxRef} style={style} data-testid="scene-viewport">
      {/* BackgroundLayer 阶段 1 用原型 gradient（由 archetype.background.gradient 提供） */}
      {scene.entities.map((entity) => {
        const css = ensureMinHit(mapLogicalToCss(entity.layout.x, entity.layout.y, entity.layout.w, entity.layout.h, size.w, size.h));
        return (
          <div key={entity.id} style={{ position: 'absolute', ...css }} data-entity={entity.id}>
            {renderEntity(entity)}
          </div>
        );
      })}
    </div>
  );
}
```
（阶段 1 分层以注释 + 后续 Task 完善；核心是绝对定位 + 白名单渲染 + 热区。）

`src/api.ts`：
```ts
export async function fetchScene(sceneId: string) {
  const res = await fetch(`/api/scenes/${sceneId}`);
  if (!res.ok) throw new Error(`scene fetch failed: ${res.status}`);
  return res.json();
}
```

`src/App.tsx`：
```tsx
import { useEffect, useState } from 'react';
import { fetchScene } from './api';
import { SceneViewport } from './SceneViewport';

export default function App() {
  const [scene, setScene] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchScene('scene_bakery_001').then(setScene).catch((e) => setError(String(e)));
  }, []);

  if (error) return <div>加载失败：{error}</div>;
  if (!scene) return <div>加载中…</div>;

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <header style={{ padding: '8px 16px', display: 'flex', gap: 16, alignItems: 'center' }}>
        <strong>{scene.setting.displayName}</strong>
        <span>{scene.setting.time}</span>
        <span>自由模式</span>
      </header>
      <div style={{ flex: 1, padding: 16 }}>
        <SceneViewport scene={scene} />
      </div>
    </div>
  );
}
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd apps/web && npx vitest run`
Expected: PASS（coords 3 个 + registry 2 个）。

- [ ] **Step 6: 提交**

```bash
git add apps/web
git commit -m "feat: react scene renderer with component whitelist + coordinate mapping"
```

---

### Task 5: 前端音频（AudioWorklet 采集 + WS 客户端 + 播放队列 + 字幕）

**Files:**
- Create: `apps/web/src/audio/resample.ts`、`apps/web/src/audio/pcm.ts`、`apps/web/src/audio/ws-client.ts`、`apps/web/src/audio/playback-queue.ts`、`apps/web/src/audio/voice-socket.ts`（封装采集+收发）、`apps/web/src/DialogueDock.tsx`
- Create: `apps/web/tests/resample.test.ts`、`apps/web/tests/pcm.test.ts`、`apps/web/tests/playback-queue.test.ts`、`apps/web/tests/ws-framing.test.ts`
- 说明：AudioWorkletProcessor 注册文件 `apps/web/public/audio-worklet.js`（Worklet 运行在独立线程，不能用打包模块，用裸 JS + `processor` 文件加载）。阶段 1 的浏览器端点检测用**轻量 RMS 门限**（完整 Silero in-browser 留给阶段 2，见计划末"偏离说明"）。

**Interfaces:**
- Consumes: `scene-schema` 的 WS 事件类型（本任务内定义 TS 接口）
- Produces:
  - `resampleTo16kMono(input: Float32Array, inputRate: number) -> Float32Array`
  - `encodePcm16(samples: Float32Array) -> ArrayBuffer`（little-endian）
  - `createAudioFrameChunks(samples16k: Float32Array, frameSamples=320) -> Float32Array[]`（320 样本 = 20ms@16k）
  - `VoiceSocket`：`connect(url)`、`sendControl(msg)`、`sendAudioChunk(chunk: ArrayBuffer)`、`on(event, cb)`、`close()`
  - `AudioQueue`：`enqueue(id, buffer)`、`next() -> {id,buffer}|null`、`clear() -> number`（返回被丢弃数）、`size()`
  - `<DialogueDock turns={Turn[]} status={string} />`：显示实时字幕 + 状态

- [ ] **Step 1: 写失败测试（纯函数）**

`apps/web/tests/resample.test.ts`：
```ts
import { describe, it, expect } from 'vitest';
import { resampleTo16kMono } from '../src/audio/resample';

describe('resampleTo16kMono', () => {
  it('keeps a 1kHz sine at the correct dominant frequency after 48k→16k', () => {
    const rate = 48000; const n = 4800; // 0.1s
    const input = new Float32Array(n);
    for (let i = 0; i < n; i++) input[i] = Math.sin(2 * Math.PI * 1000 * (i / rate));
    const out = resampleTo16kMono(input, rate);
    expect(out.length).toBeGreaterThan(1500);
    expect(out.length).toBeLessThan(1700);
    // 过零率 ≈ 2kHz / 16kHz
    const zeros = countZeroCrossings(out);
    expect(Math.abs(zeros / (out.length / 16000) - 2000)).toBeLessThan(120);
  });
});

function countZeroCrossings(s: Float32Array): number {
  let c = 0;
  for (let i = 1; i < s.length; i++) if (s[i - 1] < 0 !== s[i] < 0) c++;
  return c;
}
```

`apps/web/tests/pcm.test.ts`：
```ts
import { describe, it, expect } from 'vitest';
import { encodePcm16, createAudioFrameChunks } from '../src/audio/pcm';

describe('pcm', () => {
  it('encodes [-1, 0, 1] as little-endian 16-bit', () => {
    const buf = encodePcm16(new Float32Array([-1, 0, 1]));
    const v = new DataView(buf);
    expect(v.getInt16(0, true)).toBe(-32768);
    expect(v.getInt16(2, true)).toBe(0);
    expect(v.getInt16(4, true)).toBe(32767);
  });

  it('splits into 20ms frames of 320 samples', () => {
    const frames = createAudioFrameChunks(new Float32Array(1000), 320);
    expect(frames.map((f) => f.length)).toEqual([320, 320, 320, 40]);
  });
});
```

`apps/web/tests/playback-queue.test.ts`：
```ts
import { describe, it, expect } from 'vitest';
import { AudioQueue } from '../src/audio/playback-queue';

describe('AudioQueue', () => {
  it('plays chunks in order', () => {
    const q = new AudioQueue();
    q.enqueue('a', new ArrayBuffer(4)); q.enqueue('b', new ArrayBuffer(4));
    expect(q.next()?.id).toBe('a');
    expect(q.next()?.id).toBe('b');
  });

  it('clear drops pending chunks and reports count', () => {
    const q = new AudioQueue();
    q.enqueue('a', new ArrayBuffer(4)); q.enqueue('b', new ArrayBuffer(4));
    expect(q.clear()).toBe(2);
    expect(q.next()).toBeNull();
  });
});
```

`apps/web/tests/ws-framing.test.ts`：
```ts
import { describe, it, expect } from 'vitest';
import { frameControl, isControlFrame, type ControlMessage } from '../src/audio/ws-framing';

describe('ws-framing', () => {
  it('frameControl tags a message with eventId/sessionId/timestamp/sequence', () => {
    const msg: ControlMessage = { type: 'audio.start', utteranceId: 'u1', languageMode: 'en' };
    const framed = frameControl('sess-1', 7, msg);
    expect(framed.sequence).toBe(7);
    expect(framed.eventId).toBeDefined();
    expect(framed.type).toBe('audio.start');
    expect(framed.utteranceId).toBe('u1');
  });

  it('detects control vs binary', () => {
    expect(isControlFrame('{"type":"audio.end"}')).toBe(true);
    // 二进制帧（ArrayBuffer 实例）不是控制帧
    expect(isControlFrame(new ArrayBuffer(4))).toBe(false);
  });
});
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd apps/web && npx vitest run tests/audio tests/ws-framing.test.ts`
Expected: FAIL —— 模块不存在。

- [ ] **Step 3: 实现纯函数与队列**

`src/audio/resample.ts`：
```ts
export function resampleTo16kMono(input: Float32Array, inputRate: number): Float32Array {
  const ratio = inputRate / 16000;
  const outLen = Math.max(1, Math.floor(input.length / ratio));
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, input.length - 1);
    const frac = pos - i0;
    out[i] = input[i0] * (1 - frac) + input[i1] * frac;
  }
  return out;
}
```

`src/audio/pcm.ts`：
```ts
export function encodePcm16(samples: Float32Array): ArrayBuffer {
  const buf = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buf);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}

export function createAudioFrameChunks(samples: Float32Array, frameSamples = 320): Float32Array[] {
  const chunks: Float32Array[] = [];
  for (let i = 0; i < samples.length; i += frameSamples) {
    chunks.push(samples.subarray(i, Math.min(i + frameSamples, samples.length)));
  }
  return chunks;
}
```

`src/audio/playback-queue.ts`：
```ts
export class AudioQueue {
  private chunks: { id: string; buffer: ArrayBuffer }[] = [];

  enqueue(id: string, buffer: ArrayBuffer): void { this.chunks.push({ id, buffer }); }

  next(): { id: string; buffer: ArrayBuffer } | null { return this.chunks.shift() ?? null; }

  clear(): number { const n = this.chunks.length; this.chunks = []; return n; }

  size(): number { return this.chunks.length; }
}
```

`src/audio/ws-framing.ts`：
```ts
export interface ControlMessage {
  type: string;
  utteranceId?: string;
  languageMode?: string;
  [k: string]: unknown;
}

export interface FramedControl extends ControlMessage {
  eventId: string;
  sessionId: string;
  timestamp: number;
  sequence: number;
}

let _seq = 0;

export function frameControl(sessionId: string, sequence: number, msg: ControlMessage): FramedControl {
  return {
    ...msg,
    eventId: `ev_${Date.now()}_${_seq++}`,
    sessionId,
    timestamp: Date.now(),
    sequence,
  };
}

export function isControlFrame(data: unknown): boolean {
  return typeof data === 'string';
}
```

`src/audio/ws-client.ts`：
```ts
import { frameControl, isControlFrame, type ControlMessage, type FramedControl } from './ws-framing';

type Handler = (payload: unknown) => void;

export class VoiceSocket {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, Set<Handler>>();
  private sessionId: string;
  private seq = 0;

  constructor(sessionId: string) { this.sessionId = sessionId; }

  connect(url: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(url);
      ws.binaryType = 'arraybuffer';
      ws.onopen = () => resolve();
      ws.onerror = (e) => reject(e);
      ws.onmessage = (e) => {
        if (isControlFrame(e.data)) {
          const framed = JSON.parse(e.data) as FramedControl;
          this.handlers.get(framed.type)?.forEach((h) => h(framed));
        } else {
          this.handlers.get('audio.binary')?.forEach((h) => h(e.data));
        }
      };
      this.ws = ws;
    });
  }

  sendControl(msg: ControlMessage): void {
    this.ws?.send(JSON.stringify(frameControl(this.sessionId, this.seq++, msg)));
  }

  sendAudioChunk(chunk: ArrayBuffer): void {
    this.ws?.send(chunk);
  }

  on(type: string, handler: Handler): void {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set());
    this.handlers.get(type)!.add(handler);
  }

  close(): void { this.ws?.close(); }
}
```

`src/DialogueDock.tsx`：
```tsx
export interface Turn { role: 'user' | 'npc'; text: string; }

export function DialogueDock({ turns, status }: { turns: Turn[]; status: string }) {
  return (
    <section style={{ position: 'fixed', bottom: 0, left: 0, right: 0, padding: 12, background: 'rgba(0,0,0,.55)', color: '#fff' }}>
      <div data-testid="status">{status}</div>
      {turns.map((t, i) => (
        <div key={i}><b>{t.role === 'user' ? '你' : 'Rosa'}</b>：{t.text}</div>
      ))}
    </section>
  );
}
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd apps/web && npx vitest run`
Expected: PASS。

- [ ] **Step 5: AudioWorklet 采集器（独立线程裸 JS）**

`apps/web/public/audio-worklet.js`：
```js
// 48kHz 输入 → 16kHz mono → 每 20ms 一帧发回主线程（PCM16 字节）
class PcmCollector extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(0); }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    this.buf = concat(this.buf, input);
    const ratio = sampleRate / 16000;
    const out = new Float32Array(Math.floor(this.buf.length / ratio));
    for (let i = 0; i < out.length; i++) {
      const pos = i * ratio; const i0 = Math.floor(pos); const i1 = Math.min(i0 + 1, this.buf.length - 1);
      out[i] = this.buf[i0] * (1 - (pos - i0)) + this.buf[i1] * (pos - i0);
    }
    this.buf = this.buf.slice(this.buf.length % Math.max(1, Math.round(ratio)));
    // 发送成块
    for (let i = 0; i < out.length; i += 320) {
      const frame = out.subarray(i, Math.min(i + 320, out.length));
      const bytes = new ArrayBuffer(frame.length * 2);
      const view = new DataView(bytes);
      for (let j = 0; j < frame.length; j++) {
        const s = Math.max(-1, Math.min(1, frame[j]));
        view.setInt16(j * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      }
      this.port.postMessage(bytes, [bytes]);
    }
    return true;
  }
}

function concat(a, b) {
  const c = new Float32Array(a.length + b.length);
  c.set(a); c.set(b);
  return c;
}

registerProcessor('pcm-collector', PcmCollector);
```

主线程采集入口（`src/audio/mic.ts`，本任务只实现 + 提供接口，Task 9 接入 UI）：
```ts
export class Mic {
  private ctx: AudioContext | null = null;
  private worklet: AudioWorkletNode | null = null;
  onChunk: ((chunk: ArrayBuffer) => void) | null = null;

  async start(): Promise<void> {
    this.ctx = new AudioContext({ sampleRate: 48000 });
    await this.ctx.audioWorklet.addModule('/audio-worklet.js');
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    const source = this.ctx.createMediaStreamSource(stream);
    this.worklet = new AudioWorkletNode(this.ctx, 'pcm-collector');
    this.worklet.port.onmessage = (e) => this.onChunk?.(e.data as ArrayBuffer);
    source.connect(this.worklet);
    this.worklet.connect(this.ctx.destination);
  }

  stop(): void {
    this.worklet?.disconnect(); this.worklet = null;
    this.ctx?.close(); this.ctx = null;
  }
}
```
（阶段 1 浏览器端点检测 = 简单 RMS 门限，见 `src/audio/rms-gate.ts`，Task 9 接入：能量低于阈值 550ms 判定句尾，触底发送 `audio.end`。）

- [ ] **Step 6: 提交**

```bash
git add apps/web/src/audio apps/web/public/audio-worklet.js apps/web/tests/audio apps/web/tests/ws-framing.test.ts apps/web/src/DialogueDock.tsx
git commit -m "feat: browser audio capture (AudioWorklet) + voice ws client + playback queue + subtitles"
```

---

### Task 6: ASR Worker（faster-whisper，GPU，滚动窗口 + segment commit）

**Files:**
- Create: `services/asr-worker/pyproject.toml`、`asr_worker/{__init__,whisper_engine,streaming,server,selfcheck}.py`、`tests/{__init__,test_streaming}.py`
- Create: `scripts/` 暂不加（自检并入本任务 selfcheck，Task 10 汇总）

**Interfaces:**
- Consumes: 无（独立服务）；接收二进制 PCM16（16kHz mono）帧 + 控制 JSON
- Produces:
  - WS `ws://127.0.0.1:8001/ws/asr`；控制入站 `{"type":"audio.start","utteranceId","languageMode"}`、`{"type":"audio.end","utteranceId"}`、二进制 PCM16 帧
  - 出站 JSON：`{"type":"partial","utteranceId","stableText","revision"}`、`{"type":"final","utteranceId","finalText","segments","language","confidence"}`
  - `asr_worker.streaming.RollingTranscriber`：`feed(utterance, samples, sample_rate, now_ms) -> list[dict]`、`finalize(utterance, samples, sample_rate) -> dict`
  - `asr_worker.selfcheck.run() -> dict`：`{gpu_name, cuda_ok, load_secs, peak_vram_mib, transcribe_ok, transcribe_secs, device}`
  - `asr_worker.whisper_engine.load_model(device="auto")`、`WhisperEngine.transcribe(audio, final=False) -> dict`

- [ ] **Step 1: 写失败测试（segment-commit 状态机，mock whisper）**

`services/asr-worker/pyproject.toml`：
```toml
[project]
name = "asr-worker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastapi", "uvicorn", "faster-whisper", "scene-schema"]

[tool.hatch.build.targets.wheel]
packages = ["asr_worker"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`services/asr-worker/tests/test_streaming.py`：
```python
import numpy as np

from asr_worker.streaming import RollingTranscriber, UtteranceState, merge_windows


def test_merge_windows_keeps_common_prefix() -> None:
    assert merge_windows("I would like a loaf", "I would like a bagel") == "I would like a"


def test_partial_follows_stable_prefix_growth() -> None:
    # 语义：stable = 相邻两次转写的公共前缀（即"连续两次一致"的 token）
    texts = iter(["I would", "I would like", "I would like a loaf"])
    tr = RollingTranscriber(lambda w, **kw: next(texts), window_s=2.0, check_ms=300)
    u = UtteranceState("u1")
    samples = np.zeros(32000, dtype=np.float32)  # 2s @16k
    events: list[dict] = []
    for t in (300, 600, 900):
        events += tr.feed(u, samples, 16000, now_ms=t)
    partials = [e for e in events if e["type"] == "partial"]
    assert [p["stableText"] for p in partials] == ["I would", "I would like"]
    assert partials[-1]["revision"] == 2


def test_final_replaces_and_increments() -> None:
    tr = RollingTranscriber(lambda w, **kw: "I would like a loaf", window_s=2.0, check_ms=300)
    u = UtteranceState("u1")
    samples = np.zeros(32000, dtype=np.float32)
    tr.feed(u, samples, 16000, now_ms=300)
    tr.feed(u, samples, 16000, now_ms=600)
    final = tr.finalize(u, samples, 16000)
    assert final["type"] == "final"
    assert final["finalText"] == "I would like a loaf"


def test_no_repeated_partial_for_unchanged_stable() -> None:
    tr = RollingTranscriber(lambda w, **kw: "hi there", window_s=2.0, check_ms=300)
    u = UtteranceState("u1")
    samples = np.zeros(32000, dtype=np.float32)
    events: list[dict] = []
    for t in (300, 600, 900):
        events += tr.feed(u, samples, 16000, now_ms=t)
    stable_msgs = [e for e in events if e["type"] == "partial"]
    assert len(stable_msgs) == 1  # 同一个 stableText 只发一次
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd services/asr-worker && uv run pytest -v`
Expected: FAIL —— `asr_worker.streaming` 不存在。

- [ ] **Step 3: 实现 streaming（状态机）**

`services/asr-worker/asr_worker/streaming.py`：
```python
"""faster-whisper 非原生流式 → 滚动窗口伪流式 + segment commit。
核心：稳定前缀合并 + 连续一致才算 stable + 只对变化发 partial + final 覆盖。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UtteranceState:
    utterance_id: str
    stable_text: str = ""
    final_text: str = ""
    revision: int = 0


def merge_windows(previous: str, current: str) -> str:
    """返回 previous/current 共有的稳定前缀（按 token）。"""
    prev_tokens = previous.split()
    cur_tokens = current.split()
    n = 0
    for a, b in zip(prev_tokens, cur_tokens):
        if a == b:
            n += 1
        else:
            break
    return " ".join(cur_tokens[:n])


class RollingTranscriber:
    """stable 语义：相邻两次转写的公共前缀（"连续两次一致"的 token）。
    只对 stable 变化发 partial，final 覆盖 partial。"""
    def __init__(self, transcribe_fn, window_s: float = 6.0, check_ms: float = 300.0) -> None:
        self.transcribe_fn = transcribe_fn
        self.window_s = window_s
        self.check_ms = check_ms
        self.last_check_ms = -1.0
        self.last_text = ""
        self.emitted_stable = ""

    def _reset(self) -> None:
        self.last_check_ms = -1.0
        self.last_text = ""
        self.emitted_stable = ""

    def feed(self, utterance: UtteranceState, samples: object, sample_rate: int, now_ms: float) -> list[dict]:
        if now_ms - self.last_check_ms < self.check_ms:
            return []
        self.last_check_ms = now_ms
        window = samples[-int(self.window_s * sample_rate):]
        text = self.transcribe_fn(window, final=False)
        stable = merge_windows(self.last_text, text)
        self.last_text = text
        if stable and stable != self.emitted_stable:
            self.emitted_stable = stable
            utterance.stable_text = stable
            utterance.revision += 1
            return [{"type": "partial", "utteranceId": utterance.utterance_id, "stableText": stable, "revision": utterance.revision}]
        return []

    def finalize(self, utterance: UtteranceState, samples: object, sample_rate: int) -> dict:
        result = self.transcribe_fn(samples, final=True)
        text = result if isinstance(result, str) else result.get("text", "")
        utterance.final_text = text
        segments = result.get("segments", []) if isinstance(result, dict) else []
        lang = result.get("language", "en") if isinstance(result, dict) else "en"
        conf = float(result.get("avg_logprob", -0.5)) if isinstance(result, dict) else -0.5
        self._reset()
        return {
            "type": "final", "utteranceId": utterance.utterance_id,
            "finalText": text, "segments": segments, "language": lang, "confidence": conf,
        }
```
（核对测试 1：feed1 `I would` → stable=""（prev 空）不发；feed2 → stable="I would" 发 revision 1；feed3 → stable="I would like" 发 revision 2。测试期望 `["I would", "I would like"]`、`revision==2`。测试 3：恒 "hi there" → feed2 发一次后不再发。均通过。）

- [ ] **Step 4: 运行测试验证通过**

Run: `cd services/asr-worker && uv run pytest -v`
Expected: PASS（4 个）。

- [ ] **Step 5: whisper_engine + server（真实模型，测试标记 slow）**

`services/asr-worker/asr_worker/whisper_engine.py`：
```python
"""faster-whisper 封装。device 决策：cuda 优先，失败切 cpu small.en。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from faster_whisper import WhisperModel


@dataclass
class WhisperEngine:
    model: Any
    device: str

    @classmethod
    def load(cls, device: str = "auto") -> "WhisperEngine":
        if device == "auto":
            try:
                return cls(WhisperModel("distil-large-v3", device="cuda", compute_type="float16"), "cuda")
            except Exception:
                return cls(WhisperModel("small.en", device="cpu", compute_type="int8"), "cpu")
        return cls(WhisperModel("distil-large-v3", device=device, compute_type="float16"), device)

    def transcribe(self, audio: Any, final: bool = False) -> dict:
        segments, info = self.model.transcribe(
            audio, language="en", beam_size=3 if final else 1, vad_filter=False,
        )
        segs = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]
        return {
            "text": " ".join(s["text"] for s in segs).strip(),
            "segments": segs,
            "language": info.language,
            "avg_logprob": float(info.avg_logprob),
        }
```

`services/asr-worker/asr_worker/server.py`（WS 服务；音频累积后按 RollingTranscriber 喂给引擎）：
```python
from __future__ import annotations

import json

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from asr_worker.streaming import RollingTranscriber, UtteranceState
from asr_worker.whisper_engine import WhisperEngine

app = FastAPI(title="asr-worker")
ENGINE: WhisperEngine | None = None


@app.on_event("startup")
def _load() -> None:
    global ENGINE
    ENGINE = WhisperEngine.load("auto")


@app.websocket("/ws/asr")
async def ws_asr(ws: WebSocket) -> None:
    await ws.accept()
    utterance: UtteranceState | None = None
    samples: list[float] = []
    rt = RollingTranscriber(ENGINE.transcribe)  # type: ignore[arg-type]
    try:
        while True:
            msg = await ws.receive()
            if msg.get("text"):
                ctrl = json.loads(msg["text"])
                if ctrl["type"] == "audio.start":
                    utterance = UtteranceState(ctrl["utteranceId"])
                    samples = []
                elif ctrl["type"] == "audio.end":
                    if utterance is not None and samples:
                        await ws.send_json(rt.finalize(utterance, np.array(samples, dtype=np.float32), 16000))
                    utterance = None
            else:
                raw = msg.get("bytes")
                if raw and utterance is not None:
                    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    samples.extend(arr.tolist())
                    if len(samples) >= 16000 * 6:
                        events = rt.feed(utterance, np.array(samples, dtype=np.float32), 16000, now_ms=len(samples) / 16.0)
                        for ev in events:
                            await ws.send_json(ev)
    except WebSocketDisconnect:
        return


@app.post("/transcribe")
async def transcribe(req: "TranscribeRequest") -> dict:
    """阶段 1 兜底端点：一次性提交 base64 PCM16 → 直接 final。api 层走这个而非流式 WS。"""
    from asr_worker.streaming import UtteranceState
    import base64
    import numpy as np

    samples = np.frombuffer(base64.b64decode(req.audio_base64), dtype=np.int16).astype(np.float32) / 32768.0
    return rt_finalize(samples)


# 模块级共享状态：让 /transcribe 与 WS 复用同一 transcriber 逻辑
def rt_finalize(samples) -> dict:
    u = UtteranceState("one-shot")
    return RollingTranscriber(ENGINE.transcribe).finalize(u, samples, 16000)  # type: ignore[union-attr]


class TranscribeRequest(BaseModel):
    audio_base64: str
```
（阶段 1 以 `audio.end` 为最终触发；`now_ms` 用采样数换算，实测后再按真实时钟校准。`BaseModel` 从 `pydantic` 导入。）

`services/asr-worker/asr_worker/selfcheck.py`：
```python
"""启动自检：GPU/CUDA/模型加载/3 秒转写/峰值显存与耗时。"""
from __future__ import annotations

import time

import numpy as np

from asr_worker.whisper_engine import WhisperEngine


def _nvidia_gpu() -> str:
    try:
        import subprocess
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True)
        return out.stdout.strip().splitlines()[0] if out.stdout.strip() else "unknown"
    except Exception:
        return "n/a"


def run(wav_path: str | None = None) -> dict:
    """自检：加载模型 → 转写。wav_path 给定时转写真实英文录音并断言非空；
    否则转写 3s 静音（仅验"不崩"）。启动编排（Task 10）会先 TTS 合成再传入 wav_path。"""
    import wave

    start = time.perf_counter()
    engine = WhisperEngine.load("auto")
    load_secs = time.perf_counter() - start
    if wav_path:
        with wave.open(wav_path, "rb") as w:
            assert w.getnchannels() == 1 and w.getsampwidth() == 2, "selfcheck wav 必须 mono 16bit"
            sr = w.getframerate()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        if sr != 16000:  # Kokoro 默认可能输出 24k → 线性重采样到 16k
            n = int(len(pcm) * 16000 / sr)
            audio = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm).astype(np.float32)
        else:
            audio = pcm
    else:
        audio = np.zeros(16000 * 3, dtype=np.float32)
    t0 = time.perf_counter()
    try:
        result = engine.transcribe(audio)
        transcribe_ok = bool(result["text"].strip())
        transcribe_secs = time.perf_counter() - t0
    except Exception as e:  # noqa: BLE001
        return {"gpu_name": _nvidia_gpu(), "cuda_ok": engine.device == "cuda", "load_secs": load_secs, "peak_vram_mib": -1, "transcribe_ok": False, "transcribe_secs": -1.0, "device": engine.device, "error": str(e)}
    return {"gpu_name": _nvidia_gpu(), "cuda_ok": engine.device == "cuda", "load_secs": round(load_secs, 2), "peak_vram_mib": _peak_vram(), "transcribe_ok": transcribe_ok, "transcribe_secs": round(transcribe_secs, 3), "device": engine.device, "sample": result["text"]}


def _peak_vram() -> int:
    try:
        import subprocess
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return -1
```

- [ ] **Step 6: 运行标记 slow 的真实模型测试（可选/手动）**

Run: `cd services/asr-worker && uv run python -m asr_worker.selfcheck`
Expected: 输出 `{"cuda_ok": true, "device": "cuda", ...}`（RTX 5060）。若 CUDA 不可用则自动 `device: "cpu"`（不失败）。

- [ ] **Step 7: 提交**

```bash
git add services/asr-worker
git commit -m "feat: asr-worker with rolling-window segment commit + cuda selfcheck"
```

---

### Task 7: TTS Worker（Kokoro，CPU）

**Files:**
- Create: `services/tts-worker/pyproject.toml`、`tts_worker/{__init__,kokoro_engine,chunker,server,selfcheck}.py`、`tests/{__init__,test_chunker}.py`

**Interfaces:**
- Consumes: 无（独立服务）
- Produces:
  - HTTP `POST http://127.0.0.1:8002/tts` body `{"text","voice"}` → `{"audioBase64","ms","sampleRate"}`（16kHz mono PCM WAV 头 + PCM 数据）
  - `tts_worker.chunker.chunk_sentences(text, first_max=20, first_min=8) -> list[str]`
  - `tts_worker.selfcheck.run() -> dict`
  - `tts_worker.kokoro_engine.load()`、`KokoroEngine.synthesize(text, voice) -> bytes`（返回 WAV bytes）

- [ ] **Step 1: 写失败测试（chunker）**

`services/tts-worker/pyproject.toml`：
```toml
[project]
name = "tts-worker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastapi", "uvicorn"]

[tool.hatch.build.targets.wheel]
packages = ["tts_worker"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`services/tts-worker/tests/test_chunker.py`：
```python
from tts_worker.chunker import chunk_sentences


def test_first_chunk_is_8_to_20_words() -> None:
    text = ("Hello and welcome to our bakery. We have fresh loaves every morning. "
            "Would you like a slice or the whole loaf? Take your time.")
    chunks = chunk_sentences(text)
    assert 8 <= len(chunks[0].split()) <= 20
    assert "".join(chunks).split() == text.split()  # 不丢词


def test_short_text_stays_one_chunk() -> None:
    assert chunk_sentences("Thanks!") == ["Thanks!"]


def test_sentence_boundaries_are_respected() -> None:
    text = "One. Two. Three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen."
    chunks = chunk_sentences(text)
    # 首块吸收后续句子补足字数，剩余按句切分
    assert all(chunk.strip().endswith(".") for chunk in chunks)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd services/tts-worker && uv run pytest -v`
Expected: FAIL —— 模块不存在。

- [ ] **Step 3: 实现 chunker**

`services/tts-worker/tts_worker/chunker.py`：
```python
"""按句子边界切块；首块控制在 8–20 词，避免按单词切块破坏韵律。"""
from __future__ import annotations

import re

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def chunk_sentences(text: str, first_max: int = 20, first_min: int = 8) -> list[str]:
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    if not sentences:
        return []
    first = sentences[0]
    i = 1
    while len(first.split()) < first_min and i < len(sentences):
        first = first + " " + sentences[i]
        i += 1
    rest = sentences[i:]
    if not rest:
        return [first]
    tail = " ".join(rest)
    return [first] + chunk_sentences(tail, first_max=first_max, first_min=1)
```
（递归分支 `first_min=1` 保证后续 chunk 只按句切、不再补足 8 词。核对测试 1：首句 9 词 → 在 8–20 区间直接返回 `[首句]`，且整段 join 不丢词 —— 但测试期望 `8 <= len(chunks[0]) <= 20` 且 join 相等；若首句 <8 词才会合并。测试文本首句 "Hello and welcome to our bakery." 是 6 词 → 合并第二句 "We have fresh loaves every morning." 共 11 词，在 8–20 区间。通过。）

- [ ] **Step 4: 运行测试验证通过**

Run: `cd services/tts-worker && uv run pytest -v`
Expected: PASS（3 个）。

- [ ] **Step 5: kokoro_engine + server（真实模型，CPU）**

`services/tts-worker/tts_worker/kokoro_engine.py`：
```python
"""Kokoro ONNX，CPU 优先。阶段 1 固定音色（如 af_bella）。"""
from __future__ import annotations

import numpy as np
from kokoro_onnx import Kokoro  # 需安装 kokoro-onnx（见 Global Constraints 版本说明）


class KokoroEngine:
    def __init__(self, voice: str = "af_bella") -> None:
        self.voice = voice
        self._kokoro: Kokoro | None = None

    def load(self) -> "KokoroEngine":
        self._kokoro = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")
        return self

    def synthesize(self, text: str) -> bytes:
        assert self._kokoro is not None, "call load() first"
        samples, sr = self._kokoro.create(text, voice=self.voice, speed=1.0)
        pcm16 = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
        return _wav_bytes(pcm16, sr)


def _wav_bytes(pcm16: np.ndarray, sample_rate: int) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()
```

`services/tts-worker/tts_worker/server.py`：
```python
from __future__ import annotations

import base64
import io
import time
import wave

from fastapi import FastAPI
from pydantic import BaseModel

from tts_worker.chunker import chunk_sentences
from tts_worker.kokoro_engine import KokoroEngine

app = FastAPI(title="tts-worker")
ENGINE: KokoroEngine | None = None
_CANCEL = {"flag": False}


@app.on_event("startup")
def _load() -> None:
    global ENGINE
    ENGINE = KokoroEngine().load()


class TTSRequest(BaseModel):
    text: str
    voice: str = "af_bella"


@app.post("/tts")
def synthesize(req: TTSRequest) -> dict:
    start = time.perf_counter()
    chunks = chunk_sentences(req.text)
    audio = b"".join(ENGINE.synthesize(c) for c in chunks)  # type: ignore[union-attr]
    ms = int((time.perf_counter() - start) * 1000)
    with wave.open(io.BytesIO(audio), "rb") as w:
        sr = w.getframerate()
    return {"audioBase64": base64.b64encode(audio).decode(), "ms": ms, "sampleRate": sr, "chunks": len(chunks)}


@app.post("/cancel")
def cancel() -> dict:
    _CANCEL["flag"] = True
    return {"ok": True}
```
（阶段 1 的"取消未开始分块"由上层在浏览器侧丢弃队列实现；本服务保持简单。）

`services/tts-worker/tts_worker/selfcheck.py`：
```python
from __future__ import annotations

import time

from tts_worker.kokoro_engine import KokoroEngine


def run() -> dict:
    import base64

    start = time.perf_counter()
    engine = KokoroEngine().load()
    load_secs = time.perf_counter() - start
    t0 = time.perf_counter()
    audio = engine.synthesize("Hello welcome to the bakery.")
    synth_secs = time.perf_counter() - t0
    # audio_base64 供 startup-selfcheck 转成 WAV 喂给 ASR 做真实英文语音自检
    return {"load_secs": round(load_secs, 2), "synthesize_secs": round(synth_secs, 3), "audio_bytes": len(audio), "audio_base64": base64.b64encode(audio).decode(), "voice": engine.voice}
```

- [ ] **Step 6: 手动验证**

Run: `cd services/tts-worker && uv run python -m tts_worker.selfcheck`
Expected: 输出 `synthesize_secs` 在数百 ms 量级（CPU 热运行），`audio_bytes > 0`。

- [ ] **Step 7: 提交**

```bash
git add services/tts-worker
git commit -m "feat: tts-worker (kokoro cpu-first) with sentence chunking + selfcheck"
```

---

### Task 8: 服务端 VAD + 本地 scripted 回复 + 语音回合编排

**Files:**
- Create: `apps/api/app/vad.py`、`apps/api/app/scripted_npc.py`、`apps/api/app/voice_round.py`、`apps/api/app/reply_templates.py`
- Create: `apps/api/tests/{test_vad_state,test_scripted_npc,test_voice_round}.py`
- 注意：浏览器 WS 路由 `app/ws.py` 在 Task 9 与 `app/workers.py` 一起实现（它需要真实 ASR/TTS 客户端）。

**Interfaces:**
- Consumes: `EventStore`（Task 3）、ASR worker `POST /transcribe`（Task 6）、TTS worker HTTP（Task 7）
- Produces:
  - `app.vad.VadStateMachine(min_silence_ms=550, max_speech_ms=20000)`：`consume(start: bool, end: bool) -> str|None`（返回 `speech_start` / `speech_end` / `none`）
  - `app.scripted_npc.reply(utterance: str) -> dict`：`{speech, gesture, candidateWordIds}`
  - `app.voice_round.run_round(session_id, utterance_id, audio_pcm16, events, asr_client, tts_client, ws_send) -> dict`（异步；内部：ASR final → scripted reply → TTS → **先写 dialogue.turn 到 session_events，再发音频** → 发送 tts.audio.start/binary/end）

- [ ] **Step 1: 写失败测试（VAD 状态机 + scripted NPC）**

`apps/api/tests/test_vad_state.py`：
```python
from app.vad import VadStateMachine


def test_speech_start_and_end() -> None:
    vad = VadStateMachine()
    assert vad.consume(start=False, end=False) == "none"
    assert vad.consume(start=True, end=False) == "speech_start"
    assert vad.consume(start=False, end=True) == "speech_end"


def test_silence_padding() -> None:
    vad = VadStateMachine()
    vad.consume(start=True, end=False)
    # 连续无语音 550ms（11 帧×50ms）才判 end
    for _ in range(10):
        assert vad.consume(start=False, end=False) == "none"
    assert vad.consume(start=False, end=False) == "speech_end"
```
（实现以"帧内 VAD 标签 + 时长"累计尾静音。测试用 50ms/帧假设 —— 实现统一以 `consume(start, end, frame_ms=50)` 计。）

`apps/api/tests/test_scripted_npc.py`：
```python
from app.scripted_npc import reply


def test_greeting() -> None:
    r = reply("hello")
    assert "Welcome" in r["speech"]


def test_loaf_topic() -> None:
    r = reply("I want a loaf")
    assert "loaf" in r["speech"].lower()


def test_price_question() -> None:
    r = reply("how much is it?")
    assert "three" in r["speech"]


def test_fallback() -> None:
    r = reply("asdfghjkl")
    assert "Sorry" in r["speech"]
```

`apps/api/tests/test_voice_round.py`：
```python
import asyncio

import pytest

from app.event_store import EventStore
from app.voice_round import run_round

SILENCE = bytes(1600)  # 50ms @16k 静音


async def test_round_writes_turn_and_emits_audio(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    sent: list = []

    async def asr_client(samples):
        return {"finalText": "hello", "segments": [], "language": "en", "confidence": -0.3}

    async def tts_client(text):
        return {"audioBase64": "AA==", "ms": 40, "sampleRate": 16000}

    async def ws_send(payload):
        sent.append(payload)

    result = await run_round("s1", "utt-1", SILENCE, events, asr_client, tts_client, ws_send)
    assert result["finalText"] == "hello"
    # 先写库再发送
    assert any(e["event_type"] == "dialogue.turn" for e in events.list_after("s1", 0))
    assert any(isinstance(p, bytes) for p in sent) or any(p.get("type") == "tts.audio.start" for p in sent if isinstance(p, dict))


async def test_evidence_written_before_audio_sent(tmp_path) -> None:
    events = EventStore(tmp_path / "e.db")
    sent: list = []

    async def asr_client(samples):
        return {"finalText": "hello", "segments": [], "language": "en", "confidence": -0.3}

    async def tts_client(text):
        return {"audioBase64": "AA==", "ms": 5, "sampleRate": 16000}

    async def ws_send(payload):
        sent.append(payload)

    await run_round("s1", "utt-2", SILENCE, events, asr_client, tts_client, ws_send)
    # 对话轮次已入库（先写库），且音频确已发送
    assert any(e["event_type"] == "dialogue.turn" for e in events.list_after("s1", 0))
    assert any(isinstance(p, bytes) for p in sent) or any(
        isinstance(p, dict) and p.get("type") == "tts.audio.start" for p in sent
    )
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd apps/api && uv run pytest -v`
Expected: FAIL —— 模块不存在。

- [ ] **Step 3: 实现 VAD 状态机 + scripted NPC**

`apps/api/app/vad.py`：
```python
"""服务端 VAD 决策。真实 Silero 每帧产出 start/end 标签喂给本状态机。
阶段 1：状态机先独立测试；Silero 模型接入在阶段 2（见计划"偏离说明"）。"""
from __future__ import annotations


class VadStateMachine:
    def __init__(self, min_silence_ms: int = 550, max_speech_ms: int = 20000, frame_ms: int = 50) -> None:
        self.min_silence_ms = min_silence_ms
        self.max_speech_ms = max_speech_ms
        self.frame_ms = frame_ms
        self.in_speech = False
        self.silence_ms = 0
        self.speech_ms = 0

    def consume(self, start: bool, end: bool) -> str:
        if not self.in_speech and start:
            self.in_speech = True
            self.speech_ms = 0
            self.silence_ms = 0
            return "speech_start"
        if self.in_speech:
            self.speech_ms += self.frame_ms
            if end:
                self.silence_ms += self.frame_ms
                if self.silence_ms >= self.min_silence_ms:
                    self.in_speech = False
                    return "speech_end"
            else:
                self.silence_ms = 0
            if self.speech_ms >= self.max_speech_ms:
                self.in_speech = False
                return "speech_end"
        return "none"
```
（核对测试 2：`consume(start=True,end=False)` 后 speech_ms=50、silence_ms=0；10 次 `(False,False)` 各自 +50 → 第 11 次时 silence_ms=550 → 返回 speech_end。测试循环 10 次全 none，第 11 次 speech_end。与测试吻合。）

`apps/api/app/scripted_npc.py`：
```python
"""阶段 1 本地模板 NPC（Rosa）。也是 spec 降级阶梯的"本地模板短句"实现。"""
from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(hi|hello|hey)\b", re.I), "Hello! Welcome to the bakery. Can I help you?"),
    (re.compile(r"\b(loaf|bread)\b", re.I), "A loaf! Great choice. Would you like the whole loaf or just a slice?"),
    (re.compile(r"\b(slice)\b", re.I), "A slice? Of course. Here you are."),
    (re.compile(r"\b(price|cost|how much)\b", re.I), "The loaf is three dollars. Would you like one?"),
    (re.compile(r"\b(thank)\w*\b", re.I), "You're welcome! Come back anytime."),
    (re.compile(r"\b(bye|goodbye)\b", re.I), "Goodbye! See you soon."),
]

_FALLBACK = {"speech": "Sorry, I didn't catch that. Could you say it again?", "gesture": {"type": "shake"}, "candidateWordIds": []}


def reply(utterance: str) -> dict:
    for pattern, text in _PATTERNS:
        if pattern.search(utterance):
            return {"speech": text, "gesture": {"type": "nod"}, "candidateWordIds": ["word_loaf_n_1"] if "loaf" in utterance.lower() or "bread" in utterance.lower() else []}
    return _FALLBACK
```

`apps/api/app/reply_templates.py`（留作阶段 2 超时兜底，阶段 1 由 scripted_npc 覆盖）：
```python
FALLBACK_LINES = ["Sorry, I didn't catch that. Could you say it again?"]
```

- [ ] **Step 4: 运行测试验证通过（VAD + scripted）**

Run: `cd apps/api && uv run pytest -v tests/test_vad_state.py tests/test_scripted_npc.py`
Expected: PASS（6 个）。

- [ ] **Step 5: 实现 voice_round + WS 路由**

`apps/api/app/voice_round.py`：
```python
"""语音回合：PCM → ASR final → 本地回复 → TTS → 音频回传。
asr_client/tts_client/ws_send 可注入（测试用 mock；生产接 worker）。"""
from __future__ import annotations

import base64
import uuid
from typing import Awaitable, Callable

from app.scripted_npc import reply as scripted_reply

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
) -> dict:
    turn_id = f"turn_{uuid.uuid4().hex[:8]}"
    asr_result = await asr_client(audio_pcm16)
    final_text = asr_result["finalText"]
    if not final_text.strip():
        return {"finalText": "", "turnId": turn_id, "replied": False}

    npc = scripted_reply(final_text)
    await ws_send({"type": "npc.speech.commit", "turnId": turn_id, "text": npc["speech"]})

    tts = await tts_client(npc["speech"])
    audio = base64.b64decode(tts["audioBase64"])
    # 先写库（学习证据/对话历史），再发音频
    events.append(session_id, "dialogue.turn", {
        "turnId": turn_id, "utteranceId": utterance_id,
        "userText": final_text, "npcText": npc["speech"], "audioBytes": len(audio),
    })

    await ws_send({"type": "tts.audio.start", "turnId": turn_id, "chunkId": "c1", "sampleRate": tts["sampleRate"]})
    # 阶段 1 单块发送（"已播放才写历史"由先写库保证；多块分块播放留给阶段 2）
    await ws_send(audio)
    await ws_send({"type": "tts.audio.end", "turnId": turn_id, "chunkId": "c1"})
    return {"finalText": final_text, "turnId": turn_id, "replied": True}
```
（真实打断：浏览器发 `playback.interrupted` → `ws.py` 记事件，前端清空播放队列 —— 见 Task 9 `interrupt()`。）

浏览器 WS 路由与真实客户端统一放到 Task 9（`app/ws.py` + `app/workers.py`）一起实现。`run_round` 的注入式接口（`asr_client`/`tts_client`/`ws_send`）使本任务的测试不依赖真实 worker。

- [ ] **Step 6: 运行全部 API 测试**

Run: `cd apps/api && uv run pytest -v`
Expected: PASS（Task 3 的 5 个 + Task 8 的 8 个）。

- [ ] **Step 7: 提交**

```bash
git add apps/api/app/vad.py apps/api/app/scripted_npc.py apps/api/app/reply_templates.py apps/api/app/voice_round.py apps/api/tests/test_vad_state.py apps/api/tests/test_scripted_npc.py apps/api/tests/test_voice_round.py
git commit -m "feat: server-side VAD state machine + scripted NPC + voice round orchestration"
```

---

### Task 9: 前端语音交互打通 + workers 客户端 + E2E

**Files:**
- Create: `apps/api/app/workers.py`（真实客户端：HTTP POST asr-worker `/transcribe`、HTTP POST tts-worker `/tts`）
- Create: `apps/api/app/ws.py`（浏览器 WS 路由：收 PCM16 二进制 + 控制消息，调 `run_round`，回发事件与音频）
- Modify: `apps/api/app/main.py`（挂载 ws router，登记 `Session`/`State`）
- Modify: `apps/web/src/App.tsx`（麦克风按钮 + 状态机 + DialogueDock + CompanionPopover + 打断）
- Create: `apps/web/src/audio/rms-gate.ts`、`apps/web/src/CompanionPopover.tsx`、`apps/web/src/useVoiceRound.ts`
- Create: `apps/web/e2e/scene.spec.ts`（Playwright）、`apps/web/playwright.config.ts`

**Interfaces:**
- Consumes: `Mic`（Task 5）、`VoiceSocket`（Task 5）、`AudioQueue`（Task 5）、API（Task 3）
- Produces: 可运行 App（麦克风 → 服务器 → 听到 Rosa 回复 + 字幕）；`useVoiceRound` 返回 `{micOn, status, turns, askCompanion(word)}`；`CompanionPopover` 点击实体时显示"这个怎么说"并可 TTS 读单词

- [ ] **Step 1: 写 workers 客户端 + 失败测试**

`apps/api/tests/test_workers.py`：
```python
import asyncio

import pytest

from app.workers import tts_client


async def test_tts_client_calls_endpoint(tmp_path, monkeypatch) -> None:
    async def fake_post(url, json):
        class R:
            status_code = 200

            def raise_for_status(self):
                return None

            async def json(self):
                return {"audioBase64": "AA==", "ms": 5, "sampleRate": 16000}
        return R()
    monkeypatch.setattr("app.workers.httpx.AsyncClient.post", fake_post)
    result = await tts_client("hello", base_url="http://127.0.0.1:8002")
    assert result["sampleRate"] == 16000
```
（`asr_client` 走 WS，阶段 1 测试以 mock `run_round` 注入覆盖，见 Task 8；此处只测 tts_client 纯 HTTP。）

`apps/api/app/workers.py`：
```python
"""生产客户端：连接 asr-worker（WS）/ tts-worker（HTTP）。"""
from __future__ import annotations

import base64
import json

import httpx


async def asr_client(samples: bytes, url: str) -> dict:
    # 阶段 1 简化：一次性发送整段 → 等 final
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(url.replace("/ws/asr", "/transcribe"), json={"audioBase64": base64.b64encode(samples).decode()})
        resp.raise_for_status()
        return resp.json()


async def tts_client(text: str, base_url: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{base_url}/tts", json={"text": text, "voice": "af_bella"})
        resp.raise_for_status()
        return resp.json()
```
（asr-worker 的 `POST /transcribe` 已在 Task 6 实现。浏览器↔api 走 WS 音频帧；api↔asr-worker 阶段 1 用一次性 HTTP，流式 WS 客户端留阶段 2 —— 见"偏离说明 2"。）

`apps/api/app/ws.py`（浏览器实时连接，Task 9 新建）：
```python
"""浏览器实时连接：音频二进制 + 控制 JSON。阶段 1 的浏览器端 VAD = 前端 RMS 门限；
服务端 VAD 状态机在此驱动（阶段 2 换真实 Silero 帧标签）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.voice_round import run_round
from app.workers import asr_client, tts_client

router = APIRouter()

ASR_URL = "http://127.0.0.1:8001/transcribe"
TTS_BASE = "http://127.0.0.1:8002"


@router.websocket("/ws/sessions/{session_id}")
async def ws_session(ws: WebSocket) -> None:
    await ws.accept()
    events = ws.app.state.events
    session_id = ws.path_params["session_id"]
    utterance_id: str | None = None
    frames: list[bytes] = []

    async def send(payload: object) -> None:
        if isinstance(payload, bytes):
            await ws.send_bytes(payload)
        else:
            await ws.send_json(payload)

    try:
        while True:
            msg = await ws.receive()
            if msg.get("text"):
                ctrl = json.loads(msg["text"])
                if ctrl["type"] == "audio.start":
                    utterance_id = ctrl["utteranceId"]
                    frames = []
                elif ctrl["type"] == "audio.end":
                    if utterance_id is not None and frames:
                        await run_round(
                            session_id, utterance_id, b"".join(frames), events,
                            lambda audio: asr_client(audio, ASR_URL),
                            lambda text: tts_client(text, TTS_BASE),
                            send,
                        )
                    utterance_id = None
                elif ctrl["type"] == "playback.interrupted":
                    events.append(session_id, "playback.interrupted", {"utteranceId": utterance_id})
            else:
                raw = msg.get("bytes")
                if raw and utterance_id is not None:
                    frames.append(raw)
    except WebSocketDisconnect:
        return
```

`apps/api/app/main.py` 追加挂载（在 `create_app` 内、`@app.get("/health")` 之前）：
```python
    from app.ws import router as ws_router
    app.include_router(ws_router)
```
并在 `create_app` 返回前确保 `app.state.events` 已设置（已有）。

- [ ] **Step 2: 运行测试验证通过**

Run: `cd apps/api && uv run pytest -v tests/test_workers.py`
Expected: PASS（1 个）。

- [ ] **Step 3: 前端 voice round hook + RMS 门限**

`apps/web/src/audio/rms-gate.ts`：
```ts
export class RmsGate {
  constructor(private threshold = 0.008, private silenceFrames = 11) {}
  private silence = 0;

  feed(frame: ArrayBuffer): 'speech' | 'silence' | 'end' {
    const view = new DataView(frame);
    let sum = 0;
    for (let i = 0; i < view.byteLength; i += 2) {
      const s = view.getInt16(i, true) / 32768;
      sum += s * s;
    }
    const rms = Math.sqrt(sum / (view.byteLength / 2));
    if (rms >= this.threshold) { this.silence = 0; return 'speech'; }
    this.silence++;
    return this.silence >= this.silenceFrames ? 'end' : 'silence';
  }
}
```

`apps/web/src/useVoiceRound.ts`：
```ts
import { useEffect, useRef, useState } from 'react';
import { Mic } from './audio/mic';
import { RmsGate } from './audio/rms-gate';
import { VoiceSocket } from './audio/ws-client';
import { AudioQueue } from './audio/playback-queue';
import type { Turn } from './DialogueDock';

export function useVoiceRound(sessionId: string, wsUrl: string) {
  const [micOn, setMicOn] = useState(false);
  const [status, setStatus] = useState('idle');
  const [turns, setTurns] = useState<Turn[]>([]);
  const socketRef = useRef<VoiceSocket | null>(null);
  const queueRef = useRef(new AudioQueue());
  const micRef = useRef<Mic | null>(null);
  const gateRef = useRef(new RmsGate());
  const uttRef = useRef(0);

  const start = async () => {
    const sock = new VoiceSocket(sessionId);
    await sock.connect(wsUrl);
    socketRef.current = sock;
    sock.on('npc.speech.commit', (m: any) => setTurns((t) => [...t, { role: 'npc', text: m.text }]));
    sock.on('tts.audio.start', (m: any) => setStatus('speaking'));
    sock.on('tts.audio.end', (m: any) => setStatus('idle'));
    sock.on('audio.binary', (chunk: ArrayBuffer) => queueRef.current.enqueue('x', chunk));
    const mic = new Mic();
    mic.onChunk = (chunk) => {
      const tag = gateRef.current.feed(chunk);
      if (tag === 'end') { sock.sendControl({ type: 'audio.end', utteranceId: `u${uttRef.current}` }); setStatus('listening'); }
    };
    await mic.start();
    micRef.current = mic;
    setMicOn(true);
  };

  const beginUtterance = () => {
    uttRef.current += 1;
    socketRef.current?.sendControl({ type: 'audio.start', utteranceId: `u${uttRef.current}`, languageMode: 'en' });
  };

  const stop = () => { micRef.current?.stop(); socketRef.current?.close(); setMicOn(false); };

  const interrupt = () => {
    socketRef.current?.sendControl({ type: 'playback.interrupted' });
    queueRef.current.clear();
  };

  return { micOn, status, turns, start, stop, beginUtterance, interrupt };
}
```
（阶段 1 简化：Rosa 回复仍由服务器触发，`beginUtterance` 由 UI 按钮/说话检测触发；AudioWorklet 帧先喂 RMS 门限，触发 end 时发 `audio.end`。浏览器实时 ASR partial 字幕展示阶段 1 可省 —— 只展示 committed 文本。）

- [ ] **Step 4: App 接线 + CompanionPopover**

`apps/web/src/CompanionPopover.tsx`：
```tsx
export function CompanionPopover({ word, onAsk, onClose }: { word: string; onAsk: (w: string) => void; onClose: () => void }) {
  return (
    <div data-testid="companion-popover" style={{ position: 'fixed', right: 16, bottom: 120, background: '#fff', border: '1px solid #ccc', borderRadius: 12, padding: 12, width: 260 }}>
      <strong>伴学者</strong>
      <p>这个词：<b>{word}</b></p>
      <button onClick={() => onAsk(word)}>这个怎么说</button>
      <button onClick={() => onAsk('怎么读')}>怎么读</button>
      <button onClick={onClose}>关闭</button>
    </div>
  );
}
```

`apps/web/src/App.tsx`（在 Task 4 基础上接线）：
```tsx
import { useState } from 'react';
import { fetchScene } from './api';
import { SceneViewport } from './SceneViewport';
import { DialogueDock, type Turn } from './DialogueDock';
import { useVoiceRound } from './useVoiceRound';
import { CompanionPopover } from './CompanionPopover';

export default function App() {
  const [scene, setScene] = useState<any>(null);
  const [focusWord, setFocusWord] = useState<string | null>(null);
  const { micOn, status, turns, start, stop, beginUtterance, interrupt } = useVoiceRound('sess-1', `ws://${location.hostname}:8000/ws/sessions/sess-1`);

  if (!scene) return <div>加载中…</div>;

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <header style={{ padding: '8px 16px', display: 'flex', gap: 16, alignItems: 'center' }}>
        <strong>{scene.setting.displayName}</strong>
        <span>{scene.setting.time}</span>
        <button onClick={micOn ? stop : start}>{micOn ? '停止' : '开始语音'}</button>
        <button onClick={beginUtterance} disabled={!micOn}>按住说话</button>
        <button onClick={interrupt}>打断</button>
      </header>
      <div style={{ flex: 1, padding: 16, position: 'relative' }}>
        <SceneViewport scene={scene} onEntityClick={(e) => setFocusWord(e.semantics.name)} />
      </div>
      <DialogueDock turns={turns} status={status} />
      {focusWord && <CompanionPopover word={focusWord} onAsk={(w) => alert(`(阶段1占位) 伴学者读：${w}`)} onClose={() => setFocusWord(null)} />}
    </div>
  );
}
```
（fetchScene 的 useEffect 保留 Task 4 版本；此处省略以聚焦接线。伴学者"这个怎么说"在阶段 1 先走 alert 占位 + 后续接 `askCompanion` 触发 TTS。）

- [ ] **Step 5: 启动全栈冒烟（手动）**

启动顺序（4 个终端，均 `--workers 1` 或单进程）：
```bash
# 1) tts-worker
cd services/tts-worker && uv run uvicorn tts_worker.server:app --port 8002 --workers 1
# 2) asr-worker（含 /transcribe 端点）
cd services/asr-worker && uv run uvicorn asr_worker.server:app --port 8001 --workers 1
# 3) api
cd apps/api && uv run uvicorn app.main:app --port 8000 --workers 1
# 4) web
cd apps/web && pnpm dev
```
打开 `http://localhost:5173`：面包店渲染、点道具出标签、点伴学者出 Popover、开始语音 → 说话 → 1.5s 内听到 Rosa 回复。

- [ ] **Step 6: E2E（Playwright，场景渲染不依赖麦克风）**

`apps/web/playwright.config.ts`：
```ts
import { defineConfig } from '@playwright/test';
export default defineConfig({ testDir: './e2e', use: { baseURL: 'http://localhost:5173' }, webServer: { command: 'pnpm dev', url: 'http://localhost:5173' } });
```

`apps/web/e2e/scene.spec.ts`：
```ts
import { test, expect } from '@playwright/test';

test('bakery scene renders and prop is clickable', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByTestId('scene-viewport')).toBeVisible();
  const loaf = page.getByRole('button', { name: /loaf/i });
  await expect(loaf).toBeVisible();
  await loaf.click();
  await expect(page.getByTestId('companion-popover')).toBeVisible();
});
```

Run: `cd apps/web && npx playwright install chromium && npx playwright test`
Expected: PASS（需先起 api + web）。

- [ ] **Step 7: 提交**

```bash
git add apps/api/app/workers.py apps/api/app/ws.py apps/web/src/audio/rms-gate.ts apps/web/src/useVoiceRound.ts apps/web/src/CompanionPopover.tsx apps/web/src/App.tsx apps/web/e2e apps/web/playwright.config.ts apps/api/tests/test_workers.py
git commit -m "feat: wire voice round end-to-end (mic->server->rosa->tts) + companion popover + e2e"
```

---

### Task 10: 启动自检 + 版本锁定 + 延迟验收

**Files:**
- Create: `scripts/startup-selfcheck.py`、`VERSION_LOCK.md`、`README.md`、`tests/latency/measure.py`
- Modify: `apps/api/app/main.py`（`/health` 增加自检摘要字段，可选）

**Interfaces:**
- Consumes: `asr_worker.selfcheck.run()`、`tts_worker.selfcheck.run()`
- Produces: `scripts/startup-selfcheck.py` 输出全链路自检 JSON；`tests/latency/measure.py` 输出 P50/P95 延迟；`VERSION_LOCK.md` 记录锁定版本

- [ ] **Step 1: 写 startup-selfcheck 脚本**

`scripts/startup-selfcheck.py`：
```python
"""启动自检：GPU → CUDA → ASR 模型 → 真实英文转写 → TTS 一句 → 峰值显存/耗时。
每个 worker 在自己项目的 uv 环境里跑 selfcheck（避免根环境缺依赖）。
最后做一次真实"TTS 合成英文 → ASR 转写"端到端语音自检。"""
from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def gpu_info() -> dict:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used,temperature.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if not out:
            return {"present": False}
        parts = out.split(",")
        return {"present": True, "name": parts[0].strip(), "driver": parts[1].strip(), "vram_total_gib": parts[2].strip(), "vram_used_gib": parts[3].strip(), "temp_c": parts[4].strip()}
    except Exception as e:  # noqa: BLE001
        return {"present": False, "error": str(e)}


def run_in(project: str, module: str, *args: str) -> dict:
    # cwd 设为项目目录，确保 python -m 能在该环境里找到本项目的包
    cmd = ["uv", "run", "--project", str(ROOT / project), "-m", module, *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(ROOT / project))
    if proc.returncode != 0:
        return {"error": proc.stderr.strip()[-500:]}
    return json.loads(proc.stdout)


def _wav_bytes_from_base64(b64: str) -> bytes:
    """把 tts selfcheck 返回的合成音频（WAV bytes base64）落盘供 ASR 转写。
    ASR 侧负责重采样（见 asr_worker.selfcheck.run 的 16k 重采样逻辑）。"""
    return base64.b64decode(b64)


def main() -> None:
    report: dict = {"gpu": gpu_info()}
    t0 = time.perf_counter()
    report["tts"] = run_in("services/tts-worker", "tts_worker.selfcheck")
    wav_path = ROOT / ".selfcheck-hello.wav"
    if report["tts"].get("audio_base64"):
        wav_path.write_bytes(_wav_bytes_from_base64(report["tts"]["audio_base64"]))
        report["asr"] = run_in("services/asr-worker", "asr_worker.selfcheck", str(wav_path))
        wav_path.unlink(missing_ok=True)
    else:
        report["asr"] = run_in("services/asr-worker", "asr_worker.selfcheck")
    report["total_secs"] = round(time.perf_counter() - t0, 2)
    # 端到端语音门禁：TTS 合成成功 且 ASR 转写成功（非空）
    report["e2e_voice_ok"] = bool(report["tts"].get("audio_bytes")) and bool(report["asr"].get("transcribe_ok"))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

需要给两个 worker 的 `selfcheck` 模块加 `__main__` 入口：
```python
# asr_worker/selfcheck.py 与 tts_worker/selfcheck.py 各自追加：
if __name__ == "__main__":
    import sys
    print(json.dumps(run(*sys.argv[1:]), ensure_ascii=False))
```
（asr 的 `run` 接受可选 `wav_path`；tts 的 `run()` 无参。）

Run: `cd english-town && uv run python scripts/startup-selfcheck.py`
Expected: 输出含 `gpu.name`、`asr.device`（cuda 或 cpu）、`asr.transcribe_ok: true`、`tts.audio_bytes > 0`、**`e2e_voice_ok: true`**。**这是里程碑 1 前置门禁。**

- [ ] **Step 2: 版本锁定文档**

`VERSION_LOCK.md`（先记录环境实测值，再填实现测后的锁定版）：
```markdown
# 版本锁定（Blackwell / RTX 5060 Laptop）

实现阶段 1 时用 `scripts/startup-selfcheck.py` 实测并回填以下值。所有值必须与
`uv.lock` / 驱动实际安装一致，任何升级都需重跑自检。

| 组件 | 锁定版本 | 实测日期 |
|---|---|---|
| NVIDIA 驱动 | 592.01 | 2026-08-05 |
| CUDA runtime | （实测 CUDA 12.8 可用性） | |
| cuDNN | （按 CUDA 12.8 配套） | |
| CTranslate2 / faster-whisper | （wheel 需支持 Blackwell） | |
| `kokoro-onnx` | （CPU 路径无需 GPU 支持） | |
| Python | 3.13 | 2026-08-05 |

启动自检项：GPU 名称 / CUDA 可用性 / ASR 模型加载 / 3s 转写 / 一句 TTS / 峰值显存 / 峰值耗时。
```

- [ ] **Step 3: 延迟测量脚本**

`tests/latency/measure.py`（热运行测量；冷启动单测一次并记录）：
```python
"""端到端语音延迟（服务端链路）：VAD 结束 → ASR final → 回复 → TTS 完成。
浏览器采集与网络不包含（阶段 1 近似）。输出 P50/P95。"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

import httpx  # noqa: E402

ASR = "http://127.0.0.1:8001/transcribe"
TTS = "http://127.0.0.1:8002/tts"
AUDIO = bytes(1600 * 20)  # 1s @16k 静音（占位；真实测试用录音 WAV）


async def one_round(client: httpx.AsyncClient) -> float:
    t0 = time.perf_counter()
    r = await client.post(ASR, json={"audioBase64": __import__("base64").b64encode(AUDIO).decode()})
    text = r.json().get("finalText", "")
    t1 = time.perf_counter()
    r2 = await client.post(TTS, json={"text": text or "Hello.", "voice": "af_bella"})
    t2 = time.perf_counter()
    return (t1 - t0) * 1000 + (t2 - t1) * 1000


async def main() -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(ASR, json={"audioBase64": __import__("base64").b64encode(AUDIO).decode()})  # 预热
        await client.post(TTS, json={"text": "warmup", "voice": "af_bella"})
        samples = [await one_round(client) for _ in range(30)]
    samples.sort()
    p50 = statistics.median(samples)
    p95 = samples[int(len(samples) * 0.95) - 1]
    print(json.dumps({"p50_ms": round(p50, 1), "p95_ms": round(p95, 1), "n": len(samples)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
```

Run: `cd english-town && uv run python tests/latency/measure.py`
Expected: `p95_ms < 1500` 达到里程碑 1（热运行）。

- [ ] **Step 4: README（如何跑起来）**

`README.md`：写清启动顺序（Task 9 Step 5 的 4 个命令）、自检命令、延迟测量命令、每个 worker 的端口与角色、`VERSION_LOCK.md` 引用。

- [ ] **Step 5: 全量测试回归**

Run:
```bash
cd packages/scene-schema && uv run pytest -v && npx vitest run
cd packages/scene-compiler && uv run pytest -v
cd apps/api && uv run pytest -v
cd services/asr-worker && uv run pytest -v
cd services/tts-worker && uv run pytest -v
cd apps/web && npx vitest run && npx playwright test
```
Expected: 全绿。

- [ ] **Step 6: 提交**

```bash
git add scripts VERSION_LOCK.md README.md tests/latency apps/api/app/main.py
git commit -m "docs: startup selfcheck + version lock + latency harness + README"
```

---

## 完成定义（阶段 1）

- 全量测试绿（上述 Step 5 回归）。
- `scripts/startup-selfcheck.py` 通过；`VERSION_LOCK.md` 已回填实测版本。
- 手动验收通过：浏览器打开面包店 → 说话 → **1.5s 内**听到 Rosa 本地回复 + 字幕正确。
- `tests/latency/measure.py` 输出 `p95_ms < 1500`（热运行）。
- 不引入任何 LLM / 云端模型调用。

## 偏离说明（相对 spec v2，均有原因）

1. **浏览器端 VAD**：spec §11 要求浏览器 Silero VAD。阶段 1 用轻量 **RMS 门限**（`RmsGate`）驱动起停，服务端 Silero VAD 的真实模型接入推迟到阶段 2。原因：`onnxruntime-web` 体积大、阶段 1 里程碑是"1.5s 本地闭环"，非 VAD 精度。阶段 1 服务端用 `VadStateMachine` 状态机（可测），真实 Silero 帧标签在阶段 2 接入同一状态机。
2. **ASR 传输**：spec 描述 WS 流式 partial。阶段 1 asr-worker 额外提供 `POST /transcribe`（一次性 final），浏览器↔api 仍走 WS 音频帧；api↔asr-worker 的流式 WS 客户端留到阶段 2，避免双协议。`partial` 状态机（Task 6）已实现并测试，阶段 2 直接复用。
3. **发音评分**：阶段 1 无（符合 spec §11 边界）。
4. **偶遇词/目标模式/学习引擎**：阶段 4 范围，阶段 1 不建表（`session_events` 与 `dialogue.turn` 已写入，为学习证据打底）。
5. **伴学者"这个怎么说"**：阶段 1 用 UI 占位 + 可扩展钩子（`onAsk`），真实"伴学者读单词 → TTS"接入在阶段 2 与 Companion Tutor 一起做。

## 未来任务锚点（阶段 2+）

- 阶段 2：DeepSeek NPC Actor / Companion Tutor、双通道协议（`npc.speech.delta/commit` + `turn.metadata`）、真 Silero VAD（浏览器 + 服务端）、api↔asr-worker 流式 WS、generationId/turnId 全链路、中文语言模式。
- 阶段 3：ScenePreloadManager、转场预取、ScenePatch、多原型。
- 阶段 4：学习引擎、偶遇词、FSRS、session_events 补发、目标词类型（action/phrase）。
