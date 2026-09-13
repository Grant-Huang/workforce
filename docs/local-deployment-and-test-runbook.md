# workforce 本地化部署与测试手册

> 适用 `Grant-Huang/workforce` 在 Mac Mini / Apple Silicon 上的端到端部署。
> 配合 Cloudflare Tunnel（`rta-hub` tunnel）发布到公网，对外域名 **`workforce.inkpath.cc`**（单域名，unified server）。
> LiveKit 用 LiveKit Cloud（`wss://<project>.livekit.cloud`），Mac 不暴露任何入站端口。
> 完整架构决策见 [`docs/unified-server-architecture.md`](./unified-server-architecture.md)。

---

## 0. 前提与架构

### 0.1 unified server：单域名双 pipeline

部署在 `workforce.inkpath.cc` 一个公网域名背后的是 **`web-demo/unified_server.py`**，端口 **8788**，把两套语音 pipeline 合并到一个 aiohttp app：

| Pipeline | URL 路径 | 后端栈 | 默认模式 |
|---|---|---|---|
| **Qwen Realtime**（A 方案） | `GET /` 或 `/?mode=qwen` | 浏览器 WebSocket → Mac relay → DashScope 云 Qwen3.5-Omni-Realtime（workspace-specific endpoint） | ✅ 默认 |
| **LiveKit + Pipecat**（B 方案） | `/?mode=livekit` 或 `/livekit.html` | 浏览器 WebRTC → LiveKit Cloud → Mac 本地 Pipecat bot（SenseVoice → llama.cpp(Qwen2.5) → Qwen3-TTS）+ LanceDB 记忆 | 显式 |
| **Mac local agent** | `/?mode=livekit&ui=local` | 同上，但 bot 不自动 spawn，需手动 `/api/agent/wake` | 调试 |

页面顶部 pill 切换器调用 `window.VoiceModeSwitcher.mountSwitcher`，点击只是修改 `?mode=` query，**同一前端内切换**（无跨域），音频会话通过 `onBeforeLeave` 优雅断开 → 新 mode 重新连接。

**legacy 双域名**（双轨运行，保留作为 fallback / 老链接不挂）：
- `workforce-l.inkpath.cc` → `127.0.0.1:8766`（8766 单独 server，仍在跑）

### 0.2 流量拓扑

```
                                公网
[浏览器/手机]
    │
    ├── HTTPS ──────────────────────────────────────────► Cloudflare Tunnel ──► Mac:8788 (unified server)
    │                                                                                │
    │                                                                                ├─ mode=qwen:
    │                                                                                │     /ws (WebSocket)
    │                                                                                │     └─► DashScope Qwen Realtime 云
    │                                                                                │          wss://llm-sa1qz61xz9dg5dd3.cn-beijing.maas.aliyuncs.com
    │                                                                                │
    │                                                                                └─ mode=livekit:
    │                                                                                      ├─ /api/livekit/token → bot_manager 拉子进程
    │                                                                                      │     └─► bot 子进程 → LiveKit Cloud (outbound)
    │                                                                                      └─ /api/agent/wake 等
    │
    └── WebRTC / WSS ──► wss://<project>.livekit.cloud  ◄─── Mac:8788 (bot 出站,经统一 token API 路由)
                              ▲
                              │  (Bot 是 LiveKit 房间参与者, outbound WS)
                              │
                       Mac Mini
                       ├─ SenseVoice STT (本地 MPS, funasr)
                       ├─ Qwen2.5 LLM (本地 llama.cpp @ 127.0.0.1:8080)
                       └─ Qwen3-TTS (本地 MPS, qwen-tts)
                       └─ LanceDB 长期记忆
```

**关键设计原则**（用户已确认）：

1. Cloudflare Tunnel **只代理前端 HTTP/HTTPS**，**绝不代理** LiveKit Cloud 的 WebSocket 域名（`*.livekit.cloud`）
2. 浏览器通过 CF Tunnel 拿 token 后，token 里 `url` 字段直接指 `wss://<project>.livekit.cloud`，浏览器**绕过 Cloudflare** 出站连 LiveKit Cloud
3. Mac Mini **零入站端口** —— bot 是 LiveKit 房间的参与者，主动 outbound 连接
4. `LIVEKIT_URL`（bot 用）和 `LIVEKIT_URL_PUBLIC`（浏览器 token 用）必须都是 `wss://*.livekit.cloud`，不能是 CF Tunnel hostname；代码里有 `assert_livekit_cloud_url` 强制校验

### 0.3 端口与进程清单

| 端口 | 进程 | LaunchAgent label | 角色 |
|---|---|---|---|
| **8788** | `web-demo/pipecat-livekit/.venv/bin/python unified_server.py` | `ai.workforce.unified` | **主入口**：单域名双 pipeline |
| 8765 | Python 3.9 `web-demo/server.py` | `ai.workforce.web-demo` | legacy Qwen Realtime relay（fallback 保留） |
| 8766 | `web-demo/pipecat-livekit/.venv/bin/python server.py` | `ai.workforce.pipecat-livekit` | legacy LiveKit token + bot 编排（`workforce-l.inkpath.cc` 后端） |
| 7880 / 7881 / 7882 (UDP) | （不再使用） | — | 之前本地 SFU 的端口，已废弃。**不要再 `docker run livekit`** |
| 8080 | `com.hermes.llama-qwen7b`（系统已有） | `com.hermes.llama-qwen7b` | 本地 llama.cpp，OpenAI 兼容 `/v1`，承载 Qwen2.5 推理 |
| 20241 | `cloudflared` metrics | — | Cloudflare Tunnel 健康端点 |
| — | `cloudflared tunnel run rta-hub` | `ai.workforce.cloudflared` | Cloudflare Tunnel |

