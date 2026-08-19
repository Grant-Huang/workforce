# VoiceChat

一个 iOS App 原型：像 ChatGPT 语音模式一样，实时说话、实时听 AI 回复。默认对接 **Qwen-Audio-Realtime**（阿里云 DashScope/百炼），也可以切换回 OpenAI Realtime API——两者的 WebSocket 事件协议基本一致，切换只需改设置里的地址/模型名（以及 `AudioIOManager` 里的输入采样率，见下文）。

## 架构

```
麦克风(AVAudioEngine) → 转 16kHz/16bit/单声道 PCM → WebSocket
                                                        │
                                        Qwen-Audio-Realtime（或 OpenAI Realtime API）
                                                        │
        扬声器(AVAudioEngine) ← 24kHz PCM 音频流 ← 流式回复(文字+语音)

                用户转写文本到达 ──► 本地记忆检索(MemoryStore) ──► 命中的话通过
                                                                  conversation.item.create
                                                                  把背景信息塞回会话
                                                                  再发 response.create
```

- `VoiceChat/Audio/AudioIOManager.swift` — 麦克风采集、格式转换、流式播放。输入输出采样率**不对称**：上行 16kHz，下行 24kHz（这是 Qwen 的要求；OpenAI 是上下行都 24kHz，切换时需要改这里）。
- `VoiceChat/Realtime/RealtimeClient.swift` — WebSocket 连接与事件收发，地址/模型可配置。
- `VoiceChat/Realtime/RealtimeModels.swift` — 用到的 Realtime API 事件的最小子集封装（`session.update` / `input_audio_buffer.append` / `response.audio.delta` / `conversation.item.create` / `response.create` 等，OpenAI 和 Qwen 共用同一套事件命名）。
- `VoiceChat/Memory/MemoryStore.swift` — **本地记忆**：每句用户说的话转写后都存一条（JSON 文件），下次提问时按关键词重合度 + 时间新鲜度检索最相关的几条，喂给模型当背景信息。这是验证"本地积累的记忆能不能被语音快速总结出来"这个概念用的最简实现，以后如果这个 App 并入自书，直接把 `MemoryStore` 换成读写自书共享记忆的实现即可，`add`/`search` 是替换的接缝。
- `VoiceChat/ConversationViewModel.swift` — 把音频层、网络层、记忆层粘合起来的状态机（聆听中/助手说话中/出错），`groundAndRespond(to:)` 是"收到用户问题 → 查记忆 → 注入背景 → 触发回复"这条链路的入口。
- `VoiceChat/Views/ConversationView.swift` / `MemoryView.swift` — 对话界面（转写气泡 + 麦克风按钮）+ 记忆列表页（左上角大脑图标，方便直接看到 App 记住了什么、命中了什么，用来验证效果）。
- `VoiceChat/Settings/` — API Key（Keychain）+ 连接地址/模型/音色（UserDefaults）。

**响应时机是手动控制的**：`session.update` 里把 `turn_detection.create_response` 设成了 `false`（对应 OpenAI Realtime API 的同名开关，Qwen 大概率沿用了同一字段，但需要你实测确认），服务端还是会自动判断"用户说完了"并提交音频缓冲区，但**不会**自动开始生成回复。真正触发回复的是 `ConversationViewModel.groundAndRespond`：拿到用户转写文本后，先查本地记忆、把命中的内容通过 `conversation.item.create` 塞进对话，再发 `response.create`。这样才保证模型说话前一定已经看到了检索到的背景信息，不会有"模型已经开始回答了，记忆才注入进去"的时序问题。如果实测发现 Qwen 不认 `create_response: false`（还是会自动回复），退路是把 `turnDetection` 改成 `nil`（推空值即手动模式/push-to-talk），自己控制何时提交音频。

代码里也接了"打断"逻辑（收到 `input_audio_buffer.speech_started` 就清空播放队列 + 取消当前回复），但打断体验还没有针对 Qwen 实测调优。

## 关于模型/地址：请对照控制台核实

Qwen 的实时语音 API 这块产品线变化较快，文档里出现过好几个相近但不完全一致的地址/型号：

