# Phase 1 开发 TodoList

> 对照：[`realtime-voice-agent-runtime-design.md`](./realtime-voice-agent-runtime-design.md) V1.1-final（拍板冻结）  
> 范围：`web-demo` 体验 + 最小记忆闭环  
> 规则：一步一验收；单步避免上下文过大；完成后勾选并记下验收方式。  
> 不重复造轮子：`roadmap-todo.md` 里已完成的 source/upsert/提炼/裁剪等逻辑，Phase 1 只迁存储与接 Runtime，不重写语义。

---

## 里程碑总览

| 里程碑 | 目标 | 状态 |
|---|---|---|
| M0 | 文档冻结清单 → 本 Todo | ✅ |
| M1 | 登录 + Auth 中间件 | ✅ |
| M2 | AgentNexus Mock 种子加强 | ✅ |
| M3 | LanceDB MemoryService | ✅ |
| M4 | Context API + Providers | ✅ |
| M5 | IndexedDB Working Memory + 前端 Context Client | ✅ |
| M6 | Turn Manager（Answerability / Progressive / GREETING / 质疑） | ✅ |
| M7 | Transition Phrase + BASE_INSTRUCTIONS + 验收 | ✅ |

---

## 冻结项对照（不得偏离）

| # | 决议 | 本 Todo 覆盖 |
|---|---|---|
| 1 | AgentNexus 本地 Mock + 预置事实 | §2 ✅ |
| 2 | V1 必须登录 | §1 ✅ |
| 3 | LanceDB：profile + semantic + source_index；不深拷业务流水 | §3 ✅ |
| 4 | 丢弃 localStorage 记忆/历史，不迁移；改 IndexedDB | §5 ✅ |
| 5 | 过渡语 30–50 组，分场景；Qwen Realtime 出声 | §7 ✅（36 组） |
| 6 | 类型 A 跳过 Retrieval；质疑强制重查并回写 | §6 ✅ |
| 7 | MemoryProvider + WebSearchProvider(DDG) | §4 ✅ |
| 8 | 检索超时：只播 Immediate、取消 Refined | §6 ✅ |
| 10 | Citation 分层 C | §4 / §6 ✅ |
| 12 | API 挂 `/api/...` | §1 / §4 ✅ |
| 13 | GREETING → 画像 interests 引导 | §6 ✅ |

Phase 2+（记忆编辑 UI、Episodic 完善、Audio Cache、超时策略优化、MES 等）**不做**。

---

## §0 准备工作

- [x] 0.1 阅读 Runtime 设计 V1.1-final、roadmap 已完成项、现有 `server.py` / `app.js` / mock
- [x] 0.2 编写本 Todo（本文）
- [x] 0.3 实现后做 `py_compile` + API smoke

---

## §1 登录（Auth）

- [x] 1.1 本地账号表：`demo` / `demo123`，带 `role`/`interests`
- [x] 1.2 `POST /api/auth/login` → session token（cookie + Bearer）
- [x] 1.3 `POST /api/auth/logout`、`GET /api/auth/me`
- [x] 1.4 保护 bootstrap / context/query / memory/event
- [x] 1.5 前端登录门禁

**验收**：未登录 bootstrap → 401；登录后 interests 非空。✅ smoke 通过

---

## §2 AgentNexus Mock 加强

- [x] 2.1 种子画像 role/interests/preferred_topics
- [x] 2.2 预置 ANCHOR/PROGRESS/DECISIONS（含 M102）
- [x] 2.3 `GET .../profile` + `get_seed_memory()` 供 Gateway 镜像

---

## §3 LanceDB 长期记忆

- [x] 3.1 `lancedb` + 简易 hash embedding
- [x] 3.2 profiles / semantic / source_index
- [x] 3.3 MemoryService 按 user_id
- [x] 3.4 启动镜像 Mock（source=agentnexus）
- [x] 3.5 不深拷 MES 流水（仅 source_index 指针）

---

## §4 Context API + Providers

