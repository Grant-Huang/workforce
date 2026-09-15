"""Phase 1 简易本地登录：固定测试账号 + 内存 session token。

V1 必须登录才有 user_id，才能读写长期记忆 / bootstrap / context。
实现刻意简单：进程内 dict，重启失效——够用验收，不做生产级持久化。
"""
from __future__ import annotations

import secrets
import time
from typing import Any

from aiohttp import web

# 固定测试账号（密码仅用于本地 demo，勿当生产凭据）
USERS: dict[str, dict[str, Any]] = {
    "demo": {
        "password": "demo123",
        "user_id": "user-demo",
        "display_name": "产线演示用户",
        "channel_id": "demo-channel",
        "profile": {
            "language": "zh-CN",
            "timezone": "Asia/Shanghai",
            "response_style": "concise",
            "role": "产线主管",
            "interests": ["设备异常", "排产", "OEE"],
            "preferred_topics": ["M102温度告警", "今日排产", "产线A"],
            "greeting_style": "warm_brief",
            "workplace": "A厂",
        },
    },
}

# token -> {user_id, username, expires_at}
_sessions: dict[str, dict[str, Any]] = {}
SESSION_TTL_SEC = 7 * 24 * 3600
COOKIE_NAME = "workforce_session"


def _api_ok(data: Any = None, message: str | None = None, status: int = 200) -> web.Response:
    body: dict[str, Any] = {"status": "success", "data": data if data is not None else {}}
    if message:
        body["message"] = message
    return web.json_response(body, status=status)


def _api_err(message: str, status: int = 400, data: Any = None) -> web.Response:
    return web.json_response(
        {"status": "error", "data": data if data is not None else {}, "message": message},
        status=status,
    )


def create_session(username: str) -> str:
    user = USERS[username]
    # hex 仅含 [0-9a-f]，避免 token_urlsafe 的 -/_ 在个别 WebKit cookie 解析路径上踩坑
    token = secrets.token_hex(32)
    _sessions[token] = {
        "user_id": user["user_id"],
        "username": username,
        "channel_id": user["channel_id"],
        "expires_at": time.time() + SESSION_TTL_SEC,
    }
    return token


def get_session(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    sess = _sessions.get(token)
    if not sess:
        return None
    if sess["expires_at"] < time.time():
        _sessions.pop(token, None)
        return None
    return sess


def destroy_session(token: str | None) -> None:
    if token:
        _sessions.pop(token, None)


def extract_token(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ").strip() or None
    return request.cookies.get(COOKIE_NAME)


def require_user(request: web.Request) -> dict[str, Any]:
    """返回当前用户记录；未登录抛 HTTPUnauthorized。"""
    token = extract_token(request)
    sess = get_session(token)
    if not sess:
        raise web.HTTPUnauthorized(
            text='{"status":"error","data":{},"message":"未登录"}',
            content_type="application/json",
        )
    username = sess["username"]
    user = USERS[username]
    return {
        "user_id": user["user_id"],
        "username": username,
        "display_name": user["display_name"],
        "channel_id": user["channel_id"],
        "profile": user["profile"],
        "token": token,
    }


async def login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _api_err("无效 JSON", 400)
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = USERS.get(username)
    if not user or user["password"] != password:
        return _api_err("用户名或密码错误", 401)
    token = create_session(username)
    resp = _api_ok(
        {
            "token": token,
            "user_id": user["user_id"],
            "username": username,
            "display_name": user["display_name"],
            "profile": user["profile"],
        },
        message="登录成功",
    )
    resp.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SEC,
        httponly=True,
        samesite="Lax",
        path="/",
        secure=False,
    )
    return resp


async def logout(request: web.Request) -> web.Response:
    token = extract_token(request)
    destroy_session(token)
    resp = _api_ok({}, message="已退出")
    resp.del_cookie(COOKIE_NAME, path="/")
    return resp


async def me(request: web.Request) -> web.Response:
    try:
        user = require_user(request)
    except web.HTTPUnauthorized:
        return _api_err("未登录", 401)
    return _api_ok(
        {
            "user_id": user["user_id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "profile": user["profile"],
            "channel_id": user["channel_id"],
        }
    )


def register(app: web.Application) -> None:
    app.router.add_post("/api/auth/login", login)
    app.router.add_post("/api/auth/logout", logout)
    app.router.add_get("/api/auth/me", me)
