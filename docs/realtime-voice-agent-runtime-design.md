# Realtime Voice Agent Runtime — 记忆、上下文与 Progressive Response 设计 V1.1-final

> 状态：**已拍板（V1.1-final）**  
> 范围：在现有 `web-demo`（Qwen Realtime）之上演进  
> 权威入口：本文档  
> 关联：[`app-design.md`](./app-design.md)（产品功能；记忆章已改为指向本文）、[`agentnexus-memory-integration-proposal.md`](./agentnexus-memory-integration-proposal.md)

---

## 0. 一句话定位

> **Browser 负责低延迟交互；Server 负责真实性、长期记忆、Context、工具与业务能力。**

> **Realtime Voice Agent Runtime** — 可嵌入 MES / ERP / CRM / 知识库 / AgentNexus 的实时会话交互层。

---

## 1. V1.1 拍板决议（冻结）

| # | 决议 |
|---|---|
| 1 | **第一期宿主**：独立 Voice Assistant；AgentNexus **本地 Mock**，预置测试用「事实」 |
| 2 | **V1 必须登录**（有 `user_id`；无匿名长期记忆） |
| 3 | **长期记忆用 LanceDB（或同类向量库）**；深度策略见 **§7**（混合：画像 + 会话蒸馏事实 + 来源索引；业务系统为唯一真相源） |
| 4 | **丢弃**现有 `localStorage` 记忆/历史，**不做迁移**；改 IndexedDB / Server |
| 5 | 过渡语暂时走 **Qwen Realtime 出声**；Phrase Library **30–50 组**，**分场景** |
| 6 | 类型 A（本地可完整答）→ **跳过** Retrieval/Refined；**用户质疑**时再 Retrieval/Refined，并回写长期记忆 + 本地 Working Memory |
| 7 | Phase 1 Provider：`MemoryProvider` + **`WebSearchProvider`（DuckDuckGo）** |
| 8 | 检索超时：**只播 Immediate、取消 Refined**（注明后续优化） |
| 9 | 记忆列表/编辑/删除 UI → **Phase 2**（已记） |
| 10 | 业务事实 citation：**分层 C** — Memory/Profile 可无 citation；WebSearch / MES（及同类业务 Provider）**必须**有 citation，否则只说未查到可靠来源 |
| 11 | 本文为权威入口；`app-design.md` **合并改写**记忆相关指向 |
| 12 | API 挂在现有 **`/api/...`**（不用 `/v1` 前缀） |
| 13 | **寒暄冷启动**：用户仅说「你好呀」等低信息问候时，根据 **User Profile + 近期兴趣/话题** 主动引导进入其可能感兴趣的话题（见 §5.1） |

---

## 2. 设计目标

| # | 目标 |
|---|---|
| 1 | 多层记忆（L0–L3）+ 蒸馏；不全量把聊天当长期记忆 |
| 2 | Browser 只调统一 Context API |
| 3 | 可查就不猜；Immediate 禁止编造 |
| 4 | Progressive Response |
| 5 | 连续自然会话 + Barge-in |
| 6 | 业务系统以 Provider 形式统一接入 |

---

## 3. 与现状的关系

| 现有 | 处理 |
|---|---|
| `memory.js` / `history.js` localStorage | **废弃不迁移**；由 Local Working Memory（IndexedDB）+ Server LanceDB 取代 |
| `memoryExtraction.js` / `/api/memory-extract` | Phase 1 可暂留；权威蒸馏迁到 Server `/api/memory/event` |
| `saveIntent.js` | 保留语义 → Memory Event |
| `handleUserTurn` + `session.update.instructions` | 保留；扩展为 Answerability + Progressive |
| `agentnexus_mock.py` | **加强**：登录用户 + 预置测试事实；作为 Memory 真相源之一的 Mock |
| Qwen Realtime `/ws` | 不变；过渡语也走此通道出声（Phase 1） |

---

## 4. 总体架构

```text
Browser Voice Runtime
  Local Working Memory (IndexedDB, per user_id)
  Turn Manager / Progressive Response
  Context Client → POST /api/context/query
        │
Server (web-demo/server.py 扩展)
  Auth (login required)
  Context Gateway + Planner
  MemoryService (LanceDB)
  Providers: Memory | WebSearch(DDG) | (later MES/ERP/…)
  AgentNexus Mock (seed facts)
```

---

## 5. Progressive Response

```text
FAST_EVALUATION
  ├ confidence ≥ 0.85 且本地可答 → 类型 A：直接 Realtime 回答，跳过 Retrieval/Refined
  ├ 0.50–0.85 → 类型 B：Partial（Realtime）+ 并发 Retrieval → Refined
  └ < 0.50 → 类型 C：Transition Phrase（Realtime 出声）+ Retrieval → Refined
```

