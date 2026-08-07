# 英语小镇 · 阶段 3 设计文档：场景系统（Scene Director + ScenePreloadManager + 回合仲裁 + gesture）

日期：2026-08-07（草稿，头脑风暴产出）
状态：v1 草稿

> 本阶段是 spec `docs/superpowers/specs/2026-08-05-english-town-design.md` v2 的**里程碑 3** 落地：
> "Scene Director + ScenePreloadManager：原型目录 + 本地骨架 + 异步填充 + 转场预取 + 回合仲裁。验收＝骨架 P95<300ms / 完整 P95<2s。"
>
> 并兑现阶段 2 明确推迟的两项：**gesture 产出与渲染**、**场景切换时 generationId 每场景递增**（阶段 2 是会话级固定）。

---

## 0. 前置事实（阶段 2 合并后代码 5d9f2ec）

- **当前只有一个固定面包店场景**：`apps/api/app/scene_store.py` 的 `get_compiled_scene` 硬编码 `template_scene_plan()` → 单一 sceneId、单一 NPC Rosa；`assets/archetypes/` 只有 `bakery.json`。
- **NPC Actor 人格硬编码**：`apps/api/app/llm/npc_actor.py:18` system prompt 写死 "You are Rosa…bakery"；`_scene_hint()` 把场景词表当**提示**（`"Items nearby: …"`）而非白名单；`validate_speech` 只校验**格式**不校验词汇；`lexmatch` 事后记账。
- **generationId 会话级固定**：`SessionState.generation_id` 创建时设一次、从不改变；前端 turnGate 是 3 参 `isAcceptedTurn(current, companion, turnId)`（无 generationId）——阶段 2 有意识的推迟。
- **已有资产/包**：`packages/scene-compiler`（`compile_from_docs` / `template_scene_plan`）、`packages/scene-schema`（zod/pydantic）、`assets/icons/icon-map.json`、双通道 WS 协议、turnId 门控、cost 护栏（`llm_session_call_cap=200` / 并发 2 / tutor 同词合并）、事件日志 `session_events`。
- **全局约束**（沿用阶段 1/2，每个任务隐式遵守）：Uvicorn 单 worker；SQLite WAL + 单写队列；先写库再发送；无 `innerHTML`；组件白名单；坐标 `0..1000`；热区 ≥44px；单场景 DOM 实体 ≤40；不加载外部 URL；**16GB 内存、严禁高并发/并行进程、严格串行**。

---

## 1. 已确认决策

| # | 决策 | 结论 |
|---|---|---|
| 1 | 阶段 3 范围 | **完整阶段 3**：场景系统（多原型 + 本地骨架先行 + Scene Director 异步填充 + 转场预取）+ 回合仲裁 + gesture（point + 情绪）。一个计划。 |
| 2 | 场景驱动 | **广场 hub + 自由逛**：起始场景＝小镇广场（hub），用户走进任一出口 → 进入目标场景；每个场景由 Scene Director 用原型 + 实体目录填充。阶段 4 才有目标词表，阶段 3 不走 ArchetypeMatcher/WordPack。 |
| 3 | 场景原型 | **6 个**：广场（hub）+ 面包店/公园/车站/咖啡馆/图书馆。面包店已有，新增 5 个 JSON。 |
| 4 | Director 落地方式 | **Mock-first 完整闭环**（沿阶段 2 模式）：`SceneDirector` 抽象 + 确定性 mock（故障注入）+ OpenAI 兼容真实现（复用 `llm/client.py`）。无 key 全绿；golden 实测留真 key。 |
| 5 | gesture 深度 | **point 指物 + 情绪手势**（wave/nod/shake）。 |
| 6 | 对话自由度 | 场景词表＝**教学锚点**，**不是话题白名单**；NPC 人格从 NPC catalog 按 activeSpeaker 动态注入；校验只留格式层。 |
| 7 | objectives | 阶段 3 **留空**（Assessment Engine 阶段 4）。场景只填充不派任务。 |
| 8 | 回合仲裁 | 多 NPC 可见（0–4）、**同时仅 1 个主动**；其余做短台词/动画；`pendingSpeakers` 留 schema、v1 不排队（打断直接取消旧回合，沿用阶段 2）。 |
| 9 | 存储 | **不新增 DB 表**。场景当前状态＝进程内 + `session_events` 事件投影（`scene.entered` / `scene.patch` 是事件）；断线重放重建。 |

