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
