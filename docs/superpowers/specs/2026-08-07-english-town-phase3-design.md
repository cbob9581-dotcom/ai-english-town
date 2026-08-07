# 英语小镇 · 阶段 3 设计文档 v2：场景系统（Scene Director + ScenePreloadManager + 回合仲裁 + gesture）

日期：2026-08-07（草稿）→ 2026-08-07（v2 修订，吸收技术评审：预取改 ScenePlan、骨架完整可玩、town-map、校验分层、门控矩阵、conceptId）
状态：v2 待用户复核

> 本阶段是 spec `docs/superpowers/specs/2026-08-05-english-town-design.md` v2 的**里程碑 3** 落地：
> "Scene Director + ScenePreloadManager：原型目录 + 本地骨架 + 异步填充 + 转场预取 + 回合仲裁。"
>
> 并兑现阶段 2 明确推迟的两项：**gesture 产出与渲染**、**场景切换时 generationId 每场景递增**（阶段 2 是会话级固定）。
>
> **v2 相对 v1 的核心修订**（吸收技术评审）：① 预取对象从"骨架"改为 **ScenePlan**（骨架是本地确定性编译、无条件 <300ms，预取它无收益；真正的等待是 Director 的 1–3s）；② 骨架从"占位"翻转为**本地确定性默认填充、完整可玩**，Director 从填空变**改进/丰富**，degraded 路径随之闭合；③ **town-map.json 本地邻接表**固定 exitId→archetypeId，不依赖 LLM；④ visualKey/wordId 校验**按层分开**（构建期 vs 运行期、整 plan vs 单条）；⑤ generationId 门控**矩阵化**并拆成两个门（turn / scene）；⑥ catalog 用 **conceptId+lemma+pos**，wordId 服务端 resolve，对齐阶段 4。

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
| 10 | 预取对象 | **ScenePlan**（Director 提案，服务端缓存），**非骨架**。骨架本地编译无条件快，无需预取。预取触发＝spoke 进场即预取 back + hub hover 出口（去抖、budget 护栏）。 |
| 11 | 骨架定位 | **完整可玩**：本地确定性默认填充（每槽位按 seed 哈希取默认实体、每角色取默认 NPC），Director 做**改进/丰富**而非填空。degraded = 骨架 = 默认场景，天然可玩。 |
| 12 | 出口映射 | **town-map.json 本地邻接表**（start + edges），exitId→archetypeId 全本地确定性，不经 LLM。 |
| 13 | conceptId | catalog 存 `conceptId + lemma + pos`；wordId 由服务端 resolve（阶段 4 查 `learning_items`，否则 `word_{lemma}_{pos}_{sense}`）；Director 提 conceptId。 |
| 14 | 主规格偏差 | v1→v2 的预取/骨架/校验分层实现偏离主规格 §6 字面（"预取只缓存骨架"），但**保留其本意**（骨架快、填充异步、LLM=提案）。偏差理由见 §8。 |

---

## 2. 架构总览 + 场景生命周期

**所有新增都在 `apps/api` 内、不新增进程**（沿用阶段 2）。场景生命周期：

```
用户点击出口（门/箭头）
  → client: scene.request { exitId }
  → server: 查 town-map 得目标 archetypeId（本地确定）→ 切入新场景
       ① 新 sceneId + generationId 递增（场景切换 → 旧 generation 全部作废）
       ② 本地确定性编译**完整可玩骨架**：渐变背景 + 装饰 + 默认实体填充 + 默认 NPC + 出口 ← 骨架 P95<150ms
       ③ 记 session_events: scene.entered
       ④ ScenePlan 缓存命中（预取）→ 立即应用 → 进场即 filled（P95<400ms）
       ⑤ 未命中 → SceneDirector（LLM，mock 优先）提案 → 校验 → scene.patch → 应用（P95<2s）
       ⑥ 记 session_events: scene.patch
  → client: 应用 patch → 完整丰富场景
```

**关键改造**：阶段 2 的 `SceneStore.get_compiled_scene`（静态单场景）替换为**每会话场景状态机**：

