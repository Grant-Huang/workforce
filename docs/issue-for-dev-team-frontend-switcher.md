# 前端：Qwen Realtime ↔ LiveKit+Pipecat 切换器

## 背景

两套语音 demo 跑在不同端口（8765 / 8766）。Pipecat 的 `livekit.html` 已有跳回 8765 的链接，但 8765 的 `index.html` 缺少对称入口。

## 需求

### P0
- 在 `web-demo/index.html` 顶部提供跳转到 8766 的入口
- 样式复用 `.compare-link` / `.modeSwitcher`

### P1
- Segmented control（pill 切换器）在两种方案间切换
- 切换前必须清理会话：
  - 8765：`stopAllSessions()`（voice + text + dictation）
  - 8766：`disconnectAll()`（LiveKit room + mic）

### 验收标准
1. 从 8765 切到 8766：mic 关闭、WebSocket 断开、8766 页面正常加载
2. 从 8766 切到 8765：LiveKit 断开、mic 关闭、8765 页面正常加载
3. 切换器当前页高亮、不可重复点击

## 实现

| 文件 | 改动 |
|------|------|
| `web-demo/static/mode-switcher.js` | 共享 pill 切换逻辑 |
| `web-demo/index.html` | topbar 加 `#modeSwitcher` |
| `web-demo/static/styles.css` | `.modeSwitcher` / topbar 布局 |
| `web-demo/static/app.js` | `stopAllSessions()` + 挂载切换器 |
| `web-demo/pipecat-livekit/livekit.html` | 用 pill 替换单独 compare-link |
| `web-demo/pipecat-livekit/static/livekit-styles.css` | pill 样式（暗色主题） |
| `web-demo/pipecat-livekit/static/livekit-app.js` | `disconnectAll()` + 挂载切换器 |
| `web-demo/pipecat-livekit/server.py` | 提供 `/shared/mode-switcher.js` |
| `docs/pipecat-livekit-comparison.md` | 更新对比实验步骤 |

## 手动验收

```bash
# 终端 1 — 配置 .env 中 LIVEKIT_URL=wss://*.livekit.cloud 后
cd web-demo && python server.py          # 8765

# 终端 2
cd web-demo/pipecat-livekit && python server.py  # 8766
# （不需要本地 livekit-server；SFU 在 LiveKit Cloud）
```

1. 打开 http://127.0.0.1:8765/ ，开始语音会话
2. 点击「LiveKit + Pipecat」pill → 应跳转到 8766，DevTools 里 8765 的 WS 已关闭
3. 在 8766 连接 LiveKit，再切回「Qwen Realtime」→ room 断开，8765 正常加载