> ⚠️ **8787 端口已被 omni-server 占用**（PID 11662，跑了 9 天的长期服务）。unified server **不要用 8787**，用 **8788**。如果换机器部署，先 `lsof -nP -iTCP:8788 -sTCP:LISTEN` 确认端口空。

---

## 1. 前置准备

### 1.1 系统依赖

```bash
# macOS 26.x，Apple Silicon
# Homebrew 在 macOS 26.x 上已知不可用（"unknown or unsupported macOS version"），
# 不要 brew install cloudflared — 直接用 GitHub release 二进制
which cloudflared || {
  curl -fsSL -o /tmp/cloudflared.pkg \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.pkg"
  sudo installer -pkg /tmp/cloudflared.pkg -target /
}

# uv（Python 包管理器，venv 不带 pip 的常见替代）
which uv || curl -fsSL https://astral.sh/uv/install.sh | sh

# Python 3.9（系统 /Library/Developer/CommandLineTools 提供，跑 8765 legacy）
# Python 3.11（uv 创建，跑 8766 legacy + 8788 unified）
```

### 1.2 Cloudflare Tunnel（一次性）

参考 `~/.cloudflared/config.yml`，关键是：

```yaml
tunnel: rta-hub
credentials-file: /Users/admin/.cloudflared/<UUID>.json

ingress:
  - hostname: workforce.inkpath.cc
    # Unified server — Qwen Realtime + LiveKit+Pipecat, single-domain mode switcher
    # (web-demo/unified_server.py, port 8788). Replaces the 8765-only entry.
    service: http://127.0.0.1:8788
  - hostname: workforce-l.inkpath.cc
    # LiveKit + Pipecat legacy (web-demo/pipecat-livekit/server.py, port 8766).
    # Kept as fallback for old share links during unified-server migration.
    service: http://127.0.0.1:8766
  # ... 其他业务的 hostname ...
  - service: http_status:404
```

**别加** `wss://nian-j64dt7qv.livekit.cloud` 这条 —— LiveKit Cloud 域名不进 Cloudflare。

### 1.3 LiveKit Cloud 凭证

在 https://cloud.livekit.io 建项目，拿：

| env var | 用途 | 例 |
|---|---|---|
| `LIVEKIT_URL` | bot 出站 + 浏览器 token 默认 `url` | `wss://nian-j64dt7qv.livekit.cloud` |
| `LIVEKIT_API_KEY` | bot 出站签 token | `APIxxxxxxxxxxxxx` |
| `LIVEKIT_API_SECRET` | bot 出站签 token | 32+ 字符 secret |
| `LIVEKIT_URL_PUBLIC` | （可选）浏览器 token 显式 `url`，通常跟 `LIVEKIT_URL` 一致 | 同上 |

写到 `~/.zshenv`（一份长期凭证，所有项目共享）：

```bash
cat >> ~/.zshenv <<'EOF'

# LiveKit Cloud
export LIVEKIT_URL="wss://nian-j64dt7qv.livekit.cloud"
export LIVEKIT_API_KEY="APIxxxxxxxxxxxxx"
export LIVEKIT_API_SECRET="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
EOF
```

### 1.4 Qwen / DashScope 凭证

`workforce.inkpath.cc` (8788 → /ws) 用 workspace-specific endpoint：

```bash
# 同样 ~/.zshenv
export QWEN_API_KEY="sk-ws-..."
export QWEN_WORKSPACE_ID="llm-..."   # 注意是 Workspace ID 不是主账号 UID
```

---

## 2. 部署步骤

### 2.1 拉代码

```bash
cd ~/work/projects/workforce
git fetch origin
# 检查 remote 有没有新 commit(部署前必须 fetch — skill code-update-redeploy-protocol 强制)
git log HEAD..origin/main --oneline
# 期望有更新;否则别 restart(避免无意义中断)
git pull --ff-only
```

### 2.2 写 `.env`（不入仓，权限 600）

`.env` 是项目根 `/Users/admin/work/projects/workforce/.env`，**不是** `web-demo/.env`（unified_server.py 的相对路径是 `WEB_DEMO_DIR.parent / ".env"`，即仓库根）。

模板：

```bash
# === Qwen Realtime (8788 /ws) ===
QWEN_API_KEY=sk-ws-...
QWEN_WORKSPACE_ID=llm-...

# === unified_server.py 启动配置 ===
HOST=0.0.0.0
PRODUCTION=1

# === LiveKit Cloud (8788 /api/livekit/token + bot) ===
LIVEKIT_URL=wss://nian-j64dt7qv.livekit.cloud
LIVEKIT_API_KEY=APIxxxxxxxxxxxxx
LIVEKIT_API_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
LIVEKIT_ROOM_NAME=voicechat-compare
# LIVEKIT_URL_PUBLIC 跟 LIVEKIT_URL 保持一致, 浏览器拿 token 后直连 LiveKit Cloud
LIVEKIT_URL_PUBLIC=wss://nian-j64dt7qv.livekit.cloud

# === Mac Mini 本地 pipeline 后端(默认跟架构讨论一致) ===
LOCAL_STT_BACKEND=sensevoice
LOCAL_LLM_BACKEND=llamacpp
LOCAL_LLM_BASE_URL=http://127.0.0.1:8080/v1
LOCAL_TTS_BACKEND=qwen3_tts
LOCAL_TTS_VOICE=Cherry
LOCAL_MEMORY_ENABLED=1
PIPELINE_ENABLE_HEARTBEATS=1

# === 8766 legacy 端口(供 pipecat-livekit/server.py) ===
PIPECAT_PORT=8766

# === Agent wake API (可选, 经 Tunnel 唤醒本地 bot) ===
# AGENT_WAKE_TOKEN=some-shared-secret
```

