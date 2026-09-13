# LiveKit + Pipecat vs Qwen Realtime 对比说明

本文档说明新增的 `web-demo/pipecat-livekit/` 与现有 `web-demo/` 实时语音方案的差异，便于做 A/B 对比。

## 两套架构

### A. 现有方案（`http://127.0.0.1:8765/`）

```
Browser ──WebSocket──► server.py (relay) ──WebSocket──► Qwen3.5-Omni-Realtime
                              │
                         单一模型处理
                    STT + LLM + TTS 端到端
```

特点：

- 协议兼容 OpenAI Realtime API（`session.update` / `input_audio_buffer.append` / `response.audio.delta`）
- 记忆注入通过每轮 `session.update` 动态 patch `instructions`
- 服务端 VAD（`turn_detection.type: server_vad`）
- 音频：16kHz 上行 PCM，24kHz 下行 PCM

### B. 新方案（`http://127.0.0.1:8766/`）

```
Browser ──WebRTC──► LiveKit SFU ──WebRTC──► Pipecat Bot
                                              │
                                    transport.input()
                                              │
                                    DashScope STT (ASR)
                                              │
                                    QwenLLMService (qwen-plus)
                                              │
                                    DashScope TTS (CosyVoice)
                                              │
                                    transport.output()
```

特点：

- **传输层**：LiveKit WebRTC（SFU），更适合弱网、NAT、未来多端
- **AI 层**：模块化 pipeline，每段可独立替换/调参
- **VAD**：Silero（本地 CPU），不依赖 DashScope server_vad
- **编排**：Pipecat frame 流，便于加 metrics、过滤器、并行分支

## 为什么要对比

| 关注点 | Omni Realtime (A) | LiveKit + Pipecat (B) |
|--------|-------------------|------------------------|
| 首包延迟 | 通常更低（单模型、单连接） | 多 hop：VAD→STT→LLM→TTS |
| 回复质量 | 语音-语音联合优化 | 取决于各组件组合 |
| 打断体验 | server_vad + response.cancel | Silero VAD + InterruptionFrame |
| 运维复杂度 | 低（一个 WebSocket） | 高（LiveKit + bot 进程） |
| 可定制性 | 低（黑盒 omni） | 高（换 STT/LLM/TTS） |
| 记忆注入 | instructions patch（已验证） | 需在 LLMContext / system prompt 实现 |
| 跨端 | 浏览器 WebSocket 可行；移动端需自建 | LiveKit 有成熟 iOS/Android SDK |

## 对比实验步骤

1. 同时启动两套服务（见 `web-demo/pipecat-livekit/README.md`）
2. 打开 **8765**（`http://127.0.0.1:8765/`）或 **8766**（`http://127.0.0.1:8766/`），用顶部 **pill 切换器**在两种方案间切换
   - 切换前会自动断开当前会话（8765：`stopAllSessions`；8766：`disconnectAll`），避免并发占用 mic
3. 用相同测试句（如「我周二有什么安排？」）分别测试
4. 记录：
   - 连接建立时间
   - 说完 → 听到首包延迟
   - 打断是否灵敏、是否误触发
   - 多轮上下文是否稳定
5. 打开浏览器开发者工具 Network / Console 观察错误

### 切换器验收

| 操作 | 预期 |
|------|------|
| 8765 语音会话中 → 切到 LiveKit | mic 关闭、WebSocket 断开、8766 加载 |
| 8766 LiveKit 连接中 → 切到 Qwen | room.disconnect、mic 关闭、8765 加载 |
| 当前页 pill | 高亮且 disabled |

## 已知限制（Pipecat 方案）

- **未接入 AgentNexus 记忆**：当前 bot 使用固定 system prompt，未移植 `memory.js` 的检索逻辑
- **转写推送**：通过 LiveKit data channel 推送，浏览器端仅作展示
- **需单独跑 bot 进程**：不像 8765 那样「一个 server.py 搞定」
- **DashScope STT 为分段式**：VAD 切分后再识别，与 Omni 流式 ASR 行为不同

## 后续可扩展

1. 把 `LocalMemory.search()` 逻辑搬到 bot 侧，每轮更新 LLM system prompt
2. 增加 `PIPECAT_PRESET=realtime` 切换 qwen-tts-realtime 降低 TTS 延迟
3. 用 Pipecat metrics 导出延迟分位数，与 8765 的 `markTurnTiming` 对齐
4. 评估 LiveKit Cloud vs 自建 SFU 的部署成本

## 相关文件

- `web-demo/pipecat-livekit/bot.py` — pipeline 入口
- `web-demo/pipecat-livekit/dashscope_services.py` — DashScope STT/TTS
- `web-demo/server.py` — 现有 Qwen Realtime relay
- `web-demo/static/app.js` — 现有前端语音逻辑
