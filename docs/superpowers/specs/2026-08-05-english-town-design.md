# 英语小镇（English Town）设计文档 v2

日期：2026-08-05（v1 初稿）→ 2026-08-05（v2 修订，吸收技术评审 10 阻塞问题 + 4 补充规格）
状态：v2 已按评审修订，等待用户复核

> v2 相对 v1 的核心修订：① 场景生成改为"本地确定性骨架先行，云端异步填充"；② LLM 输出降级为"提案"并引入 Session Policy Engine / Scene Compiler / Assessment Engine；③ speech 流与结构化元数据分离为两个通道；④ 支持中文/中英混合 ASR；⑤ 持久化 `session_events` 事件表兑现断线补发；⑥ v1 边界收缩（砍多 NPC 同时对话、实时发音评分、云端图像、复杂 WorldMemory）；⑦ TTS 默认 CPU、ASR 独占 GPU；⑧ 明确状态权威、generationId、成本/超时预算、冷/热指标。

---

## 1. 产品愿景

一个**沉浸式英语学习网站**：没有固定世界，场景由 AI 实时生成并渲染到 DOM。用户通过**语音**与场景人物自由对话，点击/询问物品学单词，由一位**常驻伴学者**引导。

核心理念：沉浸体验由"低延迟语音 + 场景连续性 + 可交互对象"建立，而非实时生成大图。

## 2. 决策基线（已确认）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 视觉方向 | **Emoji/图标 标准化渲染**（`visualKey` → 固定 Emoji/图标映射 → 本地 SVG/WebP 或统一字体 → Unicode Emoji 兜底）。LLM 输出 `conceptId/visualKey`，**不输出原始 Emoji 字符**（跨平台字形差异：Windows/Chrome/macOS 不一致） |
| 2 | 伴学者 | **常驻同一人**，轻量跨场景记忆（去过的场景、学过的词、最近对话）；v1 不做复杂长期记忆抽取 |
| 3 | 自由模式 | **轻记录偶遇词**；偶遇词库可一键转目标模式 |
| 4 | 场景生成 | **场景原型 + AI 填充**，且**本地确定性骨架先行**（见 §5–§6） |
| 5 | 场景人物 | 场景含 0–4 个可见 NPC；**v1 仅允许 1 个 NPC 进入"主动对话态"**，其余只做短台词/非语言动画；伴学者独立常驻、不占 NPC 槽位 |
| 6 | 语言模式 | 支持 **英文 + 中文求助**两种输入模式（`inputLanguageMode`），见 §11 |

## 3. v1 范围边界（收缩）

**做**：
- 5 个场景原型（面包店/公园/车站 + 2）
- 每场最多 1 个主动 NPC、最多 4 个可见 NPC
- 一个固定伴学者（轻量跨场景记忆）
- 英文对话 + 中文求助两种输入模式
- CSV/TXT 词表导入；目标词类型先支持 `object / action / phrase`
- Emoji/统一图标渲染（`visualKey`）
- DeepSeek：NPC Actor + Companion Tutor
- 本地 faster-whisper + Kokoro（TTS 默认 CPU）
- SQLite WAL + `session_events` 事件表

**v1 明确不做**（后续版本再加）：
- 本地扩散模型 / 云端图像生成
- 实时发音评分（v1 只用 ASR 置信度 + 是否识别到目标词作代理）
- 多 NPC 同时主动对话
- Anki/APKG/OCR
- 复杂 WorldMemory（向量记忆抽取）
- 完全自由场景（全程序化生成）

## 4. 状态权威与架构

### 状态权威划分

| 主体 | 拥有状态 |
|---|---|
| **服务器** | 会话状态、场景版本、学习状态、对话焦点、NPC 回合 —— 唯一权威 |
| **客户端** | 临时 UI 状态、动画播放、hover、音频缓冲（XState 只是服务器状态的**镜像**，不独立推进） |
| **LLM** | 场景与对话**提案**，不拥有任何最终状态写入权限 |