**质疑路径（拍板 §6）：**

```text
类型 A 已回答
  → 用户质疑 / 纠正（“不对”“你再查查”“不是这个意思”）
  → 强制 Retrieval + Refined
  → 用权威结果更新：LanceDB 长期记忆 + Browser Working Memory
  → 必要时标记旧事实 conflict / superseded
```

同一 Turn：`turn_id` + `context_version` + `request_id`；打断或补充则 version++，旧结果 Discard。

超时（拍板 §8）：只播 Immediate，取消 Refined；文档标注 **后续优化项**（部分结果早到先播、降级 Provider 等）。

### 5.1 寒暄 / 低信息开场 → 画像引导（拍板 §13）

用户只说「你好呀」「在吗」「嗨」等 **无实质意图** 的开场时：

- **不要**只回「你好，有什么可以帮你？」就停住（冷、难进入任务）。
- **要**在问候后，用 **已知画像与兴趣** 给出 **一个** 可接话的引导（最多再跟一句可选话题），帮助快速进入用户关心的事。

#### 判定

```text
Turn 分类 = GREETING / SMALL_TALK_OPENING
条件：无实体、无明确任务动词、无业务问询；典型正则/短分类器即可（Phase 1）
```

此类 Turn **仍属类型 A**（本地可答，跳过 Retrieval/Refined），但走专用 **Greeting Guidance** 路径，而不是普通闲聊模板。

#### Context 来源（仅本地 / bootstrap，不查 WebSearch）

优先级从高到低：

1. `user_profile.interests` / `preferred_topics`（显式兴趣）  
2. `user_profile.role` / `workplace`（如「A 厂生产负责人」）  
3. `conversation_summary` / 最近 `episodic` 话题（「上次在聊 M102」）  
4. `hot_memory` 高频实体  
5. 若以上皆空 → 温和开放式引导（「今天想聊设备、进度，还是随便聊聊？」），**不编造**兴趣  

#### 话术约束

- 1–2 句内说完（符合 instructions 闲聊长度）。  
- **一次只抛 1 个主引导**（可选再带 1 个备选），避免清单念诵。  
- 引导必须能追溯到 Profile/Memory 中的已知点；禁止「我猜你喜欢…」式臆测。  
- 用户若直接换题 / 拒绝引导 → 立刻跟随，不纠缠。  

#### 示例

Profile：`role=产线主管`，`interests=["设备异常","排产"]`，近期 episodic：`M102`

> 用户：你好呀  
> Agent：你好。要不要先看看产线设备这边——比如上次说的 M102，还是今天的排产？

无画像时：

> 你好。今天想先聊工作上的事，还是随便聊聊？

#### 与 proactive_mode 的关系

- 默认 `proactive_mode=off` **不排斥**「问候引导」：问候引导是 **对 GREETING turn 的应答策略**，不是后台推送。  
- Phase 4 的 `contextual` / `full` 才做会话中途主动插话；与本节分离。  

#### Profile 字段（建议）

```json
{
  "language": "zh-CN",
  "timezone": "Asia/Shanghai",
  "response_style": "concise",
  "role": "产线主管",
  "interests": ["设备异常", "排产", "OEE"],
  "preferred_topics": ["产线A"],
  "greeting_style": "warm_brief"
}
```

AgentNexus Mock 种子用户应带上非空 `interests` / `role`，便于 Phase 1 验收「你好呀 → 画像引导」。

---

## 6. Immediate 真实性硬约束

只允许：

- 已知事实（Current Turn / Working Memory / 高置信缓存）
- 非事实反馈（过渡语库）

禁止：「我记得应该是…」「好像…」再去查。

---

## 7. 长期记忆深度设计（拍板 §3 的仔细答案）

### 7.1 原则

> **第三方业务系统（MES / ERP / AgentNexus / 文档库）是业务事实的唯一真相源。**  
> **Workforce LanceDB 不是第二套 MES，而是「会话智能层」的记忆与路由库。**

若把 MES 停机记录深拷进 LanceDB：

- 会与真相源漂移（改了 MES、库里还是旧的）
- 权限/合规难做
- 库无限膨胀

若只存「去哪查」的索引、完全不存会话事实：

- 跨会话「你上次说你负责 A 厂」会丢
- 每次闲聊也要打远端，延迟与成本差

### 7.2 推荐：混合三层写入策略（V1 冻结建议）

