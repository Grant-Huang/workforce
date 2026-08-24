# 开发 To-Do / 设计讨论纪要

这份文档记录"讨论清楚了、可以动手"的事项，来自跟用户的设计讨论。规则：一个话题只有在讨论收敛（用户确认方向）之后才进这份清单；还在来回讨论、没有定论的，留在对话里，不提前写进来。写的时候尽量注明"为什么"，不只是"做什么"，方便以后接手的人理解决策背景，不只是照着抄。

## 记忆（本地记忆缓存）

讨论于 2026-08-23，背景见 `docs/agentnexus-memory-integration-proposal.md`（智枢侧的姊妹讨论，那份是"跨频道溯源"，这里是"本地缓存怎么接多个来源"，两者互补）。

现状：`VoiceChat/Memory/MemoryStore.swift` / `web-demo/static/memory.js` 是一个扁平数组，没有来源字段（iOS 完全没有，web 端只有非正式的 `meta.source`），合并逻辑是"存在就跳过"，没有上限、没有同步状态追踪。

- [x] **给 `MemoryEntry` 加正式的来源字段**（2026-08-23 完成）：`source`（`"local"`/`"agentnexus"`/`"unknown"`，见 `MemorySource`）+ `sourceId`。iOS/web 都加了，web 端不再用 `agentnexus:${entry_id}` 字符串拼接当唯一来源（保留字符串拼接作为 `id`，因为 Phase 2 之前 `merge` 的去重逻辑还是按 `id` 走，改 `id` 会破坏现有去重；`sourceId` 是新加的正式字段）。**旧数据兼容性是这次的重点**——两端都不是简单加字段，而是显式处理"旧的 `memory.json`/localStorage 里的记录没有 `source` 字段"这个情况：iOS 用自定义 `init(from:)` + `decodeIfPresent` 兜底（否则旧数据会因为解码失败被 `MemoryStore.load()` 的 `try?` 静默清空，这是真实的数据丢失风险，不是理论问题）；web 端 `load()` 做同样的兜底。两边都不把旧数据默认成 `"local"`（那意味着"还没同步"，但旧数据说不定早就同步过，只是没记录），而是标成 `"unknown"`，诚实反映"不知道"而不是瞎猜。**验证**：web 端用 Playwright 测过三种场景——旧格式数据 reload 后不丢失且标成 unknown、新增的本地对话默认 `source: "local"`、从 AgentNexus 拉取的记忆正确带上 `source: "agentnexus"` + 真实的 `sourceId`；另外重新跑了一遍已有的对话/打断/口述回归测试 + 一次记忆检索到问答的端到端验证（确认取到的记忆真的被塞进了发给模型的 `instructions` 里），确认这次改动没有破坏原有功能。iOS 端只做了源码改动，未编译验证。这是后面几条的前提。
- [ ] **合并逻辑从"存在就跳过"改成按 `(source, sourceId)` 做 upsert**：现在的 bug 是——如果在智枢那边编辑了一条已经拉过的记忆，本地永远不会更新，因为 `entry_id` 已存在就直接跳过了。改成比较 `updated_at`，更新的才覆盖。
- [ ] **本机原始对话按来源系统整体替换，而不是永久累加 merge**：每次从某个来源拉取，把"属于这个来源的本地记录"整体替换成最新集合，而不是一条条判断"是不是被删了"——本地缓存的定位本来就是"可丢弃、可重建"的镜像，全量替换更简单也更不容易留下幽灵数据。注意本机产生、还没同步出去的记录（下一条）不能被这个替换逻辑误伤。
- [ ] **本机产生的记录也要标来源**：`source: "local"`（未同步）→ 推送确认成功后"过户"成 `source: "agentnexus", sourceId: <服务端分配的id>`。
- [ ] **推送加同步状态追踪 + 失败重试**：现在 `pushMessage`/推送是纯 fire-and-forget，失败了没有任何记录、也不会重试，那句话就永远不会出现在智枢里，本地却毫无察觉。要加"是否已确认同步"的状态，失败了在下次有网络时重试。
- [ ] **原始对话记录加滚动窗口裁剪**（具体数字后续再定，方向是"最近 N 条或最近 M 天，取较大者"）：本地缓存不该背"永久保留全部历史"的包袱，这是智枢 `history` 层该做的事；AgentNexus 拉回来的结构化记忆（ANCHOR/DECISIONS/PROGRESS）量小价值高，不用同一套裁剪策略。
- [x] **拉取时机加一条"app 回到前台时也拉一次"**（2026-08-23 完成）——web 端监听 `visibilitychange`，iOS 端监听 `UIApplication.didBecomeActiveNotification`，触发时都调 `pullMemoryInBackground`/`AgentNexusBridge.pullMemory()`。web 端用 Playwright 验证过（spy 替换 `AgentNexusBridge.pullMemory`，模拟 `visibilitychange`→visible，确认被调用）；iOS 端只做了改动，未编译验证。