写完后：

```bash
chmod 600 .env
```

### 2.3 装 Python 依赖

```bash
cd ~/work/projects/workforce/web-demo/pipecat-livekit

# 1) venv 用 uv 建(因为之后还要装本地 ML 栈,uv 比 venv+pip 顺手)
uv venv .venv --python 3.11

# 2) 先装 pipecat-livekit 主依赖
uv pip install -r requirements.txt

# 3) 再装本地 ML 栈(这一坨会拽一些意外依赖,见 §6 troubleshooting)
uv pip install -r ../pipecat-local-agent/requirements-local-mac.txt
```

**重要**：本地 ML 栈是**单独一层**的依赖。`requirements.txt` 不带它，`requirements-local-mac.txt` 单独管理（包含 torch / funasr / qwen-tts / lancedb / sentence-transformers）。

**8765 legacy 用系统 Python 3.9**（`/Library/Developer/CommandLineTools/.../Python3.9`），依赖是 `aiohttp / websockets / python-dotenv`，系统 Python 自带或者 `python3 -m pip install --user`。

### 2.4 写 wrapper 脚本（不入仓）

为了让 LaunchAgent plist **不含 secret**（plist 在 `~/Library/LaunchAgents/` 是 world-readable），用 wrapper shell 脚本从 `.env` 加载凭证再 exec python。

```bash
mkdir -p ~/work/projects/workforce/.launchd-wrappers
```

`.launchd-wrappers/start-8788.sh`（8788 unified server）：

```bash
#!/bin/bash
# Unified server for workforce.inkpath.cc (single domain, both pipelines).
# Loads repo-root .env (LiveKit Cloud + Qwen + LOCAL_* backends), then execs the
# pipecat-livekit venv's python (has both websockets/aiohttp for the Qwen Realtime
# relay AND the local ML stack for the Pipecat bot subprocess).
set -euo pipefail
export PATH="/Users/admin/work/projects/workforce/web-demo/pipecat-livekit/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
cd /Users/admin/work/projects/workforce/web-demo
set -a
source /Users/admin/work/projects/workforce/.env
set +a
exec /Users/admin/work/projects/workforce/web-demo/pipecat-livekit/.venv/bin/python -u unified_server.py
```

`.launchd-wrappers/start-8766.sh`（8766 legacy Pipecat）：

```bash
#!/bin/bash
set -euo pipefail
export PATH="/Users/admin/work/projects/workforce/web-demo/pipecat-livekit/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
cd /Users/admin/work/projects/workforce/web-demo/pipecat-livekit
set -a
source /Users/admin/work/projects/workforce/.env
set +a
exec .venv/bin/python -u server.py
```

`.launchd-wrappers/start-8765.sh`（8765 legacy Qwen Realtime relay，系统 Python 3.9）：

```bash
#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
cd /Users/admin/work/projects/workforce/web-demo
set -a
source /Users/admin/work/projects/workforce/.env
set +a
exec /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python -u server.py
```

权限：

```bash
chmod 755 ~/work/projects/workforce/.launchd-wrappers/start-*.sh

# bash 语法 sanity check
bash -n ~/work/projects/workforce/.launchd-wrappers/start-8788.sh
bash -n ~/work/projects/workforce/.launchd-wrappers/start-8766.sh
bash -n ~/work/projects/workforce/.launchd-wrappers/start-8765.sh
```

加 `.gitignore`（每台机器路径不同，不入仓）：

```gitignore
.launchd-wrappers/
```

### 2.5 写 LaunchAgent plist

`~/Library/LaunchAgents/ai.workforce.unified.plist`（8788 — 主入口）：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>ai.workforce.unified</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/admin/work/projects/workforce/.launchd-wrappers/start-8788.sh</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/admin/work/projects/workforce/web-demo</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>

    <key>StandardOutPath</key>
    <string>/tmp/workforce-unified-8788.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/workforce-unified-8788.log</string>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
        <key>Crashed</key>
        <true/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>SoftResourceLimits</key>
    <dict><key>NumberOfFiles</key><integer>1024</integer></dict>

    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
```

`~/Library/LaunchAgents/ai.workforce.pipecat-livekit.plist`（8766 legacy）和 `~/Library/LaunchAgents/ai.workforce.web-demo.plist`（8765 legacy）：结构同上，Label / ProgramArguments / WorkingDirectory / StandardOutPath 分别替换。

### 2.6 加载并验证 KeepAlive

```bash
# 加载(主入口 + 两个 legacy 兜底)
launchctl load -w ~/Library/LaunchAgents/ai.workforce.unified.plist
launchctl load -w ~/Library/LaunchAgents/ai.workforce.pipecat-livekit.plist
launchctl load -w ~/Library/LaunchAgents/ai.workforce.web-demo.plist

# 等 5 秒,看 launchd 拉起
sleep 5
launchctl list | grep -E "ai.workforce.(unified|pipecat-livekit|web-demo|cloudflared)"