- `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model=qwen-audio-3.0-realtime-plus`（本项目默认，来自"Qwen-Audio 实时语音对话模型"文档）
- `qwen-omni-turbo-realtime`、`qwen3.5-omni-plus-realtime` / `qwen3.5-omni-flash-realtime` 等（DashScope Model Studio 的其他型号/计费页出现过）

设置页里的"连接设置"就是让你不用改代码即可核对/切换这些值。`RealtimeConfigStore.defaultBaseURL` 里的 `YOUR_WORKSPACE_ID` 需要替换成你在百炼控制台里的工作空间 ID。

认证方式和 OpenAI 一致：`Authorization: Bearer <API Key>`（握手阶段校验）。

## 环境要求

- 一台 Mac，安装 Xcode 15 或更高版本
- [XcodeGen](https://github.com/yonaskolb/XcodeGen)：`brew install xcodegen`
- 一个 DashScope/百炼的 API Key（有 Qwen-Audio-Realtime / Qwen-Omni-Realtime 权限）

## 首次运行

```bash
xcodegen generate
open VoiceChat.xcodeproj
```

在 Xcode 里：

1. 选中 `VoiceChat` target → Signing & Capabilities，设置你的开发者账号（Team）。
2. 用真机运行（麦克风在模拟器上体验不佳，建议用真机）。
3. App 启动后点右上角齿轮图标：填入 API Key，核对/填写连接地址（含你的 WorkspaceId）和模型名，保存。
4. 点击麦克风按钮开始对话。

## 已知限制 / 后续可做的事

- **`create_response: false` 未实测**：整个"先查记忆再回答"的时序都依赖这个开关生效，是这一版最该优先验证的点（见上面架构里的说明）。
- **记忆检索很朴素**：关键词重合度 + 时间新鲜度打分，没有语义理解，同义表达搜不到。够验证概念，真要好用大概率需要换成向量检索（比如用 Apple 的 `NaturalLanguage` 框架做本地 embedding，或者接一个向量库）。
- **记忆不做筛选/摘要**：现在是"用户说的每句话都存"，闲聊、口头禅也会被记下来，列表会很快变得杂乱。可以加一层"只记录看起来像事实/想法的内容"的过滤，或定期跑摘要压缩。
- **未来接自书**：现在 `MemoryStore` 是纯本地 JSON 文件，接口只有 `add`/`search` 两个方法。以后如果这个 App 内置进自书、跟自书共享记忆，直接换一个实现（读写自书的存储）替掉 `MemoryStore` 就行，不需要改 `ConversationViewModel` 里的调用逻辑。
- **API Key 安全**：当前是把 Key 直接存在设备 Keychain、App 直连服务端。这对个人测试没问题，但如果要上架 App Store 或给别人用，必须换成后端下发"临时令牌"的方案，避免长期有效的正式 Key 被人从 App 里提取出来滥用。
- **打断体验未验证**：`speech_started` → 清空播放/取消回复的逻辑已经写了，但还没有针对 Qwen 实测调优（阈值、灵敏度等）。
- **断线重连**：目前网络断开后不会自动重连，只是把状态切回错误提示。
- **后台运行**：Info.plist 已声明 `audio` 后台模式，但退到后台后的长时间对话稳定性还没有针对性测试。
- **本地部署（Qwen2.5-Omni 跑在你自己 Mac mini 上）**：如果之后想换成完全本地、不经过阿里云的方案，Qwen2.5-Omni（3B/7B 稠密模型）在 Apple Silicon 上用 transformers + MPS 后端是可行的，但没有官方现成的 Realtime WebSocket 服务，需要额外写一个 Python 服务把推理包成本项目能对接的协议；这是一条独立于当前方案的后续工作，先不做。

## 验证

这个开发环境是 Linux 容器，没有 macOS/Xcode，无法在这里编译或跑模拟器验证。请在你的 Mac 上按上面步骤 `xcodegen generate` 后用 Xcode 打开、编译，跑一遍完整对话流程来验证；如果连不上，先检查设置页里的地址/模型名是否和你控制台里的一致。
