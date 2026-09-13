# LiveKit + Pipecat 对比 Demo

与现有 `web-demo/`（Qwen Realtime WebSocket 端到端）并行的第二套实时语音方案，便于对比：

| | 现有 Demo (`8765`) | 本 Demo (`8766`) |
|---|---|---|
| **传输** | Browser WebSocket → Python relay → DashScope | Browser WebRTC → LiveKit SFU → Pipecat bot |
| **语音 AI** | Qwen3.5-Omni-Realtime（STT+LLM+TTS 一体） | **本地** SenseVoice STT + Qwen2.5 LLM + Qwen TTS |
| **编排** | 前端事件驱动 + session.update | Pipecat frame pipeline |
| **VAD** | 服务端 server_vad | Silero VAD（本地） |

## 前置条件

1. **本地模型栈** — 见仓库根 `.env` 中 `LOCAL_*`（SenseVoice、llama-server、Qwen3-TTS）
2. **LiveKit Server** — 本地 dev 或 LiveKit Cloud
3. **Pipecat + 本地依赖**：

```bash
cd web-demo/pipecat-livekit
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r ../pipecat-local-agent/requirements-local-mac.txt
```

### 本地服务（Mac 上需先启动）

```bash
# 终端 A — llama-server（Qwen2.5）
./llama-server -m ~/models/qwen2.5-7b-instruct-q4_k_m.gguf --host 127.0.0.1 --port 8080

# SenseVoice / Qwen TTS 由 bot 进程内按需加载（LOCAL_STT_DEVICE / LOCAL_TTS_DEVICE=mps）
```

## 快速启动（2 个终端）

### 终端 1：LiveKit Server

```bash
livekit-server --dev --bind 127.0.0.1
# 或 ./start-livekit.sh
```

Dev 凭据：`devkey` / `secret`，URL：`ws://127.0.0.1:7880`

### 终端 2：对比 UI（会自动启动 Pipecat bot）

```bash
cd web-demo/pipecat-livekit
source .venv/bin/activate
python server.py
# 浏览器 http://127.0.0.1:8766/
```

点击「连接并开始」后，`server.py` 会**自动拉起 `bot.py`**，使用本地 pipeline。

### Mac 本地 Agent 模式（LiveKit Cloud）

```
http://127.0.0.1:8766/?ui=local
```

此模式不自动 spawn bot，需运行 `web-demo/pipecat-local-agent/pipecat_agent.py`。  
详见 [`../pipecat-local-agent/README.md`](../pipecat-local-agent/README.md)。

## 环境变量（仓库根 `.env`）

```bash
LIVEKIT_URL=ws://127.0.0.1:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=secret
LIVEKIT_ROOM_NAME=voicechat-compare

LOCAL_STT_BACKEND=sensevoice
LOCAL_STT_MODEL=iic/SenseVoiceSmall
LOCAL_STT_DEVICE=mps

LOCAL_LLM_BACKEND=llamacpp
LOCAL_LLM_MODEL=qwen2.5-7b-instruct
LOCAL_LLM_BASE_URL=http://127.0.0.1:8080/v1

LOCAL_TTS_BACKEND=qwen3_tts
LOCAL_TTS_MODEL=Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice
LOCAL_TTS_DEVICE=mps
LOCAL_TTS_VOICE=Cherry
LOCAL_TTS_LANGUAGE=Chinese
# LOCAL_TTS_MODEL_PATH=/path/to/local/qwen3-tts   # 可选：本地权重目录
```

任意组件可设 `*_BACKEND=dashscope` + `QWEN_API_KEY` 作为云端兜底。

## Pipeline 结构

```
Browser (WebRTC)
    ↓
LiveKit SFU
    ↓
Pipecat LiveKitTransport.input()
    ↓
SenseVoiceSTTService           # 本地 STT
    ↓
TranscriptBridge
    ↓
LLMContextAggregator (user)
    ↓
OpenAILLMService               # llama-server → Qwen2.5
    ↓
QwenLocalTTSService              # 本地 Qwen3-TTS
    ↓
LiveKitTransport.output()
    ↓
LLMContextAggregator (assistant)
```

## 文件说明

- `bot.py` — Pipecat + LiveKit 主 pipeline（复用 `pipecat-local-agent/local_services`）
- `server.py` — Token 签发 + 对比 UI（8766）
- `dashscope_services.py` — 云端兜底 STT/TTS（`LOCAL_*_BACKEND=dashscope` 时使用）
- `transcript_bridge.py` — 转写推送到浏览器
- `livekit.html` / `static/` — 对比前端

更详细的架构说明见 `docs/pipecat-livekit-comparison.md`。
