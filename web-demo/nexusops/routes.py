"""NexusOps Mock HTTP：与真实运营检索 API 对齐的最小表面。"""
from __future__ import annotations

from aiohttp import web

from . import search as ops_search


def _require_bearer(request: web.Request) -> None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or len(auth.removeprefix("Bearer ").strip()) < 4:
        raise web.HTTPUnauthorized(
            text='{"detail": "无效 Token"}', content_type="application/json"
        )


async def api_search(request: web.Request) -> web.Response:
    _require_bearer(request)
    q = (request.query.get("q") or "").strip()
    limit = int(request.query.get("limit") or 8)
    results = ops_search.search(q, limit=limit) if q else []
    return web.json_response({"status": "success", "data": {"results": results}})


async def api_lines(request: web.Request) -> web.Response:
    _require_bearer(request)
    return web.json_response({"status": "success", "data": {"lines": ops_search.list_lines()}})


async def api_line(request: web.Request) -> web.Response:
    _require_bearer(request)
    line = ops_search.get_line(request.match_info["line_id"])
    if not line:
        return web.json_response({"status": "error", "message": "not found"}, status=404)
    return web.json_response({"status": "success", "data": line})


async def api_orders(request: web.Request) -> web.Response:
    _require_bearer(request)
    q = (request.query.get("q") or "").strip()
    if q:
        hits = [d for d in ops_search.search(q, limit=20) if d.get("resource") == "order"]
        return web.json_response({"status": "success", "data": {"results": hits}})
    return web.json_response({"status": "success", "data": {"orders": ops_search.list_orders()}})


async def api_order(request: web.Request) -> web.Response:
    _require_bearer(request)
    order = ops_search.get_order(request.match_info["order_id"])
    if not order:
        return web.json_response({"status": "error", "message": "not found"}, status=404)
    return web.json_response({"status": "success", "data": order})


async def api_machine(request: web.Request) -> web.Response:
    _require_bearer(request)
    machine = ops_search.get_machine(request.match_info["machine_id"])
    if not machine:
        return web.json_response({"status": "error", "message": "not found"}, status=404)
    return web.json_response({"status": "success", "data": machine})


async def api_overview(request: web.Request) -> web.Response:
    _require_bearer(request)
    return web.json_response({"status": "success", "data": ops_search.get_overview()})


def register(app: web.Application, prefix: str = "/nexusops-mock") -> None:
    app.router.add_get(f"{prefix}/api/v1/search", api_search)
    app.router.add_get(f"{prefix}/api/v1/overview", api_overview)
    app.router.add_get(f"{prefix}/api/v1/lines", api_lines)
    app.router.add_get(f"{prefix}/api/v1/lines/{{line_id}}", api_line)
    app.router.add_get(f"{prefix}/api/v1/orders", api_orders)
    app.router.add_get(f"{prefix}/api/v1/orders/{{order_id}}", api_order)
    app.router.add_get(f"{prefix}/api/v1/machines/{{machine_id}}", api_machine)
