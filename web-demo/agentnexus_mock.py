"""Mock AgentNexus server — stands in for AgentNexus *after* the changes proposed in
docs/agentnexus-memory-integration-proposal.md: a long-lived bearer token (a stand-in
for a `pt_...` personal token) works directly against the plain channel memory/messages
REST API, no special Agent-Bridge-only auth path needed.

Phase 1（Runtime V1.1-final）：加强预置测试事实；种子频道带 role/interests 画像，
便于「你好呀 → 画像引导」验收。业务真相源仍是本 Mock，LanceDB 仅镜像缓存。

部分条目设 include_in_hot=False：不进 bootstrap 热记忆，需经 MemoryProvider
主动检索 AgentNexus（search_memory / GET ?q=）才能命中，用于验收 Retrieval 路径。
"""
import itertools
import re
from datetime import datetime, timezone
from typing import Any

from aiohttp import web

_id_counter = itertools.count(100)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# 与 auth.USERS["demo"] 对齐的种子画像（Mock 侧可查询）
SEED_PROFILES: dict[str, dict[str, Any]] = {
    "demo-channel": {
        "user_id": "user-demo",
        "language": "zh-CN",
        "timezone": "Asia/Shanghai",
        "response_style": "concise",
        "role": "产线主管",
        "interests": ["设备异常", "排产", "OEE"],
        "preferred_topics": ["M102温度告警", "今日排产", "产线A"],
        "greeting_style": "warm_brief",
        "workplace": "A厂",
    },
}


