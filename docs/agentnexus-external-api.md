# AgentNexus 外部对接接口

> 智枢 = **个人/协作记忆与日程**。产线/订单等运营事实见 [`nexusops-external-api.md`](./nexusops-external-api.md)。  
> 能力地图：[`capability-map.md`](./capability-map.md)

## 1. 鉴权：用户是否传 Token？

**要传。** 所有 AgentNexus API 使用：

```http
Authorization: Bearer <token>
```

| Token 类型 | 说明 |
|---|---|
| PersonalToken（推荐） | 长期 `pt_...`，适合语音 App / 后台同步 |
| 短期 JWT | 用户登录后下发，过期需刷新 |

**与 App 登录分离**：

- Workforce App session（`/api/auth/login`）≠ AgentNexus token  
- 生产环境：服务端用 `AGENTNEXUS_TOKEN` 注入，或经 `/api/config` 下发（勿把生产 token 写进前端仓库）  
- Mock：任意长度 ≥4 的 Bearer 均可通过（默认 `pt_mock_demo_token`）

## 2. Base URL / Channel

```text
{AGENTNEXUS_BASE_URL}/api/v1/channels/{channel_id}/...
```

| 环境变量 | 含义 |
|---|---|
| `AGENTNEXUS_MODE` | `mock`（默认）\| `real` |
| `AGENTNEXUS_BASE_URL` | 真实智枢根地址；设置后倾向 real |
| `AGENTNEXUS_TOKEN` | Bearer token |
| `AGENTNEXUS_CHANNEL_ID` | 默认 `demo-channel` |
| `PRODUCTION=1` | 不注册本地 `/agentnexus-mock` |

Mock 前缀：`/agentnexus-mock`（与真实路径后缀一致，便于整体切换）。

## 3. 端点

### 3.1 列出 / 检索记忆

```http
GET /api/v1/channels/{channel_id}/memory/
GET /api/v1/channels/{channel_id}/memory/?q=今日待办&limit=20
GET /api/v1/channels/{channel_id}/memory/?layer=DECISIONS
```

响应：`MemoryEntry[]`

### 3.2 创建记忆条目

```http
POST /api/v1/channels/{channel_id}/memory/
Content-Type: application/json

{ "layer": "PROGRESS", "content": "帮我记住明天早会前提醒催 WO-8842", "title": null }
```

`layer` ∈ `ANCHOR` \| `DECISIONS` \| `PROGRESS`

### 3.3 画像

```http
GET /api/v1/channels/{channel_id}/profile
```

### 3.4 消息（原始对话）

```http
GET  /api/v1/channels/{channel_id}/messages
POST /api/v1/channels/{channel_id}/messages
{ "content": "...", "sender_type": "user"|"assistant" }
```

## 4. MemoryEntry schema

```json
{
  "entry_id": "seed-todos-today",
  "channel_id": "demo-channel",
  "layer": "DECISIONS",
  "title": "今日待办",
  "content": "…",
  "sort_order": 2,
  "include_in_hot": true,
  "created_by": "mock-seed",
  "creator_type": "user",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

`include_in_hot` 为 Mock/语音侧扩展；真实智枢可忽略。  
**适合写入的内容**：日程、待办、偏好、个人笔记。  
**不要**把订单进度/设备状态当记忆真相写入——那些属 NexusOps。

## 5. 错误码

| HTTP | 含义 |
|---|---|
| 401 | Token 缺失/无效 |
| 400 | layer 非法等 |
| 404 | 频道不存在（真实侧） |

## 6. 切换清单 Mock → Real

1. 申请 PersonalToken + channel_id  
2. 设 `AGENTNEXUS_MODE=real`、`AGENTNEXUS_BASE_URL`、`AGENTNEXUS_TOKEN`、`AGENTNEXUS_CHANNEL_ID`  
3. 确认未暴露 `/agentnexus-mock`  
4. Smoke：`GET .../memory/?q=待办`、`GET .../profile`

## 7. 验收问句（AgentNexus）

- 帮我查一下今天的日程  
- 今天待办有哪些？  
- 我负责什么 / 我的偏好是什么？  
- 帮我记住：下午提醒催采购  

运营追问（产线/订单）应命中 **NexusOps**，见能力地图。