不建议做的方向（讨论中明确排除）：本地不应该自建向量检索/自己的分层整理逻辑——那是智枢该做的事，本地重复做一遍会变成两份互相打架的真相源。

## 黑话词典与记忆提炼

讨论于 2026-08-23，讨论收敛，完整设计见 `docs/app-design.md` 第七节。依赖上面"记忆"一节的来源字段（`source`/`sourceId`）——个人黑话本质上是一种记忆条目，复用同一套来源标记和同步机制。

- [x] **热词/词汇表可行性已验证（负面结论）**：实测 `qwen3.5-omni-flash-realtime` 的 `session.update` 不支持任何形式的热词/词汇表偏置，ASR 层面走不通，按下面的"记忆提炼"方案在事后纠偏。细节见 `docs/app-design.md` 7.1。
- [ ] **通用行业词典的分发机制**：一次性人工审校、当静态资源集中维护、分发到各端只读使用——现在没有"版本化资源包"这类分发通道（现有的 `/api/config` 只发了 voice 列表这种简单值），做的时候需要设计一下。
- [ ] **个人/团队黑话走记忆同步机制**：用户第一次解释某个黑话后，存成一条 `source: "local"` 的记忆条目，跟着"记忆"一节的同步机制"过户"进 AgentNexus，不单独建词典管理基础设施。
- [ ] **记忆提炼步骤**：复用口述整理的机制（一次性 `qwen-turbo` 调用），换成"提炼这轮对话里值得记住的事实"的 prompt，异步执行（不在关键延迟路径上）。具体要求：user+assistant 一起提炼（不是只处理用户那半句）、"值不值得记"在同一次调用里判断（不单独分类）、黑话词典作为背景信息一起传入。取代现在 `handleUserTurn`/`groundAndRespond` 里"每句用户话原样存、助手回复完全不存"的做法。

## 会话（插话机制 / 回复长度 / 过渡语）

讨论于 2026-08-23，讨论收敛，完整设计见 `docs/app-design.md` 第六节。

