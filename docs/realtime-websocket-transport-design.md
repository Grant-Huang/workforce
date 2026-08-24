# RealtimeClient 底层传输选型设计文档

状态：草案，等待确认后再实现。

## 1. 背景：问题的来龙去脉

真机反馈的语音会话连不上（2026-08-24），一路排查下来经过了三个阶段，每个阶段都有确凿证据，记录在这里避免下一个人重新踩一遍：

1. **一开始怀疑配置**：API Key、Workspace ID、模型名反复核对/轮换，中间踩过几次"新旧 Key/Workspace 没配对"的坑，但最终都排除了——用 `curl --http1.1` 能用完全相同的配置连上（`session.created` 正常返回）。
2. **定位到 `URLSessionWebSocketTask` 本身**：确认配置没问题后，真机上依然 100% 复现"Socket is not connected"（`nw_flow_add_write_request ... cannot accept write requests`），不分 WiFi/蜂窝网络。关键证据：
   - 用 curl 默认的 HTTP/2 发送 WebSocket 升级请求会被网关误判（服务器对普通 HTTPS 请求正常支持 h2，ALPN 会协商成 h2）
   - 一个完全无关的公开 WebSocket 测试服务（`echo.websocket.org`，不支持 h2）在同一台设备上连接正常
   - 结论：`URLSessionWebSocketTask` 在跟这台支持 h2 的服务器握手时，走了 HTTP/2 的 WebSocket 引导路径（RFC 8441 extended CONNECT），而这条路径在这台设备/这个服务器组合下不可用
