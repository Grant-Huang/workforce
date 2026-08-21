# 语音对话网页 Demo

跟 `VoiceChat/` 那个 iOS App 用的是同一套 Realtime API 协议（`session.update` / `input_audio_buffer.append` / `response.audio.delta` / …），做成网页版是因为 iOS App 没法在这个开发环境里编译运行，这个 demo 可以直接在浏览器里跑起来实测——记忆检索、AgentNexus 同步这些设计都是先在这里验证过、再同步改回 iOS 项目的。

界面：类似 ChatGPT 主对话框——顶部状态栏、中间滚动的对话气泡、底部一个可以打字的输入框 + 一个开始/结束合一的大圆按钮（不是两个按钮，点一下开始，再点一下结束，图标和颜色跟着状态变）。打字发送消息会自动开始会话，不用先点麦克风。

## 本地记忆 + AgentNexus Mock

- `static/memory.js`：本地记忆（`localStorage`），跟 `VoiceChat/Memory/MemoryStore.swift` 同一套逻辑——关键词重合度 + 时间新鲜度打分，没有语义检索。
- `static/agentnexus.js` + `agentnexus_mock.py`：**Mock 出智枢按 `docs/agentnexus-memory-integration-proposal.md` 改完之后的样子**——长期 Token 直接能用（mock 里任何非空 Bearer token 都算通过）、标准的频道记忆/消息 REST API。默认指向本地的 `/agentnexus-mock/*`（内置了两条示例记忆种子数据），生产环境要换成真实智枢地址时只需要改 `agentnexus.js` 里的 `config`。
- 每次开始对话，先从 AgentNexus Mock 拉一次记忆合并进本地缓存；对话过程中每一轮（不管是打字还是说话）都用本地记忆检索，命中的话会喂给模型。

## 一个关键的实测发现：怎么把记忆喂给模型是有讲究的

最早的设计（跟 iOS 项目一开始的实现一样）是用 `conversation.item.create` 插入一条 `role: "system"` 的消息来传递检索到的记忆。**实测发现 Qwen 会完全无视这条消息**——不管放在用户提问之前还是之后，也试过伪装成一条 `role: "assistant"` 的历史消息，模型都没有用上这些信息，只会给一个不痛不痒的通用回复。

唯一实测有效的方式是：**把记忆内容拼进 `session.update` 的 `instructions` 字段**（也就是系统提示词本身），每一轮对话根据检索结果动态патch这个字段，再触发 `response.create`。另外还发现：如果连续发两次 `session.update` 而不等第一次的 `session.updated` 确认回包，会导致后面的回复整个失败（空回复）——所以每次 patch instructions 之后必须等 `session.updated` 回来了，才能继续发 `response.create`。

这个问题在 iOS 项目里也一样存在，已经用相同方式修复了（`RealtimeClient.updateInstructions` + 等 ack 之后再 `requestResponse`）。四种写法的完整实测对比在下面"记忆注入方式实测对比"里。

## 为什么需要本地中转服务

浏览器原生 `WebSocket` API 不能自定义请求头，没法直接带 `Authorization: Bearer <key>` 连 DashScope。所以 `server.py` 起一个本地服务：网页连 `ws://127.0.0.1:8765/ws`（不需要认证），`server.py` 再拿着 `.env` 里的 Key 去连真正的 DashScope 地址，两边转发消息。**Key 全程只在这个 Python 进程里，不会出现在浏览器/前端代码里。** 同一个服务也顺带 serve 了 AgentNexus Mock（`/agentnexus-mock/*`）。

## 运行

```bash
cd web-demo
pip install -r requirements.txt
python3 server.py
```

然后浏览器打开 `http://127.0.0.1:8765/`，点圆按钮开始对话（或者直接在输入框打字），允许麦克风权限，说话试试。可以问"我今天有什么安排？"之类的问题，验证一下 Mock 里预置的日程记忆能不能被正确检索、说出来。

Key 从仓库根目录的 `.env` 读取（`QWEN_API_KEY=...`），这个文件不会被提交到 Git（已加进 `.gitignore`）。

## 已经跑通的连通性测试（2026-08-19）

用这里的 `.env` 里的 Key，直接拿 Python `websockets` 库测了几个候选地址/型号（脚本类似 `server.py` 里连上游的那部分），结果：