### 总体架构

```text
浏览器 React（图标渲染 + AudioWorklet + WS）
   ↓ WebSocket：音频二进制 + 控制 JSON
本机 FastAPI Orchestrator（CPU）
   ├─ 会话状态机 / 场景管线（ScenePreloadManager / Scene Compiler）
   ├─ 场景原型目录 + 资产目录 + 图标映射
   ├─ Session Policy Engine / Assessment Engine / 学习规划器
   ├─ 偶遇词记录 / 证据写入队列
   ├─ Prompt 构造与云端 LLM 路由
   └─ ASR Worker（GPU 独占）/ TTS Worker（CPU 优先）
   ↓
云端 DeepSeek：Scene Director / NPC Actor / Companion Tutor / Narrative Repair
数据：SQLite WAL（开发）→ PostgreSQL（部署）；进程内 LRU（开发）→ Redis（部署）
```

API Key、Prompt、学习档案与模型降级逻辑**必须留在后端**，浏览器不直接调用 DeepSeek。

## 5. 场景原型系统（本地模板，离线缓存）

每个原型定义"场景模板"，**全部是本地确定性数据**，前端可离线缓存，不依赖云端即可渲染骨架：

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
    "counter": { "x": [300, 700], "y": [600, 800], "anchor": "bottom" },
    "shelf":   { "x": [50, 250],  "y": [200, 500] },
    "door":    { "x": [880, 980], "y": [300, 800] }
  },
  "propSlots": [
    { "slotId": "counter.main", "zone": "counter", "categories": ["food", "product", "paper"] },
    { "slotId": "counter.side", "zone": "counter", "categories": ["drink", "food"] },
    { "slotId": "shelf.top",    "zone": "shelf",   "categories": ["container", "decoration"] }
  ],
  "npcSlots": [ { "slotId": "vendor", "zone": "counter", "role": "vendor" } ],
  "exits": [ { "direction": "left", "targetKind": "any" }, { "direction": "right", "targetKind": "any" } ]
}
```

- 背景/区域/槽位是**本地确定性模板**；LLM 只负责"选原型 + 填内容"。
- 原型使用**允许列表**；新原型入库需走版本化发布，不能由 LLM 现场创建。

## 6. 场景生成流程（本地先行，云端异步）与 Scene DSL

### 流程（v2 修订核心）

```text
目标模式：WordPack → ArchetypeMatcher（本地确定性匹配）→ bakery
自由模式：Brief → 本地粗分类；失败 → 最近访问地点或默认小镇广场
        ↓
Scene Compiler（本地）：编译背景 + 区域 + 默认装饰 + 占位实体
        ↓
立即挂载 SceneSkeleton（已缓存原型，目标 P95 < 300ms）
        ↓
DeepSeek 异步填充人物 / 词汇 / 剧情 / 目标（不阻塞首次可见）
        ↓
ScenePatch 增量更新 → 完整场景可交互（目标 P95 < 2s）
```

**云端 LLM 不阻塞场景第一次可见。** 场景首次可交互依赖原型缓存，不是 AI 生成。

### ScenePreloadManager（新增组件）

```text
ScenePreloadManager
  ├─ currentScene
  ├─ nextExitCandidates[]
  ├─ prefetchTTL
  ├─ briefHash
  ├─ curriculumRevision
  └─ worldMemoryRevision
