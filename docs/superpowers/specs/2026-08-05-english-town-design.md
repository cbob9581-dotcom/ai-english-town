# 英语小镇（English Town）设计文档

日期：2026-08-05
状态：已获用户确认（四轮设计逐段评审通过）

---

## 1. 产品愿景

一个**沉浸式英语学习网站**：没有固定的世界，每个场景（面包店、公园、车站……）都由 AI 实时生成，渲染到 DOM 上。用户通过**语音**与场景中的人物自由对话，点击/询问场景里的物品来学单词。全程由一位**常驻伴学者**引导。

核心理念：沉浸式体验由"低延迟语音 + 场景连续性 + 可交互对象"建立，而非依赖实时生成大图。

## 2. 锁定的决策基线（已确认）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 场景视觉方向 | **Emoji / 扁平卡通** —— 零资产管线，任何单词立刻有图形，实体天然独立可点 |
| 2 | 伴学者设定 | **常驻同一人**，跨场景记忆（记得去过的地方、学过的词、说错过的表达），是专属导师 |
| 3 | 自由模式行为 | **轻记录偶遇词** —— 不主动教；用户主动问过/对话暴露的生词静默记入"偶遇词库"，可一键转目标模式 |
| 4 | 场景生成机制 | **场景原型（Archetype）+ AI 填充** —— 手工原型定义背景/区域/槽位，LLM 选原型 + 填参数，渐进走向全自由生成 |
| 5 | 场景人物（补充确认） | 场景除伴学者外还有 **0–4 个可对话 NPC**（店主/顾客/路人，角色各异），全部走 NPC Actor 管线；伴学者是独立常驻实体、不占 NPC 槽位，专职教学。多 NPC 场景需要**对话焦点追踪** |

## 3. v1 边界假设

- **单机单用户**本地应用：SQLite + 进程内 LRU 缓存；表结构保留 `user_id` 字段，将来平滑转 PostgreSQL + Redis。
- **桌面浏览器优先**（Chrome/Edge）：麦克风 + AudioWorklet + WebSocket。移动端只保证"可看"。
- **云端 LLM = DeepSeek**（OpenAI-compatible adapter；具体模型 ID / 价格 / 限流 / 结构化输出能力以实际接入时的官方文档为准，不把新闻参数写死）。可选接入更强模型处理课程规划修复与复杂场景重写。
- **本地 GPU 只承担 ASR 与 TTS**：faster-whisper `distil-large-v3`（float16）+ Kokoro TTS。绝不常驻第二个重型语音模型，不在本机跑 LLM / 扩散图像模型。
- **图像不在本机实时生成**：使用资产检索 + 程序化/CSS 组合 + 可选云端图像 API。
- 用户输入、转写文本、文件内容一律作为**结构化字段**，不直接拼入系统 Prompt。

### 硬件与资源策略（RTX 5060 Laptop / 8GB 显存 / 16GB 内存）

| 进程 | 推荐实现 | 显存 | 内存 |
|---|---:|---:|
| ASR | faster-whisper `distil-large-v3` float16 | 1.5–2.5GB | 1–2GB |
| TTS 默认 | Kokoro（英语固定音色） | 0–1GB | 0.5–1GB |
| TTS 高质量（可选） | CosyVoice/F5-TTS 按需加载，不常驻 | 2–4GB | 2–4GB |
| 后端/前端 | FastAPI、Node | 很少 | 1–3GB |
| 系统保留 | Windows | – | 5–7GB |

- ASR 降级链：`distil-large-v3` → `large-v3-turbo` → `small.en`。
- 开发阶段省略 Redis，用进程内 LRU；正式部署再启用。
- 开发阶段省略 PostgreSQL，用 SQLite；表结构保留多用户扩展余地。

## 4. 总体架构

三层结构，**场景原型目录为"一等公民"子系统**：

