# 两条实时语音链路的对比：Qwen-Omni-Realtime 端到端 vs LiveKit + Pipecat 级联

对比对象：

| | A：现有链路 | B：新链路 |
| --- | --- | --- |
| 目录 | `web-demo/` | `pipecat_demo/` |
| 传输 | 浏览器 ↔ 自建 WebSocket 中继 ↔ DashScope Realtime | 浏览器 ↔ WebRTC(LiveKit) ↔ bot 进程 ↔ DashScope |
| 模型 | `qwen3.5-omni-flash-realtime`，语音进语音出 | Paraformer ASR + Qwen 文本模型 + CosyVoice TTS |
| VAD/断句 | DashScope 服务端 VAD（`silence_duration_ms: 900`） | 本地 Silero VAD（0.9s）+ 0.5s 防抖 |
| 编排 | 浏览器里的 `app.js` | bot 进程里的 Pipecat 管线 |

两边共用同一个 DashScope 账号、逐字相同的系统提示词（`pipecat_demo/prompts.py` 复制自 `app.js` 的 `BASE_INSTRUCTIONS`）、同一个 AgentNexus mock 记忆后端。差异应当只来自架构。

**先说结论**：A 的上限更高（少一次转文字，语气/情感信息不丢），B 的**可观测性和可控性**明显更好，工程上更容易稳住。如果目标是"把这个产品做稳"，B 这条链路值得作为主力；如果目标是"追求最自然的语音交互体验"，A 仍然是天花板更高的那条。

---

## 1. 延迟

### 可测性：这是最实际的差别

A 只能测到"转写到达之后"的部分。用户停止说话到第一段转写之间发生在模型内部，客户端看不见——`app.js` 的 `[turn timing]` 日志里这一段明确标着"(服务端,不可测)"。

B 的每一跳都在本地进程里，同一个时钟能覆盖全程。`TurnTimingObserver` 每轮输出这么一行（下面是模拟模式下实测到的一轮，云端服务换成本地假实现，所以 LLM 那段只有 2ms）：

```
[turn timing] 停止说话→转写: 0ms | 转写→回合结束(防抖): 501ms | 回合结束→LLM首字: 2ms | LLM首字→TTS首音: 78ms | TTS首音→开始播放: 5ms | 总计: 586ms
```

"防抖"单列出来是有意的：那 500ms 是我们自己选择要等的（留给用户接着往下说），不是模型慢。第一版把它算进"转写→LLM"里，读起来就像模型花了半秒，属于会误导人的指标——这类"看起来像延迟、其实是策略"的时间，必须和真实开销分开记。

一轮变慢时，A 只能知道"慢了"，B 能知道是 ASR 慢、模型慢、合成慢，还是自己的防抖设太长。调参、换模型、跟供应商提问题，靠的都是这个。

### 结构：级联天然多几跳

A 一次网络往返内完成"听懂 + 想 + 说"；B 至少三次上行调用（ASR、LLM、TTS），而且 TTS 必须等 LLM 吐出第一个完整句子才能开工。理论上 A 更快。

抵消这个劣势的地方有三处，都已经做在代码里：

1. **TTS 任务预热**：`DashScopeTTSService.on_turn_context_created` 在 LLM 刚开始生成（`LLMFullResponseStartFrame`）时就发 `run-task`，把握手的那次往返藏在模型生成第一句话的时间里，而不是加在用户等待上。
2. **逐句流式**：Pipecat 按句子边界把 LLM 输出喂给 TTS，第一句合成完就开始播，不等整段回复。
3. **WebRTC 播放**：音频走 Opus/RTP + 抖动缓冲，不是自己在浏览器里排 PCM 播放队列。

值得注意的是，A 那条链路的可感知延迟里还有一部分不是模型造成的：`app.js` 为了避免用户"想一下再接着说"被打断，自己加了 500ms 的回复防抖。B 里这件事由 `SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.5)` 承担——同样的等待，但它是框架里一个有名字的参数，而不是散在事件回调里的定时器。

### 还没有的数字

这个开发环境没有 DashScope Key，也没有真实麦克风，所以**两边的真实延迟数字都还没测**。要得到可比的数字：两条链路各跑同样的几句话，A 看浏览器 console 的 `[turn timing]`，B 看 bot 日志或网页右侧面板，对比"总计"和 B 的分段。对比"总计"时记得两边的防抖都是 0.5s，可以先各自减掉。

## 2. 打断（barge-in）

