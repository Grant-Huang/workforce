"""Token + static server for the LiveKit comparison UI.

Serves the browser page, issues LiveKit participant tokens, and auto-starts
the Pipecat bot subprocess when the user connects (compare mode).

Mac local agent mode (`?ui=local` or PIPECAT_UI_MODE=local_agent): reuses this
same frontend; only issues user tokens — run `pipecat_agent.py` separately.

Run:
  cd web-demo/pipecat-livekit
  python server.py
  # -> http://127.0.0.1:8766
  # Mac local agent UI: http://127.0.0.1:8766/?ui=local

Also requires LiveKit server (compare) or LiveKit Cloud credentials (local agent).
"""

from __future__ import annotations

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
AGENT_IDENTITY = os.environ.get("LIVEKIT_AGENT_IDENTITY", "Pipecat Local Agent")
UI_MODE_ENV = os.environ.get("PIPECAT_UI_MODE", "").strip().lower()


def _query_ui_mode(request: web.Request) -> str:
    if UI_MODE_ENV in ("local", "local_agent"):
        return "localAgent"
    if request.query.get("ui") == "local":
        return "localAgent"
    return "compare"


def _is_local_agent_ui(request: web.Request) -> bool:
    return _query_ui_mode(request) == "localAgent"


def _default_room(request: web.Request) -> str:
    if _is_local_agent_ui(request):
        return os.environ.get("LIVEKIT_ROOM_NAME", "voicechat-local")
    return DEFAULT_ROOM


async def index(_request):
    return web.FileResponse(BASE_DIR / "livekit.html")


async def config(request):
    ui_mode = _query_ui_mode(request)
    is_local = ui_mode == "localAgent"
    data = {
        "uiMode": ui_mode,
        "livekitUrl": LIVEKIT_URL,
        "defaultRoom": _default_room(request),
        "agentIdentity": AGENT_IDENTITY if is_local else "Pipecat Agent",
        "autoSpawnBot": not is_local,
        "hasLiveKitCredentials": bool(LIVEKIT_API_KEY and LIVEKIT_API_SECRET),
        "hasQwenKey": bool(os.environ.get("QWEN_API_KEY")),
        "compareUrl": "http://127.0.0.1:8765/",
    }
    if is_local:
        data["architecture"] = "LiveKit Cloud + Mac Mini local agent (STT → LLM → TTS)"
        data["llmBackend"] = os.environ.get("LOCAL_LLM_BACKEND", "llamacpp")
        data["sttBackend"] = os.environ.get("LOCAL_STT_BACKEND", "sensevoice")
        data["ttsBackend"] = os.environ.get("LOCAL_TTS_BACKEND", "qwen3_tts")
    else:
        data["architecture"] = "LiveKit WebRTC + Pipecat (STT -> LLM -> TTS)"
    return web.json_response({"status": "success", "data": data})


async def livekit_token(request):
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        return web.json_response(
            {"status": "error", "message": "LIVEKIT_API_KEY / LIVEKIT_API_SECRET not configured"},
            status=500,
        )

    room = request.query.get("room") or _default_room(request)
    participant = request.query.get("participant") or f"user-{uuid.uuid4().hex[:8]}"
    is_local = _is_local_agent_ui(request)

    bot_status = None
    if not is_local:
        bot_status = ensure_started(room)
        if bot_status.get("status") == "error":
            return web.json_response(
                {"status": "error", "message": bot_status.get("message", "bot 启动失败")},
                status=503,
            )

    token = generate_token(room, participant, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)

    payload = {
        "token": token,
        "url": LIVEKIT_URL,
        "room": room,
        "participant": participant,
        "uiMode": "localAgent" if is_local else "compare",
        "agentIdentity": AGENT_IDENTITY if is_local else "Pipecat Agent",
    }
    if bot_status is not None:
        payload["bot"] = bot_status

    return web.json_response({"status": "success", "data": payload})


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
    print(f"LiveKit UI: http://{HOST}:{PORT}/")
    print(f"Mac local agent UI: http://{HOST}:{PORT}/?ui=local")
    print(f"LiveKit URL: {LIVEKIT_URL}  default room: {DEFAULT_ROOM}")
    if UI_MODE_ENV in ("local", "local_agent"):
        print("PIPECAT_UI_MODE=local_agent — token API 不会自动 spawn bot")
    else:
        print("Compare mode: bot 会在浏览器连接时自动启动（需 QWEN_API_KEY）")
    web.run_app(app, host=HOST, port=PORT)