```text
浏览器 React（Emoji 渲染 + AudioWorklet + WS）
   ↓ WebSocket：音频二进制 + 控制 JSON
本机 FastAPI Orchestrator
   ├─ 会话状态机 / Scene DSL 校验与派发
   ├─ 场景原型目录 Archetype Catalog
   ├─ 学习规划器 / 掌握度评估器 / 偶遇词记录
   ├─ Prompt 构造与云端 LLM 路由
   └─ ASR / TTS 两个 Worker
   ↓
云端 DeepSeek：Scene Director / NPC Actor / Companion Tutor / Narrative Repair
数据：SQLite（开发）→ PostgreSQL（部署）；进程内缓存（开发）→ Redis（部署）
```

API Key、Prompt、学习档案与模型降级逻辑**必须留在 FastAPI 后端**，浏览器不直接调用 DeepSeek。

## 5. 场景原型系统（Archetype Catalog）

每个原型定义"场景模板"，不定义具体内容。LLM 负责选原型 + 填槽位。

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
  "npcSlots": [
    { "slotId": "vendor", "zone": "counter", "role": "vendor" }
  ],
  "exits": [
    { "direction": "left",  "targetKind": "any" },
    { "direction": "right", "targetKind": "any" }
  ]
}
```

- 背景与槽位是**纯模板数据，前端可离线缓存** —— ScenePlan 一到，先秒渲背景，再逐条渲染填充实体。
- 原型支持 **0–4 个 NPC 槽位**，角色各异；所有 NPC 都能与用户对话。
- 原型持续扩充；配合 ScenePatch 系统，向全自由生成渐进。

## 6. Scene DSL 与生成协议

一份完整场景**不一次生成**，拆成三阶段：`ScenePlan`（选原型+填槽位）→ `SceneSkeleton`（3–8 个首屏实体，500–1500ms 可渲染）→ `ScenePatch[]`（异步追加装饰/旁支/环境音/交互）。

ScenePlan 示例：

```json
{
  "schemaVersion": "1.0",
  "sceneId": "scene_01J...",
  "revision": 1,
  "mode": "quest",
  "archetypeId": "bakery",
  "setting": { "displayName": "Rosewood Bakery", "time": "morning" },
  "fills": [
    { "slotId": "counter.main", "entity": { "component": "prop", "wordId": "loaf", "name": "loaf" } },
    { "slotId": "counter.side", "entity": { "component": "prop", "wordId": "receipt", "name": "receipt" } }
  ],
  "characters": [ { "slotId": "vendor", "npcId": "rosa" } ],
  "objectives": [],
  "exits": []
}
```

场景增量更新用受限 JSON Patch（`type: "scene.patch"`），携带 `sceneId / baseRevision / patchId`；每个 patch **幂等**，客户端用 `patchId` 去重、`baseRevision` 防乱序。服务器**只允许修改白名单路径**，拒绝改 `sceneId`、`schemaVersion`、用户档案与已完成的学习证据。

## 7. 前端架构与渲染

**技术栈**：React 19 + TypeScript + Vite；Zustand（交互状态）；XState（会话/场景生命周期）；TanStack Query（HTTP）；Zod（DSL 运行时校验）；Motion（进出场/转场）；AudioWorklet（PCM 采集与流式播放）；Playwright + Vitest（E2E + 单元测试）。DOMPurify 仅用于受控富文本，**场景本身禁止 innerHTML**。

**组件白名单** —— AI 只能选组件、传 Schema 校验过的属性，不能输出 `<script>`、CSS 字符串、事件处理代码或外部 URL：

```ts
type EntityKind =
  | "image" | "label" | "npc" | "companion" | "prop"
  | "door" | "dialogue-zone" | "ambient-audio";

const registry = {
  image: ImageEntity, npc: NpcEntity,
  prop: PropEntity, door: ExitEntity
} satisfies EntityRegistry;
```

**页面结构**：

```text
AppShell
  ├─ TopBar：地点、模式、麦克风、字幕、设置
  ├─ SceneViewport
  │   ├─ BackgroundLayer（原型背景）
  │   ├─ EnvironmentLayer（装饰，不可点）
  │   ├─ EntityLayer（可点实体）
  │   ├─ CharacterLayer（NPC + 伴学者）
  │   └─ InteractionLayer（高亮/光晕/热区）
  ├─ DialogueDock：实时字幕、输入状态、可折叠历史
  ├─ CompanionPopover：仅在求助时出现
  └─ ObjectiveDrawer：目标模式下显示，默认折叠
