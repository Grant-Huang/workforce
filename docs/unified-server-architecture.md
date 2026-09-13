# Unified Server 设计文档

> `workforce.inkpath.cc` 单域名双 pipeline 部署的设计决策、URL 路径表、为什么这样切、已知限制、未来扩展。
> 配套文档：[本地化部署与测试手册](./local-deployment-and-test-runbook.md)（操作流程）· [pipecat 1.9 升级 PR](./pr-pipecat-1.9-settings-compat.md)（踩坑修复）

---

## 1. 背景

### 1.1 历史形态

`workforce` 仓库原本有**两个独立 voice demo**，跑在不同端口、不同域名：

| Demo | 端口 | 公网域名 | 后端 |
|---|---|---|---|
| A. Qwen Realtime | 8765 | `workforce.inkpath.cc` | `web-demo/server.py`（aiohttp WebSocket relay → DashScope 云） |
| B. LiveKit + Pipecat | 8766 | `workforce-l.inkpath.cc` | `web-demo/pipecat-livekit/server.py`（aiohttp + LiveKit token + Pipecat bot_manager） |

两套页面顶部 pill 切换器通过 `mode-switcher.js` 互相跳转，但**跳的是域名**，所以浏览器把它当成新 session：音频会话断、cookie 不共享、调参面板状态丢失、对比 A/B 时用户体验割裂。

### 1.2 为什么合并

用户原话："能不能直接用 `workforce.inkpath.cc` 一个域名，在同一个前端里切换 pipeline 来测试？"

**核心诉求**：让用户在**同一前端**切换 pipeline，**音频会话能通过 `onBeforeLeave` 优雅断开重连**，而不是跨域重开一个新页面。

### 1.3 为什么是 unified server（不是反向代理、不是子路径分 ingress）

候选方案对比：

| 方案 | 优点 | 缺点 |
|---|---|---|
| **A. Cloudflare Tunnel URL 路径分发**（`path:` ingress） | 5 分钟配完，不写代码 | `/livekit-static/app.js` 这种 URL 丑；8765/8766 的 `/static/*` 路径冲突，要改 8766 加前缀 |
| **B. 一个 aiohttp app 合并所有路由** | 用户感知最干净；未来加 pipeline 只改这一个 server | 中等代码改动（~480 行新 server.py），需小心 static 路径命名空间 |
| **C. 保留两个域名，pill 切换器只改 URL** | 最小改动（3 行 JS） | 跨域 = 浏览器开新 session，音频会话必须重连，调参 localStorage 不共享 |

选 **B**，因为：(1) 用户明确要"同一个前端里切换"；(2) B 给未来留的扩展空间最大；(3) 一次性投入换长期清晰。

---

## 2. 架构

### 2.1 顶层结构

```
                            公网
[浏览器/手机]
    │
    └── HTTPS ───► Cloudflare Tunnel ───► Mac:8788 (unified server, aiohttp)
                                                │
                                                ├─ mode=qwen (默认):
                                                │     ├─ GET  /                        → index.html
                                                │     ├─ GET  /api/config?mode=qwen    → Qwen voices
                                                │     ├─ GET  /ws                      → Qwen Realtime relay (WebSocket)
                                                │     ├─ POST /api/dictation-cleanup   → Qwen one-shot text call
                                                │     └─ POST /api/memory-extract      → Qwen one-shot memory extraction
                                                │
                                                └─ mode=livekit:
                                                      ├─ GET  /?mode=livekit          → livekit.html
                                                      ├─ GET  /api/config?mode=livekit → LiveKit Cloud config
                                                      ├─ GET  /api/livekit/token       → bot_manager.ensure_started + JWT
                                                      ├─ GET  /api/bot/status
                                                      ├─ GET  /api/agent/status
                                                      └─ GET/POST /api/agent/wake       → bot_manager.ensure_agent_started

                                                Static:
                                                ├─ /static/*            → web-demo/static/           (Qwen side)
                                                ├─ /livekit-static/*    → web-demo/pipecat-livekit/static/  (LiveKit side)
                                                └─ /shared/mode-switcher.js → web-demo/static/mode-switcher.js  (unified)
```

