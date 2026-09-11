"""Token + static server for the LiveKit comparison UI.

Serves the browser page, issues LiveKit participant tokens, and auto-starts
the Pipecat bot subprocess when the user connects.

Run:
  cd web-demo/pipecat-livekit
  python server.py
  # -> http://127.0.0.1:8766

Also requires LiveKit server (see README / start-livekit.sh).
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv

try:
    from pipecat.runner.livekit import generate_token
except ImportError as exc:
    raise SystemExit("Install dependencies: pip install -r requirements.txt") from exc

from bot_manager import ensure_started, get_status, stop as stop_bot

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent.parent / ".env")

HOST = os.environ.get("PIPECAT_HOST", os.environ.get("HOST", "127.0.0.1"))
PORT = int(os.environ.get("PIPECAT_PORT", "8766"))

LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "ws://127.0.0.1:7880")
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "secret")
DEFAULT_ROOM = os.environ.get("LIVEKIT_ROOM_NAME", "voicechat-compare")


async def index(_request):
    return web.FileResponse(BASE_DIR / "livekit.html")


async def config(_request):
    return web.json_response(
        {
            "status": "success",
            "data": {
                "livekitUrl": LIVEKIT_URL,
                "defaultRoom": DEFAULT_ROOM,
                "hasLiveKitCredentials": bool(LIVEKIT_API_KEY and LIVEKIT_API_SECRET),
                "hasQwenKey": bool(os.environ.get("QWEN_API_KEY")),
                "architecture": "LiveKit WebRTC + Pipecat (STT -> LLM -> TTS)",
                "compareUrl": "http://127.0.0.1:8765/",
            },
        }
    )


async def livekit_token(request):
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        return web.json_response(
            {"status": "error", "message": "LIVEKIT_API_KEY / LIVEKIT_API_SECRET not configured"},
            status=500,
        )

    room = request.query.get("room") or DEFAULT_ROOM
    participant = request.query.get("participant") or f"user-{uuid.uuid4().hex[:8]}"

    bot_status = ensure_started(room)
    if bot_status.get("status") == "error":
        return web.json_response(
            {"status": "error", "message": bot_status.get("message", "bot 启动失败")},
            status=503,
        )

    token = generate_token(room, participant, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)

    return web.json_response(
        {
            "status": "success",
            "data": {
                "token": token,
                "url": LIVEKIT_URL,
                "room": room,
                "participant": participant,
                "bot": bot_status,
            },
        }
    )


async def bot_status(_request):
    return web.json_response({"status": "success", "data": get_status()})


async def on_shutdown(_app):
    stop_bot()


async def shared_mode_switcher(_request):
    return web.FileResponse(BASE_DIR.parent / "static" / "mode-switcher.js")


app = web.Application()
app.on_shutdown.append(on_shutdown)
app.router.add_get("/", index)
app.router.add_get("/api/config", config)
app.router.add_get("/api/bot/status", bot_status)
app.router.add_get("/api/livekit/token", livekit_token)
app.router.add_get("/shared/mode-switcher.js", shared_mode_switcher)
app.router.add_static("/static/", BASE_DIR / "static")

if __name__ == "__main__":
    print(f"LiveKit comparison UI: http://{HOST}:{PORT}/")
    print(f"LiveKit URL: {LIVEKIT_URL}  default room: {DEFAULT_ROOM}")
    print("Bot 会在浏览器连接时自动启动（需 QWEN_API_KEY）")
    web.run_app(app, host=HOST, port=PORT)
