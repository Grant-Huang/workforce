"""Phase 1 简易本地登录：固定测试账号 + 可校验 session token。

V1 必须登录才有 user_id，才能读写长期记忆 / bootstrap / context。
Token 为 HMAC 签名（进程重启 / 多 worker 仍可校验），另保留内存表便于主动 logout。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
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

# 主动作废的 token（logout）；签名校验为主，内存表为辅
_revoked: set[str] = set()
SESSION_TTL_SEC = 7 * 24 * 3600
COOKIE_NAME = "workforce_session"
_AUTH_SECRET = (
    os.environ.get("AUTH_SECRET")
    or os.environ.get("QWEN_API_KEY")
    or "workforce-dev-auth-secret"
)


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


def _b64url_encode(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return urlsafe_b64decode(text + pad)


def create_session(username: str) -> str:
    """签发可跨进程校验的 token（hex 风格签名，避免 cookie 特殊字符问题）。"""
    if username not in USERS:
        raise KeyError(username)
    payload = {
        "u": username,
        "exp": int(time.time()) + SESSION_TTL_SEC,
        "n": secrets.token_hex(8),
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_AUTH_SECRET.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def get_session(token: str | None) -> dict[str, Any] | None:
    if not token or token in _revoked:
        return None
    # 兼容旧版纯 hex 内存 token：已无法校验（进程重启后失效）
    if "." not in token:
        return None
    try:
        body, sig = token.rsplit(".", 1)
        expect = hmac.new(
            _AUTH_SECRET.encode("utf-8"), body.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
        username = payload.get("u")
        exp = float(payload.get("exp") or 0)
        if not username or username not in USERS or exp < time.time():
            return None
        user = USERS[username]
        return {
            "user_id": user["user_id"],
            "username": username,
            "channel_id": user["channel_id"],
            "expires_at": exp,
        }
    except Exception:
        return None


def destroy_session(token: str | None) -> None:
    if token:
        _revoked.add(token)


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


def _cookie_secure(request: web.Request) -> bool:
    if request.secure:
        return True
    proto = request.headers.get("X-Forwarded-Proto", "")
    return proto.split(",")[0].strip().lower() == "https"


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
            "channel_id": user["channel_id"],
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
        secure=_cookie_secure(request),
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