- [x] **web 端打断（barge-in）没有真正取消服务端生成**（2026-08-23 完成）：`app.js` 的 `speech_started` 分支补上了 `sendEvent({type: "response.cancel"})`，并同步重置 `assistantBubbleEl`/`assistantHasDelta`，跟 iOS 的 `onSpeechStarted`（`interruptPlayback` + `cancelResponse`）对齐。用 Playwright 验证过：真实连上 Qwen 后 spy `ws.send`，模拟 `speech_started` 事件，确认 `{"type":"response.cancel"}` 真的被发出去了。
- [x] **web 端回声自我识别（用户实测发现的严重 bug，2026-08-23 完成，2026-08-24 修了一个由它引入的回归）**：用户真机实测确认——web 端对话时，助手自己说的话会被麦克风收音、当成用户输入。这条之前只在 6.2 节写了一句"没实测过是否可靠"的风险提示，没有被排进任何阶段，属于规划疏漏；用户报告后插队立刻修，跳过了原定的阶段顺序。根因见 `docs/app-design.md` 6.2：`getUserMedia` 没显式声明 `echoCancellation`，且助手音频播放路径（原生 `AudioContext.destination`）不保证被 Chromium 的 AEC 当作参考信号。修复：三处 `getUserMedia` 加 `echoCancellation`/`noiseSuppression`/`autoGainControl` 约束；播放改走 `MediaStreamAudioDestinationNode` + 隐藏 `<audio>` 元素（`setupPlayback()`）。**这个环境没有真实音箱麦克风，没法做声学回环测试**，只验证了代码路径本身没问题（约束真的传给了 API、`<audio>` 元素真的在播放、原有对话/打断/口述功能都没回归）——修复是否真的解决问题，需要用户在真机上重新验证。iOS 走系统级 AEC（`.voiceChat` 模式），用户还没测过 iOS 是否也有这个问题。**2026-08-24 用户真机实测反馈**：回声问题本身看起来是修好了，但带出一个新问题——助手说话时能听到"哒哒哒哒哒"的杂音（打断阈值敏感问题见下一条）。根因推断：`setupPlayback()` 把 `AudioContext` 强制建在 24000Hz（为了跟 24kHz 的 PCM16 chunk 严丝合缝，避免 Web Audio 图内部重采样），这导致 `MediaStreamAudioDestinationNode` 的输出、进而 `<audio>` 元素实际播放的也是 24000Hz——大多数音频硬件的原生采样率是 48000Hz 左右，直连 `.destination` 走的是 Chromium 成熟的输出重采样器，而经过 `MediaStreamTrack`/`<audio>` 元素这条（主要为 WebRTC 场景设计的）播放管线在非原生采样率下有已知的爆音/咔哒声问题。修复：`setupPlayback()` 不再强制 `sampleRate: 24000`，让 `AudioContext` 用浏览器原生采样率；`createBuffer()` 仍然显式声明每个 chunk 的真实采样率（24000），Web Audio 规范保证播放时自动重采样，行为不变，只是换了重采样发生的位置。用 Playwright 验证过 `playCtx.sampleRate` 不再被锁定成 24000、播放管线和对话流程没有回归；**咔哒声是否真的消失，这个环境没法做声学验证，需要用户真机重新确认**。
- [x] **系统提示词加回复长度三档策略**（2026-08-23 完成）：`BASE_INSTRUCTIONS`（web）/`systemInstructions`（iOS）都改成了查询类（1-3句）/列举类（最多3条左右）/分析类（先给路线图、分段、不中途冒出没预告的点）三档具体指引，分类逻辑留给模型在同一次生成里隐式完成。web 端用 Playwright 验证过 prompt 改动没有破坏正常的文字对话流程；具体分类判断得准不准，需要真实语音场景才能评估，这次只验证了"prompt 改了、协议没坏"。
- [ ] **过渡语（filler phrases）——暂不阻塞，等接入外部工具调用（比如新闻搜索）时再做**：
  - 起草文案库（几个类别 × 3-5 句，纯文本、不分音色），人工审校定稿后作为静态常量提交进代码库，参照 `VOICE_OPTIONS`/`DICTATION_CLEANUP_PROMPT` 那种"写一次、人工审核、提交源码"的模式
  - 先验证 Realtime 协议在"工具调用进行中"能不能先说一句预设文本、再等结果、再继续——这是"复用当前连接念过渡语，不额外生成音频资源"这个方案能不能成立的前提，目前完全没测过