- [x] 4.1 `POST /api/session/bootstrap`
- [x] 4.2 `POST /api/context/query` + Planner
- [x] 4.3 MemoryProvider
- [x] 4.4 WebSearchProvider（ddgs）
- [x] 4.5 统一 Result 字段
- [x] 4.6 Citation 分层 C
- [x] 4.7 `POST /api/memory/event`
- [x] 4.8 超时 `timed_out` 标记

**验收**：API smoke 2026-09-14/15：login → bootstrap → memory query → websearch+citation ✅

---

## §5 IndexedDB Local Working Memory

- [x] 5.1 `workingMemory.js`
- [x] 5.2 丢弃旧 localStorage 记忆键，不迁移
- [x] 5.3 `history.js` → IndexedDB recent_turns
- [x] 5.4 `memory.js` 薄适配 hot_memory
- [x] 5.5 登录后本地可用 + 后台 bootstrap

---

## §6 Turn Manager

- [x] 6.1–6.3 类型 A/B/C
- [x] 6.4 GREETING 画像引导
- [x] 6.5 质疑强制 Retrieval + 回写
- [x] 6.6 超时 Immediate only（注释后续优化）
- [x] 6.7 turn_id / context_version / invalidate
- [x] 6.8 接入 `handleUserTurn`

---

## §7 Transition Phrase + Instructions

- [x] 7.1 36 组分场景（`transition_phrases.py` + `phrases.js`）
- [x] 7.2 Realtime 出声（instructions 强制短说）
- [x] 7.3 `BASE_INSTRUCTIONS` 已按设计 §11 更新

---

## §8 集成与验收

- [x] 8.1 requirements 含理由注释
- [x] 8.2 index.html 登录 UI + 新脚本
- [x] 8.3 py_compile 通过
- [x] 8.4 API smoke 通过
- [x] 8.5 本地启动说明如下

### 本地启动

```bash
cd web-demo
python3.14 -m venv .venv          # 或本机可用的 3.13+/3.14
source .venv/bin/activate
pip install -r requirements.txt
PRODUCTION=0 python -u server.py  # 默认 127.0.0.1:8765；勿设 PRODUCTION=1 否则无 Mock
```

浏览器打开 `http://127.0.0.1:8765/`：

1. 登录 `demo` / `demo123`
2. 打字「你好呀」→ 应按产线主管 / 设备异常·排产 引导
3. 问「我负责什么 / M102」→ Memory 命中
4. 问「今天有什么科技新闻」→ WebSearch + citation

### 端到端验收清单

| 项 | 结果 |
|---|---|
| 登录门禁 | ✅ API + UI |
| 你好呀画像引导 | ✅ 前端 TurnManager（需连 Realtime 听感验） |
| memory query | ✅ smoke |
| websearch citation | ✅ smoke（5 条均有 URL） |
| memory/event | ✅ smoke |

---

## 未完成 / 风险（诚实记录）

1. **超时路径**：服务端 `timed_out` 时前端取消 Refined 已实现；「部分结果早到先播」标为后续优化，未做。
2. **Refined 触发时机**：用固定 ~1.2s 延迟近似等 Immediate，未严格等 `response.done`（避免深耦合）；真机可能偶发叠音，可再收紧。
3. **LanceDB embedding**：Phase 1 hash embedding，非语义模型；检索质量有限。
4. **本机已有进程占 8765 且 PRODUCTION=1**：验收时需 `PRODUCTION=0` 或换 PORT，否则 Mock 关闭。
5. **Python**：环境无 3.13 时用了 3.14 建 venv；与习惯 3.13.2 略有偏差。
6. **语音端到端**：本环境未接真麦 / 未跑 Playwright 听感；寒暄引导逻辑已在 TurnManager，需用户真机确认。

---

## 进度记录

| 日期 | 进度 | 备注 |
|---|---|---|
| 2026-09-14 | 创建本文；实现 Phase 1 | V1.1-final；API smoke 通过 |

---

*Phase 1 Todo · 与 Runtime 设计同步*