---

## 2. 架构总览 + 场景生命周期

**所有新增都在 `apps/api` 内、不新增进程**（沿用阶段 2）。场景生命周期：

```
用户点击出口（门/箭头）
  → client: scene.request { exitId }
  → server: ScenePreloadManager 切入新场景
       ① 新 sceneId + generationId 递增（场景切换 → 旧 generation 全部作废）
       ② 本地确定性编译骨架：scene.skeleton（渐变背景 + 装饰 + 槽位占位实体 + 出口） ← 骨架 P95<300ms
       ③ 记 session_events: scene.entered
  → server: SceneDirector（LLM，mock 优先）提案 ScenePlan
  → 校验 → 转成受限 JSON Patch → scene.patch（幂等：patchId 去重 + baseRevision 防乱序）
       ④ 记 session_events: scene.patch
  → client: 应用 patch → 完整可交互场景 ← 完整 P95<2s
```

**关键改造**：阶段 2 的 `SceneStore.get_compiled_scene`（静态单场景）替换为**每会话场景状态机**：

```text
apps/api/app/
  llm/scene_director.py  # SceneDirector 提案（mock 优先 + 真实现）
  scene_lifecycle.py     # per-session 场景状态机：骨架→patch→degraded；generationId 递增
  arbitration.py      # 回合仲裁状态（activeSpeaker/focus…）
  scene_store.py      # 改造：archetype 目录 + 实体/NPC 目录加载 + 骨架编译 + patch 应用
  ws.py               # 场景消息路由（scene.request/skeleton/patch/prefetch/focus/degraded）
  llm/proposals.py    # + validate_scene_plan
  llm/npc_actor.py    # 人格动态注入 + 场景词表动态
```

---

## 3. 场景协议（新消息类型）

沿用双通道协议风格，全部消息携带 `sceneId / generationId`：

| 方向 | 消息 | 说明 |
|---|---|---|
| C→S | `scene.request { exitId }` | 点击出口 |
| C→S | `npc.focus { entityId }` | 点击 NPC（仲裁切换 activeSpeaker） |
| S→C | `scene.skeleton { sceneId, generationId, archetypeId, revision, setting, entities, exits }` | 骨架，立即渲染 |
| S→C | `scene.prefetch { archetypeId, skeleton }` | 进场后预推"下一出口"骨架，客户端缓存 |
| S→C | `scene.patch { sceneId, generationId, baseRevision, patchId, ops[] }` | 增量填充（add/replace/remove，白名单路径） |
| S→C | `scene.focus { activeSpeaker, focusSource, focusExpiresAt }` | 仲裁状态广播 |
| S→C | `scene.degraded { sceneId, reason }` | Director 失败，骨架停留 |

**generationId 门控（硬规则）**：
- **服务端**：会话持有 `currentScene.generationId`，每次场景进入递增；**发送前**比对，不匹配丢弃（被取消/旧场景晚到的 LLM delta / TTS 分块 / patch 一律不发）。
- **前端**：持有 `currentGenerationId + currentTurnId`；旧 generation 的 patch/音频/字幕/metadata **一律丢弃**。
- **测试**：转场中途，旧场景晚到的 patch/音频 → 断言无应用、无播放、无字幕更新。

---

## 4. Scene Director + 校验边界 + 降级

### SceneDirector（`apps/api/app/llm/scene_director.py`）

- **输入（全 trusted 结构化）**：目标 `archetypeId`（本地确定）＋ 每个槽位的**候选实体目录**（`assets/catalog/entities.json`，按 category 过滤，LLM 只能从候选里选）＋ 角色候选（`assets/catalog/npcs.json`，按 archetype `npcSlots.role` 过滤）＋ 场景上下文（去过的场景记忆）。
- **输出 = 提案**：`ScenePlan { fills: [{slotId, wordId}], characters: [{slotId, npcId}], setting: {displayName, time} }`。**objectives 恒为空数组**。
- **实现**：复用 `llm/client.py` 的 `complete_json`（JSON-mode，`temperature=0.3`，`llm_total_timeout_tutor_s` 同档）；provider 无关；`MOCK_SCENE_SCENARIO = ok|timeout|connect_error|invalid_json|slot_mismatch|unknown_word|too_many_entities`。
- **成本**：每次转场 1 次调用，记 `llm_calls`（`role="scene_director"`），与 `llm_session_call_cap=200` 共享；budget 耗尽 → 直接骨架 + `fallback_reason=budget`。

