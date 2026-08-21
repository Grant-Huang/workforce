# 语音对话网页 Demo

跟 `VoiceChat/` 那个 iOS App 用的是同一套 Realtime API 协议（`session.update` / `input_audio_buffer.append` / `response.audio.delta` / …），做成网页版是因为 iOS App 没法在这个开发环境里编译运行，这个 demo 可以直接在浏览器里跑起来实测。

界面：类似 ChatGPT 主对话框——顶部状态栏、中间滚动的对话气泡、底部"开始对话"/"停止"两个按钮。按"开始对话"后是持续对话（服务端自动判断你说完没有），不是按住说话那种 push-to-talk；按"停止"结束整个会话。

## 为什么需要本地中转服务

浏览器原生 `WebSocket` API 不能自定义请求头，没法直接带 `Authorization: Bearer <key>` 连 DashScope。所以 `server.py` 起一个本地服务：网页连 `ws://127.0.0.1:8765/ws`（不需要认证），`server.py` 再拿着 `.env` 里的 Key 去连真正的 DashScope 地址，两边转发消息。**Key 全程只在这个 Python 进程里，不会出现在浏览器/前端代码里。**

## 运行

```bash
cd web-demo
pip install -r requirements.txt
python3 server.py
```

然后浏览器打开 `http://127.0.0.1:8765/`，点"开始对话"，允许麦克风权限，说话试试。

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

**没验证到的部分**：这个开发环境没有真实麦克风，用 Playwright + Chromium 的"伪造音频设备"（一段合成的静音/音调）跑通了整条链路——WebSocket 握手、`session.update` 被接受、音频分片能正常发送、连接能正常关闭——但因为没有真实语音内容，没能触发 VAD 识别出"你说完了"，所以没能验证到语音识别准确率、回复内容质量、真实语音下的打断体验。这些需要你在自己电脑上用真麦克风实测。

## 文件说明

- `server.py` — 中转服务（aiohttp）：serve 静态文件 + `/api/config`（把音色等非敏感配置给前端）+ `/ws`（转发到 DashScope）。
- `index.html` / `static/styles.css` — 页面结构和样式。
- `static/app.js` — 核心逻辑：麦克风采集（`ScriptProcessorNode`，降采样到 16kHz PCM16）、流式播放（24kHz PCM16 顺序调度播放）、WebSocket 事件收发、气泡渲染。

## 已知限制

- `ScriptProcessorNode` 已经是浏览器标记为 deprecated 的 API（但仍被广泛支持），更现代的写法是 `AudioWorkletNode`，demo 图简单没换。
- 只做了自动轮流对话（`create_response: true`），没有实现 iOS App 里那套"先查本地记忆再手动触发回复"的逻辑——这个 demo 单纯是用来验证语音链路本身，记忆那部分逻辑在 iOS 项目里。
- 没有断线重连、没有错误重试。