```

- 预取最多 **1 个**下一个场景；预取只缓存原型骨架 + 槽位参数，不预生成完整内容。
- 指标改为（见 §17）：**已缓存原型骨架出现 P95 < 300ms**；**云端填充后完整场景可交互 P95 < 2s**。

### Scene DSL 与生成协议

ScenePlan（选原型 + 填槽位）：

```json
{
  "schemaVersion": "1.0",
  "sceneId": "scene_01J...",
  "generationId": "gen_01J...",
  "revision": 1,
  "mode": "quest",
  "archetypeId": "bakery",
  "setting": { "displayName": "Rosewood Bakery", "time": "morning" },
  "fills": [
    { "slotId": "counter.main", "entity": { "component": "prop", "wordId": "word_loaf_n_1", "name": "loaf" } },
    { "slotId": "counter.side", "entity": { "component": "prop", "wordId": "word_receipt_n_1", "name": "receipt" } }
  ],
  "characters": [ { "slotId": "vendor", "npcId": "npc_rosa" } ],
  "objectives": [],
  "exits": []
}
```

增量更新用受限 JSON Patch（`type: "scene.patch"`），携带 `sceneId / generationId / baseRevision / patchId`；幂等，`patchId` 去重、`baseRevision` 防乱序。服务器只允许修改白名单路径；**拒绝改 `sceneId / schemaVersion / 用户档案 / 已完成学习证据`**。客户端收到**旧 generationId** 的 patch/音频直接丢弃（防旧场景污染新场景）。

## 7. LLM 提案边界（v2 修订核心）

**LLM 输出 = 提案，不是系统状态。** 任何 LLM 输出在写入前必须经过：

```text
LLM 提案 → Schema 校验 → 语义校验 → Session Policy Engine → Scene Compiler / Assessment Engine → 正式状态
```

硬性规则：

| 字段 | 规则 |
|---|---|
| `wordId` | 只能从后端传入的**候选词 ID** 中选择；LLM 不得创建词汇 ID |
| `assetKey / visualKey` | 只能从 Archetype/Asset Catalog 或图标映射中选择 |
| `layout` | 不由 LLM 生成，全部由**槽位编译器**计算 |
| `objective.status` | 只能由 **Assessment Engine** 更新 |
| `learning.evidence` | 只能由服务端根据实际用户音频/动作/对话生成 |
| `exposedWordIds` | 只能作为 LLM **建议**；最终依据实际发送给 TTS 的英文文本做服务端词法匹配 |
| `sceneEffects` | 只能描述"建议发生什么"，不能直接修改学习状态 |

**exposure 的生成（服务端可信路径）**：

```text
实际 speech → tokenize → lemma/短语归一化 → 义项匹配 → exposure 事件
```

模型自报的 `exposedWordIds` 不被信任 —— 它可能漏标/错标。

## 8. 前端架构与渲染

**技术栈**：React 19 + TypeScript + Vite；Zustand；XState（**仅作服务器状态镜像**）；TanStack Query；Zod（DSL 运行时校验）；Motion；AudioWorklet；Playwright + Vitest。场景禁止 innerHTML。

**图标渲染（v2 修订）**：

```text
canonical visualKey（如 "food.apple"）
  → 固定 Emoji/图标映射表
  → 本地 SVG/WebP 或统一 Emoji 字体（优先）
  → Unicode Emoji 兜底
```

**组件白名单**：

```ts
type EntityKind =
  | "image" | "label" | "npc" | "companion" | "prop"
  | "door" | "dialogue-zone" | "ambient-audio";