```text
apps/api/app/
  llm/scene_director.py  # SceneDirector 提案（mock 优先 + 真实现）
  scene_lifecycle.py     # per-session 场景状态机：骨架→patch→degraded；generationId 递增
  arbitration.py         # 回合仲裁状态（activeSpeaker/focus…）
  scene_store.py         # 改造：archetype/catalog/town-map 加载 + 骨架编译（确定性默认填充）+ patch 应用
  scene_prefetch.py      # ScenePlan 服务端缓存（键=archetypeId+worldMemoryRevision，TTL 60s）
  ws.py                  # 场景消息路由（scene.request/hint/skeleton/patch/focus/degraded）
  llm/proposals.py       # + validate_scene_plan
  llm/npc_actor.py       # 人格动态注入 + 场景词表动态
  llm/concepts.py        # conceptId → wordId resolve（阶段 4 接 learning_items 的接缝）
```

**场景会话模型**：`currentScene = { sceneId, generationId, archetypeId, revision, status, entities, exits, setting }`，`generationId` 每次进场递增。

---

## 3. 场景协议（新消息类型）

沿用双通道协议风格，场景类消息携带 `sceneId / generationId`：

| 方向 | 消息 | 说明 |
|---|---|---|
| C→S | `scene.request { exitId }` | 点击出口 |
| C→S | `scene.hint { exitId }` | hover 出口（去抖 ~300ms；可选，budget 护栏）——触发该目标 ScenePlan 预取 |
| C→S | `npc.focus { sceneId, generationId, characterId }` | 点击 NPC（仲裁切换 activeSpeaker） |
| S→C | `scene.skeleton { sceneId, generationId, archetypeId, revision, setting, entities, exits }` | **完整可玩默认场景**，立即渲染 |
| S→C | `scene.patch { sceneId, generationId, baseRevision, patchId, ops[] }` | 增量丰富（add/replace/remove，白名单路径） |
| S→C | `scene.focus { activeSpeaker, focusSource, focusExpiresAt }` | 仲裁状态广播 |
| S→C | `scene.degraded { sceneId, reason, fallbackReason }` | Director 失败，骨架（默认场景）停留 |

**不新增 `scene.prefetch` 消息**：预取是纯服务端 ScenePlan 缓存，协议上客户端无感知；命中时 patch 几乎即时到达。

### generationId / turnId 门控矩阵（硬规则）

阶段 2 的会话级固定 generationId 只够拦"跨场景"；同场景打断时新旧回合 generationId 相同，必须靠 turnId。矩阵写死：

| 晚到消息 | 场景已切换 | 同场景被打断 | 拦截依据 |
|---|---|---|---|
| `npc.speech.delta` / `commit` | 丢 | 丢 | genId **且** turnId |
| `tts.audio.*` / 音频帧 | 丢 | 丢 | genId **且** turnId |
| `npc.turn.metadata` | 丢 | 丢 | genId **且** turnId |
| `scene.patch` | 丢 | **保留** | 仅 genId（+ baseRevision） |
| `companion.reply` | 丢 | **保留** | genId + companion 标记 |

**前端拆成两个门**（`apps/web/src/audio/turnGate.ts`）：
- `acceptTurnMessage(currentGenId, currentTurnId, msgGenId, msgTurnId)` —— speech/audio/metadata
- `acceptSceneMessage(currentGenId, currentSceneId, msgGenId, msgSceneId, baseRevision)` —— patch
- 一个函数塞两种语义（对话 vs 场景填充）会互相误杀；拆分后 patch 不被无关对话打断误杀。

**预取与 generationId 归属**：预取载荷**不含 generationId**（服务端内部用 `prefetchId + archetypeId` 标识）；generationId 只在实际进场时由服务端分配并递增；前端只以 `scene.skeleton` 里的 generationId 为准，缓存内容仅用于跳过等待。

**测试**：转场中途，旧场景晚到的 patch/音频 → 断言无应用、无播放、无字幕更新；同场景打断后晚到的 speech → 靠 turnId 丢弃、patch 保留。

---

## 4. Scene Director + 校验边界 + 降级

### SceneDirector（`apps/api/app/llm/scene_director.py`）