A：打断依赖服务端 VAD 发回 `input_audio_buffer.speech_started`，客户端收到后清空本地播放队列并停止播放。判定在云端，网络抖动会直接反映成"打断反应慢"。

B：Silero VAD 跑在 bot 进程本地，判定不过网。Pipecat 收到打断后会一次性做三件事：清空下游音频队列、取消正在进行的 LLM 请求、把 TTS 的 WebSocket 断开重连（`InterruptibleTTSService` 的默认行为，`DashScopeTTSService` 直接继承）。最后一条尤其重要——不重连的话，服务端那个已经没人听的合成任务还会继续往回吐音频。

另外 B 会把"这一轮被打断了"如实记进上下文（`AssistantTurnStoppedMessage.interrupted`），模型下一轮知道自己上一句没说完。

## 3. 成本

计费结构不同，不能只比单价：

- **A**：按 Realtime 模型的音频 token 计费，输入输出都是音频 token。一次对话只有一个模型在计费。
- **B**：ASR 按音频时长、LLM 按文本 token、TTS 按合成字符，三笔账分开。

由此带来两个实际差别：

1. **B 的成本可以分项优化**。文本模型换成 `qwen-turbo`/`qwen-flash` 只影响 LLM 那一笔，不牵动识别和合成质量；A 换模型是整条链路一起换。
2. **B 的静音更便宜**。级联链路里静音只产生 ASR 的音频时长费用，不产生 LLM 和 TTS 费用；A 的静音一样在往模型送音频 token。

具体单价随时会变，按官方定价页算，不在这里写死数字。

## 4. 可控性

这是 B 领先最多的一项。

| 能力 | A | B |
| --- | --- | --- |
| 换 ASR / LLM / TTS 中的某一个 | 做不到，绑定同一个模型 | 换一个服务类即可 |
| 拿到用户说的文字 | 拿得到（模型附带转写） | 拿得到，且是独立可替换的 ASR |
| 在"听懂"和"回答"之间插自己的逻辑 | 只能改 instructions | 管线里加一个处理器就行（`MemoryInjector` 就是这么做的） |
| 热词/专有名词 | Realtime API 上没能跑通（见 `docs/app-design.md` 7.1） | Paraformer 支持 `vocabulary_id`，服务已经接好参数 |
| 逐位念编号这类发音要求 | 只能靠提示词约束模型 | 除提示词外，还能在进 TTS 前直接改写文本 |
| 打断/轮次策略 | 服务端 VAD 的几个参数 | 本地 VAD 参数 + 可换的轮次策略（含语义轮次检测） |

顺带一提，pipecat 默认的轮次结束策略是语义轮次模型（smart turn），比"静音多久算说完"聪明得多。本 demo **故意没用**，换成了纯静音判定，就是为了跟 A 的 `silence_duration_ms` 对齐；真要做产品，这是 B 能直接吃到的一个红利。

## 5. 记忆注入

这一项两边的难度差得最远。

A：Qwen Realtime 会忽略以 `conversation.item.create` 形式塞进去的背景信息，唯一有效的做法是把检索结果拼进 `session.update` 的 `instructions` 里，**并且必须等 `session.updated` 回来之后**才能发 `response.create`；连发两次 update 不等 ack 会出现空回复。这套时序是 `web-demo/README.md` 里记着的实测坑。

B：级联链路就是一个普通的 chat-completions 消息列表，检索结果作为一条 system 消息插在用户问题前面（`processors.MemoryInjector`）。没有往返、没有 ack、没有"这一轮用的是上一轮的 instructions"这种失败模式。每轮替换而不是叠加，所以上下文不会越滚越长。

两边的检索质量是刻意对齐的：`memory.py` 是 `memory.js` 打分逻辑（关键词重合 + 时间衰减，中文按单字索引）的移植，读的是同一个 AgentNexus mock。

## 6. 回声消除

A：浏览器用 `getUserMedia` 采音、用 AudioWorklet 自己排队播 PCM。麦克风的 AEC 只有在播放走"浏览器认得的音频输出路径"时才有参考信号；自己调度的 PCM 播放属于这条路径，但整条回声链路要靠 `echoCancellation: true` 加上打断逻辑兜着——外放场景下助手的声音被当成用户说话，是这类实现的典型故障。

B：音频走 WebRTC 全程。采集和播放都在浏览器的 WebRTC 音频管线里，Chromium 的 AEC3 直接把远端轨作为参考信号，这是 AEC 设计上最标准的用法，外放场景下明显更稳。噪声抑制、自动增益、丢包补偿、抖动缓冲同理，都是传输层白送的。