### 2.2 unified server 是怎么"合并"两个 server 的

`web-demo/unified_server.py`（约 480 行）的关键设计：

1. **`sys.path.insert(0, pipecat-livekit/)`** — 让 Python 找得到 `bot_manager` 和 `livekit_env`
2. **`load_dotenv(WEB_DEMO_DIR.parent / ".env")`** — 同一个 `.env`（Qwen + LiveKit + LOCAL_* 全部）
3. **`unified_config(request)`** — 同一个 `/api/config` endpoint，根据 `?mode=` query 返回不同 shape：
   - `mode=qwen` → 返回 `{voice, voices, hasKey, hasWorkspaceId}`（原 8765 `config` handler）
   - `mode=livekit` → 返回 `{livekitUrl, defaultRoom, agentIdentity, autoSpawnBot, hasLiveKitCredentials, hasQwenKey, sttBackend, llmBackend, ttsBackend, architecture, wakeEndpoint, ...}`（原 8766 `config` handler）
4. **`livekit_token(request)`** — 复用 8766 的逻辑；内部 `ensure_started(room)` 拉起 bot；token URL 用 `_token_url()` 返回 `LIVEKIT_URL_PUBLIC or LIVEKIT_URL`，**强制校验 `wss://*.livekit.cloud`**
5. **`relay(request)` / `dictation_cleanup(request)` / `memory_extract(request)`** — 8765 的 handler 完整搬过来
6. **`add_static`** — 两个不同 prefix 命名空间，避免 `/static/*` 冲突

### 2.3 完整路由表（25 条）

```
GET  /                            index         mode 分发 → index.html | livekit.html
GET  /livekit.html                livekit_page  显式入口（书签 / 分享链接）
GET  /shared/mode-switcher.js     shared_mode_switcher  → web-demo/static/mode-switcher.js

GET  /api/config                  unified_config          ?mode= 分发（qwen | livekit）

GET  /ws                          relay                   Qwen Realtime WebSocket relay
POST /api/dictation-cleanup       dictation_cleanup       Qwen one-shot text call
POST /api/memory-extract          memory_extract          Qwen one-shot memory extraction

GET  /api/livekit/token           livekit_token           LiveKit Cloud JWT + 拉 bot
GET  /api/bot/status              bot_status
GET  /api/agent/status            agent_status
GET  /api/agent/wake              agent_wake              bot_manager.ensure_agent_started
POST /api/agent/wake              agent_wake

GET  /static/*                    → web-demo/static/                       (Qwen side assets)
GET  /livekit-static/*            → web-demo/pipecat-livekit/static/       (LiveKit side assets)
```

### 2.4 mode-switcher.js 改造

从**跨域跳转**改成**同源 query 跳转**：

```js
// OLD (8765 + 8766 双域名)
const MODES = {
  qwen:       { url: "http://127.0.0.1:8765/" },
  livekit:    { url: "http://127.0.0.1:8766/" },
  localAgent: { url: "http://127.0.0.1:8766/?ui=local" },
};

// NEW (单域名 unified)
function targetFor(mode) {
  if (mode === "qwen")       return "/";
  if (mode === "livekit")    return "/?mode=livekit";
  if (mode === "localAgent") return "/?mode=livekit&ui=local";
}
```

`detectCurrentMode()` 改成读 `location.search` 而不是 `location.port`，因为端口统一了。

`onBeforeLeave` callback 让前端在切换前清理当前音频会话（断 WebSocket / LiveKit room），然后 `location.href = MODES[target].url` —— **同一 origin，cookie 和 sessionStorage 共享**。

---

## 3. 关键设计原则

### 3.1 Cloudflare Tunnel 只代理 HTTP，不代理 LiveKit 信令

这是用户讨论里反复强调的核心原则。`livekit_env.py::assert_livekit_cloud_url()` 强制 `LIVEKIT_URL` 和 `LIVEKIT_URL_PUBLIC` 都必须是 `wss://*.livekit.cloud`，填 `wss://workforce-l.inkpath.cc` 这种 CF Tunnel hostname 直接 `SystemExit` 启动失败。