| 地址 | 模型 | 结果 |
|---|---|---|
| `wss://dashscope.aliyuncs.com/api-ws/v1/realtime` | `qwen-audio-3.0-realtime-plus` | ✅ 连通，`session.created` 正常返回，但返回的 `turn_detection` 里没有 `create_response`/`interrupt_response` 字段 |
| `wss://dashscope.aliyuncs.com/api-ws/v1/realtime` | `qwen-omni-turbo-realtime` | ✅ 连通，`session.created` 返回的 `turn_detection` 带 `create_response: true` 和 `interrupt_response: true` |
| `wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime` | `qwen-omni-turbo-realtime` | ❌ HTTP 401（这个 Key 对国际站点无效，大概率是账号/区域问题，不是 Key 本身坏了） |

所以 iOS App 和这个网页 demo 现在都默认用 `wss://dashscope.aliyuncs.com/api-ws/v1/realtime` + `qwen-omni-turbo-realtime`——之前从文档里查到的"按工作空间分域名"的地址（`{WorkspaceId}.cn-beijing.maas.aliyuncs.com`）**没有用上**，实测这个通用地址就直接能连，不需要工作空间 ID。

请求的音色 `Cherry` 没被接受，服务端实际用的是 `Chelsie`，已经改成默认值。

## 记忆注入方式实测对比（2026-08-21）

问题："我今天有什么安排？"，Mock 记忆库里有一条日程（下午 3 点开会、晚上 7 点健身）。用 Python `websockets` 直连测试四种写法：

| 写法 | 结果 |
|---|---|
| A：先发用户提问，再发 `role:"system"` 的记忆内容 | ❌ 模型完全无视，回复"有什么事儿吗？" |
| B：先发 `role:"system"` 的记忆内容，再发用户提问 | ❌ 同上，顺序颠倒也没用 |
| C：记忆内容直接写进 `session.update` 的 `instructions` | ✅ 模型正确说出了会议和健身安排 |
| D：把记忆伪装成一条 `role:"assistant"` 的历史消息 | ❌ 同样被无视 |

结论就是上面"一个关键的实测发现"那段——只有 C 这种写法有效。另外还验证了：把 patch instructions 和后续操作之间不等 `session.updated` 确认，会导致空回复（连续两次 `session.update` 之间有竞争）；等了确认之后，同样的内容能稳定复现正确回复。

**没验证到的部分**：这个开发环境没有真实麦克风，用 Playwright + Chromium 的"伪造音频设备"（一段合成的静音/音调）跑通了整条链路——WebSocket 握手、`session.update` 被接受、音频分片能正常发送、连接能正常关闭——但因为没有真实语音内容，没能触发 VAD 识别出"你说完了"，所以语音路径下的记忆检索/打断体验没能像文字路径那样端到端验证。文字对话路径（打字提问）已经完整验证过，包括记忆检索 + 正确回复。这些需要你在自己电脑上用真麦克风实测。

## 文件说明

- `server.py` — 中转服务（aiohttp）：serve 静态文件 + `/api/config` + `/ws`（转发到 DashScope）+ 挂载 AgentNexus Mock 路由。
- `agentnexus_mock.py` — Mock 出智枢按提案改完之后的记忆/消息 REST API，纯内存存储，重启会重置回种子数据。
- `index.html` / `static/styles.css` — 页面结构和样式（开始/结束合一按钮、文字输入框）。
- `static/app.js` — 核心逻辑：麦克风采集（`ScriptProcessorNode`，降采样到 16kHz PCM16）、流式播放（24kHz PCM16 顺序调度播放）、WebSocket 事件收发、`session.update` instructions patch 记忆注入、气泡渲染。
- `static/memory.js` — 本地记忆存储 + 检索。
- `static/agentnexus.js` — 拉取/推送 AgentNexus（Mock）记忆的桥接层。

## 已知限制

- `ScriptProcessorNode` 已经是浏览器标记为 deprecated 的 API（但仍被广泛支持），更现代的写法是 `AudioWorkletNode`，demo 图简单没换。
- 本地记忆检索还是关键词打分，没有语义理解（跟 iOS 项目的已知限制一样）。
- `agentnexus_mock.py` 是纯内存存储，没有持久化，也没有完整模拟"定期整理"这类后台任务（提案里的建议二）。
- 没有断线重连、没有错误重试。