代价是多了一个组件：本地开发要跑一个 LiveKit 服务器，上线要么用 LiveKit Cloud、要么自己部署（还要考虑 TURN）。A 只需要一个 WebSocket 中继。

## 7. 稳定性与限流

A 踩过的两个坑，B 的情况：

- **共享域名不稳**：`dashscope.aliyuncs.com` 上频繁 `1011 Too many requests`，换成 workspace 专属域名后好转。B 复用同一个 `QWEN_WORKSPACE_ID`，ASR/TTS 的 WebSocket 和 LLM 的 HTTP 三个端点会一起切到专属域名。
- **限流影响面**：A 一次限流打断整场对话（就那一条连接）。B 的三个上游是独立连接，某一个被限流只影响那一段，而且 ASR 和 TTS 的失败都会走各自的重连/报错路径，不会静默地把整轮吞掉。

## 8. 工程复杂度

浏览器端代码量的差别很直观：

- A：`app.js` 1724 行 + 采集 worklet 57 行 + 播放 worklet 182 行，浏览器要自己管采集、16k 重采样、PCM 分片、24k 播放队列调度、打断、重连、Realtime 事件时序。
- B：`pipecat_demo/web/app.js` 217 行，只做四件事：取 token、连房间、发麦克风轨、播远端轨（外加渲染耗时面板）。

复杂度不是消失了，是挪了位置：B 多出 `services/` 里两个 DashScope 服务实现（ASR 333 行 + TTS 343 行），因为 Pipecat 自带 Qwen 的文本 LLM 服务，但没有 DashScope 的语音服务。这两个文件是一次性成本，且有协议测试兜着；而 A 的浏览器端复杂度是每加一个功能都要再碰一次的。

## 9. 怎么选

- 想要**最自然的语音交互**（语气、情感、抢话的自然感），A 的上限更高：少一次"语音→文字"的信息损失。
- 想要**能观测、能调、能换件**的工程链路，选 B。尤其是需要热词、需要在回答前跑自己的检索/规则、需要按环节优化成本的场景。
- 外放场景、弱网场景，B 的 WebRTC 传输是实打实的优势。
- 两条链路可以共存：B 的记忆后端就直接读 A 的 AgentNexus mock，两边同时开着能立刻 A/B。

## 10. 本次验证到哪一步

**已验证（这个环境里能跑的都跑了）**：

- 两个自己实现的 DashScope 服务，对着按官方协议应答的假服务器逐字段验证：指令格式、鉴权头、任务生命周期、握手期音频不丢、`sentence_end` 才算最终转写、二进制音频转成音频帧、每轮开新任务。
- 记忆检索：打分逻辑与 `memory.js` 对齐，AgentNexus 部分跑的是 `web-demo/agentnexus_mock.py` 本体。
- 会话链路（模拟 ASR/LLM/TTS）：从转写到出音频，含记忆注入和上下文回写。
- 真实 LiveKit 服务器 + 真实 bot 进程（模拟模式）：加入房间、发布音轨、参与者离开后进程干净退出。
- **真实音频驱动的完整多轮对话**（模拟模式）：往 LiveKit 房间里推一段真人语音（说 6 秒、停 9 秒，循环），Silero VAD 正常判定说完、轮次正常结束、回复正常合成播放、耗时分解每轮都出数、记忆逐轮累积。连续说话不停顿时，打断逻辑也如实生效（回复被截断并标记 `interrupted`）。
- 每轮耗时分解的正确性（合成帧序列）。

这一步抓到两个真 bug，都是这个 PR 自己的代码：一是"说话开始/结束"这类帧是**广播**的（每个处理器拿到的是不同对象），去重按对象 id 不管用，导致一轮真实数据后面跟着十几条全空的耗时上报，把界面上的数字覆盖成"—"；二是助手文字原本只在整轮说完后才出现，改成跟着语音逐句显示。两个都补了回归测试。

**没验证（需要你的机器）**：

- 真实 DashScope 调用。这个环境没有 Key，Paraformer 和 CosyVoice 的真实响应、真实延迟、真实费用都没跑过。
- 真实麦克风下的 VAD 判定、打断体感、外放回声——和 A 当初一样，这个环境只有伪造音频设备，触发不了真实语音的 VAD。
- 两条链路的延迟数字对比。代码里的打点都就位了，缺的是真实的一次对话。
