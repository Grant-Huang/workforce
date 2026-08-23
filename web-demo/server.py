"""Local relay server for the voice chat web demo.

The browser's native WebSocket API can't set custom headers, so it can't send
`Authorization: Bearer <key>` directly to Qwen's Realtime API. This server sits
in between: the browser connects to `ws://127.0.0.1:8765/ws` (no auth needed),
and this process opens the real upstream connection to DashScope with the API
key attached server-side, then relays frames both ways. The key never reaches
the browser/frontend code.

Run: python3 server.py   (reads QWEN_API_KEY etc. from ../.env)
"""
import asyncio
import json
import os
from pathlib import Path

import websockets
from aiohttp import web, WSMsgType
from dotenv import load_dotenv

import agentnexus_mock

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")

QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")
# QWEN_WORKSPACE_ID switches to the workspace-specific domain, which fixed the
# session.update hangs that plagued the shared dashscope.aliyuncs.com domain for
# days (see README's "重大突破" section, 2026-08-23, and docs/qwen-realtime-voice-setup.md
# for the full writeup). Get it from the 百炼 console (obtain-the-app-id-and-workspace-id
# doc) -- it's the Workspace ID, NOT your account/主账号 UID, which looks similar but
# gets rejected with "Workspace endpoint is invalid."
QWEN_WORKSPACE_ID = os.environ.get("QWEN_WORKSPACE_ID", "")
QWEN_WS_BASE_SHARED = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
QWEN_WS_BASE = os.environ.get("QWEN_WS_BASE", QWEN_WS_BASE_SHARED)
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen3.5-omni-flash-realtime")
QWEN_VOICE = os.environ.get("QWEN_VOICE", "Serena")

# Trimmed down to the requested shortlist (2026-08-23) -- was a 14-voice curated
# subset of the ~47 Qwen3.5-Omni-Realtime supports (full list:
# https://help.aliyun.com/zh/model-studio/omni-voice-list), the rest parked for
# later. Every entry here has been individually verified via a session.update
# round-trip against qwen3.5-omni-flash-realtime + the workspace domain, not just
# taken from docs -- 'Chelsie', the old default, isn't accepted by this model
# ("Voice 'Chelsie' is not supported."), so don't assume without testing.
VOICE_OPTIONS = [
    {"id": "Serena", "label": "Serena（女，温柔，默认）"},
    {"id": "Tina", "label": "Tina（女，甜美，官方默认）"},
    {"id": "Sunnybobi", "label": "Sunnybobi（女，大大咧咧的社恐邻家姑娘）"},
    {"id": "Ethan", "label": "Ethan（男，标准普通话）"},
    {"id": "Raymond", "label": "Raymond（男，清亮）"},
    {"id": "Dylan", "label": "Dylan（男，北京话）"},
]


def upstream_ws_base():
    if QWEN_WORKSPACE_ID:
        return f"wss://{QWEN_WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
    return QWEN_WS_BASE


async def index(request):
    return web.FileResponse(BASE_DIR / "index.html")


async def config(request):
    return web.json_response({
        "voice": QWEN_VOICE,
        "voices": VOICE_OPTIONS,
        "hasKey": bool(QWEN_API_KEY),
        "hasWorkspaceId": bool(QWEN_WORKSPACE_ID),
    })


async def relay(request):
    ws_client = web.WebSocketResponse(max_msg_size=10 * 1024 * 1024)
    await ws_client.prepare(request)

    if not QWEN_API_KEY:
        await ws_client.send_str(json.dumps({"type": "relay.error", "message": "QWEN_API_KEY not set in .env"}))
        await ws_client.close()
        return ws_client

    upstream_url = f"{upstream_ws_base()}?model={QWEN_MODEL}"
    headers = {"Authorization": f"Bearer {QWEN_API_KEY}"}

    try:
        upstream = await websockets.connect(upstream_url, additional_headers=headers, open_timeout=10)
    except Exception as e:
        await ws_client.send_str(json.dumps({"type": "relay.error", "message": f"upstream connect failed: {e}"}))
        await ws_client.close()
        return ws_client

    async def browser_to_upstream():
        async for msg in ws_client:
            if msg.type == WSMsgType.TEXT:
                await upstream.send(msg.data)
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                break

    async def upstream_to_browser():
        async for message in upstream:
            if ws_client.closed:
                break
            await ws_client.send_str(message)

    task_up = asyncio.create_task(browser_to_upstream())
    task_down = asyncio.create_task(upstream_to_browser())
    _, pending = await asyncio.wait({task_up, task_down}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()

    await upstream.close()
    if not ws_client.closed:
        await ws_client.close()
    return ws_client


app = web.Application()
app.router.add_get("/", index)
app.router.add_get("/api/config", config)
app.router.add_get("/ws", relay)
agentnexus_mock.register(app)
app.router.add_static("/static/", BASE_DIR / "static")

if __name__ == "__main__":
    domain_kind = "workspace-specific" if QWEN_WORKSPACE_ID else "shared (consider setting QWEN_WORKSPACE_ID)"
    print(f"Model: {QWEN_MODEL}  Voice: {QWEN_VOICE}  Key loaded: {bool(QWEN_API_KEY)}")
    print(f"Realtime endpoint: {upstream_ws_base()} [{domain_kind}]")
    print("AgentNexus mock: /agentnexus-mock/* (see agentnexus_mock.py)")
    web.run_app(app, host="127.0.0.1", port=8765)