- [ ] **VAD 阈值调优（`threshold`/`prefix_padding_ms`/`silence_duration_ms`）——2026-08-24 有了第一轮真机反馈，但还没调完**：这几个参数已经暴露在 `session.update` 里，是打断灵不灵敏、会不会被呼吸声/背景噪音误伤的真正调节旋钮。用户网页版真机实测反馈"很容易被打断，随便一些其他的声音就会中断"——把语音会话（唯一跟打断挂钩的会话）的 `threshold` 从默认的 0.5 提到了 0.65（web 的 `app.js`，iOS 的 `RealtimeModels.swift` 为了两端一致也同步提了，iOS 还没设备实测过）。**这只是基于一次反馈的单次上调，不是调好了**——`0.65` 到底合不合适（会不会调过头导致正常说话也触发不了打断）、`prefix_padding_ms`/`silence_duration_ms` 要不要一起调，都还需要用户下一轮真机测试反馈才能确定，可能还要再调几轮。
- [ ] **iOS 端回声自我识别验证**：web 端已经确认踩坑并修复（见上面那条），iOS 走 `AVAudioSession` 的 `.voiceChat` 模式（系统级 AEC，设计上比浏览器软件 AEC 更可靠），但用户还没有在 iOS 上测试过是否也有同样的问题——不能假设"用了系统级 AEC 就一定没事"，得实测确认。
- [ ] **打断时的软停止（音量淡出）——优先级低，纯打磨，不是必须项**：现在 `interruptPlayback()`/web 端的 `stopPlayback()` 都是硬切（`playerNode.stop()`/立刻停止所有 `AudioBufferSourceNode`），体感比较生硬。有些产品（如部分 Alexa 场景）会做 100-200ms 的音量淡出。这条之前讨论时就判断"不算必须项"，这次按要求重新记录下来，不代表现在要做，只是不该被默默略过。

## 会话状态机拆分：文本会话 vs 语音会话

用户网页版实测发现，讨论于 2026-08-23，讨论收敛，**并入阶段 2 一起完成**（不单独插队），完整设计见 `docs/app-design.md` 第八节。

