# LiveKit + Pipecat 对比 Demo

与现有 `web-demo/`（Qwen Realtime WebSocket 端到端）并行的第二套实时语音方案：

| | 现有 Demo (`8765`) | 本 Demo (`8766`) |
|---|---|---|
| **传输** | Browser WebSocket → Python relay | Browser **WebRTC → LiveKit Cloud** → Pipecat bot |
| **语音 AI** | Qwen Omni 端到端 | **本地** SenseVoice + Qwen2.5 + Qwen TTS |
| **编排** | 前端事件驱动 | Pipecat frame pipeline |

## 前置条件

1. **[LiveKit Cloud](https://cloud.livekit.io/)** 项目：`LIVEKIT_URL` / `API Key` / `Secret`
2. **本地模型**：SenseVoice、llama-server（Qwen2.5）、Qwen3-TTS
3. **依赖**：

```bash
cd web-demo/pipecat-livekit
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r ../pipecat-local-agent/requirements-local-mac.txt
```

### 本地服务（Mac 上需先启动）

```bash
# llama-server（Qwen2.5）
./llama-server -m ~/models/qwen2.5-7b-instruct-q4_k_m.gguf --host 127.0.0.1 --port 8080
```

**不需要**本地 `livekit-server`；信令与 SFU 均在 LiveKit Cloud。

## 快速启动

### 1. 配置 `.env`（仓库根目录）

```bash
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
LIVEKIT_ROOM_NAME=voicechat

LOCAL_STT_BACKEND=sensevoice
LOCAL_LLM_BACKEND=llamacpp
LOCAL_LLM_BASE_URL=http://127.0.0.1:8080/v1
LOCAL_TTS_BACKEND=qwen3_tts
```

### 2. 启动 8766 UI

```bash
cd web-demo/pipecat-livekit
source .venv/bin/activate
python server.py
# 浏览器 http://127.0.0.1:8766/
```

连接后 `server.py` 自动拉起 `bot.py` 加入 **LiveKit Cloud** 同一房间。

### Mac 本地 Agent 模式

```
http://127.0.0.1:8766/?ui=local
```

两种拉起方式：

```bash
# A. 手动
python ../pipecat-local-agent/pipecat_agent.py --room <房间名>

# B. 可选 HTTP 唤醒（可用 Cloudflare Tunnel 暴露此 API，勿代理 LiveKit）
curl -X POST 'http://127.0.0.1:8766/api/agent/wake?room=<房间名>'
# 若配置了 AGENT_WAKE_TOKEN：
curl -X POST -H "Authorization: Bearer $AGENT_WAKE_TOKEN" \
  'http://127.0.0.1:8766/api/agent/wake?room=<房间名>'
```

## Pipeline

```
Browser ──WebRTC──► LiveKit Cloud ◄──WebRTC── Pipecat bot (Mac)
                                              SenseVoice → Qwen2.5 → Qwen TTS
```

## 文件

- `bot.py` — Pipecat pipeline（本地 STT/LLM/TTS，compare 模式）
- `server.py` — Token + 静态页 + `/api/agent/wake`（8766）
- `bot_manager.py` — bot / agent 子进程管理
- `livekit_env.py` — LiveKit Cloud 配置校验
- `livekit.html` / `static/` — 前端
- `archive/start-livekit.sh` — **已归档**：旧本地 `livekit-server --dev`，请勿用于当前架构