def _seed_channel() -> dict:
    now = _now_iso()
    return {
        "profile": dict(SEED_PROFILES["demo-channel"]),
        "memory": [
            {
                "entry_id": "seed-anchor-1",
                "channel_id": "demo-channel",
                "layer": "ANCHOR",
                "title": None,
                "content": "用户是 A 厂产线主管，偏好中文交流，日常关注设备异常与排产。",
                "sort_order": 1,
                "created_by": "mock-seed",
                "creator_type": "user",
                "created_at": now,
                "updated_at": now,
            },
            {
                "entry_id": "seed-anchor-2",
                "channel_id": "demo-channel",
                "layer": "ANCHOR",
                "title": None,
                "content": "M102 属于产线 A；用户习惯叫它「产线A那台」。",
                "sort_order": 2,
                "created_by": "mock-seed",
                "creator_type": "user",
                "created_at": now,
                "updated_at": now,
            },
            {
                "entry_id": "seed-anchor-3",
                "channel_id": "demo-channel",
                "layer": "ANCHOR",
                "title": None,
                "content": "用户负责早班产线稳定性，遇到停机希望先口头同步再补工单。",
                "sort_order": 3,
                "created_by": "mock-seed",
                "creator_type": "user",
                "created_at": now,
                "updated_at": now,
            },
            {
                "entry_id": "seed-progress-1",
                "channel_id": "demo-channel",
                "layer": "PROGRESS",
                "title": None,
                "content": "昨天 M102 出现过温度过高告警，两分钟后短暂停机，维修怀疑冷却风扇，用户还在跟进是否更换完成。",
                "sort_order": 1,
                "created_by": "mock-seed",
                "creator_type": "user",
                "created_at": now,
                "updated_at": now,
            },
            {
                "entry_id": "seed-progress-2",
                "channel_id": "demo-channel",
                "layer": "PROGRESS",
                "title": None,
                "content": "今天早班排产偏紧：产线 A 优先保 OEE，用户想先确认有没有新的设备异常再调排产。",
                "sort_order": 2,
                "created_by": "mock-seed",
                "creator_type": "user",
                "created_at": now,
                "updated_at": now,
            },
            {
                "entry_id": "seed-progress-3",
                "channel_id": "demo-channel",
                "layer": "PROGRESS",
                "title": None,
                "content": "本周目标：盯住 M102 复发风险，并把语音助手接到日常晨会汇报里。",
                "sort_order": 3,
                "created_by": "mock-seed",
                "creator_type": "user",
                "created_at": now,
                "updated_at": now,
            },
            {
                "entry_id": "seed-decisions-1",
                "channel_id": "demo-channel",
                "layer": "DECISIONS",
                "title": "今日日程",
                "content": "上午巡线看 M102；下午 3 点跟智枢团队开会同步记忆集成；晚上 7 点健身预约。",
                "sort_order": 1,
                "created_by": "mock-seed",
                "creator_type": "user",
                "created_at": now,
                "updated_at": now,
            },
            {
                "entry_id": "seed-decisions-2",
                "channel_id": "demo-channel",
                "layer": "DECISIONS",
                "title": "排产偏好",
                "content": "用户希望早班优先保证 OEE，异常停机要先口头同步再写工单。",
                "sort_order": 2,
                "created_by": "mock-seed",
                "creator_type": "user",
                "created_at": now,
                "updated_at": now,
            },
            {
                "entry_id": "seed-decisions-3",
                "channel_id": "demo-channel",
                "layer": "DECISIONS",
                "title": "沟通偏好",
                "content": "开场喜欢直奔主题：先问设备/排产，不喜欢冗长客套。",
                "sort_order": 3,
                "created_by": "mock-seed",
                "creator_type": "user",
                "created_at": now,
                "updated_at": now,
            },
            # --- 以下默认不进 hot：专门验收「主动查 AgentNexus」---
            {
                "entry_id": "seed-retrieve-wo-8842",
                "channel_id": "demo-channel",
                "layer": "PROGRESS",
                "title": "工单 WO-8842",
                "content": "M102 冷却风扇更换，状态进行中；负责人李工（分机 203）；预计今天 14:00 前完工。备件号 FAN-M102，库位 A-03-12。",
                "sort_order": 10,
                "include_in_hot": False,
                "created_by": "mock-seed",
                "creator_type": "user",
                "created_at": now,
                "updated_at": now,
            },
            {
                "entry_id": "seed-retrieve-handover",
                "channel_id": "demo-channel",
                "layer": "PROGRESS",
                "title": "早班交接备注",
                "content": "夜班产线 A 的 OEE 报 82.4%；物料短缺 SKU-PLATE-7 已催采购，预计明天上午到货；临时用产线 B 挪了 20 片应急。",
                "sort_order": 11,
                "include_in_hot": False,
                "created_by": "mock-seed",
                "creator_type": "user",
                "created_at": now,
                "updated_at": now,
            },
            {
                "entry_id": "seed-retrieve-mes-contact",
                "channel_id": "demo-channel",
                "layer": "ANCHOR",
                "title": "联系人",
                "content": "设备异常升级联系人：维修班长王强（手机尾号 6688）；MES 工单系统入口账号与产线主管同名。",
                "sort_order": 12,
                "include_in_hot": False,
                "created_by": "mock-seed",
                "creator_type": "user",
                "created_at": now,
                "updated_at": now,
            },
            {
                "entry_id": "seed-retrieve-spare",
                "channel_id": "demo-channel",
                "layer": "DECISIONS",
                "title": "备件策略",
                "content": "M102 关键备件安全库存：冷却风扇 FAN-M102 不少于 2 个；温度传感器 TS-102 不少于 1 个。低于安全库存要自动开采购申请。",
                "sort_order": 13,
                "include_in_hot": False,
                "created_by": "mock-seed",
                "creator_type": "user",
                "created_at": now,
                "updated_at": now,
            },
        ],
        "messages": [
            {
                "msg_id": "seed-msg-1",
                "channel_id": "demo-channel",
                "content": "昨晚 M102 又抖了一下，先记着，明早对一下 WO-8842 进度。",
                "sender_type": "user",
                "created_at": now,
            },
            {
                "msg_id": "seed-msg-2",
                "channel_id": "demo-channel",
                "content": "已记下：跟进 WO-8842，并核对 FAN-M102 库存。",
                "sender_type": "assistant",
                "created_at": now,
            },
        ],
    }


_store: dict[str, dict] = {"demo-channel": _seed_channel()}


def _channel(channel_id: str) -> dict:
    return _store.setdefault(channel_id, {"memory": [], "messages": [], "profile": {}})


def get_seed_memory(channel_id: str = "demo-channel") -> list[dict]:
    """供 Context Gateway / MemoryService 镜像用（进程内真相源）。"""
    return list(_channel(channel_id).get("memory") or [])


def get_seed_profile(channel_id: str = "demo-channel") -> dict:
    ch = _channel(channel_id)
    return dict(ch.get("profile") or SEED_PROFILES.get(channel_id) or {})


def _query_tokens(query: str) -> list[str]:
    q = (query or "").strip()
    tokens = [m.group(0) for m in re.finditer(r"[A-Za-z0-9][\w\-]*|[\u4e00-\u9fff]{2,}", q)]
    return tokens or ([q] if q else [])


