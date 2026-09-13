"""Unified server for `workforce.inkpath.cc` — single-domain voice pipeline demo.

Serves BOTH pipelines under one hostname:
  - Qwen Realtime (8765's relay): /ws + /api/dictation-cleanup + /api/memory-extract
  - LiveKit + Pipecat (8766's): /api/livekit/token + /api/agent/* + /api/bot/*

Top-bar mode switcher toggles `?mode=qwen` (default) vs `?mode=livekit` vs
`?mode=livekit&ui=local` — same frontend, no cross-domain navigation.

Runs on port 8788 with the LiveKit/Pipecat venv (Python 3.11 + pipecat-ai +
local ML stack). Uses aiohttp. Bot subprocess management is delegated to
`web-demo/pipecat-livekit/bot_manager.py` (unchanged).

Static assets:
  - /static/*            -> web-demo/static/  (Qwen Realtime: app.js, styles.css, ...)
  - /livekit-static/*    -> web-demo/pipecat-livekit/static/  (livekit-app.js, ...)
  - /shared/mode-switcher.js -> single source of truth for the top-bar pill switcher

Reads: ~/.zshenv + /Users/admin/work/projects/workforce/.env (via dotenv in
web-demo/server.py's BASE_DIR.parent).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import aiohttp
import websockets
from aiohttp import web, WSMsgType
from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────────────────────
# Paths and config — same env loading as 8765 + 8766
# ─────────────────────────────────────────────────────────────────────────────

WEB_DEMO_DIR = Path(__file__).resolve().parent            # .../workforce/web-demo
PIPECAT_LIVEKIT_DIR = WEB_DEMO_DIR / "pipecat-livekit"    # .../workforce/web-demo/pipecat-livekit
STATIC_8765 = WEB_DEMO_DIR / "static"
STATIC_8766 = PIPECAT_LIVEKIT_DIR / "static"
INDEX_HTML = WEB_DEMO_DIR / "index.html"
LIVEKIT_HTML = PIPECAT_LIVEKIT_DIR / "livekit.html"
MODE_SWITCHER_JS = STATIC_8765 / "mode-switcher.js"  # unified copy lives here

# Ensure pip dependencies and bot_manager are importable
sys.path.insert(0, str(PIPECAT_LIVEKIT_DIR))
# The pipecat-livekit venv has both pipecat-ai and the local ML stack
# (funasr / qwen-tts / lancedb / sentence-transformers). The 8765 (system Python 3.9)
# imports — websockets, aiohttp, dotenv — are also in that venv, so we run this whole
# server under that Python (see wrapper start-8788.sh).

load_dotenv(WEB_DEMO_DIR.parent / ".env")

from bot_manager import (  # noqa: E402  — import after sys.path tweak
    ensure_agent_started,
    ensure_started,
    get_agent_status,
    get_status,
    stop_all,
)
from livekit_env import assert_livekit_cloud_url, livekit_settings  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Qwen Realtime config (8765 verbatim)
# ─────────────────────────────────────────────────────────────────────────────

QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")
QWEN_WORKSPACE_ID = os.environ.get("QWEN_WORKSPACE_ID", "")
QWEN_WS_BASE_SHARED = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
QWEN_WS_BASE = os.environ.get("QWEN_WS_BASE", QWEN_WS_BASE_SHARED)
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen3.5-omni-flash-realtime")
QWEN_VOICE = os.environ.get("QWEN_VOICE", "Jennifer")
QWEN_TEXT_MODEL = os.environ.get("QWEN_TEXT_MODEL", "qwen-turbo")

PRODUCTION = os.environ.get("PRODUCTION", "").lower() in ("1", "true", "yes")

VOICE_OPTIONS = [
    {"id": "Griet", "label": "Griet（女，荷兰语，成熟文艺）"},
    {"id": "Jennifer", "label": "Jennifer（女，美式英语，电影质感，默认）"},
    {"id": "Katerina", "label": "Katerina（女，俄语，御姐音色）"},
    {"id": "Mia", "label": "Mia（女，中文，细腻慢生活）"},
    {"id": "Alek", "label": "Alek（男，俄语，冷峻中带暖）"},
    {"id": "Andre", "label": "Andre（男，葡萄牙语，磁性沉稳）"},
    {"id": "Bodega", "label": "Bodega（男，西班牙语，热情大叔）"},
    {"id": "Emilien", "label": "Emilien（男，法语，浪漫大哥哥）"},
]

DICTATION_CLEANUP_PROMPT = (
    "把用户口述的这段话，整理成一段通顺、结构清晰的书面文字。"
    "去掉口语里的语气词、重复、停顿词（\"呃\"\"就是\"\"然后\"\"那个\"这些），"
    "如果用户说话时想到哪说到哪、顺序乱，帮TA理顺逻辑顺序，"
    "但不要添加原话里没有的信息，不要过度概括丢失细节，保持第一人称语气，"
    "不要用列表/编号这种书面格式（除非原话本身就是在列举好几件事）。"
    "直接给出整理后的文字，不要加任何前缀说明。"
)

MEMORY_EXTRACT_PROMPT = (
    "你是一个记忆提炼助手。给定用户和助手在一轮对话里说的话，判断这轮对话里有没有值得长期记住的"
    "事实性内容——比如用户的偏好、计划、决定、个人信息，或者用户解释了一个团队/个人黑话、术语的含义。\n\n"
    "如果有，用简洁清楚的第一人称转述提炼成 0 条到多条独立的事实（每条一两句话），不要逐字复述原话，"
    "也不要加原话里没有的信息。如果这轮只是打招呼、闲聊、追问细节但没有新信息，返回空列表。\n\n"
    "如果某条事实是在解释一个黑话/术语的含义（\"我们说的 XX 意思是 YY\"这种），把 isJargon 设为 "
    "true；其他普通事实设为 false。\n\n"
    "已知的黑话/术语（避免重复提炼这些已经记录过的）：\n{known_jargon}\n\n"
    "严格按以下 JSON 格式输出，不要有任何其他文字，不要用 markdown 代码块包裹：\n"
    '{{"facts": [{{"text": "...", "isJargon": false}}]}}\n'
    '没有值得记的内容时：{{"facts": []}}'
)


def upstream_ws_base():
    if QWEN_WORKSPACE_ID:
        return f"wss://{QWEN_WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
    return QWEN_WS_BASE


def compatible_mode_base():
    if QWEN_WORKSPACE_ID:
        return f"https://{QWEN_WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    return "https://dashscope.aliyuncs.com/compatible-mode/v1"


# ─────────────────────────────────────────────────────────────────────────────
# LiveKit / Pipecat config (8766 verbatim, with single-domain tweak)
# ─────────────────────────────────────────────────────────────────────────────

try:
    _LK = livekit_settings()
except ValueError as exc:
    raise SystemExit(f"LiveKit Cloud 配置错误: {exc}") from exc

LIVEKIT_URL = _LK["url"]
LIVEKIT_API_KEY = _LK["api_key"]
LIVEKIT_API_SECRET = _LK["api_secret"]
DEFAULT_ROOM = _LK["room"]
AGENT_IDENTITY = _LK["agent_identity"]

LIVEKIT_URL_PUBLIC = os.environ.get("LIVEKIT_URL_PUBLIC", "").strip()
if LIVEKIT_URL_PUBLIC:
    try:
        assert_livekit_cloud_url(LIVEKIT_URL_PUBLIC, "LIVEKIT_URL_PUBLIC")
    except ValueError as exc:
        raise SystemExit(f"LiveKit Cloud 配置错误: {exc}") from exc

AGENT_WAKE_TOKEN = os.environ.get("AGENT_WAKE_TOKEN", "").strip()

UI_MODE_ENV = os.environ.get("PIPECAT_UI_MODE", "").strip().lower()


def _token_url() -> str:
    return LIVEKIT_URL_PUBLIC or LIVEKIT_URL


def _query_ui_mode(request: web.Request) -> str:
    """Same logic as 8766's server.py — picks localAgent vs compare based on
    `?ui=local` or PIPECAT_UI_MODE env."""
    if UI_MODE_ENV in ("local", "local_agent"):
        return "localAgent"
    if request.query.get("ui") == "local":
        return "localAgent"
    return "compare"


def _is_local_agent_ui(request: web.Request) -> bool:
    return _query_ui_mode(request) == "localAgent"


def _wake_authorized(request: web.Request) -> bool:
    if not AGENT_WAKE_TOKEN:
        return True
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer ") and header[7:].strip() == AGENT_WAKE_TOKEN:
        return True
    return request.query.get("token", "") == AGENT_WAKE_TOKEN


# ─────────────────────────────────────────────────────────────────────────────
# Mode dispatch helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_mode(request: web.Request) -> str:
    """Return 'qwen' or 'livekit' based on `?mode=` query (default qwen).

    Livekit.html has its own internal `?ui=local` for local-agent mode — that
    doesn't change the top-level mode."""
    return request.query.get("mode", "qwen").strip().lower()