# 期望:每个 PID 列有数字,不是 '-';exit code 列是 '0'(刚启动)

# 端口
lsof -nP -iTCP:8788 -sTCP:LISTEN
lsof -nP -iTCP:8765 -sTCP:LISTEN
lsof -nP -iTCP:8766 -sTCP:LISTEN

# 健康
curl -s http://127.0.0.1:8788/api/config?mode=qwen | python3 -m json.tool   # Qwen voices
curl -s http://127.0.0.1:8788/api/config?mode=livekit | python3 -m json.tool # LiveKit Cloud URL
# 期望: livekitUrl == wss://nian-j64dt7qv.livekit.cloud, hasLiveKitCredentials=true
```

**KeepAlive 真测试（必做）**：

```bash
# 1. 记下当前 unified 8788 PID
OLD_PID=$(lsof -nP -iTCP:8788 -sTCP:LISTEN -t)
echo "old PID: $OLD_PID"

# 2. 强杀(SIGKILL 模拟崩溃)
kill -9 $OLD_PID

# 3. 等 launchd 拉新(throttle=10s 通常 5s 内就起来)
sleep 8

# 4. 验证:端口重新 listen + PID 变了
NEW_PID=$(lsof -nP -iTCP:8788 -sTCP:LISTEN -t)
echo "new PID: $NEW_PID"
[ "$OLD_PID" != "$NEW_PID" ] && [ -n "$NEW_PID" ] && echo "✓ KeepAlive works" || echo "❌ KeepAlive 失效,查 plist"

# 5. launchctl list 里 exit code 字段应是 -9(launchd 看到了 SIGKILL,判定崩溃,拉新实例)
launchctl list | grep ai.workforce.unified
# 期望: <new_pid>  -9  ai.workforce.unified
```

---

## 3. 公网验证（12 项全跑，2026-09-13 简化版）

```bash
# 1. launchd 守护
launchctl list | grep -E "ai.workforce.(unified|pipecat-livekit|web-demo|cloudflared)"
# 期望:全 PID 有值

# 2. 端口
lsof -nP -iTCP:8788 -iTCP:8765 -iTCP:8766 -sTCP:LISTEN 2>/dev/null | grep LISTEN

# 3. 单域名 — Qwen Realtime (默认)
curl -sk -o /dev/null -w "  /                  : HTTP %{http_code} %{time_total}s\n" \
  https://workforce.inkpath.cc/

# 4. 单域名 — Mac 本地 Agent (LiveKit 模式)
curl -sk -o /dev/null -w "  /?mode=livekit     : HTTP %{http_code} %{time_total}s\n" \
  "https://workforce.inkpath.cc/?mode=livekit"

# 4b. /livekit.html 应该 404（已删 — 2026-09-13）
curl -sk -o /dev/null -w "  /livekit.html      : HTTP %{http_code} (期望 404)\n" \
  https://workforce.inkpath.cc/livekit.html

# 5. /api/config 模式分发
curl -sk "https://workforce.inkpath.cc/api/config?mode=qwen" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'voices' in d, d
assert d['hasKey'], 'no QWEN_API_KEY'
print('  ✓ mode=qwen :', d['voice'], 'voices=', len(d['voices']))
"
curl -sk "https://workforce.inkpath.cc/api/config?mode=livekit" | python3 -c "
import sys, json
d = json.load(sys.stdin)['data']
assert d['livekitUrl'].startswith('wss://') and '.livekit.cloud' in d['livekitUrl'], d['livekitUrl']
assert d['hasLiveKitCredentials'], 'no LIVEKIT_API_KEY/SECRET'
assert d['uiMode'] == 'localAgent', f'expected localAgent, got {d[\"uiMode\"]}'
print('  ✓ mode=livekit :', d['livekitUrl'])
print('  ✓ uiMode      :', d['uiMode'])
print('  ✓ pipeline    :', d['sttBackend'], '/', d['llmBackend'], '/', d['ttsBackend'])
"

# 6. Local agent wake（lazy spawn via /api/livekit/token）
curl -sk "https://workforce.inkpath.cc/api/livekit/token?room=voicechat-compare&participant=smoke" | python3 -c "
import sys, json
d = json.load(sys.stdin)
data = d['data']
assert 'token' in data and len(data['token']) > 50, 'no token'
print('  ✓ token len=', len(data['token']), 'url=', data['url'])
print('  ✓ uiMode=', data['uiMode'], 'agentIdentity=', data['agentIdentity'])
print('  ✓ bot status=', data['bot']['status'], 'pid=', data['bot'].get('pid'))
"

# 7. 验证 LiveKit Cloud 房间真有 agent（不是 Popen 误报）
sleep 5  # 给 pipecat_agent.py 几秒钟 join room
python3 <<'PYEOF'
import asyncio, os
from livekit.api import LiveKitAPI, ListParticipantsRequest
async def main():
    url = os.environ['LIVEKIT_URL'].replace('wss://', 'https://')
    api = LiveKitAPI(url, os.environ['LIVEKIT_API_KEY'], os.environ['LIVEKIT_API_SECRET'])
    try:
        resp = await api.room.list_participants(ListParticipantsRequest(room='voicechat-compare'))
        agent = next((p for p in resp.participants if p.identity == 'Pipecat Local Agent' and p.state == 2), None)
        assert agent, f'no agent in room: {[(p.identity, p.state) for p in resp.participants]}'
        print(f'  ✓ LiveKit room voicechat-compare: {len(resp.participants)} participants, agent ACTIVE')
    finally:
        await api.aclose()