```

- 实体坐标用 `0..1000` 逻辑坐标，前端按容器等比映射；**不让 LLM 直接生成像素位置**。
- 实体 = Emoji 字形 + 单词标签 chip + 点击热区（**≥44×44 CSS 像素**）。
- 响应式保持逻辑坐标不变；移动端裁剪视口 + 轻量平移，不压缩实体。
- 单场景 DOM 实体上限 **40 个**；WebP/资产首屏合计 ≤ 2MB。

**实体模型**（沿用）：
```json
{
  "id": "apple-1",
  "component": "prop",
  "layout": { "x": 340, "y": 610, "w": 120, "h": 100, "anchor": "bottom" },
  "appearance": { "assetKey": "food/apple/red", "variant": "basket" },
  "semantics": { "wordId": "word_apple_n_1", "name": "apple", "aliases": ["red apple"], "description": "a red apple in the front basket" },
  "interactions": ["focus", "ask", "inspect", "pick"],
  "visibility": { "minLevel": "A1" }
}
```

## 8. 伴学者与人物系统

- **伴学者（常驻同一人）**：每个场景自动出现，身份/声音/记忆跨场景一致；默认 **🦊 小狐狸**（名字与音色可配置）。独立实体，不占 NPC 槽位。
- **交互**：点击实体或点伴学者 → CompanionPopover（这个怎么说 / 怎么读 / 什么意思 + 自由输入）。
- **渐进提示**：求助时按层级递进（情境 → 首字母 → 揭晓 + 带读）。
- **可关闭**：全局开关 + 单场挂起。
- **场景 NPC（0–4 个）**：角色各异，均可对话；走 NPC Actor 管线。
- **对话焦点追踪**：场景内维护 `conversationFocus`（`npc:xxx` / `companion` / `none`）。用户语音 → 后端判定焦点 → 只调对应角色的 Prompt。点 NPC、NPC 主动搭话、伴学者插话都会更新焦点。

## 9. 语音链路

```
audio.start → PCM frames(20ms) → vad.speech_start → asr.partial → asr.final
→ dialogue.thinking → npc.text.delta → tts.audio.chunk → playback.started/completed
```

- **采集**：AudioWorklet 48kHz → 重采样 16kHz mono PCM16 → WS 二进制帧（20ms/帧）；JSON 只发控制消息。
- **端点检测（双层）**：浏览器 Silero VAD（快速起停/UI）+ 服务端 Silero VAD（最终切句）。起音 120ms / 尾静音 550ms / 最长 20s；连续说话升 750ms，短问句降 350–450ms；保留起音前 200ms 环形缓冲。
- **faster-whisper 滚动窗口伪流式**：每 300ms 检查，窗口保留 6–12s，上次稳定文本作 prefix，连续两次一致标记 stable，端点触发后最终解码。
  - 实时阶段 `beam_size=1`；最终阶段 `beam_size=3`，`vad_filter=False`（外部已做 VAD），`language="en"`。
- **TTS（Kokoro）**：按句子/语义分块合成，首块 8–20 词，后续进播放队列；**不能按单词切块**（韵律破碎）。
- **打断流程**：检测到有效语音 → 30–80ms 淡出 → `playback.interrupted`（含已播毫秒）→ 取消未开始 TTS 分块与 LLM 请求 → 仅把"确实已播放"的内容写入对话历史 → ASR 回声抑制。
- **端到端目标**：说完到听见回复 P50 < 1.2s，P95 < 2.0s。

## 10. LLM 服务拆分

四个逻辑角色（开始时都调 DeepSeek-V4-Flash，但 Prompt / 上下文 / 输出 Schema 必须隔离）：

| 角色 | 输入 | 输出 | 时机 |
|---|---|---|---|
| Scene Director | Brief、WordPack、世界摘要 | ScenePlan / SceneSkeleton | 进场、转场 |
| NPC Actor | NPC persona、局部场景、对话焦点、最近对话 | 台词、动作、教学事件 | 每轮语音 |
| Companion Tutor | 指代对象、用户水平、求助记录 | 渐进提示 | 用户求助 |
| Narrative Repair | 校验错误、缺失目标 | ScenePatch | 场景不合规 |

- NPC Actor 请求只发局部场景（可见实体、活跃目标、用户状态、最近几轮），**不发送完整 Scene DSL**。
- 输出严格结构；`speech` 一产生即送 TTS，不必等动作/教学元数据。
- 若 API 支持流式输出，让 `speech` 放第一个字段；用增量事件协议，不裸 `JSON.parse` 不完整 JSON。
- 后端做 OpenAI-compatible adapter（`LLMProvider` Protocol：`stream_structured(model, messages, schema, timeout, idempotency_key) -> AsyncIterator[LLMEvent]`），未来可换模型。

## 11. 学习引擎

**两条词流汇入一套掌握度**：

- **目标词流（Quest）**：导入词单 → 确定性处理（文件解析 → 去重 → lemma 化 → 词性 → 义项 → IPA/中文释义 → CEFR → 场景标签 → 入库）。每场选 5–7 词：2–3 到期复习 + 2–3 新词 + 1 发音薄弱词；必须共享场景标签，不硬塞不相关词。
- **偶遇词流（Free，新增）**：自由模式下用户求助/对话暴露的生词 → 记录 `spontaneous_encounter`（词/情境/场景）→ 去重聚合进偶遇词库（含频次）→ 一键导入目标词流。两条流汇入同一套 `mastery_states`。

**掌握度数据结构**：

```sql
learning_items(id, user_id, lemma, part_of_speech, sense_key,
  definition_en, definition_zh, ipa, cefr, source_list_id, scene_tags, created_at);