# ─────────────────────────────────────────────────────────────────────────────
# Page handlers
# ─────────────────────────────────────────────────────────────────────────────

async def index(request: web.Request):
    mode = _resolve_mode(request)
    if mode == "livekit":
        return web.FileResponse(LIVEKIT_HTML)
    return web.FileResponse(INDEX_HTML)


async def livekit_page(_request: web.Request):
    """Explicit entry point — same as /?mode=livekit, for bookmarks / share links."""
    return web.FileResponse(LIVEKIT_HTML)


async def shared_mode_switcher(_request: web.Request):
    return web.FileResponse(MODE_SWITCHER_JS)


# ─────────────────────────────────────────────────────────────────────────────
# Qwen Realtime API (8765 verbatim)
# ─────────────────────────────────────────────────────────────────────────────

async def qwen_config(_request: web.Request):
    return web.json_response({
        "voice": QWEN_VOICE,
        "voices": VOICE_OPTIONS,
        "hasKey": bool(QWEN_API_KEY),
        "hasWorkspaceId": bool(QWEN_WORKSPACE_ID),
    })


async def relay(request: web.Request):
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


async def dictation_cleanup(request: web.Request):
    if not QWEN_API_KEY:
        return web.json_response({"error": "QWEN_API_KEY not set in .env"}, status=500)
    body = await request.json()
    raw_text = (body.get("text") or "").strip()
    if not raw_text:
        return web.json_response({"error": "text is required"}, status=400)
    url = f"{compatible_mode_base()}/chat/completions"
    headers = {"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": QWEN_TEXT_MODEL,
        "messages": [
            {"role": "system", "content": DICTATION_CLEANUP_PROMPT},
            {"role": "user", "content": raw_text},
        ],
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return web.json_response({"error": f"cleanup call failed: {data}"}, status=502)
                cleaned = data["choices"][0]["message"]["content"]
                return web.json_response({"cleaned": cleaned})
    except asyncio.TimeoutError:
        return web.json_response({"error": "整理超时（超过60秒），请重试"}, status=504)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)


