# LiveKit + Pipecat 级联管线（对比用）

这是 `web-demo/` 的**对照组**，不是它的替代品。

- `web-demo/`：浏览器直连 **Qwen-Omni-Realtime**，语音进、语音出，一个模型一条 WebSocket（端到端）。
- `pipecat_demo/`（本目录）：**LiveKit** 负责 WebRTC 传输，**Pipecat** 把 **Paraformer ASR → Qwen 文本模型 → CosyVoice TTS** 串成一条管线（级联）。

两边**共用同一个账号、同一份系统提示词、同一个记忆后端**，就是为了让差异只来自架构本身。逐项对比（延迟、打断、成本、可控性、记忆注入、回声消除）见 [`docs/livekit-pipecat-comparison.md`](../docs/livekit-pipecat-comparison.md)。

## 管线结构

```
浏览器（WebRTC 麦克风轨）
  → LiveKit 服务器
  → transport.input()          收音频
  → VADProcessor               Silero VAD，本地判定说话开始/结束
  → DashScopeSTTService        Paraformer 流式识别（自己实现，见 services/）
  → LLMUserAggregator          组装用户这一轮（停顿 + 防抖）
  → MemoryInjector             把检索到的记忆当普通 system 消息插进去
  → QwenLLMService             Qwen 文本模型（OpenAI 兼容端点，pipecat 自带）
  → DashScopeTTSService        CosyVoice 流式合成（自己实现，见 services/）
  → transport.output()         发音频
  → LLMAssistantAggregator     把回复写回上下文
```

每一段的耗时都由 `processors.TurnTimingObserver` 打点，输出到 bot 日志和网页右侧面板。

## 跑起来

### 0. 装依赖

```bash
python3 -m venv .venv-pipecat        # 或 uv venv .venv-pipecat
source .venv-pipecat/bin/activate
pip install -r pipecat_demo/requirements.txt
```

首次运行 Silero VAD 会下载模型权重（几 MB），需要联网。

### 1. 起一个本地 LiveKit 服务器

```bash
pipecat_demo/scripts/run_livekit_dev.sh
```

脚本会自动下载 `livekit-server` 二进制并以 `--dev` 模式启动：监听 `ws://127.0.0.1:7880`，凭据是公开的 `devkey` / `secret`，也正好是本目录的默认配置，所以本地跑不需要 LiveKit 账号。**dev 模式的凭据是公开的，不要暴露到公网。**

### 2. （可选）起 `web-demo`，作为共用的记忆后端

```bash
python web-demo/server.py
```

本目录的记忆检索直接读 `web-demo/server.py` 已经在提供的 AgentNexus mock（`/agentnexus-mock/*`），这样两条链路读的是同一份记忆。不起也能跑，只是检索不到东西。

### 3. 起本目录的网页服务

```bash
python -m pipecat_demo.token_server        # http://127.0.0.1:8766
```

打开 http://127.0.0.1:8766 ，点"开始对话"。每次会话会新建一个房间、拉起一个独立的 bot 进程；页面关掉或参与者离开，bot 进程自己退出。

### 模拟模式（不需要 API Key）

```bash
PIPECAT_MOCK=1 python -m pipecat_demo.token_server
```

ASR/LLM/TTS 换成本地假实现（固定转写、固定回复、正弦音），WebRTC 传输、VAD、轮次切换、打断、播放、耗时打点全都是真的。用来验证接线，或者在没有 Key 的机器上看链路是否通。

## 配置