def search_memory(channel_id: str, query: str, limit: int = 8) -> list[dict]:
    """进程内主动检索 AgentNexus Mock（真相源），供 MemoryProvider / REST ?q= 使用。"""
    tokens = _query_tokens(query)
    if not tokens:
        return []
    scored: list[tuple[int, dict]] = []
    for e in get_seed_memory(channel_id):
        text = f"{e.get('title') or ''}：{e.get('content') or ''}" if e.get("title") else (e.get("content") or "")
        text_l = text.lower()
        score = 0
        for t in tokens:
            tl = t.lower()
            if tl in text_l:
                score += max(2, len(t))
        if score > 0:
            scored.append((score, e))
    scored.sort(key=lambda x: (-x[0], x[1].get("sort_order") or 0))
    return [e for _, e in scored[:limit]]


def entry_to_text(entry: dict) -> str:
    title = entry.get("title")
    content = entry.get("content") or ""
    return f"{title}：{content}" if title else content


def create_memory_entry(channel_id: str, layer: str, content: str, title: str | None = None) -> dict:
    channel = _channel(channel_id)
    layer = layer.upper()
    now = _now_iso()
    entry = {
        "entry_id": f"mock-{next(_id_counter)}",
        "channel_id": channel_id,
        "layer": layer,
        "title": title,
        "content": content,
        "sort_order": len([e for e in channel["memory"] if e["layer"] == layer]) + 1,
        "created_by": "voice-app-mock-user",
        "creator_type": "user",
        "created_at": now,
        "updated_at": now,
    }
    channel["memory"].append(entry)
    return entry


def _require_bearer(request: web.Request) -> None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or len(auth.removeprefix("Bearer ").strip()) < 4:
        raise web.HTTPUnauthorized(
            text='{"detail": "无效 Token"}', content_type="application/json"
        )


async def list_memory(request: web.Request) -> web.Response:
    _require_bearer(request)
    channel_id = request.match_info["channel_id"]
    q = (request.query.get("q") or "").strip()
    if q:
        entries = search_memory(channel_id, q, limit=int(request.query.get("limit") or 20))
    else:
        entries = list(_channel(channel_id)["memory"])
        layer = request.query.get("layer")
        if layer:
            entries = [e for e in entries if e["layer"] == layer.upper()]
    return web.json_response(entries)


async def create_memory(request: web.Request) -> web.Response:
    _require_bearer(request)
    channel_id = request.match_info["channel_id"]
    body = await request.json()
    layer = (body.get("layer") or "").upper()
    if layer not in ("ANCHOR", "DECISIONS", "PROGRESS"):
        return web.json_response({"detail": "invalid layer"}, status=400)
    entry = create_memory_entry(channel_id, layer, body.get("content", ""), body.get("title"))
    return web.json_response(entry)


async def get_profile(request: web.Request) -> web.Response:
    _require_bearer(request)
    channel_id = request.match_info["channel_id"]
    return web.json_response(get_seed_profile(channel_id))


async def list_messages(request: web.Request) -> web.Response:
    _require_bearer(request)
    channel = _channel(request.match_info["channel_id"])
    return web.json_response(channel["messages"])


async def create_message(request: web.Request) -> web.Response:
    _require_bearer(request)
    channel_id = request.match_info["channel_id"]
    channel = _channel(channel_id)
    body = await request.json()
    message = {
        "msg_id": f"mock-msg-{next(_id_counter)}",
        "channel_id": channel_id,
        "content": body.get("content", ""),
        "sender_type": body.get("sender_type", "user"),
        "created_at": _now_iso(),
    }
    channel["messages"].append(message)
    return web.json_response(message)


def register(app: web.Application, prefix: str = "/agentnexus-mock") -> None:
    app.router.add_get(f"{prefix}/api/v1/channels/{{channel_id}}/memory/", list_memory)
    app.router.add_post(f"{prefix}/api/v1/channels/{{channel_id}}/memory/", create_memory)
    app.router.add_get(f"{prefix}/api/v1/channels/{{channel_id}}/profile", get_profile)
    app.router.add_get(f"{prefix}/api/v1/channels/{{channel_id}}/messages", list_messages)
    app.router.add_post(f"{prefix}/api/v1/channels/{{channel_id}}/messages", create_message)
