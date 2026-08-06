# 英语小镇 · 阶段 2 设计文档：LLM NPC 闭环（流式 NPC Actor + Companion Tutor）

日期：2026-08-06（草稿）→ 2026-08-06（v2 修订，吸收技术评审：流式替代单次 JSON、AEC/ducking/barge-in、append-only 打断、评审 12 项 + 建议项）
状态：v2 待用户复核

> 本阶段是 spec `docs/superpowers/specs/2026-08-05-english-town-design.md` v2 的**里程碑 2** 落地：
> "接 DeepSeek：NPC Actor + Companion Tutor；双通道协议；提案边界；超时/打断/历史裁剪。验收＝对话稳定、可打断、无状态越权。"
>
> **v2 相对 v1 的核心修订**（吸收评审）：① NPC 回复从"单次完整 JSON → TTS"改为**纯英文文本流式**（stream=True）→ 按句切分 → 逐句 TTS → `delta`/`commit`，元数据由服务端从 `commit.text` 派生（非 LLM 自报）；② 上真实音频健壮性：AEC + TTS 播放期 RMS ducking + barge-in 最小时长（否则"可打断"在真机自锁）；③ 打断用 append-only 独立事件而非 UPDATE；④ 双端 generationId/turnId 过期丢弃硬规则；⑤ Tutor 加缓存与强化校验；⑥ llm_calls 补排查字段 + 枚举 fallback_reason；⑦ mock 支持故障注入；⑧ 成本护栏 + 分角色超时。

---

## 0. 前置事实（阶段 1 合并后代码 433a822）

- **语音回合**：浏览器 → WS 音频帧 → `run_round`（ASR 一次性 HTTP POST → `scripted_npc` 本地模板 → `npc.speech.commit` → TTS 单块 → `dialogue.turn` **先写库再发音频**）。
- **浏览器 VAD** = RMS 门限（`RmsGate`）；**api↔asr** = 一次性 HTTP（`/transcribe`）；前端 `askCompanion` 是 `alert()` 占位。
- 仓库无任何 LLM 适配层、无 DeepSeek key 配置；`scripted_npc` 已按 spec §14 作为"DeepSeek 超时 → 本地模板短句"的兜底实现（保留复用）。
- **全局约束**（沿用阶段 1，每个任务隐式遵守）：Uvicorn 单 worker；SQLite WAL + 单写队列；先写库再发送；无 `innerHTML`；组件白名单；坐标 `0..1000`；热区 ≥44px；单场景 DOM 实体 ≤40；不加载外部 URL。

---

## 1. 已确认决策

| # | 决策 | 结论 |
|---|---|---|
| 1 | DeepSeek 接入 | 官方 key 可用。LLM 适配层 **provider 无关**（OpenAI 兼容，`base_url / model / api_key` 可配置），DeepSeek `deepseek-chat` 为默认；**mock adapter** 供离线测试（无 key 时自动落 mock）。 |
| 2 | 阶段 2 范围 | **核心闭环**：流式 NPC Actor + Companion Tutor（基础带读 + 缓存）+ 双通道协议（服务端派生元数据）+ 纯文本校验边界 + 超时/打断/历史裁剪 + **音频健壮性（AEC / ducking / barge-in）**。**推迟**：Silero VAD（浏览器+服务端，仍 RMS）、api↔asr 流式 WS（仍一次性 POST）、中文语言模式（仅 en）、渐进提示、Narrative Repair 自动修复。**gesture 整体推到阶段 3**（阶段 2 不产出、不渲染）。 |
| 3 | NPC 回复产生方式 | **纯英文文本流式**（方案 B，替代方案 A）：LLM 输出纯文本（非 JSON，`stream=True`）→ 按句切分（首块 8–20 词）→ 逐句 TTS → 边流边发 `npc.speech.delta` → 结束发 `npc.speech.commit`（全文，幂等校正）→ 服务端从 `commit.text` 派生 `candidateWordIds`（lemma 匹配场景 allowed 词表）→ `npc.turn.metadata`（**服务端权威，无任何 LLM 自报字段**）。延迟从"等完整 JSON"降到首句；协议第一天即流式，未来无需迁移。 |
| 4 | Companion Tutor 深度 | **基础带读 + 缓存**：点击实体 →「读给我听」→ Tutor 一次 JSON-mode 调用返回 `{word, scaffold}`（不在硬实时路径）→ TTS 读出 → 弹窗显示。命中 `tutor_cache` 则 0 LLM、0 TTS。无渐进提示、无自由输入追问、无"什么意思"中文释义。 |

