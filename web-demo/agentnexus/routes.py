"""Mock HTTP 表面：与真实 AgentNexus channel memory API 对齐。"""
from __future__ import annotations

from aiohttp import web

from . import store
from .search import search_entries


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
        entries = search_entries(
            store.list_memory(channel_id), q, limit=int(request.query.get("limit") or 20)
        )
    else:
        entries = store.list_memory(channel_id)
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
    entry = store.create_memory_entry(channel_id, layer, body.get("content", ""), body.get("title"))
    return web.json_response(entry)


async def get_profile(request: web.Request) -> web.Response:
    _require_bearer(request)
    channel_id = request.match_info["channel_id"]
    return web.json_response(store.get_profile(channel_id))


async def list_messages(request: web.Request) -> web.Response:
    _require_bearer(request)
    return web.json_response(store.list_messages(request.match_info["channel_id"]))


async def create_message(request: web.Request) -> web.Response:
    _require_bearer(request)
    channel_id = request.match_info["channel_id"]
    body = await request.json()
    message = store.create_message(
        channel_id, body.get("content", ""), body.get("sender_type", "user")
    )
    return web.json_response(message)


def register(app: web.Application, prefix: str = "/agentnexus-mock") -> None:
    app.router.add_get(f"{prefix}/api/v1/channels/{{channel_id}}/memory/", list_memory)
    app.router.add_post(f"{prefix}/api/v1/channels/{{channel_id}}/memory/", create_memory)
    app.router.add_get(f"{prefix}/api/v1/channels/{{channel_id}}/profile", get_profile)
    app.router.add_get(f"{prefix}/api/v1/channels/{{channel_id}}/messages", list_messages)
    app.router.add_post(f"{prefix}/api/v1/channels/{{channel_id}}/messages", create_message)