3. **换成 Starscream（PR #14/#15）之后，暴露了新问题**：Starscream 的自定义 engine（`useCustomEngine: true`）不走 `URLSessionWebSocketTask`，理论上能绕开上面的 h2 问题，但真机测试出现了两个新报错：
   - 语音会话：`HTTP upgrade rejected [handshake HTTP 401]`——查证是 Starscream 自身的已知未修复 bug（[daltoniam/Starscream#1061](https://github.com/daltoniam/Starscream/issues/1061)，标题几乎和我们的情况一字不差："WebSocket fails with 401 notAnUpgrade using valid Bearer token (works in Postman)"），Authorization 头在它的握手请求里没有正确传出去
   - 口述转文字：`masked and rsv data is not currently supported`——Starscream 帧解析器的另一个已知问题（[#791](https://github.com/daltoniam/Starscream/issues/791)、[#46](https://github.com/daltoniam/Starscream/issues/46)，其中 #46 是很多年前的老 issue，长期未修）

也就是说：**问题不是我们的配置，也不是 iOS/App 层面有什么系统性限制**（公开 WebSocket 测试证明了这点），而是"跟这台支持 HTTP/2 的服务器做 WebSocket 握手"这件事，两个我们试过的方案（系统自带的 `URLSessionWebSocketTask`、第三方库 Starscream）都各自有 bug 踩不过去。

## 2. 调研：其他 iOS App 是怎么做的

### 2.1 OpenAI 官方对移动端的建议

OpenAI Realtime API 官方文档（2026）明确建议：**浏览器和移动端客户端应该用 WebRTC，而不是 WebSocket**；WebSocket 定位是给后端服务器用的。这也侧面印证了我们这几天踩的坑不是孤例——移动端直连 Realtime WebSocket 本身就不是官方主推的路径。

但这条建议对我们**不直接适用**：我们对接的是阿里云 Qwen-Omni-Realtime，官方文档里只有 `/api-ws/v1/realtime` 这一个 WebSocket 端点，没有公开的 WebRTC 接入方式。换供应商或者等阿里云出 WebRTC 接口都不现实，所以只能继续在 WebSocket 这条路上想办法。

### 2.2 现成 WebSocket 库对比

| 方案 | 成熟度 | SPM 支持 | 是否已知能避开 h2 问题 | 结论 |
|---|---|---|---|---|
| `URLSessionWebSocketTask`（系统自带） | 官方维护 | 原生 | ❌ 已确认踩坑 | 排除（本次问题起因） |
| Starscream | 老牌但维护低迷（issue 常年不修） | ✅ | ✅（自定义 engine 不走 URLSessionWebSocketTask） | 排除（自身有匹配我们症状的未修复 bug） |
| SocketRocket（Facebook/Square，Objective-C） | 非常成熟，通过 Autobahn 全套一致性测试 | ❌ 官方仓库至今没有 `Package.swift`（[facebookincubator/SocketRocket#643](https://github.com/facebookincubator/SocketRocket/issues/643) 长期挂着未解决） | 大概率能（比 iOS 的 h2/URLSessionWebSocketTask 出现得早得多，用的是原始 NSStream/CFStream） | 排除（项目用 XcodeGen + SPM，没有 CocoaPods/Carthage 基础设施，引入 ObjC 库+手动 vendor 的集成成本和风险不比自己实现小） |
| **swift-nio + swift-nio-transport-services** | Apple 官方维护（Swift Server Work Group），生产级，被 Vapor/AsyncHTTPClient 等广泛使用 | ✅ 官方 SPM 包 | ✅ 直接暴露 `NWParameters`/`NWProtocolTLS.Options`，可以显式控制 ALPN，从根上避开 h2 协商 | **推荐** |

### 2.3 为什么推荐 swift-nio-transport-services

- **不是从零造轮子**：WebSocket 握手升级用官方的 `NIOWebSocketClientUpgrader`（swift-nio 仓库自带官方参考实现，路径 `Sources/NIOWebSocketClient`），帧编解码用官方的 `NIOWebSocket` 模块——这些代码经过 SwiftNIO 生态大规模生产验证（Vapor、AsyncHTTPClient 等都基于它），比 Starscream 这种个人维护、issue 常年不修的库可信得多。
- **直接解决根因**：`swift-nio-transport-services`（NIOTS）是 SwiftNIO 官方给 Apple 平台的适配层，底层就是 `Network.framework`，可以通过 `NWParameters`/`NWProtocolTLS.Options` 显式只提供 `http/1.1` 的 ALPN，不用像 Starscream 那样赌库内部行为——这是我们目前唯一能\*确定\*从协议层面避开 h2 问题的方案（而不是"某个库正好没走 h2 路径"这种间接推断）。
- **SPM 原生支持**，跟项目现有的 XcodeGen + SPM 集成方式完全兼容。

代价：API 层级比 Starscream 低（channel pipeline 模型，不是简单的 `WebSocket(request:).connect()`），需要自己搭 `NIOTSConnectionBootstrap` + HTTP upgrade handler + WebSocket frame 收发的胶水代码，工作量比换 Starscream 大，但比完全手写 RFC 6455 帧解析（含 masking/RSV 位处理——这正是 Starscream 出 bug 的地方）风险小得多，因为帧解析这部分完全复用官方代码。

## 3. 推荐方案

**用 `swift-nio` + `swift-nio-transport-services` 重写 `RealtimeClient` 的连接层**，如果实现过程中发现工作量或依赖体积（NIO core + NIOTransportServices + NIOWebSocket 三个包）超出预期，再退回到"基于 `Network.framework` 手写一个最小 WebSocket 客户端"这个之前讨论过的兜底方案（只需要文本/二进制帧收发，不需要压缩扩展，协议本身不算复杂，只是 Starscream 的教训说明这类实现容易在边界情况出 bug，所以列为备选而非首选）。

## 4. 实现设计（草案）

### 4.1 依赖（project.yml）

```yaml
packages:
  NIOTransportServices:
    url: https://github.com/apple/swift-nio-transport-services.git
    from: 1.21.0   # 具体版本号需在实现时用 WebFetch/浏览器核实最新稳定版
targets:
  VoiceChat:
    dependencies:
      - package: NIOTransportServices
        product: NIOTransportServices
      # NIOWebSocket 通过 swift-nio 传递依赖引入，若 XcodeGen 解析不到需显式加 swift-nio 包
```

移除 Starscream 依赖。

### 4.2 `RealtimeClient` 对外接口（不变）

维持现有公开 API 不变，`ConversationViewModel`/`DictationViewModel` 不需要改动：
`connect(baseURL:apiKey:model:instructions:voice:turnDetection:autoRespond:modalities:onSessionReady:)`、`sendUserText`、`disconnect`、`sendAudioChunk`、`updateInstructions`、`requestResponse`、`cancelResponse`，以及所有 `on*` 回调。

### 4.3 内部结构

- 用 `NIOTSConnectionBootstrap`（而不是 `ClientBootstrap`，后者是通用 NIO，不会用 `Network.framework`/不便控制 ALPN）建立连接
- 在 bootstrap 的 TLS 配置里，通过 `NWProtocolTLS.Options` 显式设置 ALPN 协议列表为仅 `["http/1.1"]`，这是整个改动里解决根因的关键一行
- Channel pipeline：`HTTPClientUpgradeHandler` + `NIOWebSocketClientUpgrader` 完成握手升级 → 升级成功后 pipeline 里只剩 WebSocket frame 编解码器
- 握手失败时的诊断：`NIOWebSocketClientUpgrader`/`HTTPClientUpgradeHandler` 在收到非 101 响应时会把原始 HTTP 响应回调出来，同样可以提取真实状态码，保留 `[handshake HTTP xxx]` 这个诊断格式
- 所有回调通过 `DispatchQueue.main.async`（或 NIO 的 `EventLoopFuture` 显式 hop 到主线程）投递，维持 PR #11 建立的"所有回调必须在主线程"约定
- 发送：JSON payload 序列化后包成 `WebSocketFrame`（`.binary` opcode，跟之前用 `.data` 帧一致）写入 channel
- 接收：`WebSocketFrame` 的 `.text`/`.binary` opcode 统一转成 `Data` 走现有的 `RealtimeIncomingEvent(json:)` 解析路径（这部分完全不用动）

### 4.4 待实现时确认的细节

- `swift-nio-transport-services` 的确切最新稳定版本号（本文档写作时无法从环境直接访问 Swift Package Index 得到 100% 准确的当前版本，需要实现前用工具核实，避免 `project.yml` 指向一个不存在的版本号导致 SPM 解析失败）
- `NIOWebSocketClientUpgrader` 的具体 API 签名（`maxFrameSize`、`requestKey` 等参数）需要对照 swift-nio 当前版本的源码核实，不要凭记忆硬编
- 没有 Swift 工具链的环境限制依旧存在，这次改动 API 层级更低、出错面更大，**强烈建议实现完之后先在本地跑一次 `xcodegen generate` + 编译，再上真机测试**，不要像上一轮 Starscream 那样直接源码走查后就合并——这次多花一轮本地验证，比再来一次"合并后真机报错、回头修"的循环成本低。

## 5. 下一步

等待确认这个方向可以开始实现，实现范围：
1. `project.yml` 换依赖（去掉 Starscream，加 NIOTransportServices）
2. 重写 `RealtimeClient.swift` 连接层
3. 本地验证 `xcodegen generate` + 编译通过（如果这次有可用的 Swift 工具链/CI，务必用上）
4. 真机测试语音会话、文字对话、口述转文字三个入口