const registry = {
  image: ImageEntity, npc: NpcEntity,
  prop: PropEntity, door: ExitEntity
} satisfies EntityRegistry;
```

**页面结构**：AppShell（TopBar / SceneViewport：BackgroundLayer→EnvironmentLayer→EntityLayer→CharacterLayer→InteractionLayer / DialogueDock / CompanionPopover / ObjectiveDrawer）。

- 坐标 `0..1000` 逻辑坐标，前端等比映射；LLM 不生成像素位置。
- 实体 = 图标 + 单词标签 chip + 点击热区（≥44×44 CSS px）。单场景 DOM 实体 ≤ 40。
- 移动端只保证可看：裁剪视口 + 轻量平移，不压缩实体。

**目标词类型与学习载体**（词表不只含名词）：

| 类型 | 示例 | 学习载体 |
|---|---|---|
| object | loaf, apple | 可点击物品 |
| action | borrow, turn left | 动画或操作序列 |
| quality | fragile | 对比两个实体（强调差异） |
| location | station, platform | 路线 / 空间选择 |
| phrase | make a reservation | NPC 对话任务 |
| function_word | although | 句型替换练习 |

v1 目标词类型先实现 `object / action / phrase`；其余类型保留 schema 位，后续版本启用。

## 9. 人物系统与回合仲裁器

### 伴学者（常驻同一人）

- 每个场景自动出现；身份/声音/记忆跨场景一致；默认 🦊（名字与音色可配置）。
- 轻量跨场景记忆：去过的场景、学过的词、最近对话轮次。
- 交互：点击实体/伴学者 → CompanionPopover（怎么说/怎么读/什么意思 + 自由输入）。
- 渐进提示：情境 → 首字母 → 揭晓 + 带读。
- 可关闭：全局开关 + 单场挂起。
- **打断策略：默认 `on_request_only`** —— 不主动插话，只在用户点击、明确称呼或连续失败时响应。

### NPC 与回合仲裁器（v2 新增）

`conversationFocus` 是必要条件但不充分，需完整仲裁状态：

```json
{
  "activeSpeaker": "npc:rosa",
  "conversationFocus": "npc:rosa",
  "focusSource": "user_click",
  "focusExpiresAt": 1780000000000,
  "pendingSpeakers": [],
  "companionInterruptionPolicy": "on_request_only"
}
```

规则：
- 同一时间只允许**一个角色**进入主动回复。
- **v1 每场只有一个 NPC 进入"主动对话态"**；其他 NPC 只做短台词或非语言动画。
- 焦点判断优先级：**点击 > 明确称呼 > 最近说话角色 > 空间指向 > 最后才交给 LLM 解析**。
- 用户打断当前 NPC 后，另一 NPC 是否继续由 `pendingSpeakers` 队列决定（v1 不排队，直接取消旧回合）。

## 10. LLM 服务拆分与语音/元数据双通道

### 四个逻辑角色（Prompt/上下文/Schema 隔离）

| 角色 | 输入 | 输出 | 时机 | v1 调用上限 |
|---|---|---|---|---|
| Scene Director | Brief、WordPack、世界摘要 | ScenePlan/SceneSkeleton（提案） | 进场、转场 | 每次转场 1 次 |
| NPC Actor | persona、局部场景、对话焦点、最近对话 | 台词、动作、教学建议 | 每轮语音 | 每轮最多 1 次 |
| Companion Tutor | 指代对象、用户水平、求助记录 | 渐进提示 | 用户求助 | 每次求助最多 1 次 |
| Narrative Repair | 校验错误、缺失目标 | ScenePatch（提案） | 场景不合规 | 每场景最多 1 次 |

- NPC Actor 请求只发局部场景，不发完整 Scene DSL。
- 输出经过 §7 提案边界处理后才生效。

### 双通道协议（v2 修订核心：解决"JSON 流式 vs TTS 立即开始"冲突）

NPC 响应拆成两个逻辑通道，**TTS 只消费 speech 通道**：

```json
{ "type": "npc.speech.delta",  "turnId": "turn_123", "text": "Would you like" }
{ "type": "npc.speech.commit", "turnId": "turn_123", "text": "Would you like the whole loaf or just a slice?" }
{ "type": "npc.turn.metadata", "turnId": "turn_123",
  "gesture": { "type": "point", "entityId": "loaf-1" },
  "candidateWordIds": ["word_loaf_n_1", "word_slice_n_1"] }