---

## 2. 架构总览 + LLM 适配层

**新增组件全部在 `apps/api` 内，不新增进程**（LLM 调用是 I/O 密集型，单 worker 异步即可）：

```text
apps/api/app/
  llm/
    client.py        # OpenAI 兼容客户端（provider 无关；支持 stream + json_object）
    mock.py          # 确定性 mock LLM（离线测试；支持故障注入 scenario）
    lexmatch.py      # 服务端词法匹配：commit.text → candidateWordIds（lemma）
    chunker.py       # 流式句子切分：delta → 句子块（首块 8-20 词）
    proposals.py     # 纯文本/JSON 校验边界
    npc_actor.py     # NPC Actor：prompt 构造 → 流式 → 切句 → 校验 → delta/commit
    tutor.py         # Companion Tutor：点击实体 → {word, scaffold} 带读提案（+缓存）
  settings.py        # + LLM 配置字段
  llm_log.py         # llm_calls 表（含 fallback_reason 枚举、attempt）
```

**`settings.py` 新增字段**（全部可 env 覆盖）：

```python
llm_base_url: str = "https://api.deepseek.com"        # 可换 OpenRouter / 本地 vLLM
llm_api_key: str = ""                                 # 从 env DEEPSEEK_API_KEY 读；空 → mock
llm_model: str = "deepseek-chat"                      # 默认；拒绝 deepseek-reasoner（不支持 JSON Output）
llm_connect_timeout_s: float = 1.5                    # 连不上 vs 生成慢 分开
llm_ttft_timeout_s: float = 2.0                       # 流式后启用（首 token 时间）
llm_total_timeout_npc_s: float = 3.0                  # spec §14 云端总超时（NPC 对话）
llm_total_timeout_tutor_s: float = 6.0                # Tutor 有 loading 态，可等
llm_max_speech_chars: int = 200                       # NPC speech 全文上限
llm_max_scaffold_chars: int = 120                     # Tutor scaffold 上限
llm_temperature_npc: float = 0.8                      # 活泼（显式设置，不用 SDK 默认）
llm_temperature_tutor: float = 0.3                    # 稳定
llm_max_tokens_npc: int = 320                         # 流式文本预算
llm_max_tokens_tutor: int = 320                       # JSON 输出须显式设足（~300+ 余量）
llm_session_call_cap: int = 200                       # 成本护栏：单 session LLM 调用上限
llm_concurrency_limit: int = 2                        # per-session 并发信号量
```

**`llm/client.py`**：用 `openai` SDK（pin 版本），`base_url` 可配置 → 天然支持任何 OpenAI 兼容端点。
- `stream_text(messages)` → async 迭代器（增量文本 deltas，`temperature=0.8`）。
- `complete_json(messages, schema_hint)` → `response_format={"type":"json_object"}`，返回解析后 dict + 用量；**Tutor 专用**。
- 只对**连接错误**重试一次（spec §14）；业务失败不重试。无 key → 工厂返回 mock。

**`llm/mock.py`**：确定性 mock，支持**故障注入**（第 12 节测试用），通过 env/注入参数选择：

```text
MOCK_LLM_SCENARIO = ok | timeout | connect_error | invalid_json
                  | bad_word_id | missing_word | too_long | truncated | empty
```

`ok` 时 NPC 返回 scripted_npc 风格文本流（按句吐字，可测切分），Tutor 返回合法 `{word, scaffold}`。其余 scenario 触发对应降级路径 —— 让"超时→降级 / 校验失败→降级"是真覆盖而非 monkeypatch 硬造。

**`llm_log.py`**：`llm_calls` 表（spec §13），与 `session_events` 同一 SQLite：