mastery_states(user_id, item_id,
  receptive_score, productive_score, pronunciation_score,
  stability, difficulty, due_at,
  exposure_count, help_count, success_count);

spontaneous_encounters(id, user_id, item_id, scene_id, context, source, count, first_seen_at, last_seen_at);
```

**证据事件**：统一 `learning.evidence` 格式（itemId / source / result / confidence / responseTimeMs / promptLevel / asrText / sceneId）。权重：自发正确产出 1.0、提示后 0.65、复述 0.4、正确动作理解 0.55、主动求助 −0.35、错误使用 −0.5。**低置信度音频标 `uncertain`，不直接判错**，必要时请用户重说。

**教学策略**：FSRS 决定何时复习；三维分数决定怎么教 —— 接受性低→口头描述选物；产出性低→NPC 创造必须主动说出目标词的情境；发音低→最小对立 + 慢速示范 + 音素提示；已掌握→自然出现、不再显式教学。

## 12. 数据与接口

**核心表**：users、user_profiles、sessions、session_briefs、world_memories、scenes、scene_revisions、entities、characters、character_memories、dialogue_turns、word_lists、learning_items、mastery_states、evidence_events、audio_artifacts、asset_catalog、llm_calls、error_events。**新增**：`spontaneous_encounters`、`scene_archetypes`。

**HTTP 接口**：

```text
POST /api/sessions
POST /api/sessions/{id}/brief
POST /api/word-lists/import
GET  /api/word-lists/spontaneous        # 偶遇词库
POST /api/word-lists/spontaneous/import # 偶遇词一键转目标词流
GET  /api/scenes/{id}
GET  /api/progress/summary
POST /api/settings/voice-test
```

**单一实时连接**：`WS /ws/sessions/{sessionId}`。所有 WS 消息包含 `eventId / sessionId / timestamp / sequence`。断线重连时客户端发送最后确认的 sequence，后端补发事件 —— 学习证据不因网络抖动丢失。

## 13. 容错与安全

**降级阶梯**（预先定义）：
- DeepSeek 超时 → NPC 用本地模板短句，界面不卡死
- DSL 校验失败 → 自动修复一次 → 仍失败用模板场景
- ASR CUDA 失败 → 切 CPU `small.en`，提示速度下降
- TTS 失败 → 显示字幕 + 浏览器 SpeechSynthesis 兜底
- Redis 不可用 → 进程内缓存
- 云图像失败 → 用通用资产；找不到资产按"同义词 → 类别 → 文本标签+通用轮廓 → 后台云生成"降级，**永远不因缺图阻塞场景进入**
- 伴学者不知道指代 → 高亮最多三个候选物品让用户选择

**安全**：用户输入/转写/文件内容作为结构化字段，不拼入系统 Prompt；外部 URL 禁止进入 Scene DSL，素材 URL 由后端把 `assetKey` 转成本站地址；日志默认不保存原始麦克风音频，只保存用户明确授权的片段。

## 14. 目录结构

```text
english-town/
  apps/
    web/                  # React
    api/                  # FastAPI 编排服务
  services/
    asr-worker/
    tts-worker/
  packages/
    scene-schema/         # JSON Schema + TS/Python 类型
    event-contracts/
    prompt-templates/
    learning-engine/
  assets/
    archetypes/           # 场景原型定义
    catalog/
    backgrounds/
    props/
    characters/
    ambience/
  infra/
    docker-compose.yml
  tests/
    contract/
    e2e/
    latency/