```

动作与教学元数据**异步处理**，不与 TTS 抢通道。

**v1 落法（不双承诺）**：先实测 DeepSeek 是否支持稳定结构化增量输出。
- 支持 → 采用 speech-first 流式：`speech` 放第一字段，流式消费文本，字段闭合即 commit，其余字段组装为 `turn.metadata` 并校验。
- 不支持 → 退回**完整 JSON 后 TTS**：协议可靠但延迟更高，把该延迟计入预算（见 §17 实测）。
- 不做"裸 `JSON.parse` 不完整 JSON"。

### generationId / turnId / utteranceId

每次场景生成与每轮对话携带 `generationId / turnId / utteranceId`。客户端收到旧 generation 的 patch/音频直接丢弃。

## 11. 语音链路

### 输入语言模式（v2 新增）

```json
{ "inputLanguageMode": "auto" }
```

- `en`（默认对话）/ `zh`（中文求助）/ `mixed` / `auto`（每句自动判断，辅助）。
- v1 最稳定做法：**用户设置默认输入语言**；伴学者请求按钮/快捷键**临时切中文**；`auto` 只作辅助。
- 识别结果保留**原始语言标签**；识别成中文**不计为该轮英语产出失败**。

### 链路

```text
audio.start → PCM frames(20ms) → vad.speech_start → asr.partial → asr.final
→ dialogue.thinking → npc.speech.delta/commit → tts.audio.chunk → playback.started/completed
```

- **采集**：AudioWorklet 48kHz → 16kHz mono PCM16 → WS 二进制帧（20ms/帧）。
- **端点检测（双层）**：浏览器 Silero VAD（起停/UI）+ 服务端 Silero VAD（切句）。起音 120ms / 尾静音 550ms / 最长 20s；连续说话 750ms、短问句 350–450ms；起音前 200ms 环形缓冲。

### 滚动窗口伪流式 ASR（v2 明确 segment commit）

faster-whisper 非原生流式。滚动窗口方案必须补齐**段提交语义**，防止文本重复、前半句反复变化、目标词重复记账、打断后旧结果覆盖：

```json
{
  "utteranceId": "utt_1",
  "audioStartMs": 1200,
  "audioEndMs": 8800,
  "segmentId": 3,
  "stableText": "I'd like a",
  "revision": 2,
  "finalText": "I'd like a loaf"
}
```

- 每 300ms 检查；窗口 6–12s；上一次稳定文本作 prefix（**注意：不能简单等同 Whisper `initial_prompt`**）。
- 连续两次一致的 token 标记 stable；端点触发后最终解码（实时 `beam_size=1`，最终 `beam_size=3`，`vad_filter=False`，语言按 `inputLanguageMode` 决策）。
- **partial 结果不得被当成最终学习证据**；只有 commit 的 final 结果可入账。

### TTS（Kokoro，CPU 优先）

- 句子/语义分块，首块 8–20 词；不能按单词切块。
- 打断流程：检测有效语音 → 30–80ms 淡出 → `playback.interrupted`（含已播毫秒）→ 取消未开始 TTS/LLM → 仅把已播放内容写入对话历史 → ASR 回声抑制。

### 发音评分边界（v2 修订）

- **v1 不承诺可靠发音评分**：只记录 ASR 置信度 + 是否识别到目标词（`pronunciation_score` = ASR 置信度代理）。
- **v2**：保存用户授权音频片段 + 强制对齐 + 音素级评分。

## 12. 学习引擎

### 两条词流汇入一套掌握度

- **目标词流（Quest）**：词表导入 → 确定性处理（解析→去重→lemma→词性→义项→IPA→CEFR→场景标签→入库）。每场选 5–7 词（2–3 到期复习 + 2–3 新词 + 1 薄弱词）；必须有共同场景标签。
- **偶遇词流（Free）入库条件（v2 明确）**：
  - 用户**主动问过一次** → 立即记录；
  - 自然对话中**连续两轮遇到** → 记录一次；
  - **仅在 NPC 台词中出现** → 只记 exposure，**不进入**待学习词。
- 两条流汇入同一套 `mastery_states`。

### 掌握度与评分算法（v2 修订：从权重表升级为可执行算法）

数据结构（保留 v1）+ 新增**每次尝试的证据上下文**：

```json
{
  "evidenceId": "ev_123",
  "attemptId": "attempt_456",
  "turnId": "turn_789",
  "objectiveId": "obj_1",
  "itemId": "word_loaf_n_1",
  "source": "spontaneous_production",
  "promptLevel": 0,
  "result": "success",
  "confidence": 0.86
}
```

规则版本化：`evidence_policy_version = "v1"`、`fsrs_algorithm_version = "fsrs-5"`。

评分权重（v1 基线，**含更新规则**）：

| 证据 | 权重 |
|---|---|
| 自发正确产出 | +1.0 |
| 提示后产出 | +0.65 |
| 复述 | +0.4 |
| 正确动作理解 | +0.55 |
| 主动求助 | −0.35 |
| 错误使用 | −0.5 |

**更新规则（v1 基线，可执行的）**：
- 证据方向决定更新的分数轴：自发产出 → `productive_score`；动作理解/接受性 → `receptive_score`；带读复述 → `pronunciation_score`（ASR 置信度代理）。
- **同一轮重复命中目标词只记一次**（按 `attemptId + turnId` 去重）。
- 低置信度（< 0.6）标 `uncertain`，**不判 fail**，也不计入成功；必要时请用户重说。
- **错误后立即重试算一次新尝试**（保留原负证据，重试结果独立入账）。
- **求助后用户正确说出**：求助负证据与产出正证据**都记**，但教学目标自动下调（说明该词需复习而非新授）。
- 进入 FSRS 时机：某词在非 `promptLevel 0` 下成功 ≥ 1 次且总尝试 ≥ 2 后，进入排期。
- **exposure 计数**：只认服务端词法匹配（§7），同一场景同一词只计一次 exposure。

### 教学策略

FSRS 决定"何时复习"；三维分数决定"怎么教"：接受性低→口头描述选物；产出性低→NPC 创造必须主动说出的情境；发音低→最小对立 + 慢速示范；已掌握→自然出现不再显式教。

## 13. 数据与接口

### 核心表

v1 表：`users`（预留）、`user_profiles`、`sessions`、`session_briefs`、`scenes`、`scene_revisions`、`entities`、`characters`、`character_memories`、`dialogue_turns`、`word_lists`、`learning_items`、`mastery_states`、`evidence_events`、`spontaneous_encounters`、`scene_archetypes`、`asset_catalog`、`icon_visual_map`、`audio_artifacts`、`llm_calls`、`error_events`。

**新增（v2）：`session_events` 持久化事件表** —— 兑现断线补发：

```sql
session_events(
  sequence     INTEGER NOT NULL,
  event_id     TEXT NOT NULL UNIQUE,
  session_id   TEXT NOT NULL,
  event_type   TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  PRIMARY KEY(session_id, sequence)
);
```

关键规则：
- **学习证据先写 SQLite，再向客户端发送确认**；`event_id` 唯一，重复提交不重复计算。
- `sequence` 由服务端产生；场景 patch 与学习证据都进事件日志。
- 音频帧**不补发**，但重连时**取消当前 utterance**。
- SQLite 开启 **WAL + busy_timeout + 事务**；所有学习状态更新走**单一写入队列**。

### HTTP 接口

```text
POST /api/sessions
POST /api/sessions/{id}/brief
POST /api/word-lists/import
GET  /api/word-lists/spontaneous
POST /api/word-lists/spontaneous/import
GET  /api/scenes/{id}
GET  /api/progress/summary
POST /api/settings/voice-test
GET  /api/archetypes
```

### 单一实时连接

`WS /ws/sessions/{sessionId}`。所有 WS 消息含 `eventId / sessionId / timestamp / sequence`；断线重连客户端发送最后确认 sequence，后端从 `session_events` 补发。

## 14. 容错、成本与安全

### 降级阶梯（v1）

- DeepSeek 超时（3s）→ NPC 用本地模板短句。
- DSL 校验失败 → 自动修复一次（Narrative Repair）→ 仍失败用模板场景。
- ASR CUDA 失败 → 切 CPU `small.en`。
- TTS 失败 → 字幕 + SpeechSynthesis 兜底。
- Redis 不可用 → 进程内缓存。云图像（v1 无）→ 通用资产。
- 伴学者不知道指代 → 高亮最多三个候选物品让用户选择。

### 成本与超时预算（v1）

- NPC Actor 每轮 1 次主调用；Companion 每次求助 1 次；Narrative Repair 每场景最多 1 次；Scene Director 每次转场 1 次。
- **预取最多 1 个场景**。
- 云端超时 **3 秒**；**仅连接错误重试一次**（业务失败不重试）。
- 记录每次 LLM 调用的 token 与耗时，超预算告警。

### 安全（v2 强化：Prompt Injection 防线）

- **trusted / untrusted 内容分区**：系统 Prompt/程序模板 = trusted；用户输入、转写、文件内容 = untrusted，且**不拼入系统 Prompt**，只作为结构化数据。
- 用户文本**长度限制**；词表内容只作为数据、不作为指令。
- LLM 输出**严格白名单**（Schema + 枚举 + 服务端校验）；目标词、资产、NPC ID 均做服务端校验。
- 场景原型使用**允许列表**。
- 内容安全策略与年龄/敏感内容设置。
- **LLM 日志脱敏 + 保留期限**；日志默认不存原始麦克风音频。
- **明确告知用户**：转写文本、学习档案、世界记忆会发送到云端（"本地 ASR ≠ 数据完全本地"）。

## 15. 资源分配与启动诊断（v2 明确）

### 进程分配（RTX 5060 Laptop / 8GB / 16GB）

| 进程 | 分配 | 说明 |
|---|---|---|
| FastAPI Orchestrator | **CPU** | 不加载推理模型 |
| ASR Worker | **GPU，唯一高优先级 GPU 任务** | faster-whisper `distil-large-v3` float16 |
| Kokoro TTS | **CPU 优先** | 实测 CPU 太慢才允许 GPU，且加 **GPU semaphore = 1**（ASR/TTS 不同时重计算） |
| 浏览器 | 正常 GPU 渲染 | 无推理占用 |

**Windows 部署硬性要求**：
- Uvicorn **只能启动一个会加载模型的 worker**；禁用 `--workers 4` 等多进程配置（重复加载模型会吃爆 16GB）。
- ASR/TTS 模型**启动时预热**。
- 记录启动耗时、峰值显存、系统内存、温度。

### 启动自检（纳入启动流程）

1. 检测 GPU 名称；2. 检测 CUDA 可用性；3. 加载模型；4. 执行 3 秒英语音频转写；5. 执行一句 TTS；6. 记录峰值显存与耗时；7. 失败自动切 CPU。

### 版本锁定

CUDA 12.8 支持 Blackwell，**不代表当前安装的 CTranslate2 wheel 一定支持 RTX 5060**。必须锁定并启动自检：NVIDIA 驱动、CUDA runtime、cuDNN、CTranslate2、`ctranslate2`。`float16` 与 `int8_float16` 在 RTX 5060 上分别实测后定案。

**v1 去掉 CosyVoice/F5-TTS 的"按需加载"**：实时对话中按需加载耗时可能超过整轮对话，等 Kokoro 链路稳定后再加。

## 16. 目录结构

```text
english-town/
  apps/
    web/                  # React（状态为服务器镜像）
    api/                  # FastAPI 编排服务（CPU）
  services/
    asr-worker/           # GPU 独占
    tts-worker/           # CPU 优先
  packages/
    scene-schema/         # JSON Schema + TS/Python 类型
    event-contracts/
    prompt-templates/
    learning-engine/
    scene-compiler/       # 槽位编译 / 布局计算
  assets/
    archetypes/           # 场景原型定义（允许列表）
    catalog/
    icons/                # visualKey → 图标映射
    backgrounds/
    props/
    characters/
    ambience/
  infra/
    docker-compose.yml
  tests/
    contract/
    e2e/
    latency/              # 冷/热/30min/降频/超时/断网