asyncio.run(main())
PYEOF

# 8. Legacy fallback (workforce-l 仍工作，作为应急回滚入口)
curl -sk -o /dev/null -w "  workforce-l.inkpath.cc : HTTP %{http_code} %{time_total}s\n" \
  https://workforce-l.inkpath.cc/ 2>/dev/null || echo "  (legacy 8766 offline — 仍 OK)"

# 9. Static 资源（注意 ?v=N 是 CF cache 戳，详见 §6.2）
for url in /static/styles.css /static/app.js /shared/mode-switcher.js \
           /static/livekit-bridge.js; do
  curl -sk -o /dev/null -w "  $url : HTTP %{http_code}\n" "https://workforce.inkpath.cc$url"
done

# 10. /livekit-static/* 应该 404（已删 — 2026-09-13）
curl -sk -o /dev/null -w "  /livekit-static/livekit-app.js : HTTP %{http_code} (期望 404)\n" \
  https://workforce.inkpath.cc/livekit-static/livekit-app.js

# 11. WebSocket /ws 真实接通 Qwen Realtime 云
python3 - <<'PYEOF'
import asyncio, json, websockets
async def main():
    async with websockets.connect("wss://workforce.inkpath.cc/ws", open_timeout=5) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": {"modalities": ["text"]}}))
        msg = await asyncio.wait_for(ws.recv(), timeout=3)
        evt = json.loads(msg)
        assert evt.get("type") == "session.created", evt
        print(f"  ✓ WS /ws session.created: {evt['event_id'][:25]}...")
asyncio.run(main())
PYEOF

# 12. 多轮 UI mock 测试（不依赖麦克风 — 见 §6.7）
# 这一项在你浏览器侧跑 — 见 §4 mock_bot 流程
```

---

## 4. 端到端浏览器验证

在桌面或手机浏览器打开 `https://workforce.inkpath.cc/`：

1. 页面应自动调 `/api/config`（默认 qwen），UI 显示"未连接"
2. 点击麦克风按钮 → 前端 `/ws` WebSocket 连接到 Qwen Realtime → 说话即听到回复
3. **顶部 pill 切换器**：点 "LiveKit + Pipecat" → URL 变 `?mode=livekit`（**不跨域**）→ 页面切换 + 重新调 `/api/config?mode=livekit`
4. 在 LiveKit 页面点 "连接并开始" → token API 拉起 bot → 加入 LiveKit Cloud 房间 → 说话即听到本地 pipeline 回复
5. **同一前端内**来回切 pill 验证：`onBeforeLeave` 应该优雅断开当前会话，刷新页面到新 mode

如果首包慢：检查 `PIPELINE_ENABLE_HEARTBEATS=1` 是否设置；检查 llama-server（端口 8080）是否在跑（`launchctl list | grep llama-qwen7b`）。

---

## 5. 升级与重启

### 5.1 拉新代码后

```bash
cd ~/work/projects/workforce
git fetch origin
git log HEAD..origin/main --oneline   # 看改了啥
# 注意检查 docs/pipecat-local-agent-mac-ops.md / docs/architecture.md / docs/unified-server-architecture.md 有没有新部署要求
git pull --ff-only                    # 无冲突才用 ff;有冲突手动解决

# 关键判断:代码改动是前端 / 后端 / 都要重启?
# - web-demo/static/ 或 web-demo/index.html 静态改 → 浏览器强制刷新就行(注意 CDN 缓存,见 §6)
# - 改 web-demo/server.py 或 bot_manager.py → 必重启 8765 / 8766
# - 改 web-demo/unified_server.py → 必重启 8788
# - 改 web-demo/pipecat-livekit/server.py → 必重启 8766
# - 改 local_services/*.py → 必重启 bot(下次 /api/agent/wake 拉的就是新代码)

launchctl unload ~/Library/LaunchAgents/ai.workforce.unified.plist
launchctl load -w ~/Library/LaunchAgents/ai.workforce.unified.plist
```

**别** `kill -9 <PID>` 然后立刻起一个 — 可能跟 launchd 的 KeepAlive 双进程抢端口。

### 5.2 改 `.env`

- Qwen / LiveKit Cloud / LOCAL_* 等 env 都是启动时 `load_dotenv()` 读一次，**改完需要重启对应 service**
- `HOST` / `PRODUCTION` 这类需要完全重启 server 才能生效

### 5.3 macOS 休眠

`pmset` 关掉自动休眠（避免夜间 Mac 睡了 LiveKit Cloud 房间断连）：

```bash
sudo pmset -a displaysleep 0
sudo pmset -a sleep 0
# 或更温和: 系统设置 → 能源 → "当显示器关闭时防止自动休眠"
```

---

## 6. 已知坑与排查

### 6.1 端口 8787 被 omni-server 占

`lsof -nP -iTCP:8787 -sTCP:LISTEN` 如果有 python 进程在 listen（一般是 omni 项目的 `omni.server`），那是另一个项目，**不要 kill**。unified server 必须用 8788 或其他空端口。如果换机器部署，先验证端口空。

### 6.1.1 `livekit-app.js` 必须显式 `?mode=livekit`

**坑过**:livekit.html 引用的 `livekit-app.js` 里 `loadConfig()` 调用 `/api/config` 不带 query,默认走 qwen shape(`{voice, voices, hasKey, hasWorkspaceId}`),没有 `data` 包装也没有 `sttBackend` 字段。`config = body.data` 后所有 `config.sttBackend` 都 undefined,页面立刻报 `连接失败: undefined is not an object (evaluating 'config.sttBackend')`。

