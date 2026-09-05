# Qwen Realtime 语音模型配置指南

这份文档记录了这个项目在接入 Qwen Realtime 语音 API 过程中踩过的坑、最终验证有效的配置，以及背后的原因。目的是让以后接手这块代码的人（包括未来的自己）不用重新踩一遍。

## 一句话结论

**用业务空间专属域名，不要用共享域名；模型用 `qwen3.5-omni-flash-realtime`；音色用 `Ethan` 或列表里任何一个测过的音色，不要用 `Chelsie`。**

```
wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model=qwen3.5-omni-flash-realtime
```

## 踩过的坑：共享域名的多天稳定性问题

项目最初用的是官方文档里最先给出的通用地址：

```
wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen-omni-turbo-realtime
```

这个地址**能连上**，`session.created` 也能正常收到，问题出在后续交互上——发第一个或第二个 `session.update`（初始配置 / 每轮记忆 patch）之后，经常**完全没有响应**，不报错、不断开，就是安静下来，有时候等 30 秒都没用。偶尔会收到明确的 `1011 (internal error) Too many requests` 拒绝，但更多时候是彻底静默。

排查过程中验证/排除过的假设：
- **不是我们自己代码的 bug**——同样的消息序列直连 Qwen（不经过任何中转层）连续测试，一样会复现。
- **不是"没等 ack 就发下一条"的时序问题**——加了显式等待 `session.updated` 确认、加了超时兜底，问题依然存在。
- **不是简单的"发送间隔不够"**——同一个 check-in 周期里，"等 5 秒再发第二条" 一次成功、"等 3 秒再发" 却在第一条就卡住，说明不是靠加延迟能解决的干净规律。
- **像是限流的一种更隐蔽的表现**——阿里云官方限流文档里提到的 `Throttling.BurstRate`（"调用频率骤增，触发系统稳定性保护"）是最接近的解释，但即使是"干净"、没有密集连接历史的单次测试，也会随机复现，说明限流本身可能也不是全部原因。

前后花了将近一整天，跨多个 check-in 周期反复测试，才最终定位到根本原因——不是限流、不是时序、不是代码 bug，是**用错了域名**。

## 根本原因与修复：共享域名 vs 业务空间专属域名

阿里云百炼的 API 域名分两种：

| | 共享域名 | 业务空间专属域名 |
|---|---|---|
| 地址 | `dashscope.aliyuncs.com` | `{WorkspaceId}.cn-beijing.maas.aliyuncs.com` |
| 官方描述 | "原有中心化共享域名，当前可继续使用" | "推荐用于生产环境"，提供"更高吞吐、更低时延与业务空间级流量隔离" |
| 我们的实测 | 反复出现 `session.update` 无响应 | 换成这个之后，问题**彻底消失** |

换成专属域名之后重新测试：单轮对话全程 4.5 秒内干净走完；4 轮记忆问答连续测试（包括之前一直失败的"背景信息没有相关内容时应该诚实说不知道，而不是编造答案"这道题）**全部正确**，每一轮 `session.update` 都在 0.3 秒内收到确认，没有一次卡住。

另外一个印证：专属域名对不支持的参数会**立刻、清晰地报错**（比如后面提到的音色报错），不是像共享域名那样该报错时不报错、直接哑火——这本身就是两种域名稳定性差异的一种体现。

### 怎么获取 Workspace ID