```

共享 Schema 以 **JSON Schema 为唯一事实源**，生成 TS 与 Pydantic 类型。

## 17. 里程碑、验收与性能指标

### 里程碑

| 阶段 | 内容 | 验收 |
|---|---|---|
| 1 | 手工面包店原型：本地骨架渲染 + 点击 + 字幕 + AudioWorklet + ASR + Kokoro（全本地，不含 LLM） | 说完后 1.5s 内听到回复（本地链） |
| 2 | 接 DeepSeek：NPC Actor + Companion Tutor；双通道协议；提案边界；超时/打断/历史裁剪 | 对话稳定、可打断、无状态越权 |
| 3 | Scene Director + ScenePreloadManager：原型目录 + 本地骨架 + 异步填充 + 转场预取 + 回合仲裁 | 骨架 300ms / 完整 2s（见指标） |
| 4 | 学习引擎：词表导入 + 证据 + FSRS + 目标模式 + 偶遇词流 + 实时重规划 + session_events | 每个目标词可追溯证据；断线补发不丢 |
| 5 | 复杂 WorldMemory 记忆抽取 + 发音评分（强制对齐）+ 云端图像 | 长期记忆增强生效（轻量伴学者记忆在 v1 已具备，本阶段做记忆抽取与增强） |

### 性能指标（v2 修订：区分冷/热，并落到具体模块）

| 指标 | 目标 | 说明 |
|---|---|---|
| 已缓存原型骨架出现 | **P95 < 300ms** | 本地模板渲染，不依赖云端 |
| 云端填充后完整场景可交互 | **P95 < 2s** | 含 Scene Director 异步填充 |
| VAD 判定用户说完 | 0.35–0.65s | – |
| 最终 ASR | P95 < 0.5s | 热运行 |
| 说完到听见回复端到端 | P50 < 1.2s / P95 < 2.0s | 热运行 |
| 场景 Schema 有效率 | > 99.5% | 含修复后 |
| 目标词视觉/对话覆盖率 | 100% | – |
| 浏览器稳定内存 | < 1.5GB | – |
| 本机总内存长期使用 | < 14GB | – |

**测量必须分场景**：冷启动 / 模型热启动 / 正常热运行 / 连续 30 分钟 / 高温降频 / 云端超时 / 断网降级。LLM 首有效台词 P95 < 0.8s 等指标**先记录实际数据**，再决定是否作为硬指标。

## 18. 技术栈

- 前端：React 19 + TS + Vite / Zustand / XState（镜像）/ TanStack Query / Zod / Motion / AudioWorklet / Playwright + Vitest
- 后端：FastAPI（单 worker）/ faster-whisper（CTranslate2）/ Silero VAD / Kokoro（CPU 优先）/ OpenAI-compatible LLM adapter
- 数据：SQLite WAL（开发）→ PostgreSQL（部署）；进程内 LRU（开发）→ Redis（部署）
- 工具：uv、pnpm workspace

## 19. 开放项（实现时验证）

- DeepSeek：具体模型 ID / 价格 / 限流 / **是否支持稳定结构化增量输出**（决定 §10 双通道的 v1 落法）。
- RTX 5060 上 CTranslate2/CUDA 12.8 实际兼容性；`float16` vs `int8_float16` 实测。
- 伴学者默认形象与音色（🦊 + Kokoro 音色名）可配置。
- 中文 ASR 质量与 `auto` 模式误判率实测。
