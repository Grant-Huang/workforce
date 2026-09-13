"""Token + static server for the LiveKit comparison UI.

Serves the browser page, issues LiveKit participant tokens, and auto-starts
the Pipecat bot subprocess when the user connects (compare mode).

All modes use **LiveKit Cloud** (wss://*.livekit.cloud) — configure LIVEKIT_*
in repo-root .env.

Run:
  cd web-demo/pipecat-livekit
  python server.py
  # -> http://127.0.0.1:8766
  # Mac local agent UI: http://127.0.0.1:8766/?ui=local
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
UI_MODE_ENV = os.environ.get("PIPECAT_UI_MODE", "").strip().lower()

try:
    from livekit_env import livekit_settings

    _LK = livekit_settings()
except ValueError as exc:
    raise SystemExit(f"LiveKit Cloud 配置错误: {exc}") from exc

LIVEKIT_URL = _LK["url"]
LIVEKIT_API_KEY = _LK["api_key"]
LIVEKIT_API_SECRET = _LK["api_secret"]
DEFAULT_ROOM = _LK["room"]
AGENT_IDENTITY = _LK["agent_identity"]

# Optional browser-facing SFU URL override (defaults to LiveKit Cloud LIVEKIT_URL).
LIVEKIT_URL_PUBLIC = os.environ.get("LIVEKIT_URL_PUBLIC", "").strip()


def _token_url() -> str:
    """URL the browser should use to reach the LiveKit SFU."""
    return LIVEKIT_URL_PUBLIC or LIVEKIT_URL



def _query_ui_mode(request: web.Request) -> str:
    if UI_MODE_ENV in ("local", "local_agent"):
        return "localAgent"
    if request.query.get("ui") == "local":
        return "localAgent"
    return "compare"


def _is_local_agent_ui(request: web.Request) -> bool:
    return _query_ui_mode(request) == "localAgent"


async def index(_request):
    return web.FileResponse(BASE_DIR / "livekit.html")


async def config(request):
    ui_mode = _query_ui_mode(request)
    is_local = ui_mode == "localAgent"
    data = {
        "uiMode": ui_mode,
        "livekitUrl": _token_url(),
        "defaultRoom": DEFAULT_ROOM,
        "agentIdentity": AGENT_IDENTITY if is_local else "Pipecat Agent",
        "autoSpawnBot": not is_local,
        "hasLiveKitCredentials": bool(LIVEKIT_API_KEY and LIVEKIT_API_SECRET),
        "hasQwenKey": bool(os.environ.get("QWEN_API_KEY")),
        "compareUrl": "http://127.0.0.1:8765/",
        "sttBackend": os.environ.get("LOCAL_STT_BACKEND", "sensevoice"),
        "llmBackend": os.environ.get("LOCAL_LLM_BACKEND", "llamacpp"),
        "ttsBackend": os.environ.get("LOCAL_TTS_BACKEND", "qwen3_tts"),
        "architecture": "LiveKit Cloud + 本地 Pipecat pipeline (SenseVoice → Qwen2.5 → Qwen TTS)",
    }
    if is_local:
        data["note"] = "本模式需手动运行 pipecat_agent.py"
    return web.json_response({"status": "success", "data": data})


async def livekit_token(request):
    room = request.query.get("room") or DEFAULT_ROOM
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
        "url": _token_url(),
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
    print(f"LiveKit Cloud: {LIVEKIT_URL}  room: {DEFAULT_ROOM}")
    if UI_MODE_ENV in ("local", "local_agent"):
        print("PIPECAT_UI_MODE=local_agent — token API 不会自动 spawn bot")
    else:
        print("Compare mode: bot 会在浏览器连接时自动启动（本地 SenseVoice + llama-server + Qwen TTS）")
    web.run_app(app, host=HOST, port=PORT)