| 存什么 | 存哪 | 权威性 | 深度 |
|---|---|---|---|
| **User Profile** | LanceDB `profile` | Workforce 自有（用户偏好/语言/风格） | 浅、可改 |
| **Semantic facts（用户断言 / 蒸馏事实）** | LanceDB `semantic` | 会话侧权威；带 `provenance` | 中：短文本事实 + embedding |
| **Episodic summary** | LanceDB `episodic` | 会话摘要，非业务流水账 | 浅：摘要 + topic + 时间窗 |
| **Source Index / Routing** | LanceDB `source_index` | **指针**，指向 3rd 系统 | 浅：entity → provider + query hint |
| **Hot cache of retrieved context** | LanceDB 或内存 TTL | **非权威副本**；必带 `citation` + `fetched_at` + TTL | 极浅：可丢可刷 |
| **MES/ERP 原始事件流水** | **不进**长期深存 | 永远回源查询 | — |

### 7.3 Source Index 长什么样

```json
{
  "entity_id": "machine:M102",
  "aliases": ["M102", "产线A那台"],
  "providers": [
    {
      "provider": "mes",
      "resource": "machine_event_history",
      "hint": "machine_id=M102"
    },
    {
      "provider": "memory",
      "resource": "semantic",
      "hint": "belongs_to=line_A"
    }
  ],
  "last_verified_at": null
}
```

回答「昨天 M102 为什么停机？」时：

1. Working Memory / semantic：可能知道「M102∈产线A」（会话事实）  
2. source_index：路由到 MES Provider  
3. **停机原因以 MES 实时结果为准**，不读 LanceDB 里过期的「昨天停机原因」深拷贝  

### 7.4 AgentNexus Mock（Phase 1）

- 作为 **个人/协作记忆真相源的 Mock**（不是 MES）  
- 预置测试事实（身份、偏好、若干 PROGRESS/ANCHOR）  
- LanceDB 可镜像一份 **带 `source=agentnexus` + sourceId** 的缓存；拉取以 Mock API 为准做 reconcile  

### 7.5 质疑后的回写

用户质疑类型 A 答案后：

1. 强制 `/api/context/query`（提高 provider 覆盖 / 忽略过期 cache）  
2. Refined 播报  
3. 更新 semantic（纠正事实）+ 刷新 hot cache + 更新 source_index 的 `last_verified_at`  
4. 旧条目 `status=superseded` 或冲突标记，避免下次仍答错  

### 7.6 LanceDB 集合建议（V1）

```text
users          — 登录用户元数据
profiles       — user_id → profile JSON
semantic       — embedding + text + provenance + user_id + ttl/status
episodic       — session summaries
source_index   — entity routing
hot_cache      — optional TTL cache of provider payloads (citation required)
```

---

## 8. Local Working Memory（Browser）

IndexedDB，按 `user_id` 隔离；**无缓存不阻塞开麦**；后台 `/api/session/bootstrap` 刷新。

```json
{
  "user_profile": {},
  "conversation_summary": {},
  "recent_turns": [],
  "hot_memory": [],
  "active_entities": [],
  "active_tasks": []
}
```

启动：有本地 → 立即可用 → 后台 bootstrap；无本地 → bootstrap（轻量 1–10KB）后再热。

---

## 9. Context API（挂在 `/api/...`）

```http
POST /api/session/bootstrap
POST /api/context/query
POST /api/memory/event
POST /api/memory/distill
POST /api/tool/execute
POST /api/session/end
```

另保留：`/ws`、`/api/config`、`/api/dictation-cleanup`；登录相关待定（如 `/api/auth/login`）。

### `/api/context/query`（示意）

```json
{
  "session_id": "S1001",
  "turn_id": "T102",
  "context_version": 3,
  "query": "昨天M102为什么停机？",
  "context": { "active_topic": "M102" },
  "options": { "latency_budget_ms": 1500, "max_results": 10 }
}
```

Phase 1 Planner：

- 个人/偏好/「我之前说…」→ `MemoryProvider`（LanceDB + AgentNexus Mock）  
- 时效性外部事实（天气/新闻/公开网页）→ `WebSearchProvider`（DuckDuckGo）  
- 两者可并行  

统一 Result 含 `citation` / `confidence` / `freshness` / `source`。

---

## 10. 过渡语库（拍板 §5）

- **30–50 组**，分场景，例如：  
  - `retrieve_generic`：我查一下 / 嗯～我确认一下  
  - `retrieve_record`：我看一下记录  
  - `retrieve_search`：我搜一下最新的公开信息  
  - `retrieve_memory`：我翻一下之前的笔记  
  - `partial_bridge`：我记得…先确认一下最新数据  