1. 登录[阿里云百炼控制台](https://bailian.console.aliyun.com/)，选好地域（这个项目用的是北京，即 `cn-beijing`）。
2. 点右上角的用户菜单图标，弹出的对话框里就有"业务空间ID"。
3. 如果需要看所有业务空间的列表（需要主账号权限）：右上角设置图标 → [业务空间管理](https://bailian.console.aliyun.com/?tab=globalset#/efm/business_management) → Workspace ID 列。

**重要**：Workspace ID 跟账号 ID / 主账号 UID 是完全不同的两个东西，容易搞混——账号 ID 是一串纯数字，格式类似 `1292837354873682`；Workspace ID 是带前缀的字符串，格式类似 `llm-sa1qz61xz9dg5dd3`。如果拿账号 ID 当 Workspace ID 去拼域名，两种完全不同类型的接口（Realtime WebSocket、普通的 `chat/completions` HTTP 接口）都会一致返回 `BadRequest.IllegalEndpoint: Workspace endpoint is invalid.`——这个报错本身就是"ID 类型拿错了"的信号，不是"专属域名不支持这个能力"。

官方文档：[获取 APP ID 和 Workspace ID](https://help.aliyun.com/zh/model-studio/obtain-the-app-id-and-workspace-id)

## 模型选择：qwen-omni-turbo-realtime → qwen3.5-omni-flash-realtime

项目最初用的 `qwen-omni-turbo-realtime` 是较早的模型版本。目前（2026-08）官方在售的 Realtime 模型是 Qwen3.5-Omni 系列：

| 模型 | 定位 | 响应速度 | 说明 |
|---|---|---|---|
| `qwen3.5-omni-flash-realtime` | **推荐默认** | 更快（约 5.1 秒） | 官方原话："大多数生产场景中平衡延迟、质量和响应的默认选择"，价格也更低 |
| `qwen3.5-omni-plus-realtime` | 高性能 | 略慢（约 5.8 秒） | 智能水平接近 Qwen3.5-Plus，成本不是主要考虑因素时选这个 |

两者都已经在这个项目的实际测试中验证过（连通性 + 多轮记忆问答），都能正常工作。项目默认用 Flash——语音对话场景延迟比顶尖推理能力更重要，日常场景（日程回忆、工作总结）Flash 完全够用。如果以后发现某些复杂问题 Flash 答得不够好，可以在设置里手动切换成 Plus，不需要改代码。

**新模型的额外能力**（`qwen-omni-turbo-realtime` 不具备或没有明确支持）：支持 Function Calling（模型可自主判断是否需要调用外部工具）、支持联网搜索、语义打断能力增强。这些暂时还没在这个项目里用上，是后续可以探索的方向。

官方文档：[Qwen-Omni-Realtime](https://help.aliyun.com/zh/model-studio/realtime)

## 音色：Chelsie 不再支持，改用 Ethan

旧模型默认用的音色 `Chelsie`，在新模型上会报错：

```
InternalError.Algo.InvalidParameter: Voice 'Chelsie' is not supported.
```

新模型支持的音色是完全不同的一套，官方列了 47 个（[完整列表](https://help.aliyun.com/zh/model-studio/omni-voice-list)）。项目里最初挑了 14 个测过确认可用的，2026-08-23 按需求精简成 6 个；2026-08-28 按新需求换成了下面这 8 个（代码里 `web-demo/server.py` 的 `VOICE_OPTIONS`）：

`Griet`（女，荷兰语，成熟文艺）、`Jennifer`（女，美式英语，电影质感，**默认**）、`Katerina`（女，俄语，御姐音色）、`Mia`（女，中文，细腻慢生活）、`Alek`（男，俄语，冷峻中带暖）、`Andre`（男，葡萄牙语，磁性沉稳）、`Bodega`（男，西班牙语，热情大叔）、`Emilien`（男，法语，浪漫大哥哥）。

跟之前那 6 个不同，这 8 个**还没有逐个做过 `session.update` 实测**（只是从官方音色列表文档核对了描述），换成默认值 `Jennifer` 或切换到列表里的其他音色之后，如果服务端返回类似 `Voice 'XXX' is not supported.` 的报错，参照上面 `Chelsie` 的坑排查——先照着 `test_voice_list.py`（scratchpad 里的测试脚本，逻辑很简单）测一下能不能用，别直接假设文档里写的就一定对。

## 代码里的配置位置

| 项目 | 文件 | 说明 |
|---|---|---|
| 网页 demo | `web-demo/server.py` | `QWEN_WORKSPACE_ID`、`QWEN_MODEL`、`QWEN_VOICE`、`VOICE_OPTIONS`，从 `.env` 读取，未设置 `QWEN_WORKSPACE_ID` 时自动回退到共享域名 |
| 网页 demo | `.env.example` | Workspace ID 配置说明和获取链接 |
| iOS App | `VoiceChat/Settings/RealtimeConfigStore.swift` | `defaultModel`、`defaultVoice`、`voiceOptions`、`workspaceId`、`effectiveBaseURL`（有 Workspace ID 时自动派生专属域名，否则用手填的 `baseURL`） |
| iOS App | `VoiceChat/Settings/SettingsView.swift` | 设置页的 Workspace ID 输入框、音色改成了 Picker（原来是自由文本框） |

两边都保留了"没有 Workspace ID 时回退到共享域名"这条路径，不强制要求配置——但强烈建议配置，共享域名的稳定性问题是真实、可复现的，不是这个项目独有的偶发情况。

## 参考链接

- [限流 - 大模型服务平台百炼](https://help.aliyun.com/zh/model-studio/rate-limit)
- [错误码 - 大模型服务平台百炼](https://help.aliyun.com/zh/model-studio/error-code)
- [Base URL 总览](https://help.aliyun.com/zh/model-studio/base-url)
- [获取 APP ID 和 Workspace ID](https://help.aliyun.com/zh/model-studio/obtain-the-app-id-and-workspace-id)
- [业务空间实现资源隔离与权限管控](https://help.aliyun.com/zh/model-studio/use-workspace)
- [Qwen-Omni-Realtime 主文档](https://help.aliyun.com/zh/model-studio/realtime)
- [非实时/实时模型支持的音色列表](https://help.aliyun.com/zh/model-studio/omni-voice-list)
- 详细的排查过程和原始测试记录见 `web-demo/README.md`（按日期组织，2026-08-21 到 2026-08-23）