**修法**(已加):`loadConfig()` 显式 `fetch('/api/config?mode=livekit${UI_QUERY ? \`&${UI_QUERY}\` : ""}')`。注释里写清楚为什么不能省略,避免后人再改回。

### 6.1.2 CDN 缓存拖老 JS

Cloudflare Tunnel 默认对静态资源 cache 4 小时(`max-age=14400`)。即使 unified server 给 `/livekit-static/livekit-app.js` 设了 `Cache-Control: no-cache`,CF edge 仍可能给走默认策略(尤其是 `cf-cache-status: HIT` 时)。

**两件事**:
1. **unified_server.py 给 HTML 和 static 加 middleware 注入 `Cache-Control: no-cache`**(`@web.middleware async def _no_cache_for_static` + `def _html_response()`)—— 详见源码
2. **改 JS/CSS 文件 URL 时 bump query string** —— 例 `?v=2` → `?v=3`。**这是因为 CF 缓存 key 含 query string**,新 key 会 MISS,新内容立刻生效。`livekit.html` 已用 `?v=2`,下次部署时 bump 到 `?v=3`。

**验证方式**:看响应 header 里 `cf-cache-status: MISS` vs `HIT`。HIT 说明 CF 还缓存了老版本。

### 6.2 CDN 缓存拖旧代码

CF 默认对 `/static/*.js` 缓存 4 小时。代码部署后浏览器还看到旧代码：

```bash
# 看本地 MD5 vs 公网 MD5
curl -s http://127.0.0.1:8788/static/app.js | md5
curl -s https://workforce.inkpath.cc/static/app.js | md5
curl -I https://workforce.inkpath.cc/static/app.js | grep -iE "cf-cache|cache-control"
# cf-cache-status: HIT + cache-control: max-age=14400 = CDN 钉死
```

修法：在 `unified_server.py` 的 static handler 里给 `Cache-Control: max-age=10`，或前端用 `?v=<commit-sha>` query string。

### 6.3 funasr 拽的意外依赖

`uv pip install -r requirements-local-mac.txt` 会装：

| 包 | 原因 | 必要性 |
|---|---|---|
| `gradio` + `gradio-client` + `hf-gradio` | funasr/modelscope 用 gradio 做 demo UI | 生产不需要，**但 uninstall 风险**(funasr 内部 import 路径可能引用)。保守保留 |
| `modelscope` + `modelscope-hub` | funasr 默认 backend | 可选 |
| `hydra-core` | omegaconf 配置系统 | funasr 内部用 |
| `aliyun-python-sdk-core/kms` | modelscope 拉 | 同上 |
| `fastapi` + `uvicorn` + `starlette` | funasr 1.4+ 给 gradio demo 用的 server | 同上 |

**重复装**:无（`uv pip list | sort | uniq -d` 验证过）。
**建议**:Mac Mini 8GB+ 内存、50GB+ 磁盘，反正都是本地开发机，**别折腾**。生产部署到 Linux 容器时再说，那时候可以做个精简 `requirements-prod.txt`。

### 6.4 Hermes `[IMPORTANT] Background process proc_xxx exited (exit code None)` 误报

如果用 `terminal(background=true)` + `exec python -u server.py > /tmp/log 2>&1` 这种 FD 替换模式启服务，Hermes 进程监视器会误报"进程退出"，但实际进程在跑。验证：

```bash
lsof -nP -iTCP:8788 -sTCP:LISTEN  # 看端口仍 listen
curl -s http://127.0.0.1:8788/api/config  # 看 API 仍响应
```

修法：用 LaunchAgent（§2.5）替代 `terminal(background=true)`，launchd 不会触发这个误报。

### 6.5 `LIVEKIT_URL_PUBLIC` 不能填 CF Tunnel 域名

`livekit_env.py::assert_livekit_cloud_url()` 会强制校验 `LIVEKIT_URL` 和 `LIVEKIT_URL_PUBLIC` 都必须是 `wss://*.livekit.cloud`。如果填 `wss://workforce-l.inkpath.cc`（CF Tunnel hostname），`server.py` / `unified_server.py` 启动直接 `SystemExit`。**CF Tunnel 不能代理 LiveKit WebSocket 信令**（只能代理 HTTP/HTTPS；TCP/UDP 只对 WARP 客户端可见），即使技术上能 proxy，额外握手延迟 + Idle 断联 + ICE 异常都会让通话质量崩。

### 6.6 pipecat 升级会再炸（必看）

历史上 pipecat 1.8 → 1.9 移了 settings 类到 `pipecat.services.settings`。每次升级先看 changelog，跑这条巡检：

```bash
cd ~/work/projects/workforce
grep -rn "self\.Settings(" web-demo/ 2>/dev/null
# 期望:空
```

如果搜到自定义 STT/LLM/TTS 还在用 `self.Settings(...)`，升级前先批量改成 `STTSettings(...)` / `LLMSettings(...)` / `TTSSettings(...)`。完整修复记录见 [`docs/pr-pipecat-1.9-settings-compat.md`](./pr-pipecat-1.9-settings-compat.md)。

### 6.7 多轮 UI mock 测试（不依赖麦克风）

**症状**：浏览器在 Browserbase headless / iOS Safari 上没法测试真麦克风 + SenseVoice STT 链路；想验证 UI 状态机（micBtn 红背景、voiceOrb 显示、bubble 累积）但又得跑全套真 pipeline。