async def memory_extract(request: web.Request):
    if not QWEN_API_KEY:
        return web.json_response({"error": "QWEN_API_KEY not set in .env"}, status=500)
    body = await request.json()
    user_text = (body.get("userText") or "").strip()
    assistant_text = (body.get("assistantText") or "").strip()
    known_jargon = body.get("knownJargon") or []
    if not user_text or not assistant_text:
        return web.json_response({"error": "userText and assistantText are both required"}, status=400)
    prompt = MEMORY_EXTRACT_PROMPT.format(known_jargon="（无）" if not known_jargon else "、".join(known_jargon))
    url = f"{compatible_mode_base()}/chat/completions"
    headers = {"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": QWEN_TEXT_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"用户说：{user_text}\n助手回复：{assistant_text}"},
        ],
        "response_format": {"type": "json_object"},
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return web.json_response({"error": f"extract call failed: {data}"}, status=502)
                content = data["choices"][0]["message"]["content"]
                try:
                    parsed = json.loads(content)
                except (ValueError, TypeError):
                    return web.json_response({"error": f"model returned non-JSON content: {content[:200]}"}, status=502)
                facts = parsed.get("facts") if isinstance(parsed, dict) else None
                if not isinstance(facts, list):
                    return web.json_response({"error": "model response missing a facts list"}, status=502)
                return web.json_response({"facts": facts})
    except asyncio.TimeoutError:
        return web.json_response({"error": "提炼超时（超过60秒），请重试"}, status=504)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)


# ─────────────────────────────────────────────────────────────────────────────
# LiveKit / Pipecat API (8766 verbatim, dispatch-aware on /api/config)
# ─────────────────────────────────────────────────────────────────────────────

async def unified_config(request: web.Request):
    """Single /api/config that returns the right shape per mode.

    The frontend's app.js (Qwen) reads {voice, voices, hasKey, hasWorkspaceId}.
    The frontend's livekit-app.js reads {livekitUrl, defaultRoom, agentIdentity,
    hasLiveKitCredentials, hasQwenKey, sttBackend, llmBackend, ttsBackend,
    architecture, wakeEndpoint, ...}.

    The mode= query picks the shape so the same endpoint serves both."""
    mode = _resolve_mode(request)
    if mode == "livekit":
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
            "compareUrl": "/?mode=qwen",                # single-domain: same hostname, different mode
            "localAgentUrl": "/?mode=livekit&ui=local",
            "sttBackend": os.environ.get("LOCAL_STT_BACKEND", "sensevoice"),
            "llmBackend": os.environ.get("LOCAL_LLM_BACKEND", "llamacpp"),
            "ttsBackend": os.environ.get("LOCAL_TTS_BACKEND", "qwen3_tts"),
            "architecture": "LiveKit Cloud + 本地 Pipecat pipeline (SenseVoice → Qwen2.5 → Qwen TTS)",
            "wakeEndpoint": "/api/agent/wake",
            "wakeAuthRequired": bool(AGENT_WAKE_TOKEN),
        }
        if is_local:
            data["note"] = (
                "本模式可用 POST/GET /api/agent/wake?room=... 拉起 pipecat_agent.py，"
                "或手动运行该脚本"
            )
        return web.json_response({"status": "success", "data": data})

    # mode == qwen (default) — return Qwen Realtime config
    return await qwen_config(request)


