# NexusOps 外部对接接口

> NexusOps = **工厂运营真相源**（产线 / 设备 / 订单 / 物料 / 质量 / 维修）。  
> 个人日程与待办见 [`agentnexus-external-api.md`](./agentnexus-external-api.md)。  
> 能力地图：[`capability-map.md`](./capability-map.md)

## 1. 鉴权：是否传 Token？

**要传。**

```http
Authorization: Bearer <token>
```

| 环境 | Token |
|---|---|
| Mock | 任意 ≥4 字符 Bearer（默认 `ops_mock_demo_token`） |
| Real | NexusOps 下发的 API Token / 服务账号 |

与 App session、AgentNexus token **三者分离**，分别用环境变量注入。

## 2. Base URL

```text
{NEXUSOPS_BASE_URL}/api/v1/...
```

| 环境变量 | 含义 |
|---|---|
| `NEXUSOPS_MODE` | `mock`（默认）\| `real` |
| `NEXUSOPS_BASE_URL` | 真实 NexusOps 根地址 |
| `NEXUSOPS_TOKEN` | Bearer token |
| `PRODUCTION=1` | 不注册 `/nexusops-mock` |

Mock 前缀：`/nexusops-mock`

## 3. 端点（Mock 最小契约；真实侧应对齐或提供等价检索）

### 3.1 统一检索（语音主路径）

```http
GET /api/v1/search?q=产线情况&limit=8
```

响应：

```json
{
  "status": "success",
  "data": {
    "results": [
      {
        "resource": "overview",
        "entity_id": "plant-A-shift-morning",
        "title": "今日产线总览",
        "text": "A 厂早班：产线 A 受阻…",
        "citation": {
          "system": "nexusops",
          "resource": "overview",
          "id": "plant-A-shift-morning",
          "label": "NexusOps overview/plant-A-shift-morning"
        }
      }
    ]
  }
}
```

**Citation 必填**（Runtime 分层 C）：无 citation 的结果不得当业务事实播报。

### 3.2 资源直查

```http
GET /api/v1/overview
GET /api/v1/lines
GET /api/v1/lines/{line_id}
GET /api/v1/orders
GET /api/v1/orders?q=延误
GET /api/v1/orders/{order_id}
GET /api/v1/machines/{machine_id}
```

统一返回：

```json
{ "status": "success", "data": { ... } }
```

## 4. 领域实体（Mock 种子）

| 资源 | 示例 ID | 要点 |
|---|---|---|
| line | `line-A/B/C` | A 受阻、B 正常、C 换型 |
| machine | `M102` | 产线 A，故障，关联 WO-8842 |
| order | `ORD-20260915-01` | 产线 A，延误 |
| work_order | `WO-8842` | 冷却风扇更换，进行中 |
| material | `SKU-PLATE-7` | 短缺，影响 ORD-03 |

实体互链：产线A ↔ M102 ↔ WO-8842 ↔ ORD-20260915-01，支撑追问。

## 5. 错误码

| HTTP | 含义 |
|---|---|
| 401 | Token 无效 |
| 404 | 实体不存在 |

## 6. 切换清单 Mock → Real

1. 申请 API Token  
2. `NEXUSOPS_MODE=real` + `NEXUSOPS_BASE_URL` + `NEXUSOPS_TOKEN`  
3. 确认 `GET /api/v1/search?q=延误` 返回带 `citation` 的结果  
4. 语音侧 `NexusOpsProvider` 自动走 HttpClient

## 7. 验收问句（NexusOps）

1. 现在产线情况怎么样？  
2. 设备情况怎么样？ → M102 故障 / WO-8842  
3. 这条线（产线 A）的订单怎样？ → ORD-20260915-01  
4. 订单是否延误？ → 已延误，预计明天 11:00  

个人「今日待办里还有什么」→ **AgentNexus**，不要用 NexusOps 顶替。