- [x] **拆成两套独立的连接/状态机（web + iOS 均已完成，2026-08-23/24）**：文本会话（打字+口述转文字，彻底不碰麦克风、不碰 TTS，回复只要文字）跟语音会话（现有实时对话，不变）之前共用同一套连接，空闲时发文字消息会调起语音会话的连接逻辑，导致打字发消息也会打开麦克风、语音播放回复——已确认属实、已修复，两端都是。web 端拆成 `ws`/`STATE`（语音）+ `textWs`/`TEXT_STATE`（文本）两套独立连接持有者；iOS 端对称地拆成 `client`/`ConversationState`（语音，`ConversationViewModel.swift`）+ `textClient`/`TextSessionState`（文本，新增），`groundAndRespond` 改成按 `session: RealtimeClient` 参数化，两端都避免了复制一份记忆检索/grounding 逻辑。**协议细节已实测**（不是假设，web 端验证）：直接连真实 `qwen3.5-omni-flash-realtime` 测过 `session.update.modalities: ["text"]`——服务端完全不生成任何音频相关事件（`got_audio_event: False`），回复通过 `response.text.delta`/`response.text.done` 这对全新事件（跟语音会话用的 `response.audio_transcript.*`不是同一对）返回；iOS 端的 `RealtimeModels.swift`/`RealtimeClient.swift` 按同样的事件名加了 `.textDelta`/`.textDone` 支持，复用同一套已验证结论，不重复实测协议本身。**加了一条之前没有的规则：三种输入方式（语音会话/文本会话/口述）互斥，同一时刻只能有一个在活动**——不只是 UX 顺手，是因为文本会话和语音会话共用同一份流式拼接状态（web 的 `assistantBubbleEl`/`assistantHasDelta`，iOS 的 `assistantLineIndex`），两边同时有回复在流式返回会互相污染，互斥是最简单正确的解法。**验证情况两端不同**：web 端用 Playwright 端到端验证过（打字发消息全程不碰麦克风、`state` 不受影响、拿到纯文字回复；语音会话本身含打断完全不受影响，回归测过；三种输入方式互斥的按钮禁用状态验证过）；iOS 端只做了源码改动，用手工检查大括号/圆括号配对数量确认没有语法层面的低级错误（这个环境没有 Swift 工具链，没法编译，更没法在真机/模拟器上跑）——**功能是否真的按预期工作，完全没有验证过，需要用户在 Xcode 里编译并实测**。
- [x] **口述"直接发"改指向文本会话（web + iOS 均已完成，2026-08-23/24）**：web 端 `promoteDictationConnection` 重命名为 `promoteDictationConnectionToTextSession`，现在过户给 `textWs` 而不是 `ws`；iOS 端对称地把 `ConversationViewModel.sendText(_:reusing:)`（原本过户给语音会话）改成了 `sendTextSessionMessage(_:reusingDictationClient:)`，过户给 `textClient`。两端都顺带把口述自己发起的 `session.update` 改成了 `modalities: ["text"]`（口述从来不生成回复，跟文本会话本来就是同一种配置），这样过户不需要再改 modalities，只需要一次 `instructions` patch——比之前"过户给语音会话"更简单，不是更复杂。**验证情况两端不同**：web 端 Playwright 验证过（口述"直接发"全程不碰麦克风、正确过户进 `textWs`、`textState` 变成 `ready`、拿到文字回复；口述"编辑后发"路径不受影响）；iOS 端只做了源码改动，未编译验证。
- [x] **共享会话历史空间（2026-08-24 确认已满足，作为状态机拆分的副产品，不是单独实现的）**：状态机拆分时，`transcript`（iOS）/`chatEl`+`addBubble`（web）被有意保留在 `ConversationViewModel`/模块顶层，而不是复制一份挂到 `textClient`/`textWs` 各自的作用域下——语音会话和文本会话的回调（`wireCallbacks`/`wireTextCallbacks`，`handleServerEvent`/`handleTextSessionEvent`）都写向同一份历史，从来没有分裂过。**web 端 Playwright 验证过**：打字发一轮、结束文本会话、开始语音会话再发一轮（模拟转写事件），两轮对话在同一份连续历史里按时间顺序都在，没有互相覆盖或丢失。iOS 端因为是同一个 `ConversationViewModel` 实例持有 `transcript`，同构不需要单独验证。
- [x] **语音会话进行中隐藏转写，改显示动效球（web 已完成，2026-08-24；iOS 未开始）**：web 端加了 `#voiceOrbContainer`/`#voiceOrb`，语音会话连接期间（`state !== IDLE`，含 CONNECTING/LISTENING/SPEAKING）隐藏 `#chat`、显示动效球，会话结束后隐藏球、露出 `#chat`（转写数据模型完全没变，一直在实时写入 `chatEl`，这条只是切换可见性，不是延迟写入）。球本身用两个 `AnalyserNode` 驱动：LISTENING 时接麦克风采集链路（`source.connect(micAnalyser)`，跟已有的 `processorNode` 并联，不影响原有的语音发送逻辑），SPEAKING 时接播放链路（每个 chunk 的 `AudioBufferSourceNode` 同时连到 `playAnalyser` 和 `playDestNode`）；没有真人说话/播放时叠加一条正弦波"呼吸"基线，避免 CONNECTING 或短暂静音时球看起来是死的。浅蓝色 `radial-gradient` + 随音量变化的 `box-shadow` 光晕，视觉效果和光晕强度全部在 JS 里算（没有用 CSS `@keyframes`，避免跟音量驱动的 `transform` 互相打架）。**Playwright 验证过**：开始语音会话后 `#chat` 隐藏、球显示、`orbAnimationId` 确认动画在跑、球的内联 `transform` 样式真的随时间变化（不是冻结画面）；模拟一整轮对话（转写→回复）期间转写照常写入 `chatEl`（只是不可见）；结束会话后球隐藏、`#chat` 恢复显示且能看到刚才那轮对话、动画循环真的停了（没有留一个看不见但一直在跑的 rAF 循环）；原有的语音会话/打断/文本会话/口述回归测试全部重跑过，没有受影响。**iOS 端已完成 port（2026-08-24）**：`AudioIOManager` 加了 `onInputLevel`/`onOutputLevel` 回调（分别 tap 麦克风输入和 `mainMixerNode` 输出，算 RMS），`ConversationViewModel` 新增 `@Published orbLevel`（按 `state` 是 `.listening` 还是 `.assistantSpeaking` 决定读哪个来源，其他状态归零，避免残留读数），`ConversationView` 新增 `voiceOrbView`（`TimelineView(.animation)` 驱动呼吸基线 + `orbLevel` 驱动的缩放/光晕，浅蓝色 `RadialGradient`，视觉参数照抄 web 端的数值），`!viewModel.isIdle` 时显示球、隐藏 transcript（`.error` 状态不算在内，出错时转写和错误信息照常显示，不会被球挡住）。**RMS 到视觉强度的缩放系数（6倍）是从 web 端未经验证地照搬过来的猜测值**——iOS 端麦克风/播放的真实电平大小从来没有在真机上测量过，这个环境没有 Swift 工具链，只做了源码改动和括号配对检查，编译和真机效果都没有验证过，很可能需要用户真机测完之后重新调这个系数。
- [x] **会话历史持久化（用户提问，现状确认为"丢失"；web + iOS 均已完成，2026-08-24）**：之前只有用户说的话会推送 AgentNexus（fire-and-forget，不确认成功），助手回复完全不存（不推 AgentNexus、本地也不存），完整会话记录本身（带顺序、带说话人）完全没有持久化，刷新/杀 App 就没了——这跟"记忆"（`LocalMemory`/`MemoryStore` 里抽取出来的可检索片段）是两个不同粒度的东西。**方案已实现**：助手回复也推送到 AgentNexus 的消息记录（复用已有的 `pushMessage`/`AgentNexusClient.pushMessage`，`senderType`/`sender_type` 传 `"assistant"`，跟用户消息走同一条已有接口，不需要新接口）；本地新增一个独立于 `LocalMemory`/`MemoryStore` 的简单存储（web 的 `history.js`/`ConversationHistory`，用 `localStorage`；iOS 的 `ConversationHistoryStore`，JSON 文件，结构照抄 `MemoryStore` 的持久化模式），持久化完整对话轮次（带说话人、带顺序，不做检索、不做筛选，纯记录）。两端都统一在一个入口写：web 的 `addBubble()`（用户轮次）+ `finalizeAssistantTurn()`（助手轮次，`response.done` 时触发，从累积好的完整文本里取，不是从增量 delta 里拼）；iOS 对称地加了 `appendUserTurn()`/`finalizeAssistantTurn()`。**两端都特意在用户打断（barge-in）时不持久化那句被打断的半截回复**——那是没说完的、不完整的一轮，跟被舍弃的行为保持一致，不强行存一句不完整的话。**验证情况不同**：web 端 Playwright 端到端验证过——文字轮次+模拟语音轮次都正确写入本地 `ConversationHistory`（说话人、顺序都对）、助手回复真的带 `sender_type: "assistant"` 推送到了 mock AgentNexus（直接查询 mock 的 messages 接口确认）、刷新页面后历史还在（真的落了 `localStorage`，不是内存态）；iOS 端只做了源码改动，未编译验证。

