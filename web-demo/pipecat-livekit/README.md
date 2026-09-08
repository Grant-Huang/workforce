# LiveKit + Pipecat 对比 Demo

与现有 `web-demo/`（Qwen Realtime WebSocket 端到端）并行的第二套实时语音方案，便于对比：

| | 现有 Demo (`8765`) | 本 Demo (`8766`) |
|---|---|---|
| **传输** | Browser WebSocket → Python relay → DashScope | Browser WebRTC → LiveKit SFU → Pipecat bot |
| **语音 AI** | Qwen3.5-Omni-Realtime（STT+LLM+TTS 一体） | DashScope STT + qwen-plus + CosyVoice TTS |
| **编排** | 前端事件驱动 + session.update | Pipecat frame pipeline |
| **VAD** | 服务端 server_vad | Silero VAD（本地） |

## 前置条件

1. **Qwen API Key** — 与现有 demo 共用仓库根目录 `.env` 里的 `QWEN_API_KEY`
2. **LiveKit Server** — 本地开发可用 dev 模式（默认凭据见下方）

## 快速启动（3 个终端）

### 终端 1：LiveKit Server

```bash
docker run --rm -p 7880:7880 -p 7881:7881 -p 7882:7882/udp \
  livekit/livekit-server --dev
```

Dev 模式默认凭据（已在 `.env.example` 中）：

- `LIVEKIT_URL=ws://127.0.0.1:7880`
- `LIVEKIT_API_KEY=devkey`
- `LIVEKIT_API_SECRET=secret`

### 终端 2：Pipecat Bot

```bash
cd web-demo/pipecat-livekit
pip install -r requirements.txt
python bot.py --room voicechat-compare
```

### 终端 3：对比 UI

```bash
cd web-demo/pipecat-livekit
python server.py
# 浏览器打开 http://127.0.0.1:8766/
```

点击「连接并开始」，允许麦克风。Bot 加入后会播放欢迎语。

## 环境变量

在仓库根 `.env` 追加（见 `.env.example`）：

```bash
# LiveKit（本地 dev 默认值）
LIVEKIT_URL=ws://127.0.0.1:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=secret
LIVEKIT_ROOM_NAME=voicechat-compare

# Pipecat pipeline 模型（可选）
PIPECAT_STT_MODEL=paraformer-realtime-v1
PIPECAT_LLM_MODEL=qwen-plus
PIPECAT_TTS_MODEL=cosyvoice-v3-flash
PIPECAT_TTS_VOICE=longxiaochun_v2
```

## Pipeline 结构

```
Browser (WebRTC)
    ↓
LiveKit SFU
    ↓
Pipecat LiveKitTransport.input()
    ↓
DashScopeSTTService          # 语音识别
    ↓
TranscriptBridge             # 转写推送到浏览器 data channel
    ↓
LLMContextAggregator (user)
    ↓
QwenLLMService               # qwen-plus（compatible-mode）
    ↓
DashScopeTTSV2Service        # CosyVoice TTS
    ↓
LiveKitTransport.output()
    ↓
LLMContextAggregator (assistant)
```

## 对比建议

1. **延迟**：同一句话从说完到听到回复的首包时间
2. **打断**：说话时能否自然 barge-in（Pipecat 依赖 Silero VAD + interruption frames）
3. **音质/自然度**：端到端 Omni vs 分离式 TTS
4. **稳定性**：WebSocket relay vs WebRTC（弱网、NAT）
5. **可扩展性**：Pipecat 可替换 STT/LLM/TTS 组件；Omni 是黑盒一体

## 文件说明

- `bot.py` — Pipecat + LiveKit 主 pipeline
- `server.py` — Token 签发 + 对比 UI（8766）
- `dashscope_services.py` — DashScope STT/TTS 封装
- `transcript_bridge.py` — 转写推送到浏览器
- `livekit.html` / `static/` — 对比前端

更详细的架构说明见 `docs/pipecat-livekit-comparison.md`。