- **输入（全 trusted 结构化）**：目标 `archetypeId`（本地确定）＋ 每个槽位的**候选概念目录**（`assets/catalog/entities.json`，按 category 过滤，LLM 只能从候选 conceptId 里选）＋ 角色候选（`assets/catalog/npcs.json`，按 archetype `npcSlots.role` 过滤）＋ 场景上下文（去过的场景记忆）。
- **输出 = 提案**：`ScenePlan { fills: [{slotId, conceptId}], characters: [{slotId, npcId}], setting: {displayName, time} }`。**objectives 恒为空数组**。Director 只做**选择与丰富**（骨架已有默认填充，它替换/加装饰/设名字时间），**不能创建 conceptId / npcId / persona 文本**。
- **实现**：复用 `llm/client.py` 的 `complete_json`（JSON-mode，`temperature=0.2`——选择型任务非创作，降确定性失败率，golden 更稳；创造性留给 NPC Actor 0.8；`llm_total_timeout_tutor_s` 同档）；provider 无关。
- **故障注入**：`MOCK_SCENE_SCENARIO = ok|timeout|connect_error|invalid_json|slot_mismatch|unknown_word|too_many_entities|partial_fills|duplicate_slot`。
  - `partial_fills`＝只填一半槽位（合法但不完整）；`duplicate_slot`＝同一 slotId 出现两次。二者都过 JSON 格式校验，正是提案边界该拦的高频失败。
- **成本**：每次转场 1 次调用（预取另计），记 `llm_calls`（`role="scene_director"`），与 `llm_session_call_cap=200` 共享；budget 耗尽 → 跳过 Director/预取 → 骨架停留 + `fallback_reason=budget`。

### 校验边界（`proposals.validate_scene_plan`）——按层分开

**构建期（CI + 启动自检，缺失即失败）**：
| 规则 |
|---|
| `entities.json` 全部 `visualKey` ⊆ `icon-map.json` 全集；缺失即构建/自检失败（防"加词忘补图标，运行时整场景降级且排查指错方向"） |

**运行期（单条问题 → 只拒该条，plan 照常应用）**：
| 规则 |
|---|
| 单个 `fills[].conceptId` 不在该槽位 category 候选 → 拒该条 fill，其余照常（骨架该槽位默认项保留） |
| 单个 `visualKey` 缺映射 → 该实体落**占位图标**，plan 照常 |
| 单个 `characters[].npcId` 不在角色候选 → 拒该条，其余照常 |

**整 plan 级（结构非法 → degraded）**：
| 规则 |
|---|
| `invalid_json` / schema 解析失败 → degraded |
| 引用不存在的 slotId → degraded |
| 填充后实体总数 > 40 → degraded（骨架默认项回退） |
| 试图修改白名单外路径（sceneId / schemaVersion / 用户档案 / 学习证据）→ degraded |

> 与 §7 gesture 的处理同原则："只拒掉问题项、其余照常"，只有整 plan 结构非法才整轮降级。

### 降级

- Director 超时 / connect / invalid_json / 整 plan 非法 → 骨架停留为**完整可玩默认场景**（`status: degraded`：有默认实体、有默认 NPC、能点、能对话、出口可用），前端只需一个小徽标；记 `fallback_reason`（`scene.degraded` 事件里带一份，重放/排查时"为什么是骨架"一眼可见）。
- **不做 Narrative Repair 自动修复**（校验失败直接降级，延续阶段 2 既定，Narrative Repair 继续推迟）。

---

## 5. 对话自由度原则（NPC Actor）—— 显式硬原则

> 场景词表是**教学锚点**，不是**话题栅栏**。

- 场景词表（Director 填的实体）作为 prompt 的 `"Items nearby: …"` 提示，帮助 NPC **主动引导**到焦点词；但**词汇层零限制**。
- 用户聊任何方向（包括完全不在场景词表里的内容）→ NPC 自然跟随、保持人格一致，只要输出过**格式校验**（纯英文 ASCII、长度、无代码块/URL）。
- `lexmatch` 只做**事后记账**（这句实际提到了哪些场景词 → 学习证据用，锚定 resolve 后的 wordId），反向不约束 NPC。
- **人格动态注入**：`npc_actor` 的 system prompt 不再写死 "You are Rosa…bakery"，而是从 `npc_catalog` 取当前 `activeSpeaker` 的 persona（角色名 / 场景身份 / 教学等级 A1-A2）；多 NPC 切换说话人 = 换人格。
- **persona 永不自 LLM**：`npcs.json` 是 trusted（我方编写），Director 只能**选择** `npcId`，绝不能生成或覆写 persona 文本（persona 是唯一进 system prompt 的动态内容，放开即 injection 通道）——见 §4 校验表与 §10 显式禁止。
- **`setting.displayName` 是 LLM 产出 → 按 untrusted 处理**：长度上限 + 纯 ASCII 字符集，且只进 user 结构化字段（同阶段 2 的 transcript 处理），**不进 system 段**。
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
- 点击某 NPC → `npc.focus { sceneId, generationId, characterId }` → 切换 activeSpeaker（广播 `scene.focus`）。
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

