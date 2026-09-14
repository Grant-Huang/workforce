# VoiceChat

一个语音对话产品原型：像 ChatGPT 那样，既能打字聊天，也能像语音模式一样实时说话、实时听 AI 回复，还能"口述转文字"——说一段话，AI 整理成通顺的书面文字再发出去，类似 Typeless。默认对接 **Qwen-Omni-Realtime**（阿里云 DashScope/百炼），也可以切换回 OpenAI Realtime API——两者的 WebSocket 事件协议基本一致。

**当前只有网页版**（`web-demo/`）。这个项目最早同时维护一个原生 iOS App（SwiftUI）和网页 demo，两边跑同一套协议、功能对齐；2026-08-28 决定暂时把 iOS 端全部移除，只保留网页版——原因是这个开发环境没有 Swift 工具链/Xcode，iOS 端的所有改动全靠"源码走查 + 端侧真机反馈"来回沟通，尤其是自定义 WebSocket 传输层（为了绕开 `URLSessionWebSocketTask` 在这条链路上的已知问题而手写的 swift-nio-transport-services 实现）在真机上排查出好几处根本性的连接 bug，投入产出比很差；相比之下网页版从一开始就能在这个环境里用 Playwright 端到端跑通、验证快得多。**以后如果要重新做原生 iOS，`VoiceChat/` 目录被删除前的完整源码和这次调试过程留下的详细文档（`docs/realtime-websocket-transport-design.md`）都还在 git 历史里**，不是凭空重来。

网页 demo 的完整说明（怎么跑起来、架构、功能设计、实测记录）见 [`web-demo/README.md`](web-demo/README.md)。

## 相关文档

- [`docs/app-design.md`](docs/app-design.md) — 完整功能设计（三种交互模式、口述转文字、回复长度策略等）
- [`docs/qwen-realtime-voice-setup.md`](docs/qwen-realtime-voice-setup.md) — Qwen Realtime API 踩过的坑（域名选择、模型/音色选型）
- [`docs/agentnexus-memory-integration-proposal.md`](docs/agentnexus-memory-integration-proposal.md) — 给智枢（AgentNexus）团队的记忆体系集成建议
- [`docs/roadmap-todo.md`](docs/roadmap-todo.md) — 开发讨论纪要/已完成事项记录（其中部分历史条目涉及已移除的 iOS 端，作为决策背景保留，不代表当前代码状态）
- [`docs/testing-deployment.md`](docs/testing-deployment.md) — 部署/测试相关笔记（同样有历史 iOS 内容）
- [`docs/realtime-websocket-transport-design.md`](docs/realtime-websocket-transport-design.md) — 已移除的 iOS 端 WebSocket 传输层调试全过程，供以后重新做原生 iOS 时参考