### 校验边界（`proposals.validate_scene_plan`）

| 规则 |
|---|
| 每个 `fills[].wordId` 必须 ∈ 该槽位 category 的候选实体目录（服务端查目录，不信 LLM） |
| 每个 `characters[].npcId` 必须 ∈ 该 archetype `npcSlots.role` 的角色候选 |
| 填充后实体总数 ≤ 40；坐标由槽位编译器计算，**不由 LLM 生成** |
| 填充实体的 `visualKey` 必须 ∈ 图标映射表 |
| patch 只允许改白名单路径；**拒绝改** sceneId / schemaVersion / 用户档案 / 学习证据 |
| 超时/校验失败 → **骨架停留**（`scene.degraded`），绝不应用不完整填充 |

### 降级

- Director 超时 / connect 失败 / invalid_json / 校验失败 → 骨架停留为**半成品场景**（`status: degraded`：渐变背景 + 装饰 + 槽位占位实体 + 出口可用；NPC 走本地 scripted 模板），记 `fallback_reason`。
- **不做 Narrative Repair 自动修复**（校验失败直接降级，延续阶段 2 既定，Narrative Repair 继续推迟）。

---

## 5. 对话自由度原则（NPC Actor）—— 显式硬原则

> 场景词表是**教学锚点**，不是**话题栅栏**。

- 场景词表（Director 填的实体）作为 prompt 的 `"Items nearby: …"` 提示，帮助 NPC **主动引导**到焦点词；但**词汇层零限制**。
- 用户聊任何方向（包括完全不在场景词表里的内容）→ NPC 自然跟随、保持人格一致，只要输出过**格式校验**（纯英文 ASCII、长度、无代码块/URL）。
- `lexmatch` 只做**事后记账**（这句实际提到了哪些场景词 → 学习证据用），反向不约束 NPC。
- **人格动态注入**：`npc_actor` 的 system prompt 不再写死 "You are Rosa…bakery"，而是从 `npc_catalog` 取当前 `activeSpeaker` 的 persona（角色名 / 场景身份 / 教学等级 A1-A2）；多 NPC 切换说话人 = 换人格。
- **验证方式**：测试构造"用户问场景词表之外的问题"，断言 NPC 正常流式回复、不降级、不拒绝。

---

## 6. 回合仲裁

会话状态新增仲裁字段（主规格 §9）：

```json
{ "activeSpeaker": "npc:rosa", "conversationFocus": "npc:rosa",
  "focusSource": "user_click", "focusExpiresAt": 1780000000000,
  "pendingSpeakers": [], "companionInterruptionPolicy": "on_request_only" }
```

- 焦点优先级：**点击 > 称呼 > 最近说话角色 > 空间指向 > LLM 解析**。
- 场景 0–4 个可见 NPC，**同时仅 1 个进入主动对话态**；其余 NPC 只做短台词/动画。
- 点击某 NPC → `npc.focus { entityId }` → 切换 activeSpeaker（广播 `scene.focus`）。
- 用户说话 → 当前 activeSpeaker 回复（其 persona 注入 NPC Actor）。
- 伴学者🦊 常驻所有场景、不占 NPC 槽位、`on_request_only`（默认不插话，只在点击/称呼/连续失败时响应）。
- **v1 不排队**：`pendingSpeakers` 留 schema 位，打断直接取消旧回合（沿用阶段 2 append-only 打断）。

---

## 7. 前端场景渲染 + turnGate + gesture

### 场景状态机（Zustand）

```ts
scene: { sceneId, generationId, archetypeId, revision,
         status: "skeleton" | "filled" | "degraded",
         entities, exits, setting }
```

