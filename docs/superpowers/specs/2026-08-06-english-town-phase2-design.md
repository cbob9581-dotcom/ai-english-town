# 英语小镇 · 阶段 2 设计文档：LLM NPC 闭环（DeepSeek NPC Actor + Companion Tutor）

日期：2026-08-06
状态：草稿（待用户复核）

> 本阶段是 spec `docs/superpowers/specs/2026-08-05-english-town-design.md` v2 的**里程碑 2** 落地：
> "接 DeepSeek：NPC Actor + Companion Tutor；双通道协议；提案边界；超时/打断/历史裁剪。验收＝对话稳定、可打断、无状态越权。"

---

## 0. 前置事实（阶段 1 合并后代码 433a822）

- **语音回合**：浏览器 → WS 音频帧 → `run_round`（ASR 一次性 HTTP POST → `scripted_npc` 本地模板 → `npc.speech.commit` → TTS 单块 → `dialogue.turn` **先写库再发音频**）。
- **浏览器 VAD** = RMS 门限；**api↔asr** = 一次性 HTTP（`/transcribe`）；前端 `askCompanion` 是 `alert()` 占位。
- 仓库无任何 LLM 适配层、无 DeepSeek key 配置；`scripted_npc` 已按 spec §14 作为"DeepSeek 超时 → 本地模板短句"的兜底实现（保留复用）。
- **全局约束**（沿用阶段 1，每个任务隐式遵守）：Uvicorn 单 worker；SQLite WAL + 单写队列；先写库再发送；无 `innerHTML`；组件白名单；坐标 `0..1000`；热区 ≥44px；单场景 DOM 实体 ≤40；不加载外部 URL。

---

## 1. 已确认决策

| # | 决策 | 结论 |
|---|---|---|
| 1 | DeepSeek 接入 | 官方 key 可用。LLM 适配层 **provider 无关**（OpenAI 兼容，`base_url / model / api_key` 可配置），DeepSeek `deepseek-chat` 为默认；**mock adapter** 供离线测试（无 key 时自动落 mock）。 |
| 2 | 阶段 2 范围 | **核心闭环**：NPC Actor + Companion Tutor（基础带读）+ 双通道协议 + 提案边界 + 超时(3s)/打断/历史裁剪。**推迟**：Silero VAD（浏览器+服务端，仍 RMS）、api↔asr 流式 WS（仍一次性 POST）、中文语言模式（仅 en）、手势/高亮可视化渲染（metadata 只传不画）、渐进提示、Narrative Repair 自动修复。 |
| 3 | 回复产生方式 | **方案 A（单次结构化响应）**：LLM 一次 JSON-mode 调用 → 校验 → `npc.speech.commit`(全文) + `npc.turn.metadata` → TTS 整段。协议保留 `npc.speech.delta` 消息类型但 **v1 不发射**（未来升流式 B 不改协议）。若实施早期 spike 实测 DeepSeek 稳定增量输出可靠，可在同阶段内把 commit-only 升级为 `delta + 句子级 TTS`。 |
| 4 | Companion Tutor 深度 | **基础带读**：点击实体 →「读给我听」→ Tutor 一次 LLM 调用给出 `{word, scaffold}` → TTS 读出 → 弹窗显示。无渐进提示、无自由输入追问、无"什么意思"中文释义。 |

---

## 2. 架构总览 + LLM 适配层

**新增组件全部在 `apps/api` 内，不新增进程**（LLM 调用是 I/O 密集型，单 worker 异步即可）：

```text
apps/api/app/
  llm/
    client.py        # OpenAI 兼容客户端（provider 无关）
    mock.py          # 确定性 mock LLM（离线测试）
    proposals.py     # 提案边界：JSON Schema + 白名单校验
    npc_actor.py     # NPC Actor：构造 prompt → 调用 → 校验 → 提案
    tutor.py         # Companion Tutor：点击实体 → 带读提案
  settings.py        # + LLM 配置字段
  llm_log.py         # llm_calls 表（每次调用 token/耗时/成败）
```

**`settings.py` 新增字段**（全部可 env 覆盖）：

```python
llm_base_url: str = "https://api.deepseek.com"   # 可换 OpenRouter / 本地 vLLM
llm_api_key: str = ""                            # 从 env DEEPSEEK_API_KEY 读
llm_model: str = "deepseek-chat"                 # 默认；可换
llm_timeout_s: float = 3.0                       # spec §14 云端超时 3s
llm_max_speech_chars: int = 200                  # NPC speech 长度上限
llm_max_scaffold_chars: int = 120                # Tutor scaffold 长度上限
```

**`llm/client.py`**：用 `openai` SDK（pin 版本），`base_url` 可配置 → 天然支持任何 OpenAI 兼容端点。`complete(messages, json_schema)` 走 `response_format={"type":"json_object"}`，返回解析后的 dict + 用量（prompt/completion tokens）。**只对连接错误重试一次**（spec §14），业务失败不重试。无 key → 工厂返回 mock。