- 收 `scene.skeleton` → 渲染**完整可玩默认场景**，状态 `skeleton`（此时已可点、可对话）
- 收 `scene.patch` → 应用 ops → 状态 `filled`
- 收 `scene.degraded` → 骨架停留 + 小徽标
- 旧 generationId 任何消息 → 丢弃

### turnGate 升级（两个门）

`apps/web/src/audio/turnGate.ts`：3 参 → 拆成 `acceptTurnMessage(currentGenId, currentTurnId, msgGenId, msgTurnId)` + `acceptSceneMessage(currentGenId, currentSceneId, msgGenId, msgSceneId, baseRevision)`。见 §3 门控矩阵。

### SceneViewport 扩展

- 背景从硬编码渐变 → **archetype 驱动**（`archetype.background.gradient` + decor 渲染）
- 出口门组件可点击 → `scene.request`；hover → 去抖 `scene.hint`
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

### （可选，低优先级）发现计数

纯前端 + 进程内：点过的实体标 `discovered`，TopBar 显示"本场景 N/M" + 跨场景累计总数。不涉及 Assessment Engine、不写库、不改 state-audit 断言——给手动冒烟方向，也是阶段 4 ObjectiveDrawer 的 UI 地基。**可砍**。

---

## 8. 转场预取（v2 重写：ScenePlan 而非骨架）

**为什么**：骨架＝本地确定性编译（`assets/archetypes/*.json` 启动时全量载入内存，无网络/LLM/磁盘 IO），耗时几十毫秒，**不预取也无条件成立**。真正的用户等待是进场后 Director 的 1–3s。所以预取的是 **Director 提案（ScenePlan）**。

- **缓存**：服务端 `scene_prefetch.py`，键 = `(archetypeId, worldMemoryRevision)`，TTL 60s（世界记忆/访问历史变化即失效；骨架确定性不过期，故只有 ScenePlan 需要 TTL）。
- **触发**：
  - **spoke 场景进场即预取 back**（town-map 中 spoke 唯一出口 → 100% 可预测，无条件预取）；
  - **hub 场景 hover 出口时预取目标**（去抖 ~300ms，客户端 `scene.hint { exitId }`）。
- **护栏**：预算偏低（接近 `llm_session_call_cap`）时跳过预取；预取调用照记 `llm_calls`（`role="scene_director"`，`attempt` 标记 prefetch）。hub 多出口命中率 ~20%——多数 hover 是浪费，故仅在有预算时做、且 TTL 到期即释放。
- **进场**：`scene.request` → 服务端本地编译骨架（快）→ 发 `scene.skeleton`，随后 `scene.patch`（命中缓存时 patch 同帧到达，客户端一次渲染循环内应用）；未命中 → 骨架 + 异步 Director（patch 稍后到）。
- **协议**：客户端无感知（不新增 prefetch 消息）；generationId 只在进场时分配（§3）。
- **命中体验**：转场即完整（P95<400ms）；未命中体验：骨架可玩 → 2s 内丰富。

**指标（v2 替换 v1 的两档）**：

| 指标 | 目标 |
|---|---|
| 骨架出现（本地编译，无条件） | P95 < 150ms |
| 命中预取的完整场景可交互 | P95 < 400ms |
| 未命中的完整场景可交互 | P95 < 2s |

---

## 9. 断线补发

- 重连 → client 发最后确认 `sequence` + 当前 `sceneId/generationId` → 服务端从 `session_events` 重放 `scene.entered` + `scene.patch` 重建场景 → 恢复对话。
- 音频帧**不补发**；重连时**取消当前 utterance**（沿用阶段 2 规则）。
- `session_events` 新增事件类型：`scene.entered`、`scene.patch`、`scene.degraded`。

**哪些状态可重建 / 不可重建（显式分类）**：

| 类别 | 状态 |
|---|---|
| **可重建**（事件重放） | sceneId / archetypeId / revision / entities / exits / setting / status |
| **重置为默认**（事件里没有） | activeSpeaker（→ 场景默认 NPC）、focus 全字段、pendingSpeakers、预取缓存 |
| **明确取消** | 当前 utterance、in-flight Director 调用 |

