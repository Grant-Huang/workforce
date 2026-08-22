# 语音对话网页 Demo

跟 `VoiceChat/` 那个 iOS App 用的是同一套 Realtime API 协议（`session.update` / `input_audio_buffer.append` / `response.audio.delta` / …），做成网页版是因为 iOS App 没法在这个开发环境里编译运行，这个 demo 可以直接在浏览器里跑起来实测——记忆检索、AgentNexus 同步这些设计都是先在这里验证过、再同步改回 iOS 项目的。

界面：类似 ChatGPT 主对话框——顶部状态栏、中间滚动的对话气泡、底部一个可以打字的输入框 + 一个开始/结束合一的大圆按钮（不是两个按钮，点一下开始，再点一下结束，图标和颜色跟着状态变）。打字发送消息会自动开始会话，不用先点麦克风。

## 本地记忆 + AgentNexus Mock

- `static/memory.js`：本地记忆（`localStorage`），跟 `VoiceChat/Memory/MemoryStore.swift` 同一套逻辑——关键词重合度 + 时间新鲜度打分，没有语义检索。
- `static/agentnexus.js` + `agentnexus_mock.py`：**Mock 出智枢按 `docs/agentnexus-memory-integration-proposal.md` 改完之后的样子**——长期 Token 直接能用（mock 里任何非空 Bearer token 都算通过）、标准的频道记忆/消息 REST API。默认指向本地的 `/agentnexus-mock/*`（内置了三条示例记忆种子数据），生产环境要换成真实智枢地址时只需要改 `agentnexus.js` 里的 `config`。
- 每次开始对话，先从 AgentNexus Mock 拉一次记忆合并进本地缓存；对话过程中每一轮（不管是打字还是说话）都用本地记忆检索，命中的话会喂给模型；原始对话内容顺带推给 AgentNexus 当消息记录。
- `static/saveIntent.js`：识别"记住""帮我记一下""提醒我"这类明确意图，命中的话**不**走普通的检索问答流程，而是把内容写进 AgentNexus 的结构化记忆层（`PROGRESS`，通过 `agentnexus.js` 的 `createMemoryEntry`），模型只需要简短确认，不用检索/复述。跟"每轮对话都当消息推送"是两条不同的路径，对应提案文档里"原始对话 vs 精选记忆"的分工。

## 提示词（2026-08-22 修订）

`BASE_INSTRUCTIONS`（`app.js`）/ `systemInstructions`（`VoiceChat/ConversationViewModel.swift`）两边同步改了一版：

- **不再限制"1-3 句话"**：改成按问题类型决定长度——闲聊/简单问题几句说完；工作纪要、待办清单、技术问题可以说得详细，但要按口语习惯组织（"主要有这么几件事，第一……第二……"），不是书面分点腔调，内容特别多就先说整体再问要不要展开。
- **背景信息缺失时的过渡语**：不是额外传"本地记忆没命中"这个信号给模型，而是让模型自己根据背景信息里到底有没有相关内容来判断——命中了就有内容可用，没命中背景信息就是空的，模型看到没相关信息、又是需要具体记录的问题时，会按指令诚实说"这个我目前没有相关记录"而不是编。这条纯粹是提示词层面的改动，不依赖 Function Calling 能不能用。

这版还没来得及实测（改完的时候 API Key 正好在限流窗口里，见下面那条），等限流过了要重新验证一遍语气/长度是不是符合预期。

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

## 一个还没查清楚的可靠性问题：连接多了之后会"哑掉"

**现象**：`session.update`（初始配置）之后能正常收到 `session.created`，但后续再发的 `session.update`（记忆 patch）和 `response.create` 完全没有回应——不报错，就是安静下来，之前一直能用的流程突然什么都不返回了。