**`llm/mock.py`**：确定性 mock。`npc_actor` 按输入匹配固定模板（复用 `scripted_npc` 的回复集，返回合法提案）；`tutor` 返回固定 `{word, scaffold}`。测试离线、快速、确定。

**`llm_log.py`**：`llm_calls` 表（spec §13 表清单含此项），与 `session_events` 同一 SQLite：

```sql
CREATE TABLE IF NOT EXISTS llm_calls(
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id        TEXT NOT NULL,
  turn_id           TEXT,
  role              TEXT NOT NULL,      -- npc_actor | companion_tutor
  model             TEXT NOT NULL,
  prompt_tokens     INTEGER,
  completion_tokens INTEGER,
  latency_ms        INTEGER,
  ok                INTEGER NOT NULL,   -- 0=失败(超时/校验失败/网络)
  error             TEXT,
  created_at        TEXT NOT NULL
);
```

每次调用写一行（含降级原因）。超时/校验失败也记 `ok=0` —— 兑现"记录每次 LLM 调用 token 与耗时"。

**改动面**：`run_round` 的 `scripted_reply(final_text)` 替换为 `npc_actor.reply(...)`；`scripted_npc` 保留为降级兜底。前端不感知适配层，只感知新增消息类型。

---

## 3. 双通道协议 + NPC Actor

### 双通道协议（spec §10，v1 只发 `commit`）

```jsonc
// 出站（v1 实际发射的两条）
{ "type": "npc.speech.commit", "turnId": "turn_123", "text": "The loaf is three dollars. Would you like one?" }
{ "type": "npc.turn.metadata", "turnId": "turn_123",
  "gesture": { "type": "point", "entityId": "loaf-1" },
  "candidateWordIds": ["word_loaf_n_1"] }

// 保留类型（v1 不发射；升流式 B 时启用，前端不需改）
{ "type": "npc.speech.delta", "turnId": "turn_123", "text": "The loaf is" }
```

- **TTS 只消费 speech 通道**（`commit.text`）；metadata 异步不抢通道（v1 顺发，不并发）。
- 前端：`commit` → 字幕入 DialogueDock；`metadata` → 存进 turn 对象（阶段 2 仅存储/记录；手势/高亮可视化渲染推迟）。

### NPC Actor 提案流程

```text
ASR final（untrusted）
  → prompt 构造：trusted 系统人格(Rosa/面包店/教学等级) + untrusted 数据(最近对话轮次、场景实体名、用户文本)
  → client.complete(json_object)            [3s 超时；连接错误重试 1 次]
  → proposals.validate_npc_reply(doc, allowed_word_ids)   [提案边界]
  → 通过 → npc.speech.commit + npc.turn.metadata → TTS
  → 超时 / 校验失败 / 网络错误 → 降级 scripted_npc（spec §14）
  → 每次调用写 llm_calls（含降级原因）
```

### 提案边界（spec §7 硬约束落实）

| 字段 | 规则 |
|---|---|
| `speech` | 1..200 字符，纯英文（阶段 2 无中文模式）；为 TTS 单段 |
| `gesture.type` | 枚举 `[nod, shake, point, wave]` |
| `gesture.entityId` | 若出现，必须存在于当前场景实体 id 集合 |
| `candidateWordIds` | **只能从后端传入的候选词 ID 集合里选**（当前场景实体 `semantics.wordId` 列表）；LLM 不得创建词 ID —— 集合成员校验 |

**不信任 LLM 自报的 exposure**：`candidateWordIds` 只作元数据传递，学习证据入账留给阶段 4（服务端词法匹配）。

### 历史裁剪与上下文

- 每个会话维护**最近 10 轮**对话（`dialogue.turn` 的 user/npc 文本），按 ~1200 字符预算截断进 actor prompt。
- 上下文构成：系统人格（trusted 模板）+ 场景概要（当前实体名、角色）+ 最近对话（untrusted 数据）+ 本次用户文本。
- 场景实体的 `allowed_word_ids` / 实体 id 集合由 `scene_store` 的编译场景提供。

### 安全（spec §14）

- 系统 prompt 只有程序模板（trusted）；用户文本、转写结果作为结构化数据放进 user 消息，**绝不拼进系统 prompt**。
- LLM 输出严格白名单 + 服务端校验（上表）。
- 用户文本长度限制；LLM 日志（`llm_calls`）不存原始麦克风音频、不含 key。

### 降级阶梯（阶段 2 实现 spec §14 前半段）

- DeepSeek 超时(3s) / 校验失败 / 连接错误重试仍败 → `scripted_npc` 本地模板短句（阶段 1 已建好）。
- 每轮 LLM 调用 1 次；超预算告警依赖 `llm_calls` 查询（阶段 2 不建告警器）。

