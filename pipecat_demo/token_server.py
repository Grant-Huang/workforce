"""Web server for the comparison client: static files, LiveKit tokens, bot processes.

Runs on port 8766 so it can sit next to web-demo/server.py (8765) and the two arms can
be A/B'd in two browser tabs.

Each session gets its own room and its own bot process. That mirrors how a real
deployment works (one agent process per call) and, more practically, means a crashed
or wedged bot can't poison the next session -- a fresh process joins the next room.
"""
import asyncio
import os
import secrets
import sys
from pathlib import Path

from aiohttp import web
from loguru import logger

from pipecat_demo import config
from pipecat_demo.livekit_token import generate_user_token

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"

# Set PIPECAT_MOCK=1 to run sessions against the offline stand-ins (no DashScope key).
MOCK_MODE = os.environ.get("PIPECAT_MOCK", "").lower() in ("1", "true", "yes")

_bots: dict[str, asyncio.subprocess.Process] = {}


async def index(request: web.Request) -> web.StreamResponse:
    """Serve the comparison client."""
    return web.FileResponse(WEB_DIR / "index.html")


async def get_config(request: web.Request) -> web.Response:
    """Report the pipeline configuration (never the API key itself)."""
    return web.json_response(
        {"status": "success", "data": {**config.summary(), "mock": MOCK_MODE}}
    )


async def create_session(request: web.Request) -> web.Response:
    """Create a room, spawn a bot for it, and return the browser's join credentials."""
    room_name = f"voicechat-{secrets.token_hex(4)}"

    try:
        process = await _spawn_bot(room_name)
    except Exception as e:
        logger.error(f"Failed to spawn bot: {e}")
        return web.json_response(
            {"status": "error", "message": f"启动 bot 进程失败：{e}"}, status=500
        )

    _bots[room_name] = process
    logger.info(f"Session {room_name} started (bot pid {process.pid}, mock={MOCK_MODE})")

    return web.json_response(
        {
            "status": "success",
            "data": {
                "room": room_name,
                "url": config.LIVEKIT_URL,
                "token": generate_user_token(room_name),
                "mock": MOCK_MODE,
            },
        }
    )


async def end_session(request: web.Request) -> web.Response:
    """Stop the bot for a room.

    The bot also exits on its own when the participant disconnects; this is for the
    case where the browser tab goes away without a clean LiveKit disconnect.
    """
    body = await request.json()
    room_name = body.get("room")
    process = _bots.pop(room_name, None)
    if process and process.returncode is None:
        process.terminate()
    return web.json_response({"status": "success", "data": {"room": room_name}})


async def _spawn_bot(room_name: str) -> asyncio.subprocess.Process:
    args = [sys.executable, "-m", "pipecat_demo.bot", "--room", room_name]
    if MOCK_MODE:
        args.append("--mock")
    return await asyncio.create_subprocess_exec(*args, cwd=str(config.REPO_ROOT))


async def _reap_bots(app: web.Application) -> None:
    """Drop finished bot processes so the table doesn't grow across sessions."""
    while True:
        await asyncio.sleep(10)
        for room_name, process in list(_bots.items()):
            if process.returncode is not None:
                logger.info(f"Bot for {room_name} exited with {process.returncode}")
                _bots.pop(room_name, None)


async def _on_startup(app: web.Application) -> None:
    app["reaper"] = asyncio.create_task(_reap_bots(app))


async def _on_cleanup(app: web.Application) -> None:
    app["reaper"].cancel()
    for process in _bots.values():
        if process.returncode is None:
            process.terminate()


def create_app() -> web.Application:
    """Build the aiohttp application."""
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/config", get_config)
    app.router.add_post("/api/session", create_session)
    app.router.add_post("/api/session/end", end_session)
    app.router.add_static("/static/", WEB_DIR)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


def main() -> None:
    """Run the server."""
    logger.info(f"LiveKit: {config.LIVEKIT_URL}  mock={MOCK_MODE}  key={bool(config.QWEN_API_KEY)}")
    logger.info(f"Listening on http://{config.HOST}:{config.PORT}/")
    web.run_app(create_app(), host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
