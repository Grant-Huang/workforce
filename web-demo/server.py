"""Local relay server for the voice chat web demo.

The browser's native WebSocket API can't set custom headers, so it can't send
`Authorization: Bearer <key>` directly to Qwen's Realtime API. This server sits
in between: the browser connects to `ws://127.0.0.1:8765/ws` (no auth needed),
and this process opens the real upstream connection to DashScope with the API
key attached server-side, then relays frames both ways. The key never reaches
the browser/frontend code.

Run: python3 server.py   (reads QWEN_API_KEY etc. from ../.env)

Binds to 127.0.0.1 by default (local-only, matches the docs above). Set HOST=0.0.0.0
in .env only when fronting this with a tunnel/reverse proxy that needs to reach it
from outside the machine. When doing that, also set PRODUCTION=1 to stop registering
the AgentNexus mock routes (agentnexus_mock.py is seed-data-only, not meant to be
reachable from outside).
"""
import asyncio
import json
import os
from pathlib import Path

import aiohttp
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
QWEN_VOICE = os.environ.get("QWEN_VOICE", "Jennifer")

HOST = os.environ.get("HOST", "127.0.0.1")
PRODUCTION = os.environ.get("PRODUCTION", "").lower() in ("1", "true", "yes")

# Switched to this shortlist (2026-08-28) -- full list of ~47 voices Qwen3.5-Omni-Realtime
# supports: https://help.aliyun.com/zh/model-studio/omni-voice-list. Unlike the previous
# shortlist, these have NOT been individually verified via a session.update round-trip
# against qwen3.5-omni-flash-realtime + the workspace domain -- 'Chelsie', an earlier
# default, wasn't accepted by this model ("Voice 'Chelsie' is not supported."), so don't
# assume without testing if one of these turns out unsupported too.
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


# The dictate-to-text mode's "AI cleanup" step (docs/app-design.md section 3.2) --
# a one-shot plain text call, not the Realtime WS. Deliberately a lighter model than
# the realtime conversation: this task is just cleanup, not open-ended reasoning.
#
# qwen3.5-flash was the original choice here and measured a real, reproducible 17-35s+
# latency (once exceeding a 30s timeout) -- bad enough that both server and client
# needed their own timeout backstops (see below and finishDictation in app.js). Swapping
# to qwen-turbo (same endpoint, same domain, only the model name differs) measured
# 0.9-4.3s across three calls with no quality loss on this task -- a real infra-vs-model
# distinction, not a fluke: same request path, ~10x faster with a smaller/older model.
# The 60s/65s timeouts stay in place as a safety net (variance can still happen), but
# the UI's "整理中…" wait should now be brief in the common case, not a genuinely long
# wait to design around.
QWEN_TEXT_MODEL = os.environ.get("QWEN_TEXT_MODEL", "qwen-turbo")
DICTATION_CLEANUP_PROMPT = (
    "把用户口述的这段话，整理成一段通顺、结构清晰的书面文字。"
    "去掉口语里的语气词、重复、停顿词（\"呃\"\"就是\"\"然后\"\"那个\"这些），"
    "如果用户说话时想到哪说到哪、顺序乱，帮TA理顺逻辑顺序，"
    "但不要添加原话里没有的信息，不要过度概括丢失细节，保持第一人称语气，"
    "不要用列表/编号这种书面格式（除非原话本身就是在列举好几件事）。"
    "直接给出整理后的文字，不要加任何前缀说明。"
)

# Memory extraction (docs/app-design.md 7.3): same one-shot qwen-turbo mechanism as
# dictation cleanup above, different prompt -- distills a completed user+assistant turn
# into 0+ standalone facts worth remembering, run async off the critical path (see
# memoryExtraction.js). Empirically verified live against qwen-turbo (2026-08-24):
# response_format: json_object reliably returns parseable JSON, correctly identifies
# jargon explanations vs plain facts, returns an empty list for trivial turns (a
# greeting), and correctly avoids re-extracting a term already in the known-jargon list.
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


async def dictation_cleanup(request):
    """Cleans up a raw voice-dictated transcript into well-organized written text --
    the AI-cleanup step for the dictate-to-text mode (docs/app-design.md section 3.2).
    A plain one-shot text call, deliberately separate from the Realtime WS session
    that captured the transcript in the first place."""
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


async def memory_extract(request):
    """Distills a completed user+assistant turn into 0+ standalone facts worth
    remembering (docs/app-design.md 7.3) -- see MEMORY_EXTRACT_PROMPT's comment for
    what's been verified about this. Called async, off the critical path, once a reply
    is already showing (see memoryExtraction.js) -- unlike dictation_cleanup above,
    nothing is waiting on this response."""
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


app = web.Application()
app.router.add_get("/", index)
app.router.add_get("/api/config", config)
app.router.add_post("/api/dictation-cleanup", dictation_cleanup)
app.router.add_post("/api/memory-extract", memory_extract)
app.router.add_get("/ws", relay)
if not PRODUCTION:
    agentnexus_mock.register(app)
app.router.add_static("/static/", BASE_DIR / "static")

if __name__ == "__main__":
    domain_kind = "workspace-specific" if QWEN_WORKSPACE_ID else "shared (consider setting QWEN_WORKSPACE_ID)"
    print(f"Model: {QWEN_MODEL}  Voice: {QWEN_VOICE}  Key loaded: {bool(QWEN_API_KEY)}")
    print(f"Realtime endpoint: {upstream_ws_base()} [{domain_kind}]")
    if PRODUCTION:
        print("AgentNexus mock: disabled (PRODUCTION=1)")
    else:
        print("AgentNexus mock: /agentnexus-mock/* (see agentnexus_mock.py)")
    print(f"Listening on {HOST}:8765")
    web.run_app(app, host=HOST, port=8765)