async def bot_status(_request: web.Request):
    return web.json_response({"status": "success", "data": get_status()})


async def agent_status(_request: web.Request):
    return web.json_response({"status": "success", "data": get_agent_status()})


async def agent_wake(request: web.Request):
    if not _wake_authorized(request):
        return web.json_response(
            {"status": "error", "message": "unauthorized — set Authorization: Bearer <AGENT_WAKE_TOKEN>"},
            status=401,
        )
    room = request.query.get("room") or DEFAULT_ROOM
    status = ensure_agent_started(room)
    if status.get("status") == "error":
        return web.json_response(
            {"status": "error", "message": status.get("message", "agent 启动失败")},
            status=503,
        )
    return web.json_response({"status": "success", "data": status})


async def livekit_token(request: web.Request):
    room = request.query.get("room") or DEFAULT_ROOM
    participant = request.query.get("participant") or f"user-{uuid.uuid4().hex[:8]}"
    is_local = _is_local_agent_ui(request)

    bot_status_data = None
    if not is_local:
        bot_status_data = ensure_started(room)
        if bot_status_data.get("status") == "error":
            return web.json_response(
                {"status": "error", "message": bot_status_data.get("message", "bot 启动失败")},
                status=503,
            )

    # Lazy import — pipecat.runner.livekit only loads when first token is requested
    from pipecat.runner.livekit import generate_token

    token = generate_token(room, participant, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)

    payload = {
        "token": token,
        "url": _token_url(),
        "room": room,
        "participant": participant,
        "uiMode": "localAgent" if is_local else "compare",
        "agentIdentity": AGENT_IDENTITY if is_local else "Pipecat Agent",
    }
    if bot_status_data is not None:
        payload["bot"] = bot_status_data
    return web.json_response({"status": "success", "data": payload})


async def on_shutdown(_app):
    stop_all()


# ─────────────────────────────────────────────────────────────────────────────
# App wiring
# ─────────────────────────────────────────────────────────────────────────────

app = web.Application()
app.on_shutdown.append(on_shutdown)

# Pages
app.router.add_get("/", index)
app.router.add_get("/livekit.html", livekit_page)
app.router.add_get("/shared/mode-switcher.js", shared_mode_switcher)

# Unified config (mode-dispatched)
app.router.add_get("/api/config", unified_config)

# Qwen Realtime (8765 routes)
app.router.add_get("/ws", relay)
app.router.add_post("/api/dictation-cleanup", dictation_cleanup)
app.router.add_post("/api/memory-extract", memory_extract)

# LiveKit / Pipecat (8766 routes)
app.router.add_get("/api/bot/status", bot_status)
app.router.add_get("/api/agent/status", agent_status)
app.router.add_get("/api/agent/wake", agent_wake)
app.router.add_post("/api/agent/wake", agent_wake)
app.router.add_get("/api/livekit/token", livekit_token)

# Static assets — namespaced to avoid /static/* collision
app.router.add_static("/static/", STATIC_8765, show_index=False)        # Qwen side
app.router.add_static("/livekit-static/", STATIC_8766, show_index=False)  # LiveKit side

# Note: PRODUCTION check for AgentNexus mock dropped — we only run this server
# in production (port 8788 behind CF Tunnel); the 8765 standalone can keep the
# dev mock if needed. agentnexus_mock.* is no longer imported here.


if __name__ == "__main__":
    HOST = os.environ.get("HOST", "0.0.0.0")
    PORT = int(os.environ.get("UNIFIED_PORT", "8788"))

    domain_kind = "workspace-specific" if QWEN_WORKSPACE_ID else "shared (consider setting QWEN_WORKSPACE_ID)"
    print(f"=== workforce unified server ===")
    print(f"Qwen Realtime: model={QWEN_MODEL} voice={QWEN_VOICE} endpoint={upstream_ws_base()} [{domain_kind}]")
    print(f"LiveKit Cloud: {LIVEKIT_URL} room={DEFAULT_ROOM}")
    if LIVEKIT_URL_PUBLIC:
        print(f"LIVEKIT_URL_PUBLIC (browser token url): {LIVEKIT_URL_PUBLIC}")
    print(f"Agent wake:    POST/GET /api/agent/wake auth={'required' if AGENT_WAKE_TOKEN else 'open'}")
    print(f"Listening on {HOST}:{PORT}")
    print(f"Single domain: https://workforce.inkpath.cc/")
    print(f"  Default (Qwen Realtime)         : /")
    print(f"  LiveKit + Pipecat               : /?mode=livekit")
    print(f"  Mac local agent                 : /?mode=livekit&ui=local")
    print(f"  Legacy fallback (workforce-l)   : still served by 8766 separately")
    web.run_app(app, host=HOST, port=PORT)