**已经排除的可能性**：
- 不是记忆注入的写法问题——用同样的 4 条消息（`session.update` → `conversation.item.create` → `session.update` → `response.create`）连续 burst 发送，直连 Qwen（不经过 `server.py`）连续测 5 次全部成功；经过 `server.py` 中转、在刚重启、状态干净的服务器上测也是 5 次全部成功。
- 不是 `server.py` 中转逻辑的 bug——上面这次干净测试证明转发逻辑本身没问题。

**观察到的规律**：这个问题只在**同一个 Key 短时间内已经建立过很多次连接之后**才会出现——今天这一整个 session 里，为了验证各种细节，用同一个 Key 建立过几十次 WebSocket 连接。重启中转服务、或者等一段时间之后重试，又能恢复正常。

**最可能的原因**：Qwen 那边对短时间内大量重复连接做了限流/风控，连接本身被接受（所以 `session.created` 能收到），但后续交互被静默拦截。**2026-08-22 这次拿到了直接证据**：同样的重连测多了之后，有一次服务端明确返回了 `1011 (internal error) Too many requests. Your requests are being throttled due to system capacity limits. Please try again later.`——不是猜的了，就是限流。之前"哑掉不报错"的情况，大概率是限流的另一种表现（有时候直接报错拒绝，有时候安静不回应），具体看服务端当时怎么处理。

**这意味着什么**：
- 这不是这次改动引入的新问题，也不是"记忆注入"这个功能本身的缺陷——是这个开发环境里高强度反复测试带来的副作用。
- 你自己单独使用时（不会在几分钟内建立几十次连接）大概率不会碰到。
- 如果真的遇到"连上了但一直没反应"，可以先怀疑是不是短时间内重连太多次，等一会儿或者用另一个 Key 试试。
- `app.js` 里已经加了超时兜底（`sendSessionUpdateAndWait` 4 秒没收到 `session.updated` 就放弃等待、继续往下走，不会卡死整个对话），所以就算真的遇到这个问题，App 也不会永久卡住，只是那一轮的记忆可能没用上最新内容。

## 多轮日程问答实测（2026-08-22）

限流窗口过后，跑了一次完整的多轮测试：在本地记忆里 mock 了 4 条内容（周二产品评审会、周四财务预算、下周一交季度报告、用户身份偏好），用跟 `MemoryStore.swift`/`memory.js` 完全一致的关键词检索逻辑，在**同一个 WebSocket 连接**上连续问了 5 个问题（脚本：`test_schedule_mock.py`）。原始输出：

| # | 提问 | 本地检索命中 | 模型回答 |
|---|---|---|---|
| 1 | 我周二有什么安排？ | 3 条（含周二会议） | "嗯，那你今天打算怎么安排呢？有没有什么紧急的事情要做呀。"——**没有用上检索到的内容，反而把问题抛回给用户** |
| 2 | 周四要准备什么？ | 3 条（含周四会议） | "哎呀，今天嘛，首先得把周四上午10点的财务预算会议准备一下……"——**正确**，完整带出了周四会议、季度报告、周二评审会三条内容 |
| 3 | 我今天有什么安排？ | 0 条（记忆里没有"今天"这个词，检索不到任何内容） | "听起来你今天的工作安排挺满的呢。那你觉得这些任务都能顺利完成吗……"——**编造了内容**，没有按提示词要求诚实说"没有相关记录"，反而凭空说"安排挺满" |
| 4 | 帮我看看下周一有什么事？ | 3 条（含下周一报告） | "我觉得这些任务应该都能顺利完成吧。不过也得小心点……"——**没有回答问题本身**，答非所问 |
| 5 | 我下个月的年假是什么时候？ | 4 条（误召回，实际无关） | "嗯，我也觉得应该没问题。你那边呢？感觉压力大不大？"——**完全答非所问**，像是在接着上一轮某个不存在的话题聊 |