- Phase 1：**经 Qwen Realtime 出声**（系统把选中的短语作为本轮要说的内容驱动模型/或注入 instructions 强制短说——实现时选一种，避免模型自由发挥掺事实）  
- 实现约束：过渡语 **不得含业务事实**；只做 latency masking  
- 同一 voice 一致性；Audio Cache 可 Phase 1.5  

触发：预计 Retrieval > ~1200ms 才播；类型 A 不播。

---

## 11. `instructions` 回答策略（冻结文案）

```text
你是一个语音助手。回答长度根据问题自适应：
- 闲聊/确认/指令类（"好"/"再见"/"打开灯"）：1 句话内
- 普通问询（问"几点"/问"天气"等）：1-2 句
- 解释/分析类（"为什么"/"怎么理解"）：可以详细说，但避免无意义重复
- 一般不要客套（如"好问题！"）；仅当问题的确深入时，可适当客套一句并简要说明原因
- 不要列表/标题/emoji，纯口语

寒暄开场（用户只说「你好」「在吗」等、没有具体问题）：
- 先简短回礼，再根据当前提供的用户画像/兴趣/近期话题，用一句话自然引导到对方可能关心的事
- 一次只提一个主话题（最多再带一个备选）；不要念清单
- 只能使用已提供的画像与记忆，不要臆测用户兴趣；若没有画像，用一句开放式询问即可

真实性：
- 只使用当前提供的背景/Context；没有依据时不要编造
- 需要外部事实且尚未提供 Context 时，不要猜测
```

（可与现有编号念法、口语规则合并进 `BASE_INSTRUCTIONS`。）

---

## 12. 登录（拍板 §2）

- V1 **必须登录** 才能开语音会话 / 读写记忆  
- `user_id` 贯穿：bootstrap、LanceDB 分区、Working Memory、AgentNexus Mock  
- Phase 1 可用简单本地账号表或固定测试用户 + session cookie/token（实现细节 Phase 1 Todo 再定）

---

## 13. Phase 划分（更新后）

### Phase 1（体验 + 最小记忆闭环）

- 登录  
- AgentNexus Mock + 种子事实  
- LanceDB：profile + semantic + source_index（可先简）  
- IndexedDB Working Memory（丢弃旧 localStorage）  
- `/api/session/bootstrap`、`/api/context/query`  
- Providers：Memory + WebSearch(DDG)  
- Answerability + 类型 A 跳过 Refined；质疑强制重查  
- **GREETING 冷启动**：低信息问候 → Profile/兴趣引导（§5.1）；Mock 种子用户带 interests  
- Transition Phrase 30–50 组分场景；Realtime 出声  
- 超时：Immediate only  
- **Citation 分层 C**：Memory/Profile 可无 citation；WebSearch（及日后 MES）结果无 citation 则不得当业务事实播报  
- 更新 `BASE_INSTRUCTIONS`  

### Phase 2

- 记忆列表 / 编辑 / 删除 UI  
- Memory Distillation 服务端化  
- Episodic 完善、Audio Cache、超时策略优化  

### Phase 3

- Document / MES / ERP Providers、Permission  
- 延续分层 C：MES/ERP 等业务 Provider **必须** citation  

### Phase 4

- Proactive Agent  

---

## 14. Citation 策略（拍板 C，冻结）

| Context 来源 | 播报业务断言 | 无 citation 时 |
|---|---|---|
| Memory / Profile / 会话 Working Memory | 允许 | 可播（仍禁止编造；仅限已提供内容） |
| WebSearch（DuckDuckGo） | **必须**有 citation | 只说未查到可靠来源 / 不编造网页事实 |
| MES / ERP / 文档库等业务 Provider | **必须**有 citation | 同上 |

Refined Response 的 `instructions` 中应标明每条 Context 的 `source` + `citation`；模型不得把无 citation 的 WebSearch/MES 片段当作已证实事实说出。

---

## 15. V1 原则（摘要）

1. Local-first latency  
2. Server-grounded truth；**3rd 系统业务事实不深拷**  
3. Never guess when retrieval is possible  
4. Context 是客户端核心抽象  
5. Retrieval ≠ Action  
6. Progressive Response  
7. Transition 不掺事实  
8. 类型 A 可跳过 Refined；质疑则重查并回写  
9. API 用 `/api/...`  
10. Voice 一致性  
11. **Citation 分层 C**（Memory/Profile 可无；WebSearch/MES 必须有）  
12. **寒暄用画像引导进题**，不臆测兴趣；无画像则开放式一句询问  

---

*V1.1-final · 用户拍板完整 · 2026-09-14*
