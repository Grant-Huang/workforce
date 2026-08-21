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

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")

QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")
QWEN_WS_BASE = os.environ.get("QWEN_WS_BASE", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen-omni-turbo-realtime")
QWEN_VOICE = os.environ.get("QWEN_VOICE", "Chelsie")


async def index(request):
    return web.FileResponse(BASE_DIR / "index.html")


async def config(request):
    return web.json_response({"voice": QWEN_VOICE, "hasKey": bool(QWEN_API_KEY)})


async def relay(request):
    ws_client = web.WebSocketResponse(max_msg_size=10 * 1024 * 1024)
    await ws_client.prepare(request)

    if not QWEN_API_KEY:
        await ws_client.send_str(json.dumps({"type": "relay.error", "message": "QWEN_API_KEY not set in .env"}))
        await ws_client.close()
        return ws_client

    upstream_url = f"{QWEN_WS_BASE}?model={QWEN_MODEL}"
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
            elif msg.type == WSMsgType.ERROR:
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
app.router.add_static("/static/", BASE_DIR / "static")

if __name__ == "__main__":
    print(f"Model: {QWEN_MODEL}  Voice: {QWEN_VOICE}  Key loaded: {bool(QWEN_API_KEY)}")
    web.run_app(app, host="127.0.0.1", port=8765)