**方案**：`~/work/projects/_test-fixtures/livekit-mock-bot/mock_bot.py` 直接连 LiveKit Cloud 房间，以 `Pipecat Local Agent` identity 发 transcript JSON frames——**跟真 pipecat_agent.py / transcript_bridge.py 用的 schema 完全一致**。浏览器收到同样的 data channel payload，跑同样的 `onDataReceived` handler。

```bash
# 在 Mac 上跑（不需要改任何源码）
cd ~/work/projects/workforce/web-demo/pipecat-livekit
set -a && source /Users/admin/work/projects/workforce/.env && set +a
.venv/bin/python /Users/admin/work/projects/_test-fixtures/livekit-mock-bot/mock_bot.py --flow mixed

# 可选 flows: quick (1 turn) | mixed (4 turns, 中英 + 多行) | long (单 turn 长文本)
# 可选 --delay 2.0  减慢速度（默认 1.0 = 实时节奏）
# 可选 --no-exit  跑完不进 disconnect
```

测试时浏览器要打开 `https://workforce.inkpath.cc/?mode=livekit&v=10`，点 "连接并开始"。Browserbase headless 也行（它有 LiveKit client CDN,只是没真麦克风）。

**验证脚本**（在浏览器 console 跑）：

```js
JSON.stringify({
  bubbles: Array.from(document.querySelectorAll('.bubble')).map(b => ({
    role: b.className.replace('bubble ', '').trim(),
    text: b.querySelector('.bubble-text').textContent
  })),
  micBtnClasses: Array.from(document.getElementById('micBtn').classList),
  orbDisplay: getComputedStyle(document.getElementById('voiceOrbContainer')).display
}, null, 2)
```

**2026-09-13 实测**：13 帧 mixed → 8 个 bubble，字符级一致无丢字。micBtn 加 `.active` 类（红背景），voiceOrb `display: flex`。

**注意事项**：
- mock_bot 用真 `Pipecat Local Agent` identity —— 跟真 pipecat_agent.py 互踢，**同一时间只能有一个**。
- 要测真 agent 时先让 mock_bot 退出，再 wake agent。
- mock_bot 的 transcript frames 跟 schema 完全兼容 —— 不需要改前端代码。

### 6.8 双 tunnel / 双进程抢端口

**症状**：`lsof -nP -iTCP:8788` 看有两个进程（或一个 LaunchAgent 一个手工 `terminal(background=true)`），公网 502/503 随机。

**修法**：

```bash
# 1. 找出所有相关进程
ps aux | grep -E "[u]nified_server.py" | awk '{print $2}'

# 2. launchctl unload plist(避免杀完立刻被拉起)
launchctl unload ~/Library/LaunchAgents/ai.workforce.unified.plist

# 3. 杀残留手工进程
for p in $(pgrep -f "unified_server.py"); do kill -9 $p; done
sleep 1

# 4. 重新载入 plist
launchctl load -w ~/Library/LaunchAgents/ai.workforce.unified.plist

# 5. 验证只有一个进程,PPID=1
sleep 6
ps aux | grep -E "[u]nified_server.py" | wc -l   # 期望 = 1
```

---

## 7. 相关文件索引

| 路径 | 角色 |
|---|---|
| **`web-demo/unified_server.py`** | **8788 主入口：单域名双 pipeline** |
| `web-demo/server.py` | 8765 legacy Qwen Realtime relay（aiohttp + websockets） |
| `web-demo/pipecat-livekit/server.py` | 8766 legacy LiveKit token server |
| `web-demo/pipecat-livekit/bot_manager.py` | bot 子进程管理（ensure_started / ensure_agent_started / stop_all） |
| `web-demo/pipecat-livekit/livekit_env.py` | LiveKit Cloud settings 加载 + `assert_livekit_cloud_url` 校验 |
| `web-demo/pipecat-local-agent/pipecat_agent.py` | Mac 本地 Pipecat agent 主入口 |
| `web-demo/pipecat-local-agent/local_services/config.py` | `LocalAgentConfig`：从 env 读 LOCAL_* 后端配置 |
| `web-demo/pipecat-local-agent/local_services/factory.py` | `build_stt/llm/tts` 工厂 |
| `web-demo/pipecat-local-agent/local_services/stt_sensevoice.py` | SenseVoice STT 服务（**PR 修了 1.9 兼容**） |
| `web-demo/pipecat-local-agent/local_services/llm_local.py` | llama.cpp OpenAI 兼容 LLM 客户端（1.9 兼容，类属性.Settings 仍可用） |
| `web-demo/pipecat-local-agent/local_services/tts_qwen_local.py` | Qwen3-TTS 本地 MPS（**PR 修了 1.9 兼容**） |
| `web-demo/pipecat-local-agent/local_services/memory_lancedb.py` | LanceDB 长期记忆 |
| `web-demo/static/mode-switcher.js` | **单域名** pill 切换器（点切换 `?mode=`） |
| `web-demo/pipecat-livekit/livekit.html` | livekit 页面（资源路径 `/livekit-static/*`） |
| `web-demo/index.html` | Qwen Realtime 默认页面（资源路径 `/static/*`） |
| `~/.zshenv` | 系统级凭证（LiveKit / Qwen / DashScope），所有项目共享 |
| `~/work/projects/workforce/.env` | 项目级 LOCAL_* 配置 + 凭证副本 |
| `~/work/projects/workforce/.launchd-wrappers/` | LaunchAgent wrapper（不入仓） |
| `~/Library/LaunchAgents/ai.workforce.{unified,pipecat-livekit,web-demo}.plist` | 守护进程定义 |
| `~/.cloudflared/config.yml` | Cloudflare Tunnel ingress |