> `focusExpiresAt` 若未来要入事件，**只存 `focusSource` + 相对 TTL**，绝对时间由运行时计算（重放不同时刻会得到不同结果）。当前 focus 不入事件，重连即重置。

---

## 10. 成本与安全

- Scene Director 每次转场 1 次 + 预取调用另计；与 NPC/Tutor 共享 `llm_session_call_cap=200` + `llm_concurrency_limit=2`；budget → 跳过预取/降级骨架 + `fallback_reason=budget`。
- 所有 LLM 输入结构化（trusted 模板 + untrusted 数据分开）；输出严格白名单 + 服务端校验（§4）。
- **persona 文本永不自 LLM**（§5）：Director 只能选 npcId，禁止生成/覆写 persona（唯一进 system prompt 的动态内容，injection 面）。`setting.displayName` 按 untrusted 处理（长度 + ASCII + 只进 user 结构化字段）。
- `scene.patch` 白名单路径，拒绝改 sceneId/schemaVersion/用户档案/学习证据。
- `candidateWordIds` 全服务端派生（lexmatch 锚定 resolve 后的 wordId）。

---

## 11. 资产工作

| 资产 | 内容 |
|---|---|
| `assets/archetypes/town-map.json` | **本地邻接表**：`{ start: "plaza", edges: { plaza: {left: bakery, …}, bakery: {back: plaza}, … } }`；启动自检校验每条边的目标存在、spoke 可回 hub、无悬挂出口 |
| `assets/archetypes/{plaza,park,station,cafe,library}.json` | 新增 5 个原型（面包店已有，核对槽位）；各含 background/zones/propSlots/npcSlots/exits |
| `assets/catalog/entities.json` | 分类概念目录：category → [{conceptId, name, lemma, pos, visualKey}]，Director 候选源；**不存 wordId**（§1.13） |
| `assets/catalog/npcs.json` | 角色目录：role → [{npcId, name, persona, emoji, voice}]，Rosa 在内 |
| `assets/icons/icon-map.json` | 扩展覆盖目录内全部 visualKey（构建期校验保证完整；运行期缺映射落占位，不崩） |

**Kokoro 多音色**：`npcs.json` 带 voice，切 activeSpeaker 即切音色。在 §12.3 自检对 catalog 全部音色**各合成一次短句并记录耗时**；若发现首用某音色有明显额外开销，在预热阶段一次性载入（避免换 NPC 出现 800ms 抖动）。

---

## 12. 测试与验收

### 12.1 单测（mock Director / mock LLM，含故障注入）

| 用例 |
|---|
| Director `ok`：合法 ScenePlan 应用 → 丰富场景（骨架默认项被替换/补充） |
| Director 故障注入全覆盖：timeout / connect_error / invalid_json / slot_mismatch / unknown_word / too_many_entities / **partial_fills** / **duplicate_slot** → 各走对应路径 |
| 骨架编译确定性：**输入仅 (archetypeId, seed)**，seed 派生自 sceneId；时间/随机/世界记忆只经 patch 进入——同输入同骨架，测试不随机挂 |
| patch 幂等：patchId 去重、baseRevision 防乱序 |
| validate_scene_plan 按层：conceptId 不在候选 → 拒单条；visualKey 缺映射 → 占位、plan 照常；npcId 不在候选 → 拒单条；invalid_json / 未知 slotId / 超 40 → degraded |
| conceptId → wordId resolve：无 learning_items 时按 `word_{lemma}_{pos}_{sense}` 生成，确定性 |
| 对话自由度：用户问场景词表之外 → NPC 正常流式、不降级 |
| gesture 校验：合法 type / entityId 不在场 → 只拒 gesture |

### 12.2 集成 + 回归 + 冒烟