理由：
- Cloudflare Tunnel 的 `service:` 字段支持 `http://` / `https://`，**`tcp://` / `udp://` 只对 WARP 客户端可见，不对公网**
- 即使技术上能代理 WebSocket，额外握手延迟 + Idle 断联 + ICE 异常都会让通话质量崩
- LiveKit Cloud 本身就是为了公网 WebRTC 设计的，自己处理 SFU / ICE / TURN

### 3.2 Mac Mini 零入站端口

Pipecat bot 是 **LiveKit 房间的参与者**，主动 outbound 连接 LiveKit Cloud。Mac 没有任何端口对外暴露 —— Cloudflare Tunnel 是出站连接，bot 也是出站连接。**整个公网-到-LAN 路径上零入站**。

### 3.3 Plist 不含 secret，凭证走 .env

`~/Library/LaunchAgents/ai.workforce.unified.plist` 是 **world-readable**（任何本机用户都能看）。如果把 `LIVEKIT_API_SECRET` 放 plist 里就是泄密。

解法：用 wrapper shell 脚本 `start-8788.sh`，从 `~/work/projects/workforce/.env`（权限 600）source 凭证，然后 `exec python`。plist 只指向 wrapper 路径。

### 3.4 端口选择：8788 不是 8787

**坑过**：原本计划用 8787，启动时遇到 `OSError: address already in use` —— omni-server 进程（PID 11662，跑了 9 天）一直占着 8787。按 SOUL.md 不能 kill 别人的项目服务，所以改用 8788。**跨用户端口冲突是真实风险**，部署手册第 0.3 节明确标注了这个坑。

---

## 4. 部署拓扑

### 4.1 launchd 守护的 4 件套

| Label | 端口 | 角色 | 必须 |
|---|---|---|---|
| `ai.workforce.unified` | 8788 | 主入口，单域名双 pipeline | ✅ 是 |
| `ai.workforce.pipecat-livekit` | 8766 | legacy LiveKit（`workforce-l.inkpath.cc` 后端） | 兜底 |
| `ai.workforce.web-demo` | 8765 | legacy Qwen Realtime | 兜底 |
| `ai.workforce.cloudflared` | 20241 metrics | Cloudflare Tunnel | ✅ 是 |

**legacy 两件（8765/8766）短期保留**，原因：
1. 老链接 / 老书签不挂
2. unified server 出问题时能快速切回
3. 等 unified 稳定 1-2 周后，可以 `launchctl unload + 删 plist + 改 CF config` 完全退役

### 4.2 端口分布

```
Mac Mini
├─ 8788  unified server       (workforce.inkpath.cc 主入口)
├─ 8765  legacy Qwen          (workforce.inkpath.cc 老路径，unified 上线后实际已不用)
├─ 8766  legacy LiveKit       (workforce-l.inkpath.cc)
├─ 8080  llama.cpp Qwen2.5    (com.hermes.llama-qwen7b LaunchAgent)
├─ 20241 cloudflared metrics
└─ 7880/7881/7882 UDP         (不再用 —— 不再 docker run livekit)
```

---

## 5. 已知限制 / 权衡

### 5.1 切 mode 时音频会话必须重连

浏览器同 origin 跳 `?mode=` 会触发 `location.href` 导航，整个 JS context 重建。`onBeforeLeave` 让前端优雅断开，但**会话还是会断**（即使没有 cross-origin）。这不是 unified server 的限制，是浏览器本身的限制。

**缓解**：
- mode-switcher UI 提示"已切换，需要重新连接"
- 如果未来要"零重连切换"，需要把两个 pipeline 都跑在同一个 WebRTC 房间 / 同一个 WebSocket 里（架构大改）

### 5.2 `/api/config` 单 endpoint 双 shape