## 优先级与实施顺序

以上所有 to-do（记忆、黑话词典/记忆提炼、会话三块）放在一起看，按依赖关系和风险/价值比排出的顺序：

1. **阶段 0——独立、零风险的小修，随时可以先做**：web 端打断补 `response.cancel`（纯 bug fix）、系统提示词加长度三档策略（纯 prompt 改动）、拉取时机加"前台唤醒也拉一次"。这三条互相独立、不依赖任何其他改动，价值确定，建议最先做掉。
2. **阶段 1——记忆基础设施：来源字段**：`MemoryEntry` 加 `source`/`sourceId` 字段（iOS+web 两端）。这是后面几乎所有记忆相关改动的地基，建议单独一轮做完、测好，不要跟功能性改动混在一起做。
3. **阶段 2——建在来源字段之上的机制修正 + 会话状态机拆分**（2026-08-23 扩大范围，两块并入一起做）：
   - 记忆同步机制修正（都直接依赖阶段 1）：合并逻辑改成 upsert、本机原始对话按来源整体替换、本机产生的记录标 `source: "local"`、推送同步状态追踪+失败重试。这四条互相关联紧密（都是"如何维护 source 字段所代表的语义"的具体规则），建议一起做、一起测，不要拆得太碎导致中间态不一致。
   - 会话状态机拆分（用户网页版实测发现，见上面"会话状态机拆分"一节）：文本会话/语音会话拆成两套独立状态机、口述"直接发"改指向文本会话、共享会话历史空间、语音会话隐藏转写改显示动效球、会话历史持久化。用户明确要求"合并到阶段 2 一起完成"，不单独插队。跟记忆同步机制修正这两块彼此独立（一个改 `MemoryStore`/记忆同步，一个改连接/状态机），可以并行安排，但都算阶段 2 的范围，一起验收。
