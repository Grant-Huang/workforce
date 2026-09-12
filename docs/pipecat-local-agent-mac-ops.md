# Mac Mini 本地 Agent 运维三项注意

部署 `web-demo/pipecat-local-agent/` 时，以下三点直接影响稳定性。

---

## 1. Cloudflare：只发布前端，不要代理 LiveKit

### 正确做法

```
用户浏览器 ──HTTPS──► Cloudflare Tunnel ──► Mac: 静态前端 / chat UI
              │
              └── WebRTC / WSS ──► wss://<project>.livekit.cloud  （直连，不经过 Cloudflare）
```

- **前端页面**：`https://chat.yourdomain.com` → Tunnel 指向 Mac 上的静态站或轻量 API  
- **LiveKit 信令与媒体**：前端 SDK 配置 `liveKitUrl: wss://<project>.livekit.cloud`  
- **Mac Agent**：`.env` 里 `LIVEKIT_URL=wss://<project>.livekit.cloud`（同一原生 endpoint）

### 错误做法

- 把 `wss://<project>.livekit.cloud` CNAME 到 Cloudflare 并走 Tunnel 代理  
- 在前端里把 LiveKit URL 写成 `wss://chat.yourdomain.com/livekit-proxy`

**后果**：额外 WebSocket 握手延迟、Idle 断连、WebRTC ICE 异常。

`pipecat_agent.py` 启动时会校验：`LIVEKIT_URL` 含 `cloudflare` / `trycloudflare` 等关键字会直接报错。

---

## 2. MPS 显存竞争：LLM 走 Ollama 独立进程

SenseVoice（STT）和 Qwen3-TTS 跑在 **Pipecat 同一 Python 进程**内，会争用 Metal/MPS。

| 组件 | 推荐部署 | 原因 |
|------|----------|------|
| SenseVoice STT | Pipecat 进程内，`LOCAL_STT_DEVICE=mps` | 与音频 pipeline 同进程延迟低 |
| Qwen3-TTS | Pipecat 进程内，`LOCAL_TTS_DEVICE=mps` | 同上 |
| Qwen2.5 LLM | **Ollama 独立进程**，HTTP 调用 | 独立内存通道，避免三模型抢 MPS |

```bash
# Mac Mini 上先起 Ollama（独立守护进程）
ollama serve
ollama pull qwen2.5:7b

# .env
LOCAL_LLM_BACKEND=ollama
LOCAL_LLM_MODEL=qwen2.5:7b
LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1
```

若必须用 `LOCAL_LLM_BACKEND=llamacpp` 且进程内加载 GGUF，建议 STT/TTS 改 `cpu` 或错峰，启动时会有 warning。

---

## 3. 休眠与长连接保活

### macOS 能源设置

**系统设置 → 电池/能源 → 当显示器关闭时防止自动休眠**（或 `caffeinate` 常驻）。

Mac 休眠会断开 LiveKit 出站 WebSocket，外网用户进房时 agent 不在线。

### Pipecat 心跳与自动重连

`LiveKitParams` **没有**单独的心跳开关；保活靠：

1. **`WorkerRunner` 持续运行** — agent 进程不退出即在 room 内等待  
2. **`PipelineParams.enable_heartbeats`** — 检测 pipeline 卡死（默认开启）

```bash
# .env
PIPELINE_ENABLE_HEARTBEATS=1
PIPELINE_HEARTBEATS_PERIOD_SECS=5
PIPELINE_HEARTBEATS_MONITOR_SECS=30
LIVEKIT_AUTO_RECONNECT=1
LIVEKIT_RECONNECT_DELAY_SECS=5
```

断线后 agent 会按 `LIVEKIT_AUTO_RECONNECT` 自动重新加入 room。

### 推荐：launchd 守护

```xml
<!-- ~/Library/LaunchAgents/com.voicechat.pipecat-agent.plist 示例 -->
<!-- KeepAlive + RunAtLoad，崩溃后由 macOS 拉起 -->
```

配合 `caffeinate -dims` 或「防止休眠」确保 7×24 待命。

---

## 检查清单

- [ ] 前端 LiveKit URL = `wss://*.livekit.cloud`，未走 Cloudflare  
- [ ] Tunnel 仅 `chat.yourdomain.com` 等前端域名  
- [ ] Ollama 独立运行，LLM 不走进程内 llama.cpp  
- [ ] Mac 已禁用自动休眠  
- [ ] `PIPELINE_ENABLE_HEARTBEATS=1`，`LIVEKIT_AUTO_RECONNECT=1`  
- [ ] `pipecat_agent.py` 由 launchd 或 tmux 常驻
