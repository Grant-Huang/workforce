"""Token + static server for the Mac Mini local Pipecat agent UI.

Issues browser participant tokens for LiveKit Cloud. The agent itself is started
separately on the Mac: `python pipecat_agent.py --room <room>`.

Run:
  cd web-demo/pipecat-local-agent
  python server.py
  # -> http://127.0.0.1:8767
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

from local_services.config import LocalAgentConfig

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent.parent / ".env")

HOST = os.environ.get("LOCAL_AGENT_HOST", os.environ.get("HOST", "127.0.0.1"))
PORT = int(os.environ.get("LOCAL_AGENT_PORT", "8767"))
AGENT_IDENTITY = os.environ.get("LIVEKIT_AGENT_IDENTITY", "Pipecat Local Agent")


def _config_payload(cfg: LocalAgentConfig) -> dict:
    return {
        "livekitUrl": cfg.livekit_url,
        "defaultRoom": cfg.livekit_room,
        "agentIdentity": AGENT_IDENTITY,
        "hasLiveKitCredentials": bool(cfg.livekit_api_key and cfg.livekit_api_secret),
        "architecture": "LiveKit Cloud + Mac Mini local agent (STT → LLM → TTS)",
        "sttBackend": cfg.stt_backend,
        "llmBackend": cfg.llm_backend,
        "ttsBackend": cfg.tts_backend,
        "memoryEnabled": cfg.memory_enabled,
    }


async def index(_request):
    return web.FileResponse(BASE_DIR / "index.html")


async def config(_request):
    cfg = LocalAgentConfig.from_env()
    return web.json_response({"status": "success", "data": _config_payload(cfg)})


async def livekit_token(request):
    cfg = LocalAgentConfig.from_env()
    try:
        cfg.validate_livekit()
    except ValueError as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=500)

    room = request.query.get("room") or cfg.livekit_room
    participant = request.query.get("participant") or f"user-{uuid.uuid4().hex[:8]}"

    if cfg.livekit_api_key and cfg.livekit_api_secret:
        token = generate_token(room, participant, cfg.livekit_api_key, cfg.livekit_api_secret)
    else:
        return web.json_response(
            {
                "status": "error",
                "message": "LIVEKIT_API_KEY / LIVEKIT_API_SECRET required to issue user tokens",
            },
            status=500,
        )

    return web.json_response(
        {
            "status": "success",
            "data": {
                "token": token,
                "url": cfg.livekit_url,
                "room": room,
                "participant": participant,
                "agentIdentity": AGENT_IDENTITY,
            },
        }
    )


async def shared_mode_switcher(_request):
    return web.FileResponse(BASE_DIR.parent / "static" / "mode-switcher.js")


app = web.Application()
app.router.add_get("/", index)
app.router.add_get("/api/config", config)
app.router.add_get("/api/livekit/token", livekit_token)
app.router.add_get("/shared/mode-switcher.js", shared_mode_switcher)
app.router.add_static("/static/", BASE_DIR / "static")

if __name__ == "__main__":
    cfg = LocalAgentConfig.from_env()
    print(f"Local agent UI: http://{HOST}:{PORT}/")
    print(f"LiveKit Cloud: {cfg.livekit_url}  room: {cfg.livekit_room}")
    print(f"Agent identity: {AGENT_IDENTITY}")
    print("先在 Mac 上运行: python pipecat_agent.py --room", cfg.livekit_room)
    web.run_app(app, host=HOST, port=PORT)