```sql
CREATE TABLE IF NOT EXISTS llm_calls(
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id        TEXT NOT NULL,
  generation_id     TEXT,
  turn_id           TEXT,
  utterance_id      TEXT,
  role              TEXT NOT NULL,      -- npc_actor | companion_tutor
  model             TEXT NOT NULL,
  prompt_tokens     INTEGER,
  completion_tokens INTEGER,
  latency_ms        INTEGER,
  ttft_ms           INTEGER,            -- 流式后才有意义，先留列
  finish_reason     TEXT,               -- stop | length | ...
  fallback_reason   TEXT,               -- 枚举：timeout|connect|invalid_json|schema_reject
                                        --     |length_truncated|no_key|budget|none
  attempt           INTEGER,            -- 第几次尝试（重试=2）
  ok                INTEGER NOT NULL,   -- 0=失败
  error             TEXT,
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_session ON llm_calls(session_id, created_at);
```

`fallback_reason` 用枚举而非自由文本 —— 首次调延迟/预算参数时可聚合统计。每次调用写一行（含降级原因），超时/校验失败/预算拦截也记 `ok=0`。

**改动面**：`run_round` 的 `scripted_reply(...)` 替换为 `npc_actor.stream_reply(...)`；`scripted_npc` 保留为降级兜底。前端不感知适配层，只感知新增消息类型。

---

## 3. NPC Actor 流式管线

```text
ASR final（untrusted）
  → prompt 构造：trusted 系统人格(Rosa/面包店/教学等级) + untrusted 数据(最近对话轮次、场景实体名、用户文本，结构化字段)
  → client.stream_text(messages)              [connect 1.5s / ttft 2.0s / total 3.0s；连接错误重试 1 次]
  → chunker：delta 累积 → 句子块（句号/问号/叹号处切句、完整短句照发；20 词强制断句、词边界不断词）
  → 每句：校验 → 发 npc.speech.delta → 逐句 TTS → tts.audio.start/audio/end（chunkId 递增）
  → 流结束：npc.speech.commit（全文，幂等）→ lexmatch 派生 candidateWordIds → npc.turn.metadata
  → 超时 / 校验失败 / 网络错误 / length_truncated → 降级 scripted_npc（发单句 commit + metadata）
  → 每次调用写 llm_calls（含 fallback_reason）
```

- **TTS 只消费 speech 通道**：每句 delta 即触发该句 TTS（首句 8–20 词，不能按单词切块 —— spec §11）。
- **逐句 TTS 串行**：读流 → 切句 → 一句完整即 await 该句 TTS（发音频）→ 再处理下一句；LLM 流在后台持续产出、服务端按句缓冲。不追求"TTS 与 LLM 生成并发"（Kokoro 本就整句合成，收益有限；并发编排留给未来）。
- `chunker.py` 纯函数可测：`feed(delta) -> list[str]`（完整句子）、`finalize() -> str`（余量）。
- `lexmatch.py` 纯函数可测：`derive_candidate_word_ids(text, allowed: {wordId: lemma}) -> list[wordId]` —— tokenize → 小写去标点 → 屈折归一（复数 -s/-es/-ies 等）→ 对场景实体词表匹配。**这是 spec §7 服务端词法匹配的最终形态**，不信任 LLM 自报 exposure。

### 纯文本校验边界（proposals.validate_speech）

| 规则 |
|---|
| `text` 1..200 字符；纯英文 ASCII + 基本标点（拒绝换行、代码块、URL） |
| **超长直接判失败降级，绝不截断** —— 截断会让 TTS 读半句话，比模板句更糟 |
| 不含系统 prompt 片段特征 |

`npc.speech.commit` 后派生 metadata；`npc.speech.delta` 的逐句文本同样过长度校验（超长立即降级）。

---

## 4. 双通道协议 + 过期丢弃

### 协议（spec §10，v1 即流式）

```jsonc
// 流式中逐句发（TTS 逐句消费）
{ "type": "npc.speech.delta",  "generationId": "gen_...", "turnId": "turn_123", "text": "The loaf is three dollars." }
{ "type": "tts.audio.start",   "generationId": "gen_...", "turnId": "turn_123", "chunkId": "s1", "sampleRate": 24000 }
// …该句 TTS 音频二进制…
{ "type": "tts.audio.end",     "generationId": "gen_...", "turnId": "turn_123", "chunkId": "s1" }

// 流结束（幂等校正）
{ "type": "npc.speech.commit", "generationId": "gen_...", "turnId": "turn_123", "text": "The loaf is three dollars. Would you like one?" }
{ "type": "npc.turn.metadata", "generationId": "gen_...", "turnId": "turn_123",
  "candidateWordIds": ["word_loaf_n_1"] }
```