**结论：只有第 2 轮是对的，其余 4 轮全部出问题**——要么无视检索到的内容、要么在没有检索到内容时编造答案（明确违反提示词里"诚实说明没有记录"的要求）、要么完全答非所问。而且从第 3 轮开始，回答的语气越来越像是在延续一个"最近工作压力大/进展顺利"的假想话题，跟实际提问脱节。

**初步排查**：为了判断是不是"同一连接上连续做多次 `session.update` patch 导致上下文错乱"，补了一个对照测试——只问第 1 个问题、但用全新连接、不带任何历史。这个对照测试还没跑完就被限流打断了（`1011 Too many requests`），说明**这次限流窗口非常窄**——满打满算只够跑一次 5 轮的完整测试加一次基线连通性检查，多一次单独请求就又被限流。所以目前还没能定位到底是"连接内历史累积导致模型跑偏"，还是"该模型在这类多轮场景下本身就不稳定"，需要下次限流解除后优先补跑这个对照实验。

**这对架构的影响**：之前（8-21）验证过的"instructions patch 能让模型正确说出记忆内容"这个结论本身没有错（第 2 轮就是证据），但这次测试说明**单次验证成功不代表多轮场景稳定**——真实使用中一次对话往往有很多轮，如果模型在第 2、3 轮之后就开始不看检索内容、甚至编造答案，这是一个比"要不要支持 Function Calling"更优先、更基础的问题。在查清楚根因之前，不建议直接按这次的多轮结果去改设计（比如去改"每轮都要不要重发完整历史"），先把对照实验补上再说。

## Function Calling 支持情况

限流窗口内没能补跑到 `test_function_calling.py`（`tools`/`session.tools` 声明 + 会触发工具调用的提问），所以 Qwen Realtime API 是否支持 Function Calling **仍未验证**，Route A / Route B 的选择还悬而未决。跟上面的多轮问答问题一起，是下一次限流解除后要优先补的两个实验。

## 连接生命周期设计（2026-08-22）

App 请求语音连接时的三条规则，以及 `static/app.js` 里的落地方式：

**(a) 建立不成要告诉客户，不能卡在"连接中…"。** 三种"建立不成"都要覆盖：
- Socket 本身没建立起来（`ws.onerror`）——直接提示"WebSocket 出错"，回到未连接状态。
- Socket 建立了，但中转服务连不上 Qwen 上游（`server.py` 发 `relay.error` 后关闭连接）——把具体错误原因带到 UI 上，而不是被后续 `stop()` 默认的"未连接"文案覆盖掉。
- **最隐蔽的一种**：socket 建立了，中转也连上了，但 Qwen 那边"哑掉"、`session.created` 一直不来（README 上面记录过的已知问题）——加了一个 8 秒的 `connectTimeoutId`，超时还没进入 LISTENING 状态就主动放弃并提示"连接超时，请重试"，而不是无限挂在"连接中…"。

**(b) 建立成功后不主动释放。** 现有代码里只有两处会关闭连接：用户按停止按钮（`stop()`），以及 (a)/(c) 描述的这几种"确实建立不起来/该收了"的情况。没有任何计时器或逻辑会在对话正常进行时主动掐断——这条本来就是满足的，这次改动没有引入新的主动释放路径。

**(c) 长时间没说话可以主动释放。** 加了一个 5 分钟的空闲计时器（`IDLE_TIMEOUT_MS`），只在 LISTENING 状态（已连接、正等用户说话）下计时——助手正在说话或者还在连接中都不计入"用户没说话"。每次回到 LISTENING（新连接刚建立、语音识别到用户开始说话、或者一轮回复讲完）都会重新起算。计时器到点会带着"长时间没有说话，已自动挂断"的提示自动挂断，而不是无声无息地断开。

这几条逻辑用 Playwright 直接调用页面暴露的全局函数（`setState`/`stop`/`armIdleTimer` 等）做了结构性验证——没有真的走 Qwen 连接，所以没有消耗限流配额。

## 限流应对（结合官方文档，2026-08-22）

