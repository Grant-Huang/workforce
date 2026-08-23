# 测试与部署指南

这个仓库目前有两个可运行的东西：`VoiceChat/`（iOS App，源码在仓库里，但工程文件要在 Mac 上本地生成）和 `web-demo/`（网页版，随时能跑，用来验证 Realtime API 协议本身）。这份文档只讲"怎么跑起来测试"，架构设计看根目录 `README.md` 和 `web-demo/README.md`。

## iOS App

### 前置条件

- 一台 **Mac**（iOS 开发离不开 Xcode，Windows/Linux 编译不了）
- Xcode 15 或更高版本（App Store 装）
- [XcodeGen](https://github.com/yonaskolb/XcodeGen)：`brew install xcodegen`
- 一个 Apple ID（免费的就行，不需要付费开发者账号，区别只是免费账号生成的证书 7 天过期，到期后在 Xcode 里重新 Run 一次就行）
- 一个有 Qwen-Audio-Realtime / Qwen-Omni-Realtime 权限的 DashScope/百炼 API Key
- 一部 iPhone（真机测试；模拟器麦克风体验差，不建议）

### 生成并运行工程

```bash
git clone <本仓库地址>
cd workforce
git checkout claude/voice-input-cross-platform-app-pxo3yp   # 或者已合并到 main 后直接用 main
xcodegen generate
open VoiceChat.xcodeproj
```

`VoiceChat.xcodeproj` 不在 git 里（`.gitignore` 排除了），每次拉新代码后如果 `project.yml` 有变化都要重新跑一次 `xcodegen generate`。

在 Xcode 里：

1. 选中 `VoiceChat` target → **Signing & Capabilities** → Team 选你的 Apple ID。
2. 用 USB 或同 WiFi 连上你的 iPhone，Xcode 顶部运行目标选你的手机。
3. `Cmd+R` 编译运行。
4. 手机上第一次打开会提示"不受信任的开发者"：设置 → 通用 → VPN与设备管理 → 信任一下对应的开发者证书。

### 首次配置

1. App 打开后点右上角齿轮图标进入设置。
2. 填入 API Key（保存到 Keychain，不会离开设备，除了发给 Qwen 的请求）。
3. **强烈建议填写"业务空间（Workspace ID）"**——留空时用共享域名，实测有过持续多天的稳定性问题（`session.update` 无响应）；填了之后自动切到专属域名，问题消失。获取方式和踩过的坑详见 `docs/qwen-realtime-voice-setup.md`。
4. 模型/音色已经预填好实测过的默认值（`qwen3.5-omni-flash-realtime` + `Ethan`），音色可以在设置页的下拉菜单里换成另外 13 个测过能用的选项；一般不用改地址，除非哪天这些值又变了（Qwen 这条产品线迭代较快，建议先看上面那份配置指南文档）。
5. 点麦克风开始对话，点"记忆"图标（左上角大脑）能看到 App 目前记住的内容。

### 常见问题

| 现象 | 大概率原因 |
|---|---|
| 连不上 / 一直"连接中" | API Key 错、或设置页地址/模型名跟你控制台里的对不上 |
| 能连上但没声音 | 检查手机静音开关；检查 `AudioIOManager` 里输出采样率是否还是 24kHz（如果换了模型这个可能要跟着改） |
| 完全没反应，连"连接中"都没出现 | 麦克风权限没给，去 设置 → 隐私 → 麦克风 里确认 App 有权限 |
| 证书过期提示 | 免费 Apple ID 证书 7 天有效期，Xcode 里重新 Run 一次即可 |

### ⚠️ 关于"测试过了吗"的诚实说明

这个开发环境是无 GUI 的 Linux 容器，没有 Xcode、没有 macOS、没有 Swift 编译器——**iOS App 的 Swift 代码从写出来到现在，从未在任何环境里实际编译或运行过**。这一点很重要，不要被"代码写完了"误认为"App 能跑了"。

实际验证过的东西分三层，覆盖范围逐层变窄：

1. **Qwen Realtime API 协议本身**（域名、模型、音色、`session.update` 时序）——用 Python `websockets` 脚本直连 Qwen 测过，这层的结论是可靠的，跟客户端用什么语言无关。
2. **完整的交互设计**（专属域名、Flash 模型、音色选择器、连接生命周期规则）——在 **web demo（JS 实现）** 上用 Playwright 做过真实浏览器端到端测试，这层的"设计对不对"是验证过的。
3. **iOS 的 Swift 源码本身**——只做过手工数括号配对，确认没有明显语法失衡，**没有做过真正的编译检查**，SwiftUI 的具体写法（`Picker`、`Form`/`Section` 嵌套、新加的 `RealtimeConfigStore.effectiveWorkspaceURL` 等静态方法调用）有没有问题完全未知。

**第一次在 Mac 上跑 `xcodegen generate` + Xcode 编译，请当成这份代码的"首次真实测试"，大概率需要修一些小问题，不是"应该没问题、编译一下确认而已"。**

## App Store 上架清单

这个 App 目前是"能在自己手机上跑起来"的原型状态，离"能公开上架"还有一段距离。上架前需要过一遍这份清单，按顺序来。

### 0. 先想清楚：要不要现在就走公开上架

现在的架构是**用户自己申请阿里云 API Key、自己填进设置页**——这个模式对开发者自己用、或者小范围分享给懂技术的朋友完全没问题，但对公开上架给普通用户是不现实的（普通用户不会去开通阿里云账号）。如果只是想让自己和身边人能装上用，**TestFlight 内部测试**（最多 100 人，不需要过 App Store 审核，几乎是"发个链接就能装"）大概率已经够用，可以跳过下面大部分步骤。真要走公开上架，请先看第 3 条的架构问题。

### 1. 账号与证书

- [ ] 注册 **Apple Developer Program**（$99/年，个人或公司账号均可），跟目前用的免费 Apple ID 是两回事
- [ ] 在 App Store Connect 里生成好 Distribution 证书和 Provisioning Profile（Xcode 登录付费账号后大多能自动处理）
- [ ] 确认 Bundle Identifier（`project.yml` 里定义）在 App Store Connect 里注册好，且后续不能随便改

### 2. App Store Connect 素材

- [ ] App 名称、副标题、分类（大概率是"工具"或"效率"）
- [ ] App 图标：1024×1024 PNG，不能有透明通道
- [ ] 各尺寸截图（至少 6.7 寸机型一套，App Store Connect 会列出具体要求的尺寸）
- [ ] App 描述、关键词、支持网址
- [ ] **隐私政策 URL**（必填）——App 用到麦克风和网络请求（语音发给 Qwen），审核会要求说明数据怎么处理、存在哪、是否第三方共享
- [ ] App Privacy 问卷（App Store Connect 里的"隐私"标签页）：如实填写麦克风数据、对话内容的收集/使用情况

### 3. 代码/架构层面必须处理的事

- [ ] **API Key 管理方式要改**：现在是用户自己填 Key 存本机 Keychain，`SettingsView.swift` 里已经有一句提醒"发布上架前请改为后端下发临时令牌，避免把正式密钥打包进 App"——这不是小改动，需要一个你自己管控的后端（`web-demo/server.py` 现在的中转逻辑就是雏形）来代理请求，用户不直接接触 Qwen 的 Key
- [ ] 麦克风权限说明文案（`Info.plist` 里的 `NSMicrophoneUsageDescription`，`project.yml` 里定义）要写清楚"用来做什么"，审核对这个卡得比较严
- [ ] 检查 App 里有没有开发调试用的东西不该带到生产版本（比如 Mock 数据、测试用的固定音色列表要不要精简、日志里会不会打印敏感信息）
- [ ] 考虑要不要加崩溃收集/基础的可观测性（不是强制，但公开上架后你需要知道用户端出了什么问题）

### 4. 提交前自查

- [ ] 在真机上（不是模拟器）完整走一遍：首次打开引导、权限申请、语音对话、文字对话、记忆功能、设置页所有字段
- [ ] 断网/弱网场景下 App 不能白屏崩溃，至少要有清晰的错误提示（连接生命周期那块的错误提示逻辑已经做了，需要在真实网络环境里过一遍）
- [ ] 过一遍 [App Store 审核指南](https://developer.apple.com/app-store/review/guidelines/)，尤其关注跟"用户生成内容""第三方 API 依赖""数据收集"相关的条款
- [ ] 确认没有硬编码任何测试用的密钥/密码到仓库或者打包进 App

### 5. 提交与审核

- [ ] Xcode 里 Product → Archive，验证通过后 Distribute App → App Store Connect
- [ ] 在 App Store Connect 里选择这个构建版本，走完提交流程
- [ ] 首次审核一般 1-3 天；语音类 App 因为涉及麦克风权限，审核时会重点看权限说明文案是否清楚、App 的核心功能是否名副其实
- [ ] 被拒绝很常见，仔细看拒绝理由（Resolution Center 里会写明），修完重新提交不需要重新排队太久

## 网页 Demo（`web-demo/`）

不需要 Mac，任何装了 Python 的机器都能跑，适合快速验证协议本身、不想每次都编译 iOS 的时候用。

```bash
cd web-demo
pip install -r requirements.txt
python3 server.py
```

浏览器打开 `http://127.0.0.1:8765/`，允许麦克风权限，点"开始对话"。Key 从仓库根目录的 `.env` 读取（`QWEN_API_KEY=...`），照着 `.env.example` 建一份，不要提交到 git。

这个 demo 只做基础的自动轮流对话（没有接 iOS App 里那套"本地记忆检索"逻辑），单纯用来验证语音链路和协议细节，细节和已经跑过的连通性测试记录见 `web-demo/README.md`。

## 尚未覆盖的部分

这两个环境都没有真实麦克风/扬声器可用（这个开发环境是无 GUI 的 Linux 容器），所以下面这些必须靠你在自己设备上实测，目前完全没验证过：

- 真实语音的识别准确率
- 回复内容质量、语气
- 打断体验（协议层已确认 Qwen 支持 `interrupt_response`，但阈值/灵敏度这些参数没调过）
- 后台运行的稳定性
- 弱网/断线重连behavior（目前没做自动重连）

## 以后：Android

计划是先用 [Capacitor](https://capacitorjs.com/) 把 `web-demo/` 的网页壳打包成 Android（和 iOS）App，复用已经写好的 JS 音频/协议逻辑，而不是把 Swift 代码整个翻译成 Kotlin。这部分还没开始，等 iOS 这边稳定下来再启动，届时这份文档会补上对应的构建步骤。