统一读仓库根目录的 `.env`（和 `web-demo/server.py` 同一个文件）。复用 `QWEN_API_KEY` / `QWEN_WORKSPACE_ID`；本条链路特有的参数都带 `PIPECAT_` 前缀，改这边不会影响那边。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `QWEN_API_KEY` | — | 必填（模拟模式除外） |
| `QWEN_WORKSPACE_ID` | 空 | 填了就走专属域名（ASR/TTS 的 WebSocket 和 LLM 的 HTTP 都切） |
| `PIPECAT_LLM_MODEL` | `qwen-plus` | 文本模型；`qwen-turbo` / `qwen-flash` 更快 |
| `PIPECAT_ASR_MODEL` | `paraformer-realtime-v2` | 流式识别模型 |
| `PIPECAT_ASR_MAX_SENTENCE_SILENCE_MS` | `800` | ASR 内部断句的静音阈值（不是轮次结束判定） |
| `PIPECAT_TTS_MODEL` / `PIPECAT_TTS_VOICE` | `cosyvoice-v2` / `longxiaochun_v2` | 音色要和模型版本匹配（v2 模型配 `_v2` 音色） |
| `PIPECAT_VAD_STOP_SECS` | `0.9` | 本地 VAD 判定"说完了"的静音时长，对齐另一条链路的 `silence_duration_ms: 900` |
| `PIPECAT_RESPONSE_DEBOUNCE_SECS` | `0.5` | 说完后再等一会儿才回答，对齐 app.js 的 `RESPONSE_DEBOUNCE_MS` |
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | dev 默认值 | 换成 LiveKit Cloud 就能测真实网络 |
| `PIPECAT_PORT` | `8766` | 本目录网页服务端口（`web-demo` 占 8765） |

## 测试

```bash
python -m pytest tests/ -q
```

- `test_dashscope_stt.py` / `test_dashscope_tts.py`：拿 `tests/fake_dashscope.py`（一个按官方协议应答的假服务器）验证两个自己实现的服务——指令字段、鉴权头、任务生命周期、握手期音频不丢、`sentence_end` 才算最终转写、二进制音频转成音频帧。
- `test_memory.py`：分词/打分与 `memory.js` 对齐；AgentNexus 部分直接跑 `web-demo/agentnexus_mock.py` 本体。
- `test_processors.py`：记忆注入的位置与替换语义、每轮耗时分解。
- `test_bot_pipeline.py`：从"有了转写"到"出音频"整条会话链路（模拟服务）。
- `test_livekit_integration.py`：真实 LiveKit 服务器上跑真实 bot 进程（模拟模式）——加入房间、发布音轨、参与者离开后进程正常退出。没起 LiveKit 时自动跳过。其中还有一个用真人语音驱动完整轮次的测试，需要你提供一段 16 位单声道的录音：

```bash
PIPECAT_TEST_SPEECH_WAV=/path/to/speech.wav python -m pytest tests/test_livekit_integration.py -q
```

### 不开浏览器跑一轮

```bash
python -m pipecat_demo.scripts.drive_session --wav question.wav --mock --duration 40
```

拿一个 wav 当麦克风推进房间，把 bot 发回来的事件（转写、回复、记忆命中、每轮耗时）按 JSON 打到标准输出。想看真实云端链路的延迟数字就去掉 `--mock`。录音里最好在说完后留几秒静音，否则每轮都会被"用户还在说话"打断——那是打断逻辑在正常工作，不是故障。

## 目录

```
config.py           配置（复用 .env，PIPECAT_* 是本链路专属）
prompts.py          系统提示词，逐字复制自 app.js 的 BASE_INSTRUCTIONS
memory.py           记忆检索（移植 memory.js 打分 + AgentNexus 客户端）
processors.py       记忆注入处理器 + 每轮耗时观察器
services/           DashScope ASR / TTS 的 Pipecat 服务实现
bot.py              管线组装与运行（一次会话一个进程）
token_server.py     网页服务 + LiveKit token + 拉起 bot 进程
livekit_token.py    token 生成（薄封装 pipecat 的实现）
mock_services.py    离线假实现，仅 --mock 使用
web/                浏览器客户端（约 200 行，对比 app.js 的 1700+ 行）
scripts/            本地 LiveKit 服务器、客户端 SDK 下载
```