查了百炼官方限流文档（`help.aliyun.com/zh/model-studio/rate-limit` + `error-code`），几个关键点：

- 限流不是单一维度：有 RPM/TPM 配额（`Throttling.RateQuota`/`AllocationQuota`），还有单独的**突增保护**（`Throttling.BurstRate`）——官方原话"调用频率骤增，触发系统稳定性保护"。**这个才是我们踩中的坑**：短时间内建立很多条新连接，就算总调用量没超配额，也会被这层保护单独拦下来，跟 `1011 Too many requests` 报错对应。
- 配额是按**主账号**维度合并计算的，账号下所有 Key/子账号/业务空间共享。
- 官方建议：降低并发/连接建立频率，请求间做平滑退避，而不是密集重试。

结合我们自己的测试历史，落到设计上的结论：
- **真实用户使用不会碰到这个问题**——一次对话是一条长连接（最长 120 分钟），中途靠 `session.update` patch 记忆而不是重新连接。触发限流的是"测试脚本"这种用法：一个脚本 = 一条新连接，短时间跑多个脚本 = 短时间建很多条连接。
- 已经落地的 (a) 条款（连接失败要告诉用户）本身就是应对限流的第一道防线——用户如果真的撞上限流，至少会看到明确的错误提示，而不是卡死。
- **还没做但值得做**：连接失败时的指数退避自动重试（目前只是把错误亮出来，不会自动重试）。给以后的自己/接手的人提个醒：加的话触发条件要narrow到"确认是限流/网络类失败"，不要对所有失败都重试。
- **我们自己后续测试时**：改成一条连接里顺序跑多个子测试（`test_combined_single_conn.py`），而不是每个脚本单独开一条连接——今天已经改成这种写法，但改的时候限流窗口还没恢复，没能验证效果，下次需要先确认窗口开着再跑。

## 开源本地部署选项调研（2026-08-22）

如果未来想要一个不依赖云端 API、可以本地部署的替代方案，跟 Qwen-Omni-Realtime 这种"端到端语音输入输出"最接近的开源选项：

| 模型 | 参数规模 | 显存需求（BF16） | 开源协议 | 备注 |
|---|---|---|---|---|
| **Qwen2.5-Omni-7B** | 7B | 纯文本+短音频场景显著低于视频场景；官方给的显存表以视频输入为主（15s 视频 ~78GB 起，随时长上涨），纯语音对话场景没有单独列出但会远低于视频数字 | **Apache-2.0**（可商用） | 阿里自己开源的版本，架构（Thinker-Talker）和这次用的云端 Realtime API 是同一血缘，最贴近现在的技术栈 |
| **Qwen2.5-Omni-3B** | 3B（部分资料显示约 6B） | 约 18-22GB | **Qwen Research License**（仅限研究/非商用） | 消费级显卡能跑，但license不能商用，作为参考基线更合适 |
| **Qwen3-Omni-30B-A3B** | 30B 总参数，MoE 激活约 3B | 视频场景 78-145GB（随时长），纯语音场景应显著更低 | Apache-2.0 | 更新的一代，多数榜单上超过 2.5-Omni，但部署门槛更高 |
| **Kyutai Moshi** | 7B（Temporal Transformer） | 消费级 GPU（L4）即可，官方演示延迟低至 ~200ms | CC-BY 4.0 | 真正"从头设计成语音优先"的模型（不是文本模型加语音模态），全双工、延迟做得特别好，但中文能力/生态不如 Qwen 系 |

结论：如果要找"最像现在这个 Qwen-Omni-Realtime、还能本地部署"的选项，**Qwen2.5-Omni-7B**（Apache-2.0，可商用）是最直接的对应——同一个团队、同一套架构思路，中文语音表现应该也是这几个里最贴近云端 API 体验的。如果更看重延迟和"专门为实时语音设计"这个特性，Kyutai Moshi 值得关注，但中文场景需要自己评估效果。本地部署都需要一张显存充足的独立 GPU（消费级卡对 7B 纯语音场景可能勉强够用，但没有官方专门给出的纯语音显存数字，需要自己实测），不是能在手机/普通笔记本上直接跑起来的量级——如果目标是"完全离线的移动端"，可能还是要退回到"云端 Realtime API + 本地缓存兜底"这个现有设计，而不是端到端本地大模型。

