# VoiceChat

一个 iOS App 原型：像 ChatGPT 那样，既能打字聊天，也能像语音模式一样实时说话、实时听 AI 回复，还能"口述转文字"——说一段话，AI 整理成通顺的书面文字再发出去，类似 Typeless。默认对接 **Qwen-Omni-Realtime**（阿里云 DashScope/百炼），也可以切换回 OpenAI Realtime API——两者的 WebSocket 事件协议基本一致，切换只需改设置里的地址/模型名（以及 `AudioIOManager` 里的输入采样率，见下文）。

`web-demo/` 是同一套 Realtime API 协议的网页版验证 demo（浏览器 UI + 本地 Python 中转服务）。这个项目里新功能一直是**先在网页 demo 上做、用 Playwright 端到端测试验证过设计没问题，再移植到 iOS**——因为 iOS App 没法在这个开发环境里编译运行，网页 demo 是唯一能真正跑起来验证协议细节和交互逻辑的地方。细节和实测记录见 `web-demo/README.md`。

怎么在自己机器上把 iOS App / 网页 demo 跑起来测试、iOS 上架 App Store 需要准备什么，见 [`docs/testing-deployment.md`](docs/testing-deployment.md)。完整的功能设计（三种交互模式、口述转文字的技术方案、ChatGPT 风格视觉设计）见 [`docs/app-design.md`](docs/app-design.md)。Qwen Realtime API 踩过的坑（域名选择、模型/音色选型）见 [`docs/qwen-realtime-voice-setup.md`](docs/qwen-realtime-voice-setup.md)。

## 三种交互模式

App 里有三种"把话传给模型"的方式，容易被笼统地说成"语音功能"，但实现和用户预期不一样：

1. **实时语音对话**：按麦克风 → 建立 Realtime 长连接 → 说话 → 模型**用语音**回复 → 可以随时打断。
2. **打字发文字**：正常打字，走文字消息，不涉及语音。
3. **语音口述转文字**（新增）：想发文字消息但不想打字，说出来就行——语音被转写、经过 AI 整理去掉口语填充词，变成一条**文字消息**，不产生语音回复。输入栏上有 X（取消）/ ■（整理后填回输入框，可编辑再发）/ ↑（整理后直接发送，不用二次确认）三个操作。

三种模式的详细设计和技术方案见 `docs/app-design.md`。

## 架构

```
麦克风(AVAudioEngine) → 转 16kHz/16bit/单声道 PCM → WebSocket
                                                        │
                                          Qwen-Omni-Realtime（或 OpenAI Realtime API）
                                                        │
        扬声器(AVAudioEngine) ← 24kHz PCM 音频流 ← 流式回复(文字+语音)

                用户转写文本到达 ──► 本地记忆检索(MemoryStore) ──► 命中的话通过
                                                                  session.update
                                                                  把背景信息 patch 进
                                                                  instructions，再发
                                                                  response.create

        口述转文字：同一条 Realtime 连接机制，但 create_response 恒为 false、
        从不发 response.create ——只用来拿转写文本，转写完走一次独立的文本 LLM
        调用（AI 整理），不产生语音回复
```

- `VoiceChat/Audio/AudioIOManager.swift` — 麦克风采集、格式转换、流式播放。输入输出采样率**不对称**：上行 16kHz，下行 24kHz（这是 Qwen 的要求；OpenAI 是上下行都 24kHz，切换时需要改这里）。
- `VoiceChat/Realtime/RealtimeClient.swift` — WebSocket 连接与事件收发，地址/模型可配置，`connect(...)` 要等 `onSessionReady` 回调（对应 `session.updated` 确认）才算真正连上，不是 socket 一开就算连上了。
- `VoiceChat/Realtime/RealtimeModels.swift` — 用到的 Realtime API 事件的最小子集封装（`session.update` / `input_audio_buffer.append` / `response.audio.delta` / `conversation.item.create` / `response.create` 等，OpenAI 和 Qwen 共用同一套事件命名）。
- `VoiceChat/Memory/MemoryStore.swift` — **本地记忆**：每句用户说的话转写后都存一条（JSON 文件），下次提问时按关键词重合度 + 时间新鲜度检索最相关的几条，喂给模型当背景信息。这是验证"本地积累的记忆能不能被语音快速总结出来"这个概念用的最简实现，以后如果这个 App 并入自书，直接把 `MemoryStore` 换成读写自书共享记忆的实现即可，`add`/`search` 是替换的接缝。
- `VoiceChat/ConversationViewModel.swift` — 把音频层、网络层、记忆层粘合起来的状态机（连接中/聆听中/助手说话中/出错），`groundAndRespond(to:)` 是"收到用户问题 → 查记忆 → 注入背景 → 触发回复"这条链路的入口；`sendText(_:)` 是打字/口述转文字共用的发送入口，`sendText(_:reusing:)` 是口述"直接发"路径专用的——复用口述连接而不是重新握手一条新连接（见下面"口述转文字"）。也在这里做连接生命周期管理：连接失败要报错不能静默卡住、连上了不主动断开、5 分钟没说话自动挂断。
- `VoiceChat/Dictation/DictationViewModel.swift` / `DictationCleanupClient.swift` — 口述转文字：独立于 `ConversationViewModel` 的轻量状态机（两者互斥但不共享逻辑，避免两套状态机纠缠），`DictationCleanupClient` 是拿到原始转写文本后调用的一次性文本 LLM 请求（不是 Realtime 长连接），做"去口语填充词、理顺逻辑"这一步。
- `VoiceChat/Views/ConversationView.swift` / `MemoryView.swift` — 对话界面：ChatGPT 风格布局（文字输入胶囊 + 语音按钮同一行，口述时切换成 X/■/↑ 一行，消息气泡用 ChatGPT 同款配色）+ 记忆列表页（左上角大脑图标，方便直接看到 App 记住了什么、命中了什么）。
- `VoiceChat/Settings/` — API Key（Keychain）+ 连接地址/专属域名 Workspace ID/模型/音色（UserDefaults）。

