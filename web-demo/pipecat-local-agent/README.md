# Mac Mini 本地 Pipecat Agent（LiveKit Cloud 出站）

手机/浏览器通过 **LiveKit Cloud** 走 WebRTC；Mac Mini 在局域网内 **出站** 连 LiveKit，无需公网 IP、无需开放 UDP 端口。

```
[手机/网页] ──WebRTC──> LiveKit Cloud
                            ▲
                            │ 出站 WebSocket + 媒体
                            ▼
┌──────────────────────────────────────┐
│ Mac Mini（局域网）                    │
│  pipecat_agent.py                    │
│    ├─ SenseVoice STT    (MPS)        │
│    ├─ LanceDB 记忆       (本地向量)   │
│    ├─ Qwen2.5 LLM       (llama.cpp)  │
│    └─ Qwen3-TTS         (MPS)        │
└──────────────────────────────────────┘
```

### Cloudflare 用法（重要）

| 流量 | 走哪里 |
|------|--------|
| 前端页面 `https://chat.yourdomain.com` | **可以** Cloudflare Tunnel |
| LiveKit `wss://<project>.livekit.cloud` | **必须直连**，不要 Tunnel 代理 |
| Mac Agent 出站连接 | 直连 LiveKit Cloud |

详见 [`docs/pipecat-local-agent-mac-ops.md`](../../docs/pipecat-local-agent-mac-ops.md)。

## 快速开始（Mac Mini）

### 1. LiveKit Cloud

1. 在 [LiveKit Cloud](https://cloud.livekit.io/) 创建项目  
2. 记下 `wss://xxx.livekit.cloud`、`API Key`、`API Secret`  
3. 前端/App 用 LiveKit SDK 连同一 room；Mac 上跑 agent 加入同一 room  

### 2. 本地 LLM（llama.cpp / llama-server）

LLM 使用 **llama-server** 独立进程（OpenAI 兼容 HTTP），与 Pipecat 内 SenseVoice/TTS 分离：

```bash
# 示例：启动 llama-server（路径按你的 llama.cpp 安装调整）
./llama-server \
  -m ~/models/qwen2.5-7b-instruct-q4_k_m.gguf \
  --host 127.0.0.1 --port 8080
```

`.env` 默认：

```bash
LOCAL_LLM_BACKEND=llamacpp
LOCAL_LLM_MODEL=qwen2.5-7b-instruct
LOCAL_LLM_BASE_URL=http://127.0.0.1:8080/v1
```

### 3. 环境变量（仓库根 `.env`）

```bash
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
LIVEKIT_ROOM_NAME=voicechat-local
LIVEKIT_AGENT_IDENTITY=Pipecat Local Agent

LOCAL_STT_BACKEND=sensevoice
LOCAL_STT_MODEL=iic/SenseVoiceSmall
LOCAL_STT_DEVICE=mps

LOCAL_LLM_BACKEND=llamacpp
LOCAL_LLM_MODEL=qwen2.5-7b-instruct
LOCAL_LLM_BASE_URL=http://127.0.0.1:8080/v1

LOCAL_TTS_BACKEND=qwen3_tts       # 或 dashscope 云端兜底
LOCAL_TTS_MODEL=qwen3-tts
LOCAL_TTS_DEVICE=mps
LOCAL_TTS_VOICE=Cherry

LOCAL_MEMORY_ENABLED=1
LANCEDB_PATH=./data/lancedb
```

### 4. 安装并运行 Agent

```bash
cd web-demo/pipecat-local-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-local-mac.txt

# 实现 TTS：编辑 local_services/tts_runtime.py 接入你的 Qwen3-TTS
# 或先用云端兜底：LOCAL_TTS_BACKEND=dashscope + QWEN_API_KEY
```

### 5. 前端测试（复用 workforce LiveKit 页面 8766）

浏览器 UI 与 LiveKit 对比 demo **共用** `web-demo/pipecat-livekit/` 前端，不单独起 8767。

```bash
# 终端 1 — llama-server（见上文）
# 终端 2 — Agent 加入 LiveKit Cloud 房间
python pipecat_agent.py --room voicechat-local

# 终端 3 — 复用 pipecat-livekit 前端（仅签发用户 token，不 spawn bot）
cd ../pipecat-livekit
python server.py
# 浏览器 http://127.0.0.1:8766/?ui=local
```

页面顶部切换器可选「Mac 本地 Agent」，或直接访问 `?ui=local`。

## 模型后端（环境变量）

| 组件 | 变量 | 可选值 |
|------|------|--------|
| STT | `LOCAL_STT_BACKEND` | `sensevoice`（默认）, `dashscope` |
| STT | `LOCAL_STT_MODEL`, `LOCAL_STT_DEVICE` | FunASR 模型名, `mps`/`cpu`/`cuda` |
| LLM | `LOCAL_LLM_BACKEND` | `llamacpp`（默认）, `openai_compat`, `dashscope` |
| LLM | `LOCAL_LLM_MODEL`, `LOCAL_LLM_BASE_URL` | 模型名, llama-server endpoint |
| TTS | `LOCAL_TTS_BACKEND` | `qwen3_tts`（默认）, `dashscope` |
| TTS | `LOCAL_TTS_MODULE` | 自定义 Python 模块（需 `synthesize()`） |
| 记忆 | `LOCAL_MEMORY_ENABLED`, `LANCEDB_PATH` | LanceDB 本地路径 |

云端兜底：任意组件设 `*_BACKEND=dashscope` 并配置 `QWEN_API_KEY`（复用 `pipecat-livekit/dashscope_services.py`）。

## 与 `pipecat-livekit/`（8766）的区别

| | 8766 对比 demo | Mac 本地 Agent |
|--|----------------|----------------|
| 前端 | 同一套 `livekit.html` | `8766/?ui=local` |
| LiveKit | 本地 dev server 或 Cloud | **LiveKit Cloud** |
| STT/LLM/TTS | DashScope 云端 | **Mac 本地**（可配置） |
| Bot | server 自动 spawn | **手动** `pipecat_agent.py` |
| 记忆 | 无 | **LanceDB** |

## 文件

- `pipecat_agent.py` — Agent 入口（Mac 上手动运行）
- `transcript_bridge.py` — 转写推送到 LiveKit data channel
- `local_services/config.py` — 环境变量
- `local_services/factory.py` — 按 backend 组装服务
- `local_services/stt_sensevoice.py` — SenseVoice
- `local_services/llm_local.py` — llama-server / DashScope
- `local_services/tts_qwen_local.py` + `tts_runtime.py` — Qwen3-TTS 接入点
- `local_services/memory_lancedb.py` — LanceDB RAG

详细架构见 `docs/pipecat-local-agent-architecture.md`。  
Mac Mini 运维三项注意（Cloudflare / MPS / 休眠保活）见 `docs/pipecat-local-agent-mac-ops.md`。