Sources: [限流文档](https://help.aliyun.com/zh/model-studio/rate-limit) · [错误码文档](https://help.aliyun.com/zh/model-studio/error-code) · [Qwen2.5-Omni GitHub](https://github.com/QwenLM/Qwen2.5-Omni) · [Qwen3-Omni GitHub](https://github.com/QwenLM/Qwen3-Omni) · [Kyutai Moshi GitHub](https://github.com/kyutai-labs/moshi)

## 限流的另一种表现：静默超时，不只是 1011 报错（2026-08-22 补充）

之前记录的都是连接被显式拒绝（`1011 Too many requests`）。这次 check-in 补测发现了另一种表现：**连接和第一次 `session.update` 都成功了（收到 `session.created` 和 `session.updated`），但紧接着发的第二次 `session.update`（每轮记忆 patch 用的那种）完全没有任何响应——不报错、不返回 `session.updated`，就是安静下来，10 秒超时后只能自己放弃**。两次独立测试复现了同样的模式（都是死在"这条连接上的第二次 `session.update`"），第三次追加测试（在两次 update 之间加了 5 秒间隔）则是在第一次 `session.update` 上就直接收到了显式的 `1011`。

这说明限流在临近触发阈值时可能有个渐进过程：先是"接受连接、但后续交互被静默丢弃"，账号被判定为更明确超限之后才会开始直接用 `1011` 拒绝。也印证了 8-21 那次"哑掉"现象记录里的猜测——两种表现（静默不回应 / 显式 1011）很可能是同一个限流机制在不同压力程度下的不同反应，不是两个独立问题。

对代码设计的影响：`sendSessionUpdateAndWait` 的超时兜底（4 秒放弃等待 ack、继续走下去）已经覆盖了"静默不回应"这种情况，不需要额外改动——这次复测算是为这个已有的兜底设计提供了一次真实的验证场景。

## 文件说明

- `server.py` — 中转服务（aiohttp）：serve 静态文件 + `/api/config` + `/ws`（转发到 DashScope）+ 挂载 AgentNexus Mock 路由。
- `agentnexus_mock.py` — Mock 出智枢按提案改完之后的记忆/消息 REST API，纯内存存储，重启会重置回种子数据。
- `index.html` / `static/styles.css` — 页面结构和样式（开始/结束合一按钮、文字输入框）。
- `static/app.js` — 核心逻辑：麦克风采集（`ScriptProcessorNode`，降采样到 16kHz PCM16）、流式播放（24kHz PCM16 顺序调度播放）、WebSocket 事件收发、`session.update` instructions patch 记忆注入、气泡渲染。
- `static/memory.js` — 本地记忆存储 + 检索。
- `static/agentnexus.js` — 拉取/推送 AgentNexus（Mock）记忆的桥接层。
- `static/saveIntent.js` — 识别"记住…"类明确保存意图，从中抽取要保存的内容。

## 已知限制

- `ScriptProcessorNode` 已经是浏览器标记为 deprecated 的 API（但仍被广泛支持），更现代的写法是 `AudioWorkletNode`，demo 图简单没换。
- 本地记忆检索还是关键词打分，没有语义理解（跟 iOS 项目的已知限制一样）。
- `agentnexus_mock.py` 是纯内存存储，没有持久化，也没有完整模拟"定期整理"这类后台任务（提案里的建议二）。
- 连接失败会明确提示用户并回到可重试状态（见上面"连接生命周期设计"），但没有自动重连/自动重试——需要用户自己再按一次开始。