| 层 | 内容 |
|---|---|
| 场景生命周期集成 | scene.request → skeleton（完整可玩）→ patch → filled；**转场中途旧 generation 晚到 → 丢弃**（无应用/无播放/无字幕）；**同场景打断晚到 speech → 丢、patch 保留** |
| 回合仲裁 | 点击切换 activeSpeaker（characterId）、说话跟随、companion on_request_only |
| 预取 | spoke 进场即预取 back；hub hover 去抖触发；命中 → patch 即时；budget 低 → 跳过 |
| 断线补发 | 重放 scene.entered + scene.patch 重建场景；仲裁状态重置默认 |
| state-audit | 扩展新事件类型，仍断言仅 session_events / llm_calls / tutor_cache 变化 |
| 回归 | 阶段 1/2 全部 Python/vitest 套件保持绿 |
| 手动冒烟 | 真 key golden（Director 延迟/token），复用 llm-smoke 模式 |

### 12.3 工具 + 启动自检

- **`/dev/archetypes` 预览路由（web dev 模式）+ 可选 `scripts/archetype-preview`**：无语音/无 LLM/无 WS，纯本地编译渲染**全部原型骨架**；支持切换 archetype、显示槽位边界框/热区/实体计数。它是 §12.1 骨架确定性 + 实体 ≤40/热区 ≥44px 的**可视化验证**，也解决"调坐标要启麦克风说话"的低效问题。
- **`scripts/scene-latency.py`**：脚本化跑 30 次转场（mock Director + 真 Director 各一轮），输出 P50/P95/max + 分位直方图，存 `tests/fixtures/scene-latency/`；**区分冷启动首次转场与热运行**。
- **`startup-selfcheck.py` 扩展**：① town-map 边完整性校验；② entities visualKey ⊆ icon-map 校验；③ 全部音色短句合成耗时记录；④ Director 探活（失败 → 骨架场景 + 明确日志）。

**验收指标**（v2）：骨架 P95<150ms；命中预取完整 P95<400ms；未命中完整 P95<2s。本地先记录实测数据，真 key golden 再定值。

---

## 13. 顺手清理（阶段 2 carry 落地——**提到计划最前执行**）

1. **`OpenAIClient.aclose()`**：`apps/api/app/llm/client.py` 加 FastAPI lifespan shutdown 钩子，显式关闭 httpx 池（Director 加入后连接池压力放大，lifespan 钩子是十分钟的活）。
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

## 15. 交付物形态与实现顺序

**新建**：`apps/api/app/llm/scene_director.py`、`apps/api/app/scene_lifecycle.py`、`apps/api/app/arbitration.py`、`apps/api/app/scene_prefetch.py`、`apps/api/app/llm/concepts.py`、`assets/archetypes/town-map.json`、`assets/archetypes/{plaza,park,station,cafe,library}.json`、`assets/catalog/{entities,npcs}.json`、`scripts/scene-latency.py`

**修改**：`apps/api/app/scene_store.py`、`apps/api/app/ws.py`、`apps/api/app/llm/proposals.py`（+validate_scene_plan）、`apps/api/app/llm/npc_actor.py`（人格动态 + 场景词表动态）、`apps/api/app/llm/tutor.py`（120 动态）、`apps/api/app/llm/client.py`（aclose）、`apps/api/app/main.py`（lifespan）、`apps/web/src/audio/turnGate.ts`（两个门）、`apps/web/src/useVoiceRound.ts`、`apps/web/src/SceneViewport.tsx`（archetype 驱动 + 出口 + hover hint + 骨架/patch 状态）、`apps/web/src/registry.tsx`（door + gesture）、`apps/web/src/App.tsx`（/dev/archetypes 预览路由）、`assets/icons/icon-map.json`

**测试**：`apps/api/tests/test_scene_{lifecycle,director,validation,arbitration,prefetch,replay,gesture,concepts}.py` 等；前端 vitest 补 case

**实现顺序（资产铺量放链路验证后，返工代价最小）**：
1. **§13 顺手清理**（aclose / tutor 动态 / env quirks 文档）——前置小活
2. **town-map + 骨架完整可玩**（确定性默认填充，seed 派生）——先做
3. **/dev/archetypes 预览路由**——调坐标不再依赖语音链路
4. **plaza + bakery 打通全链路**（hub-spoke 双向 + generationId 门控矩阵）
5. **Director + 提案边界**（conceptId resolve + 校验分层 + 故障注入）
6. **预取重写**（ScenePlan 缓存 + hover/spoke 触发）
7. **批量补 park / station / cafe / library 4 原型**
8. **gesture + 回合仲裁**
9. **（可选）发现计数 + scene-latency 测量**

实现计划由 `writing-plans` 输出（`docs/superpowers/plans/2026-08-07-english-town-phase3.md`）。