`/api/config` 根据 `?mode=` 返回不同 JSON 结构。Qwen frontend 读 `{voice, voices, hasKey, hasWorkspaceId}`，LiveKit frontend 读 `{livekitUrl, ...}`。同一 endpoint 两种 shape，前端不会用错（因为 mode 决定走哪个 frontend），但 API 文档角度不够干净。

**替代方案**：拆成 `/api/config/qwen` 和 `/api/config/livekit`。**未做**，因为：
- 当前前端代码不需要改
- 拆 API 反而让 mode-switcher 切换后多一次路径切换

### 5.3 bot_manager 复用而非合并

unified server 直接 `from bot_manager import ensure_started, ensure_agent_started` —— **bot 子进程管理逻辑完全没动**，还是 8766 那套。好处：
- 改动面小
- 8766 legacy 仍能独立运行（兜底）

坏处：
- bot 子进程的 stdout/stderr 输出仍走 8766 那套 `/tmp/` 日志路径，**不归 unified server 管**
- 如果想看 bot 日志，得去 `/tmp/workforce-pipecat-livekit-bot-*.log`，而不是 unified server 的 `/tmp/workforce-unified-8788.log`

**未来改进**：把 bot_manager 改造成接受 caller 的 log_dir 参数，统一日志路径。

### 5.4 unified server 启动时 bot 不会自动启动

bot 是**按需 spawn**（用户进 LiveKit 房间才拉）。这跟 8766 legacy 行为一致。**优点**：不占内存；**缺点**：第一次切到 livekit 模式时 token API 调用会比较慢（30-45s，因为要拉 bot 子进程 + 加载本地 ML 模型）。

**未来改进**：可以加 `PRELOAD_BOT=1` env，启动 unified 时后台预热一次，token API 就快了。

---

## 6. 未来扩展

### 6.1 加新 pipeline（比如 ElevenLabs / GPT-4o Realtime）

只需要：
1. 新建 `web-demo/<new-pipeline>/server.py`，或
2. 直接在 `unified_server.py` 里加新的 `build_<pipeline>_handlers()` + `add_router()` 调用

不动的部分：
- 路由分发表（只在末尾追加）
- 静态资源命名空间（再加一个 `/<pipeline>-static/*`）
- mode-switcher.js 加一个 mode

### 6.2 把 legacy 退役

等 unified server 稳定后：

```bash
# 1. 卸载 legacy LaunchAgent
launchctl unload ~/Library/LaunchAgents/ai.workforce.pipecat-livekit.plist
launchctl unload ~/Library/LaunchAgents/ai.workforce.web-demo.plist

# 2. 删 plist + wrapper（也可保留作为 fallback）
rm ~/Library/LaunchAgents/ai.workforce.pipecat-livekit.plist
rm ~/Library/LaunchAgents/ai.workforce.web-demo.plist
rm ~/work/projects/workforce/.launchd-wrappers/start-8765.sh
rm ~/work/projects/workforce/.launchd-wrappers/start-8766.sh

# 3. 改 cloudflared config.yml,把 workforce-l 也指向 unified server(或删掉那条)
# 然后 launchctl reload cloudflared
```

### 6.3 unified server 拆成多个 worker

如果未来 STT/TTS/LLM 加载慢影响 token API 延迟，可以：
- 把 `unified_server.py` 拆成两个进程：`unified_web.py`（HTTP）+ `unified_worker.py`（ML pipeline，per-core pinning）
- 用 systemd / supervisord 管理（macOS 上 launchd 也行，但配置更复杂）

当前 unified server 启动后只占 ~200MB（python aiohttp + imports），bot 子进程按需 spawn 才是 ML 栈（torch / qwen-tts）的大头。

### 6.4 浏览器 audio device 持久化

切 mode 后浏览器 JS context 重建，audio output device 选择会重置成默认。可以加：
- `localStorage.workforce.audioOutputId` 持久化 deviceId
- 启动时 `navigator.mediaDevices.selectAudioOutput({deviceId})` 重新选

跟 unified server 无关，前端做就行。

---

## 7. 相关决策记录

### 7.1 unified server vs 多域名 vs path-based ingress