- `npc.speech.commit` 是权威全文：前端用其**覆盖**已累积的 delta 字幕（幂等）。
- 阶段 2 metadata 只含服务端派生的 `candidateWordIds`；gesture 阶段 3 再加。
- 前端现在就实现 **delta 累积 → commit 覆盖**逻辑（v1 即使不发 delta 也跑通，未来升流式零改动）。

### 双端过期丢弃硬规则

- **服务端**：每轮生成 `turnId`；会话持有 `session.currentGenerationId`（阶段 2 静态场景=会话级固定值；阶段 3 场景变更时递增）。**发送前**比对 `currentGenerationId`，不匹配则丢弃（被取消的回合晚到的 LLM delta / TTS 分块一律不发）。
- **前端**：持有 `currentTurnId` + `currentGenerationId`；收到非当前 `turnId` 的 `delta/commit/metadata/tts.audio.start` 一律丢弃 —— 不入字幕、不进播放队列（音频块靠 `tts.audio.start` 携带的 turnId 归属，丢弃即清队）。
- **测试**：构造"打断后旧 LLM 响应晚到"用例，断言无音频发出、无字幕更新。

---

## 5. 音频健壮性（AEC + ducking + barge-in）—— 阶段 2 必做

**问题**：阶段 2 仍用 RMS 门限 + 无真回声消除；TTS 扬声器声音被麦克风采到 → RMS 越阈 → 触发 `audio.start` → 打断刚开口的 NPC → 自锁死循环。

**四层处理**：

1. **`getUserMedia` 显式约束**（`mic.ts`）：`{ echoCancellation: true, noiseSuppression: true, autoGainControl: true }`。
2. **TTS 播放期 RMS ducking**（前端 `RmsGate`）：播放中抬高越阈门限（增益因子可配，默认 ~2×）；退出播放后 **300ms** 才恢复常态。
3. **barge-in 最小时长**（前端 `RmsGate`）：**连续 300ms**（默认，可配置）超阈才算有效打断；单帧尖峰不算。`RmsGate` 改为带 hold 计时的状态机。
4. **服务端 `playbackState`**：`tts.audio.start` 置 playing，`tts.audio.end`/打断 置 idle。播放中收到 `audio.start` 视为 barge-in：取消 pending 回合 + 标记当前回合打断；**spurious 守卫**——`audio.start` 后 500ms 内无任何音频帧到达则自动忽略（不取消回合、不记打断）。

**冒烟**：耳机与扬声器两种都要测（耳机通过 ≠ 扬声器通过）。

---

## 6. 打断与事件日志（append-only）

**回合任务机制**：每个语音回合跑在**独立 asyncio 任务**里（`asyncio.create_task`），WS 接收循环保持空闲、可随时处理控制消息 —— 否则回合内 await TTS 时 `audio.start`/`interrupt` 消息排不到队、无法取消。打断 = 取消该回合任务（未完成的 LLM 流 / TTS 一并取消），晚到的结果由 §4 双端过期丢弃兜底。

**不得对已写行做 UPDATE** —— `session_events` 是 append-only、可按 sequence 重放（spec §12）；UPDATE 会让重放与实时不一致。

- `dialogue.turn` 先写库（含全文 npcText）。
- 打断**追加独立事件**：

```json
{ "type": "dialogue.turn.interrupted", "generationId": "gen_...", "turnId": "turn_123", "playedMs": 1840 }
```

- 读取时由投影逻辑合并（`dialogue.turn` + 匹配 turnId 的 `interrupted` 事件 → 打断标记 + playedMs）。不需要新表。
- **触发打断的两条路径**：① 播放中用户 barge-in（§5）；② 播放中收到显式 `playback.interrupted { playedMs }`。两者都走"取消 pending 回合 + append interrupted 事件 + 前端停止播放/清队"。

---

## 7. Companion Tutor（基础带读 + 缓存 + 强化校验）

### 流程

