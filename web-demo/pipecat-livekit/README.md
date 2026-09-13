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
2. **LiveKit Server** — 本地已安装或 Docker dev 模式均可
3. **Pipecat** — 通过本项目 `requirements.txt` 安装（若你全局已装 Pipecat，建议仍用 venv 隔离版本）

## 快速启动（2 个终端）

### 终端 1：LiveKit Server

**方式 A — 本机已安装（推荐）**

```bash
livekit-server --dev --bind 127.0.0.1
# 或
./start-livekit.sh   # 自动检测 PATH 里的 livekit-server
```

**方式 B — Docker**

```bash
docker run --rm -p 7880:7880 -p 7881:7881 -p 7882:7882/udp \
  livekit/livekit-server --dev
```

Dev 模式凭据（与 `.env` 默认一致）：`devkey` / `secret`，URL：`ws://127.0.0.1:7880`

### 终端 2：对比 UI（会自动启动 Pipecat bot）

```bash
cd web-demo/pipecat-livekit
python3 -m venv .venv && source .venv/bin/activate   # 推荐，避免与全局 Pipecat 版本冲突
pip install -r requirements.txt
python server.py
# 浏览器打开 http://127.0.0.1:8766/
```

点击「连接并开始」后，`server.py` 会**自动拉起 `bot.py` 子进程**加入同一房间，无需再开第三个终端。

### Mac 本地 Agent 模式（复用本前端）

同一套 `livekit.html`，加 `?ui=local` 即可测试 Mac Mini 出站 Agent（LiveKit Cloud）：

```
http://127.0.0.1:8766/?ui=local
```

此模式下 **不会自动 spawn bot**，需先在 Mac 上运行 `web-demo/pipecat-local-agent/pipecat_agent.py`。  
详见 [`../pipecat-local-agent/README.md`](../pipecat-local-agent/README.md)。

若需手动调试 bot（使用你已安装的 Pipecat 环境）：

```bash
cd web-demo/pipecat-livekit
source .venv/bin/activate   # 或你的 Pipecat 虚拟环境
export LIVEKIT_URL=ws://127.0.0.1:7880
export LIVEKIT_API_KEY=devkey
export LIVEKIT_API_SECRET=secret
python bot.py --room voicechat-compare
```

## 本机安装验证

```bash
# LiveKit
livekit-server --version
curl http://127.0.0.1:7880/          # 启动后应返回 OK

# Pipecat（在项目 venv 内）
python -c "import pipecat; print(pipecat.__version__)"
python -c "from pipecat.transports.livekit.transport import LiveKitTransport; print('livekit extra ok')"
```

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