---

## 4. Companion Tutor（基础带读）

替换阶段 1 的 `alert()` 占位：

```text
点击实体（如 loaf）→ CompanionPopover 显示该词 + 「读给我听」
  → 前端发 WS 控制：{ "type": "companion.ask", "entityId": "loaf-1", "word": "loaf" }
  → 服务端 tutor.reply(word)：一次 LLM 调用(json_object) 返回
      { "word": "loaf", "scaffold": "A loaf is a big piece of bread. Say it: loaf." }
  → 校验（word 必须等于请求的实体词；scaffold 长度上限 ~120 字符）
  → 出站：
      { "type": "companion.reply", "turnId": "...", "word": "loaf", "scaffold": "..." }
      + tts.audio.start / audio / tts.audio.end（TTS 读 scaffold；缺省则读单词）
  → 弹窗显示 word + scaffold
  → 超时/校验失败 → 直接读单词（scaffold 缺省），不阻断带读
```

- Tutor prompt：trusted 角色（"给英文单词 + 一句含该词的简单句，输出 JSON"）+ untrusted 数据（实体名/词）。
- 复用现有 WS 单连接 + 前端播放队列（`audio.binary` 已路由到播放器），无新通道。
- 走 `llm_calls` 记账，`role=companion_tutor`。

---

## 5. 打断与历史（spec §11 兑现）

- 每个语音回合跑在**独立 asyncio 任务**里，按 `utteranceId` 跟踪。
- **用户开始新话语（`audio.start`）即触发打断**：前端停止正在播放的音频 + 清空播放队列；服务端取消 pending 回合（省 LLM/TTS 成本，防旧回复覆盖），并把进行中的回合标记 `interrupted`。
- `playback.interrupted { playedMs }` → 取消未开始 TTS；`dialogue.turn` 写入带 `interrupted: true` + `playedMs`；已写全文保留。
  - ⚠️ 说明：严格"只把已播放部分写入历史"需 ms→文本映射，阶段 2 以 `interrupted` 标记 + `playedMs` 记录代替（简化、可追溯），精确映射留后续阶段。
- 历史窗口 = 最近 10 轮（§3.4）。

---

## 6. 测试与验收

| 层 | 内容 |
|---|---|
| 单测（mock LLM） | 提案校验（合法通过 / 非法 wordId / 非法 gesture / 超长 speech 拒绝）；`npc_actor`（happy path / 超时→scripted 降级 / 校验失败→降级）；tutor 流程；打断取消；历史窗口构造；`llm_calls` 写入（含 ok=0 行） |
| 集成（mock asr/tts/llm） | WS 全回合：audio 进 → `commit`+`metadata` 出 → audio 出；中途打断 → 回合取消；**无状态越权**（LLM 提案不改任何库状态 —— 断言除 `dialogue.turn`/`llm_calls` 外无写入） |
| 回归 | 阶段 1 全部 Python/vitest 套件保持绿 |
| 手动冒烟 | `scripts/llm-smoke.py`（真 key）：NPC 一回合 + Tutor 一回合，打印 JSON + token 用量 —— 不进 CI |

**验收标准**（spec 里程碑 2）：对话稳定（mock 可测）、可打断（任务取消可测）、**无状态越权**（LLM 只出提案，校验后除 `dialogue.turn`/`llm_calls` 外不写任何状态 —— 测试断言）。真实模型门禁 `e2e_voice_ok` / `p95` 仍随阶段 1 既定决策推迟（需模型 + CUDA runtime）。

---

## 7. 范围外（本阶段明确不做）

- Silero VAD（浏览器 + 服务端）——仍 RMS 门限
- api↔asr-worker 流式 WS ——仍一次性 HTTP POST
- 中文语言模式 ——仅 en
- 手势/实体高亮可视化渲染 ——metadata 只传不画
- 渐进提示（情境→首字母→揭晓+带读）
- Narrative Repair 自动修复 ——校验失败直接降级
- Scene Director / ScenePreloadManager / ScenePatch（阶段 3）
- 学习引擎 / 偶遇词 / 证据入账（阶段 4）

---

## 8. 交付物形态

- 新建：`apps/api/app/llm/{client,mock,proposals,npc_actor,tutor}.py`、`apps/api/app/llm_log.py`、`scripts/llm-smoke.py`
- 修改：`apps/api/app/settings.py`、`apps/api/app/ws.py`、`apps/api/app/voice_round.py`、`apps/web/src/useVoiceRound.ts`、`apps/web/src/CompanionPopover.tsx`（接 `companion.ask/reply` + metadata 存储）
- 测试：`apps/api/tests/test_{proposals,npc_actor,tutor,interrupt,llm_log}.py` 等；前端对应 vitest 补 case
- 实现计划由 `writing-plans` 输出（`docs/superpowers/plans/2026-08-06-english-town-phase2.md`）
