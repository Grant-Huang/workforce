# VoiceChat

一个 iOS App 原型：像 ChatGPT 语音模式一样，实时说话、实时听 AI 回复，支持说话打断（barge-in）。

## 架构

```
麦克风(AVAudioEngine) → 转 24kHz/16bit/单声道 PCM → WebSocket
                                                        │
                                            OpenAI Realtime API
                                                        │
        扬声器(AVAudioEngine) ← PCM 音频流 ← 流式回复(文字+语音)
```

- `VoiceChat/Audio/AudioIOManager.swift` — 麦克风采集、格式转换、流式播放、打断时清空播放队列。
- `VoiceChat/Realtime/RealtimeClient.swift` — 到 OpenAI Realtime API 的 WebSocket 连接与事件收发。
- `VoiceChat/Realtime/RealtimeModels.swift` — 用到的 Realtime API 事件的最小子集封装。
- `VoiceChat/ConversationViewModel.swift` — 把音频层和网络层粘合起来的状态机（聆听中/助手说话中/打断/出错）。
- `VoiceChat/Views/ConversationView.swift` — 对话界面（转写气泡 + 麦克风按钮）。
- `VoiceChat/Settings/` — API Key 输入与 Keychain 存取。

服务端语音活动检测（`server_vad`）会在你开口说话时发出 `input_audio_buffer.speech_started` 事件；App 收到后立刻停止播放并取消当前回复，实现"打断"效果。

## 环境要求

- 一台 Mac，安装 Xcode 15 或更高版本
- [XcodeGen](https://github.com/yonaskolb/XcodeGen)：`brew install xcodegen`
- 一个有 Realtime API 权限的 OpenAI API Key

## 首次运行

```bash
xcodegen generate
open VoiceChat.xcodeproj
```

在 Xcode 里：

1. 选中 `VoiceChat` target → Signing & Capabilities，设置你的开发者账号（Team）。
2. 用真机运行（麦克风在模拟器上体验不佳，建议用真机）。
3. App 启动后点右上角齿轮图标，粘贴你的 OpenAI API Key，保存。
4. 点击麦克风按钮开始对话；再次点击或说话打断都能停止/继续。

## 已知限制 / 后续可做的事

- **API Key 安全**：当前是把 Key 直接存在设备 Keychain、App 直连 OpenAI。这对个人测试没问题，但如果要上架 App Store 或给别人用，必须换成后端下发"临时令牌"的方案（`POST /v1/realtime/sessions`），避免长期有效的正式 Key 被人从 App 里提取出来滥用。
- **断线重连**：目前网络断开后不会自动重连，只是把状态切回错误提示。
- **后台运行**：Info.plist 已声明 `audio` 后台模式，但退到后台后的长时间对话稳定性还没有针对性测试。
- **模型/语音可配置化**：`ConversationViewModel` 里的 `systemInstructions` / `voice` 目前是硬编码，后续可以加到设置页里给用户自己调。
- **多语言**：System instructions 目前是中文助手人设，可按需替换。

## 验证

这个开发环境是 Linux 容器，没有 macOS/Xcode，无法在这里编译或跑模拟器验证。请在你的 Mac 上按上面步骤 `xcodegen generate` 后用 Xcode 打开、编译，跑一遍完整对话流程（包括中途打断）来验证。