**响应时机是手动控制的**：`session.update` 里把 `turn_detection.create_response` 设成了 `false`（对应 OpenAI Realtime API 的同名开关）。服务端还是会自动判断"用户说完了"并提交音频缓冲区，但不会自动开始生成回复；真正触发回复的是 `ConversationViewModel.groundAndRespond`：拿到用户转写文本后，先查本地记忆、把命中的内容通过 `session.update` 的 `instructions` 字段 patch 进对话，再发 `response.create`。这样才保证模型说话前一定已经看到了检索到的背景信息，不会有"模型已经开始回答了，记忆才注入进去"的时序问题（实测过 `conversation.item.create(role: "system")` 这条路子——Qwen 会静默忽略，只有 `session.update.instructions` 真的有效）。口述转文字复用同一个转写事件，但从来不发 `response.create`，只用来拿文字。

代码里也接了"打断"逻辑（收到 `input_audio_buffer.speech_started` 就清空播放队列 + 取消当前回复），协议层面已确认 Qwen 支持（`interrupt_response: true`），但实际打断体验（阈值、灵敏度）还没有针对真人说话调优过。

## 关于模型/地址

**强烈建议配置专属域名（Workspace ID）**，不要用共享域名。共享域名 `wss://dashscope.aliyuncs.com/api-ws/v1/realtime` 实测出现过持续多天的 `session.update` 无响应问题；换成按业务空间分域名的 `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime` 之后，同样的测试场景反复重跑，这个问题彻底消失了。这不是猜测的缓解措施，是有对照测试的根因定位——完整的踩坑过程、Workspace ID 怎么获取、和账号 ID 有什么区别，见 [`docs/qwen-realtime-voice-setup.md`](docs/qwen-realtime-voice-setup.md)。

当前默认配置（`RealtimeConfigStore` 里，设置页可以核对/切换，不用改代码）：

- 地址：留空 Workspace ID 时回退到共享域名；填了 Workspace ID 自动切到专属域名（强烈建议填）
- 模型：`qwen3.5-omni-flash-realtime`（官方推荐用于大多数生产场景，比 Plus 版本延迟低、成本低，实测过效果没有明显下降）
- 音色：`Serena`（默认），可选 `Tina`/`Sunnybobi`/`Ethan`/`Raymond`/`Dylan`——这 6 个都逐一用 `session.update` 往返测过能用；Qwen3.5-Omni-Realtime 支持的音色列表比这大得多，`Chelsie`（最早的默认值）在新模型上已经不再支持，别假设旧配置还能用

Qwen 这条产品线迭代较快，以后如果连不上了，先重新跑一遍连通性测试（脚本模式见 `web-demo/README.md`）比瞎猜有效。

认证方式和 OpenAI 一致：`Authorization: Bearer <API Key>`（握手阶段校验）。口述转文字的 AI 整理步骤额外用到一个纯文本模型 `qwen-turbo`（走 HTTP 的 compatible-mode 接口，不是 WebSocket），同一个 API Key、同一个专属域名。

## 环境要求

