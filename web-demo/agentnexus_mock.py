"""Mock AgentNexus server — stands in for AgentNexus *after* the changes proposed in
docs/agentnexus-memory-integration-proposal.md: a long-lived bearer token (a stand-in
for a `pt_...` personal token) works directly against the plain channel memory/messages
REST API, no special Agent-Bridge-only auth path needed.

Mirrors the real schema from grant-huang/agentnexus's
backend/app/api/v1/memory/routes.py (EntryResponse fields) closely enough that the
client code written against this mock should need only a base-URL change to point at
the real thing once it exists. State is in-process memory only — restarting the
server resets it back to the seed data below.
"""
import itertools
from datetime import datetime, timezone

from aiohttp import web

_id_counter = itertools.count(100)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_channel() -> dict:
    now = _now_iso()
    return {
        "memory": [
            {
                "entry_id": "seed-anchor-1",
                "channel_id": "demo-channel",
                "layer": "ANCHOR",
                "title": None,
                "content": "用户是这个语音 App 项目的开发者，偏好中文交流，正在做「语音 App 接智枢记忆」的原型验证。",
                "sort_order": 1,
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
                "content": "本周目标：跑通语音 App 本地缓存 + AgentNexus Mock 的完整链路，验证记忆能不能被语音检索出来。",
                "sort_order": 1,
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
                "content": "下午 3 点跟智枢团队开会，同步语音 App 记忆集成方案的评审意见；晚上 7 点有一个健身预约。",
                "sort_order": 1,
                "created_by": "mock-seed",
                "creator_type": "user",
                "created_at": now,
                "updated_at": now,
            },
        ],
        "messages": [],
    }


_store: dict[str, dict] = {"demo-channel": _seed_channel()}


def _channel(channel_id: str) -> dict:
    return _store.setdefault(channel_id, {"memory": [], "messages": []})


def _require_bearer(request: web.Request) -> None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or len(auth.removeprefix("Bearer ").strip()) < 4:
        raise web.HTTPUnauthorized(
            text='{"detail": "无效 Token"}', content_type="application/json"
        )
    # Any non-empty bearer token is accepted here — that's the point of the mock:
    # simulating the proposed fix where a long-lived pt_ token works on this endpoint
    # instead of only short-lived login JWTs (proposal §2).


async def list_memory(request: web.Request) -> web.Response:
    _require_bearer(request)
    channel = _channel(request.match_info["channel_id"])
    entries = channel["memory"]
    layer = request.query.get("layer")
    if layer:
        entries = [e for e in entries if e["layer"] == layer.upper()]
    return web.json_response(entries)


async def create_memory(request: web.Request) -> web.Response:
    _require_bearer(request)
    channel_id = request.match_info["channel_id"]
    channel = _channel(channel_id)
    body = await request.json()
    layer = (body.get("layer") or "").upper()
    if layer not in ("ANCHOR", "DECISIONS", "PROGRESS"):
        return web.json_response({"detail": "invalid layer"}, status=400)

    now = _now_iso()
    entry = {
        "entry_id": f"mock-{next(_id_counter)}",
        "channel_id": channel_id,
        "layer": layer,
        "title": body.get("title"),
        "content": body.get("content", ""),
        "sort_order": len([e for e in channel["memory"] if e["layer"] == layer]) + 1,
        "created_by": "voice-app-mock-user",
        "creator_type": "user",
        "created_at": now,
        "updated_at": now,
    }
    channel["memory"].append(entry)
    return web.json_response(entry)


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
    app.router.add_get(f"{prefix}/api/v1/channels/{{channel_id}}/messages", list_messages)
    app.router.add_post(f"{prefix}/api/v1/channels/{{channel_id}}/messages", create_message)