---

## 8. 一键检查清单

部署完跑一遍这个脚本，**全绿**才算上线：

```bash
echo "=== 1. launchd 守护(主入口 + 两个 legacy + cloudflared)==="
launchctl list | grep -E "ai.workforce.(unified|pipecat-livekit|web-demo|cloudflared)"
# 期望 4 行,PID 列有数字

echo "=== 2. 端口 ==="
lsof -nP -iTCP:8788 -iTCP:8765 -iTCP:8766 -sTCP:LISTEN 2>/dev/null | grep LISTEN
# 期望 3 行(unified + 2 legacy)

echo "=== 3. 公网 单域名 ==="
curl -sk -o /dev/null -w "  /                       : %{http_code} %{time_total}s\n" https://workforce.inkpath.cc/
curl -sk -o /dev/null -w "  /?mode=livekit          : %{http_code} %{time_total}s\n" "https://workforce.inkpath.cc/?mode=livekit"
curl -sk -o /dev/null -w "  /?mode=livekit&ui=local : %{http_code} %{time_total}s\n" "https://workforce.inkpath.cc/?mode=livekit&ui=local"
# 期望三行 200,时延 < 200ms

echo "=== 4. LiveKit Cloud 配置(从 8788 /api/config?mode=livekit)==="
curl -s "https://workforce.inkpath.cc/api/config?mode=livekit" | python3 -c "
import sys, json
d = json.load(sys.stdin)['data']
assert d['livekitUrl'].startswith('wss://') and '.livekit.cloud' in d['livekitUrl'], d['livekitUrl']
assert d['hasLiveKitCredentials'], 'no LIVEKIT_API_KEY/SECRET'
print('  ✓ livekitUrl :', d['livekitUrl'])
print('  ✓ stt/llm/tts:', d['sttBackend'], '/', d['llmBackend'], '/', d['ttsBackend'])
"

echo "=== 5. ML 栈 ==="
cd ~/work/projects/workforce/web-demo/pipecat-livekit
.venv/bin/python -c "
import torch, funasr, lancedb, sentence_transformers
assert torch.backends.mps.is_available(), 'MPS 不可用'
print('  ✓ torch:', torch.__version__, 'mps=True')
print('  ✓ funasr:', funasr.__version__)
print('  ✓ lancedb:', lancedb.__version__)
"

echo "=== 6. llama.cpp ==="
curl -s http://127.0.0.1:8080/v1/models | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('  ✓ llama.cpp 模型数:', len(d.get('data', [])))
"

echo "=== 7. bot 拉起(local agent 路径)==="
curl -s "https://workforce.inkpath.cc/api/agent/wake?room=voicechat-compare" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('  ✓ bot:', d.get('status'), d.get('data'))
"

echo "=== 8. token API(compare 模式,触发完整 STT+LLM+TTS 加载)==="
curl -s "https://workforce.inkpath.cc/api/livekit/token?room=voicechat-compare&participant=smoke" | python3 -c "
import sys, json
d = json.load(sys.stdin)
data = d['data']
assert 'token' in data and len(data['token']) > 50, 'no token'
print('  ✓ token len=', len(data['token']), 'url=', data['url'])
"

echo "=== 9. Legacy fallback ==="
curl -sk -o /dev/null -w "  workforce-l.inkpath.cc : %{http_code} %{time_total}s\n" https://workforce-l.inkpath.cc/

echo "=== 10. Static 资源(两种 prefix 都验)==="
for url in /static/styles.css /static/app.js /shared/mode-switcher.js \
           /livekit-static/livekit-app.js /livekit-static/livekit-styles.css; do
  curl -sk -o /dev/null -w "  $url : %{http_code}\n" "https://workforce.inkpath.cc$url"
done

echo "=== 11. WebSocket /ws 接通 Qwen Realtime ==="
python3 - <<'PYEOF'
import asyncio, json, websockets
async def main():
    async with websockets.connect("wss://workforce.inkpath.cc/ws", open_timeout=5) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": {"modalities": ["text"]}}))
        msg = await asyncio.wait_for(ws.recv(), timeout=3)
        evt = json.loads(msg)
        assert evt.get("type") == "session.created", evt
        print(f"  ✓ WS /ws session.created: {evt['event_id'][:25]}...")
asyncio.run(main())
PYEOF

echo "=== 12. KeepAlive test(脚本外手动跑 §2.6)==="

echo "=== ALL GREEN ==="
```

---

## 9. 关联文档

- [`docs/unified-server-architecture.md`](./unified-server-architecture.md) — unified server 设计决策、URL 路径表、为什么单域名
- [`docs/pr-pipecat-1.9-settings-compat.md`](./pr-pipecat-1.9-settings-compat.md) — pipecat 1.9 升级踩坑与修复
- [`docs/pipecat-local-agent-architecture.md`](./pipecat-local-agent-architecture.md) — 本地 agent 架构
- [`docs/pipecat-local-agent-mac-ops.md`](./pipecat-local-agent-mac-ops.md) — Mac Mini 运维三项注意（Cloudflare / MPS / heartbeat）
- [`docs/pipecat-livekit-comparison.md`](./pipecat-livekit-comparison.md) — 两套 pipeline 对比