- 收 `scene.skeleton` → 渲染骨架（占位实体 + 槽位位置 + 出口门），状态 `skeleton`
- 收 `scene.patch` → 应用 ops → 状态 `filled`
- 收 `scene.degraded` → 骨架停留 + 小徽标
- 旧 generationId 任何消息 → 丢弃

### turnGate 升级

`apps/web/src/audio/turnGate.ts`：3 参 → **4 参** `isAcceptedTurn(currentGenId, currentTurnId, companion, msgGenId, msgTurnId)`。音频块、字幕、metadata、patch 全过这道门。

### SceneViewport 扩展

- 背景从硬编码渐变 → **archetype 驱动**（`archetype.background.gradient` + decor 渲染）
- 出口门组件可点击 → `scene.request`
- skeleton→filled 之间轻量 shimmer；degraded 显示徽标
- 实体数 ≤40、热区 ≥44px、坐标 0..1000 约束沿用

### Gesture 渲染（`npc.turn.metadata.gesture`）

| type | 前端表现 |
|---|---|
| `point {entityId}` | 目标实体高亮描边动画 + NPC 旁浮动 👉 |
| `wave` | 👋 浮动 |
| `nod` | 绿色 ✓ 弹跳 |
| `shake` | 红色 ✗ 摆动 |

服务端校验：`type` ∈ 枚举；`entityId` 若给出必须 ∈ 当前场景实体，否则**只拒掉 gesture、其余 metadata 照常**（不整轮降级）。

---

## 8. 转场预取

- `ScenePreloadManager`（per-session）：进场后预推**最多 1 个**"下一出口"的骨架（只缓存原型骨架 + 槽位参数，**不预生成完整内容**；预取 TTL 默认 60s，过期需重新请求 skeleton）。
- 目标选择：目标场景是 hub 时选**最近去过的目的地**（无则第一个出口）；否则预取 hub。
- 点击出口时：缓存命中 → 骨架立即渲染再等 patch；未命中 → 发 `scene.request` 等 skeleton。

---

## 9. 断线补发

- 重连 → client 发最后确认 `sequence` + 当前 `sceneId/generationId` → 服务端从 `session_events` 重放 `scene.entered` + `scene.patch` 重建场景 → 恢复对话。
- 音频帧**不补发**；重连时**取消当前 utterance**（沿用阶段 2 规则）。
- `session_events` 新增事件类型：`scene.entered`、`scene.patch`。

---

## 10. 成本与安全

- Scene Director 每次转场 1 次；与 NPC/Tutor 共享 `llm_session_call_cap=200` + `llm_concurrency_limit=2`；budget → 骨架 + `fallback_reason=budget`。
- 所有 LLM 输入结构化（trusted 模板 + untrusted 数据分开）；输出严格白名单 + 服务端校验（§4）；`candidateWordIds` 全服务端派生。
- `scene.patch` 白名单路径，拒绝改 sceneId/schemaVersion/用户档案/学习证据。

---

## 11. 资产工作

| 资产 | 内容 |
|---|---|
| `assets/archetypes/` | 新增 广场/公园/车站/咖啡馆/图书馆 5 个 JSON（面包店已有，核对槽位）；各含 background/zones/propSlots/npcSlots/exits |
| `assets/catalog/entities.json` | 分类实体目录：category → [{wordId, name, lemma, visualKey}]，Director 候选源（新增） |
| `assets/catalog/npcs.json` | 角色目录：role → [{npcId, name, persona, emoji, voice}]，Rosa 在内（新增） |
| `assets/icons/icon-map.json` | 扩展覆盖目录内全部 visualKey（缺映射时落占位符，不崩） |

---

## 12. 测试与验收

### 12.1 单测（mock Director / mock LLM，含故障注入）

| 用例 |
|---|
| Director `ok`：合法 ScenePlan 应用 → 完整场景 |
| Director 故障注入全覆盖：timeout / connect_error / invalid_json / slot_mismatch / unknown_word / too_many_entities → 各走对应降级路径 |
| 骨架编译确定性（同 archetype → 同骨架） |
| patch 幂等：patchId 去重、baseRevision 防乱序 |
| validate_scene_plan 边界：wordId 不在候选、npcId 不在角色候选、实体超 40、visualKey 不在映射 → 全拒 |
| 对话自由度：用户问场景词表之外 → NPC 正常流式、不降级 |
| gesture 校验：合法 type / entityId 不在场 → 只拒 gesture |