```text
点击实体（如 loaf）→ CompanionPopover 显示该词 + 「读给我听」
  → 前端发 WS 控制：{ "type": "companion.ask", "entityId": "loaf-1" }     ← 只传 entityId，不传 word
  → 服务端从编译场景查该实体 semantics.wordId / lemma 得 word            ← 单一事实源
  → 查 tutor_cache[word_id]：
       命中 → 直接返回缓存 scaffold + 缓存音频（0 LLM、0 TTS）
       未命中 → tutor.reply(word)：一次 JSON-mode 调用返回
                 { "word": "loaf", "scaffold": "A loaf is a big piece of bread. Say it: loaf." }
                → 校验 → TTS 合成 → 写 tutor_cache（含音频）→ 返回
  → 出站：
      { "type": "companion.reply", "turnId": "...", "word": "loaf", "scaffold": "..." }
      + tts.audio.start / audio / tts.audio.end
  → 弹窗显示 word + scaffold
  → 超时/校验失败 → 降级为只读单词（scaffold 缺省），不阻断带读
```

### 校验（proposals.validate_tutor —— 不只是 word==请求词 + 长度）

| 规则 |
|---|
| `word` 必须等于请求实体词的**服务端** wordId/lemma |
| `scaffold` 必须**包含目标词**（允许屈折形式，lemma 匹配） |
| `scaffold` 纯 ASCII 英文 + 基本标点；拒绝换行、代码块标记、URL |
| 不含 system prompt 片段特征 |
| 不含目标词 → 降级为只读单词（不读一句不含目标词的话） |
| 超长（>120 字符）→ 判失败降级，不截断 |

### tutor_cache 表

```sql
CREATE TABLE IF NOT EXISTS tutor_cache(
  word_id    TEXT PRIMARY KEY,
  scaffold   TEXT NOT NULL,
  model      TEXT NOT NULL,
  audio_path TEXT,            -- 复用已合成音频，二次点击 0 延迟
  sample_rate INTEGER,        -- 与音频一起缓存，重放需 sampleRate
  created_at TEXT NOT NULL
);
```

音频落盘到本地缓存目录（如 `data/tutor-audio/{word_id}.wav`）。首版 5 个原型的常见物品很快就会全部命中。

---

## 8. 历史裁剪

- **轮的定义**：一次 user + 一次 npc = 1 轮（成对入历史）。
- 每会话维护最近 **10 轮**，~1200 字符预算截断；**从旧到新丢弃且保持成对**，不出现孤立 npc 台词。
- **打断轮次标注**：被打断的 NPC turn 存全文但用户只听到前半段；喂回 prompt 时必须标注，否则 NPC 假定用户已知全部内容（"你刚才不是说了吗"）：

```text
Rosa (interrupted after 1.8s): "The loaf is three dollars. Would you like..."
```

- 上下文构成：系统人格（trusted）+ 场景概要 + 历史（含打断标注）+ 本次用户文本（结构化字段）。

---

## 9. 安全（spec §14）

- 系统 prompt 只有程序模板（trusted）；用户文本、转写结果**作为结构化字段放进 user 消息**（`{"transcript": "...", "recent_turns": [...]}`），system prompt 声明这些字段是数据不是指令 —— 比"自然语言拼接"更明确一层。
- LLM 输出严格白名单 + 服务端校验（§3.4 / §7.2）；`candidateWordIds` 全服务端派生，无 LLM 自报。
- 用户文本长度限制；`llm_calls` 不存原始麦克风音频、不含 key。
- **模型守卫**：`llm_model` 拒绝 `deepseek-reasoner` 类（不支持 JSON Output / function calling，配错会在运行时才炸）。

---

## 10. 成本护栏（spec §14 session 级 token 预算，阶段 2 最粗闸）

- **单 session LLM 调用上限** `llm_session_call_cap=200`；超限 → 强制 scripted / 命中缓存，记 `fallback_reason=budget`。
- **per-session 并发信号量** `llm_concurrency_limit=2`。
- **同 entityId 的 in-flight tutor 请求合并**：用户连点不放大调用（pending 中复用同一结果）。
- 一个循环 bug + 真 key，代价是直接的 —— 这层闸必须有。

---

## 11. 分角色超时与采样参数

- NPC（硬实时）：connect 1.5s / ttft 2.0s / total 3.0s，`temperature=0.8`。
- Tutor（有 loading 态）：connect 1.5s / total 6.0s，`temperature=0.3`。
- 单一 total timeout 无法区分"连不上"和"生成慢" —— 分档记录到 `llm_calls`（`latency_ms`/`ttft_ms`/`fallback_reason`），用实测再校准（§12.2）。

---

## 12. 测试与验收

### 12.1 单测（mock LLM，含故障注入）