```

- Python 用 `uv` 管理；Node 用 `pnpm workspace`。
- 共享 Schema 以 **JSON Schema 为唯一事实源**，生成 TypeScript 与 Pydantic 类型，避免前后端手工维护两份接口。

## 15. 里程碑与验收指标

| 阶段 | 内容 | 验收 |
|---|---|---|
| 1 | 手工面包店场景：DOM 渲染 + 点击 + 字幕 + AudioWorklet + faster-whisper + Kokoro | 用户说完后 1.5s 内听到 NPC 首段声音 |
| 2 | 接 DeepSeek：NPC Actor + Companion Tutor，严格结构化输出，超时/取消/打断/历史裁剪 | 对话链路稳定、可打断 |
| 3 | Scene Director：原型系统 + Skeleton/Patch + 资产目录 + 转场预取 | 已预取转场 300ms 内首屏，未预取 2s 内可交互 |
| 4 | 学习引擎：词表导入 + 证据 + FSRS + 目标模式 + 偶遇词流 + 实时重规划 | 每个目标词可追溯到视觉/对话/表现证据 |
| 5 | WorldMemory + NPC 长期记忆 + 发音评分 + 云端图像（最后做） | 跨场景记忆生效 |

**首版性能目标**：
- 首场景骨架出现 P95 < 2.0s；命中预取转场 P95 < 0.3s
- VAD 判定用户说完 0.35–0.65s；最终 ASR P95 < 0.5s
- LLM 首个有效台词 P95 < 0.8s；TTS 首音频 P95 < 0.35s
- 说完到听见回复端到端 P50 < 1.2s / P95 < 2.0s
- 场景 Schema 有效率 > 99.5%；目标词视觉/对话覆盖率 100%
- 浏览器稳定内存 < 1.5GB；本机总内存长期使用 < 14GB

## 16. 技术栈清单

- 前端：React 19 + TypeScript + Vite / Zustand / XState / TanStack Query / Zod / Motion / AudioWorklet / Playwright + Vitest
- 后端：FastAPI / faster-whisper（CTranslate2）/ Silero VAD / Kokoro TTS / OpenAI-compatible LLM adapter
- 数据：SQLite（开发）→ PostgreSQL（部署）；进程内 LRU（开发）→ Redis（部署）
- 工具：uv（Python）、pnpm workspace（Node）

## 17. 开放项（实现时确认）

- DeepSeek 具体模型 ID、价格、限流、结构化输出能力 —— 以实际接入时官方 API 文档为准。
- 伴学者默认形象与音色（🦊 + Kokoro 音色名）可配置。
- CUDA 12.8 + CTranslate2 在 RTX 5060（Blackwell）上的稳定 `compute_type`（float16 → int8_float16）需实测。