### 12.2 集成 + 回归 + 冒烟

| 层 | 内容 |
|---|---|
| 场景生命周期集成 | scene.request → skeleton → patch → filled；**转场中途旧 generation 晚到 → 丢弃**（无应用/无播放/无字幕） |
| 回合仲裁 | 点击切换 activeSpeaker、说话跟随、companion on_request_only |
| 预取 | 骨架缓存命中即时渲染 |
| 断线补发 | 重放 scene.entered + scene.patch 重建场景 |
| state-audit | 扩展新事件类型，仍断言仅 session_events / llm_calls / tutor_cache 变化 |
| 回归 | 阶段 1/2 全部 Python/vitest 套件保持绿 |
| 手动冒烟 | 真 key golden（Director 调用延迟/token），复用 llm-smoke 模式 |

### 12.3 启动自检

`startup-selfcheck.py` 扩展：探测 archetype/catalog 目录完整性 + Director 探活（失败 → 骨架场景 + 明确日志）。

**验收指标**（主规格）：已缓存原型骨架 P95 < 300ms；云端填充后完整场景可交互 P95 < 2s。本地先记录实测数据，真 key golden 再定值。

---

## 13. 顺手清理（阶段 2 carry 落地）

1. **`OpenAIClient.aclose()`**：`apps/api/app/llm/client.py` 加 FastAPI lifespan shutdown 钩子，显式关闭 httpx 池。
2. **tutor prompt 120 字符硬编码**：`apps/api/app/llm/tutor.py` 的 "under 120 characters" 改从 `settings.llm_max_scaffold_chars` 动态取。
3. **env quirks 文档化**：vitest `--maxWorkers=1`、vite shim 换根提升构建、tts-worker 缺 `babel.core`、ASR venv 缺 `cublas64_12.dll`、无 `DEEPSEEK_API_KEY` → golden 手动测量——记入 `VERSION_LOCK.md` 或 README。

---

## 14. 范围外（本阶段明确不做）

- Narrative Repair 自动修复（校验失败直接降级）
- Assessment Engine / objectives 评分 / 学习证据入账（阶段 4）
- Silero VAD（浏览器+服务端）/ api↔asr 流式 WS / 中文语言模式（延续阶段 2 推迟）
- 渐进提示（情境→首字母→揭晓+带读）/ 自由输入追问（阶段 4 学习引擎一起）
- 复杂 WorldMemory（向量记忆抽取）
- 场景持久化 DB 表（`scenes`/`scene_revisions` 留阶段 4；阶段 3 事件投影 + 进程内即可）
- 目标模式 / ArchetypeMatcher / WordPack（阶段 4）

---

## 15. 交付物形态

- **新建**：`apps/api/app/llm/scene_director.py`、`apps/api/app/scene_lifecycle.py`、`apps/api/app/arbitration.py`、`assets/archetypes/{plaza,park,station,cafe,library}.json`、`assets/catalog/{entities,npcs}.json`、`scripts/scene-smoke.py`（可选 golden）
- **修改**：`apps/api/app/scene_store.py`、`apps/api/app/ws.py`、`apps/api/app/llm/proposals.py`（+validate_scene_plan）、`apps/api/app/llm/npc_actor.py`（人格动态 + 场景词表动态）、`apps/api/app/llm/tutor.py`（120 动态）、`apps/api/app/llm/client.py`（aclose）、`apps/api/app/main.py`（lifespan）、`apps/web/src/audio/turnGate.ts`（4 参）、`apps/web/src/useVoiceRound.ts`、`apps/web/src/SceneViewport.tsx`（archetype 驱动 + 出口 + 骨架/patch 状态）、`apps/web/src/registry.tsx`（door + gesture）、`apps/web/src/CompanionPopover.tsx`（companion 常驻）、`assets/icons/icon-map.json`
- **测试**：`apps/api/tests/test_scene_{lifecycle,director,validation,arbitration,prefetch,replay,gesture}.py` 等；前端 vitest 补 case
- 实现计划由 `writing-plans` 输出（`docs/superpowers/plans/2026-08-07-english-town-phase3.md`）
