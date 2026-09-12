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

### 2. 本地 LLM（推荐 Ollama 独立进程，避免 MPS 争抢）

```bash
# Ollama 作为独立守护进程 — 与 Pipecat 内 SenseVoice/TTS 分离
ollama serve
ollama pull qwen2.5:7b
```

### 3. 环境变量（仓库根 `.env`）

```bash
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
LIVEKIT_ROOM_NAME=voicechat-local

LOCAL_STT_BACKEND=sensevoice
LOCAL_STT_MODEL=iic/SenseVoiceSmall
LOCAL_STT_DEVICE=mps

LOCAL_LLM_BACKEND=ollama          # llamacpp | ollama | openai_compat | dashscope
LOCAL_LLM_MODEL=qwen2.5:7b
LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1

LOCAL_TTS_BACKEND=qwen3_tts       # 或 dashscope 云端兜底
LOCAL_TTS_MODEL=qwen3-tts
LOCAL_TTS_DEVICE=mps
LOCAL_TTS_VOICE=Cherry

LOCAL_MEMORY_ENABLED=1
LANCEDB_PATH=./data/lancedb
```

### 4. 安装并运行

```bash
cd web-demo/pipecat-local-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-local-mac.txt

# 实现 TTS：编辑 local_services/tts_runtime.py 接入你的 Qwen3-TTS

python pipecat_agent.py --room voicechat-local
```

## 模型后端（环境变量）

| 组件 | 变量 | 可选值 |
|------|------|--------|
| STT | `LOCAL_STT_BACKEND` | `sensevoice`（默认）, `dashscope` |
| STT | `LOCAL_STT_MODEL`, `LOCAL_STT_DEVICE` | FunASR 模型名, `mps`/`cpu`/`cuda` |
| LLM | `LOCAL_LLM_BACKEND` | `llamacpp`, `ollama`, `openai_compat`, `dashscope` |
| LLM | `LOCAL_LLM_MODEL`, `LOCAL_LLM_BASE_URL` | 模型名, OpenAI 兼容 endpoint |
| TTS | `LOCAL_TTS_BACKEND` | `qwen3_tts`（默认）, `dashscope` |
| TTS | `LOCAL_TTS_MODULE` | 自定义 Python 模块（需 `synthesize()`） |
| 记忆 | `LOCAL_MEMORY_ENABLED`, `LANCEDB_PATH` | LanceDB 本地路径 |

云端兜底：任意组件设 `*_BACKEND=dashscope` 并配置 `QWEN_API_KEY`（复用 `pipecat-livekit/dashscope_services.py`）。

## 与 `pipecat-livekit/`（8766）的区别

| | 8766 对比 demo | 本 agent |
|--|----------------|----------|
| LiveKit | 本地 dev server | **LiveKit Cloud** |
| STT/LLM/TTS | DashScope 云端 | **Mac 本地**（可配置） |
| 部署位置 | ECS 或本机 | **Mac Mini 出站** |
| 记忆 | 无 | **LanceDB** |

## 文件

- `pipecat_agent.py` — 入口
- `local_services/config.py` — 环境变量
- `local_services/factory.py` — 按 backend 组装服务
- `local_services/stt_sensevoice.py` — SenseVoice
- `local_services/llm_local.py` — llama.cpp / Ollama / DashScope
- `local_services/tts_qwen_local.py` + `tts_runtime.py` — Qwen3-TTS 接入点
- `local_services/memory_lancedb.py` — LanceDB RAG

详细架构见 `docs/pipecat-local-agent-architecture.md`。  
Mac Mini 运维三项注意（Cloudflare / MPS / 休眠保活）见 `docs/pipecat-local-agent-mac-ops.md`。