| 用例 | scenario |
|---|---|
| 流式 happy path（delta 按句 → commit → lexmatch 派生 wordIds 正确） | ok |
| 超时 → 降级 scripted_npc + `fallback_reason=timeout` | timeout |
| 连接错误重试一次仍败 → 降级 | connect_error |
| 校验失败（超长/非法字符）→ 降级，不截断 | too_long |
| 流中途 `length_truncated` → 降级 | truncated |
| `lexmatch` 单测（loaf/loaves、apple/apples、无匹配→[]） | —（纯函数） |
| `chunker` 单测（按句切分、短句照发、20 词强断、finalize 余量） | —（纯函数） |
| Tutor：缓存命中 0 LLM 0 TTS | ok |
| Tutor：scaffold 不含目标词 → 只读单词降级 | bad_word_id / missing_word |
| Tutor：invalid_json → 降级 | invalid_json |
| 打断：`audio.start` 触发 → pending 取消 → append `dialogue.turn.interrupted` | — |
| **过期丢弃**：打断后旧 LLM 响应晚到 → 无音频发出、无字幕更新 | — |

### 12.2 集成 + 回归 + 冒烟

| 层 | 内容 |
|---|---|
| 集成（mock asr/tts/llm） | WS 全回合：audio 进 → delta+commit+metadata 出 → 逐句 audio 出；中途打断 → 回合取消 + 事件追加；spurious `audio.start`（无帧）被忽略 |
| **无状态越权（写实断言）** | 测试前后对**所有表**做 count + checksum 快照，断言仅 `session_events` / `llm_calls` / `tutor_cache` 发生变化 —— 不做"看了一眼没写 mastery_states"的弱断言 |
| 回归 | 阶段 1 全部 Python/vitest 套件保持绿 |
| 手动冒烟 | `scripts/llm-smoke.py`（真 key）：**跑 20 次**记录 TTFT / 总延迟 / token 分布 → 存 `tests/fixtures/llm-golden/`，作为 timeout/max_tokens 的定值依据 + mock 模板来源（防 mock 与真实漂移） |

### 12.3 启动自检

`scripts/startup-selfcheck.py` 扩展：启动时一次**极短 completion 探活**（如 "hi"），失败 → 明确日志 + 落 mock + `fallback_reason=no_key/connect`，与阶段 1 ASR/TTS 预热同一诊断流程。

**验收标准**（spec 里程碑 2）：对话稳定（mock 可测）、可打断（真机 + mock 取消均可测）、**无状态越权**（写实断言）。真实模型门禁 `e2e_voice_ok` / `p95` 仍随阶段 1 既定决策推迟（需模型 + CUDA runtime）。

---

## 13. 范围外（本阶段明确不做）

- Silero VAD（浏览器 + 服务端）——仍 RMS 门限
- api↔asr-worker 流式 WS ——仍一次性 HTTP POST
- 中文语言模式 ——仅 en
- **gesture 产出与渲染 —— 整体推到阶段 3**（阶段 2 不产出、不渲染）
- 渐进提示（情境→首字母→揭晓+带读）
- Narrative Repair 自动修复 ——校验失败直接降级
- Scene Director / ScenePreloadManager / ScenePatch（阶段 3）
- 学习引擎 / 偶遇词 / 证据入账（阶段 4）

---

## 14. 交付物形态

- 新建：`apps/api/app/llm/{client,mock,lexmatch,chunker,proposals,npc_actor,tutor}.py`、`apps/api/app/llm_log.py`、`scripts/llm-smoke.py`、`tests/fixtures/llm-golden/`
- 修改：`apps/api/app/settings.py`、`apps/api/app/ws.py`、`apps/api/app/voice_round.py`、`apps/web/src/useVoiceRound.ts`、`apps/web/src/audio/rms-gate.ts`（ducking + barge-in hold）、`apps/web/src/audio/mic.ts`（autoGainControl）、`apps/web/src/CompanionPopover.tsx`（companion.ask/reply + metadata）
- 测试：`apps/api/tests/test_{stream,npc_actor,lexmatch,chunker,proposals,tutor,interrupt,stale_drop,llm_log,budget}.py` 等；前端对应 vitest 补 case
- 实现计划由 `writing-plans` 输出（`docs/superpowers/plans/2026-08-06-english-town-phase2.md`）
