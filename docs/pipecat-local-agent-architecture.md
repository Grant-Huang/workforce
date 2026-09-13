# Mac Mini 本地 Agent + LiveKit Cloud 架构

## 流量路径

```
┌─────────────┐     WebRTC (公网)      ┌──────────────────┐
│ 手机 / 网页  │ ────────────────────► │  LiveKit Cloud   │
└─────────────┘                        │  (SFU + 信令)    │
                                       └────────┬─────────┘
                                                │
                           出站 WSS / 媒体订阅   │  （Mac 无需公网 IP）
                                                ▼
                                       ┌──────────────────┐
                                       │ Mac Mini 局域网   │
                                       │ pipecat_agent.py │
                                       └──────────────────┘
```

Mac 作为 **Agent 出站连接** LiveKit Cloud，与 [LiveKit Agents 部署模型](https://docs.livekit.io/) 一致：SFU 在云端，推理在边缘。

### Cloudflare 边界（必读）

- **可以 Tunnel**：前端静态页，如 `https://chat.yourdomain.com`  
- **禁止 Tunnel 代理**：`wss://<project>.livekit.cloud` — 前端与 Mac Agent 均 **原生直连** LiveKit Cloud  
- WebRTC 媒体不经 Cloudflare；Tunnel 只承载 HTML/JS 即可

详见 [`pipecat-local-agent-mac-ops.md`](pipecat-local-agent-mac-ops.md)。

### MPS 与进程布局

- **Pipecat 进程内**：SenseVoice STT + Qwen3-TTS（`LOCAL_STT_DEVICE` / `LOCAL_TTS_DEVICE=mps`）  
- **独立进程**：Qwen2.5 via **llama-server**（`LOCAL_LLM_BACKEND=llamacpp`）— 避免三模型抢 Metal

### 保活

- macOS：**防止自动休眠**  
- Pipecat：`PIPELINE_ENABLE_HEARTBEATS=1` + `LIVEKIT_AUTO_RECONNECT=1`（`LiveKitParams` 无单独 heartbeat 字段）

## Pipecat Pipeline

```
LiveKitTransport.input()
  → SenseVoiceSTT (LOCAL_STT_*)
  → LanceDBMemoryProcessor (LOCAL_MEMORY_*)
  → LLMContextAggregator (user)
  → Qwen LLM (LOCAL_LLM_* → llama-server HTTP)
  → QwenLocalTTS (LOCAL_TTS_*)
  → LiveKitTransport.output()
  → LLMContextAggregator (assistant)
```

记忆模块在 STT 之后、LLM 之前：根据用户转写检索 LanceDB，动态 patch `system_instruction`。

## 环境变量一览

见 `web-demo/pipecat-local-agent/README.md` 与仓库根 `.env.example` 中 `LOCAL_*` 段。

## 混合部署建议

| 场景 | 建议 |
|------|------|
| Mac 离线 / GPU 忙 | `LOCAL_STT_BACKEND=dashscope` 等云端兜底 |
| 仅 LLM 本地 | STT/TTS 用 dashscope，LLM 用 llamacpp |
| 全本地隐私 | sensevoice + llamacpp + qwen3_tts + LanceDB |

## 前端

复用 `web-demo/pipecat-livekit/` 同一套页面：`http://127.0.0.1:8766/?ui=local`。  
手机/网页使用 LiveKit Client SDK，连接 **LiveKit Cloud** 同一 `room`；用户 token 由 `pipecat-livekit/server.py` 签发（不要暴露 API Secret）。

Mac 上 agent token 由 `pipecat_agent.py` 启动时用 `LIVEKIT_API_KEY/SECRET` 生成，或预置 `LIVEKIT_AGENT_TOKEN`。

## 相关目录

- `web-demo/pipecat-local-agent/` — 本 agent
- `web-demo/pipecat-livekit/` — 8766 LiveKit Cloud + 本地 Pipecat pipeline
- `web-demo/` — 8765 Qwen Realtime WebSocket demo