**决策**：选 unified server（方案 B）

**理由**：
- A (path-based ingress)：URL 丑，static 路径难维护，未来加 pipeline 要改 CF config
- B (unified server)：用户感知最干净，扩展空间最大，改动一次性
- C (多域名 + 切换器改 URL)：跨域 = 浏览器新 session，不满足"同一前端切换"诉求

### 7.2 不动 legacy 双端口

**决策**：保留 8765 / 8766 作为 fallback，至少 1-2 周观察期

**理由**：
- unified server 上线 24h 内如果出问题，可以快速切回 legacy（cloudflared config 改一行 + reload）
- 老外链 / 老书签不挂
- 一次性退役风险大，分阶段退役可控

### 7.3 端口 8788 不是 8787

**决策**：unified server 用 8788，避开被 omni-server 长期占用的 8787

**理由**：
- 8787 已被 PID 11662 (`omni-server`) 占 9 天
- 按 SOUL.md 不能跨用户 kill 别人的服务
- 8788 空、cloudflared config 没占用，跟现有端口空间不冲突

**未来**：如果 8788 跟别的项目冲突，再换。建议部署手册第 0.3 节必读。

### 7.4 Plist 不含 secret

**决策**：所有 plist 不放 secret，凭证走 wrapper shell + repo-root `.env`

**理由**：
- `~/Library/LaunchAgents/` 是 world-readable
- 早先版本的 plist 直接写 LIVEKIT_API_KEY 等于公开密钥
- wrapper 模式：plist → bash → source .env → exec python

**未来**：如果有更优雅的方案（比如 `launchctl setenv` 在 user space 注入）可以考虑。当前 wrapper 模式足够简单清晰。

---

## 8. 相关文件

| 路径 | 角色 |
|---|---|
| `web-demo/unified_server.py` | unified server 主入口，~480 行，合并 8765+8766 路由 |
| `web-demo/static/mode-switcher.js` | 单域名 pill 切换器（改写自原 8766 版本） |
| `web-demo/pipecat-livekit/livekit.html` | livekit 页面，资源路径改 `/livekit-static/*` |
| `web-demo/pipecat-livekit/bot_manager.py` | bot 子进程管理（unified server 复用） |
| `web-demo/pipecat-livekit/livekit_env.py` | LiveKit Cloud settings 加载 + URL 校验 |
| `web-demo/pipecat-local-agent/local_services/*.py` | 本地 ML pipeline（STT/TTS/LLM/memory），bot_manager 调用 |
| `~/.cloudflared/config.yml` | `workforce.inkpath.cc → 127.0.0.1:8788` |
| `~/Library/LaunchAgents/ai.workforce.unified.plist` | LaunchAgent 定义 |
| `~/work/projects/workforce/.launchd-wrappers/start-8788.sh` | wrapper shell（不在 plist 里放 secret） |
| `~/work/projects/workforce/.env` | Qwen + LiveKit + LOCAL_* 凭证（权限 600） |

---

## 9. 时间线

| 日期 | 事件 |
|---|---|
| 2026-09-12 ~20:00 | 部署原 8765 / 8766 双域名架构 |
| 2026-09-12 ~20:30 | 发现 pipecat 1.9 STTSettings bug，修 `stt_sensevoice.py` |
| 2026-09-12 ~21:30 | 写完两份文档（PR #1 + 部署手册） |
| 2026-09-12 ~22:00 | 用户要求单域名，写 `unified_server.py` |
| 2026-09-12 ~22:05 | unified server 启动失败（8787 被 omni 占），改用 8788 |
| 2026-09-12 ~22:10 | unified server 上线，12 项端到端测试全绿 |
| 2026-09-12 ~22:15 | token API 又崩，发现 TTS 同样的 pipecat 1.9 bug，修 `tts_qwen_local.py` |
| 2026-09-12 ~22:20 | token API 跑通，KeepAlive 真崩测试通过 |
| 2026-09-12 ~22:30 | 更新文档（PR 文档扩到 TTS，部署手册改 unified，新增本设计文档） |