- 一台 Mac，安装 Xcode 15 或更高版本
- [XcodeGen](https://github.com/yonaskolb/XcodeGen)：`brew install xcodegen`
- 一个 DashScope/百炼的 API Key（有 Qwen-Omni-Realtime 权限；口述转文字额外需要 `qwen-turbo` 这个文本模型的权限）
- 建议同时准备好 Workspace ID（业务空间 ID，不是账号 ID），获取方式见上面链接的配置指南

## 首次运行

```bash
xcodegen generate
open VoiceChat.xcodeproj
```

在 Xcode 里：

1. 选中 `VoiceChat` target → Signing & Capabilities，设置你的开发者账号（Team）。
2. 用真机运行（麦克风在模拟器上体验不佳，建议用真机）。
3. App 启动后点右上角齿轮图标：填入 API Key，**强烈建议同时填上业务空间（Workspace ID）**——原因见上面"关于模型/地址"。模型/音色已经预填好实测过的默认值，一般不用改。
4. 点击麦克风按钮开始实时语音对话；或者直接在下面的输入框打字；或者点输入框旁边的麦克风图标口述，说完点 ■（整理后填回输入框）或 ↑（整理后直接发送）。

更详细的部署步骤、常见问题排查、App Store 上架清单，见 [`docs/testing-deployment.md`](docs/testing-deployment.md)。

## 已知限制 / 后续可做的事

- **记忆检索很朴素**：关键词重合度 + 时间新鲜度打分，没有语义理解，同义表达搜不到。够验证概念，真要好用大概率需要换成向量检索（比如用 Apple 的 `NaturalLanguage` 框架做本地 embedding，或者接一个向量库）。
- **记忆不做筛选/摘要**：现在是"用户说的每句话都存"，闲聊、口头禅也会被记下来，列表会很快变得杂乱。可以加一层"只记录看起来像事实/想法的内容"的过滤，或定期跑摘要压缩。
- **未来接自书**：现在 `MemoryStore` 是纯本地 JSON 文件，接口只有 `add`/`search` 两个方法。以后如果这个 App 内置进自书、跟自书共享记忆，直接换一个实现（读写自书的存储）替掉 `MemoryStore` 就行，不需要改 `ConversationViewModel` 里的调用逻辑。
- **API Key 安全**：当前是把 Key 直接存在设备 Keychain、App 直连服务端。这对个人测试没问题，但如果要上架 App Store 或给别人用，必须换成后端下发"临时令牌"的方案，避免长期有效的正式 Key 被人从 App 里提取出来滥用——完整的上架前置条件清单见 `docs/testing-deployment.md` 的"App Store 上架清单"一节。
- **打断体验未针对真人语音调优**：协议层已确认支持（见上面的实测说明），但阈值、灵敏度这些参数还没有拿真实说话调过。
- **断线重连**：目前网络断开后不会自动重连，只是把状态切回错误提示。
- **后台运行**：Info.plist 已声明 `audio` 后台模式，但退到后台后的长时间对话稳定性还没有针对性测试。
- **真实语音识别未测过**：口述转文字这条链路（识别 → AI 整理 → 发送/回填）在网页 demo 上是用假麦克风 + 手动注入模拟转写文本测的端到端逻辑，没有真实语音跑过；iOS 上更进一步，连假麦克风测试都还没做（这个开发环境没有 Swift 工具链）。
- **本地部署（Qwen2.5-Omni 跑在你自己 Mac mini 上）**：如果之后想换成完全本地、不经过阿里云的方案，Qwen2.5-Omni（3B/7B 稠密模型）在 Apple Silicon 上用 transformers + MPS 后端是可行的，但没有官方现成的 Realtime WebSocket 服务，需要额外写一个 Python 服务把推理包成本项目能对接的协议；这是一条独立于当前方案的后续工作，先不做。

## 验证：这份代码实际测过多少

这个开发环境是无 GUI 的 Linux 容器，没有 macOS、没有 Xcode、没有 Swift 编译器——**iOS App 的 Swift 代码从写出来到现在，从未在任何环境里实际编译或运行过**，不要把"代码写完了"当成"App 能跑了"。实际验证覆盖分三层，范围逐层变窄：

1. **Qwen Realtime API 协议本身**（域名、模型、音色、`session.update` 时序）——用 Python `websockets` 脚本直连 Qwen 反复测过，这层结论可靠，跟客户端用什么语言无关。
2. **完整的交互设计**（专属域名、Flash 模型、音色选择、连接生命周期规则、口述转文字的整条链路、"直接发"的连接复用优化）——在 **web demo（JS 实现）** 上用 Playwright 做过真实浏览器端到端测试，这层的"设计对不对"是验证过的。
3. **iOS 的 Swift 源码本身**——只做过手工数括号配对，确认没有明显语法失衡，**没有做过真正的编译检查**。第一次在 Mac 上跑 `xcodegen generate` + Xcode 编译，请当成这份代码的"首次真实测试"，大概率需要修一些小问题。

更详细的部署步骤、常见问题排查表、和"这一块具体测过什么/没测过什么"，见 [`docs/testing-deployment.md`](docs/testing-deployment.md)。