4. **阶段 3——记忆内容质量：记忆提炼 + 黑话词典**（依赖阶段 1 的来源字段，因为提炼出来的记忆也要打 `source: "local"` 标记；建议在阶段 2 做完之后再做——提炼产出的是"要被同步"的高质量记忆，如果同步机制还是旧的 fire-and-forget，再好的提炼质量也会面临"发出去但不知道成没成功"的问题）：记忆提炼步骤、通用词典分发机制、个人黑话走记忆同步。
5. **阶段 4——容量与裁剪**：原始对话记录加滚动窗口裁剪。建议放在记忆提炼（阶段 3）**之后**，不是之前——提炼上线后本地存储的是"筛选过、精炼过"的内容，条目增长速度会比"每句话都存"慢很多，裁剪的具体阈值在提炼上线前后应该是不同的数字，先上线提炼、观察一段时间实际增长速度，再定裁剪阈值更靠谱。
6. **阶段 5——阻塞、继续延后**：过渡语（filler phrases）。这条被一个还没造的能力（外部工具调用/新闻搜索）挡住，不参与上面的排序，等那个能力开始做的时候再启动。

## 做完这批之后，还需要补全的周边能力

这些不是某一条具体功能的 to-do，是"支撑上面这些机制长期健康运转"所需要的周边基础设施，目前完全没有，建议这批做完后专门排一轮：

1. **合并/upsert/替换逻辑的自动化测试**：现在几乎所有验证方式都是"手工 Playwright 脚本跑一次、跑完就删"，没有沉淀成可重复运行的测试。一旦记忆同步逻辑变成状态机式的规则（source/upsert/replace/重试），光靠"手工跑一次觉得对"不够可靠——这类代码最容易在边界情况出 bug（比如"本机暂存记录会不会被同源全量替换误删"，设计阶段就已经特意提醒过要小心）。iOS/web 两端各自实现同一套合并语义，测试用例集合至少应该对齐。
2. **可观测性**：现在同步成功没成功、提炼有没有生效、黑话词典有没有命中，用户端完全看不到任何痕迹。`MemoryView` 做完这批之后值得加：显示每条记忆的来源（`source` 字段现在有了，UI 上却看不到）、显示同步状态（已同步/待同步/失败）——不是给最终用户用的，是给开发调试用的，但不做的话，这些机制出问题会很难定位。
3. **人工纠错能力**：`MemoryView` 现在明确是只读的（代码注释原话），记忆提炼上线后这个缺口更紧迫——提炼这个动作本身会引入新的失真风险（讨论过：概括过度、编造），没有人工编辑/删除能力的话，这个风险没有兜底。
4. **通用词典的分发通道**：见上面"黑话词典与记忆提炼"一节，现有的 `/api/config` 只发过 voice 列表这种简单值，没有"版本化资源包"的概念，做词典分发时要顺带补上。
5. **文档跟实现的漂移检查**：每加一种新的 `source` 类型（agentnexus/local/以后可能的其他系统），需要一个地方集中记录"当前支持哪些 source、各自语义是什么"，不然容易出现"代码里冒出一个新 source 值，没人记得它是干嘛的"——README 落后于实际实现好几个版本这件事已经发生过一次，这批做完后应该专门检查一遍文档有没有跟上。
6. **两端实现对齐检查清单**：这次讨论期间好几次都是"先在 web 做、验证完再补 iOS，过程中发现漏了没同步"——这批工作量更大（来源字段、upsert、提炼逻辑都是两端要对齐的状态机代码），风险比之前更高，值得考虑做一份"两端功能对照表"当检查清单，不是每次靠记性。
7. **成本留意**：记忆提炼上线后，每轮对话都会多打一次 `qwen-turbo` 调用（异步、不阻塞体验，但是新增的持续性 API 调用量）。不是说现在要做限流或预算控制，只是这是一个新的成本项，做完这批之后应该心里有数、留意实际调用频率。
