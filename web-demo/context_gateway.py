"""Context Gateway：bootstrap / context.query / memory.event。

Planner（见 docs/capability-map.md）：
- 日程/待办/偏好/个人记忆 → MemoryProvider（AgentNexus）
- 产线/设备/订单/物料/质量/维修 → NexusOpsProvider
- 时效外部事实 → WebSearchProvider
- 可并行；超时标记 timed_out
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from aiohttp import web

import agentnexus
import auth
import nexusops
from memory_service import get_memory_service
from providers.memory_provider import MemoryProvider
from providers.nexusops_provider import NexusOpsProvider
from providers.websearch_provider import WebSearchProvider
from transition_phrases import pick_phrase, phrase_count

_websearch = WebSearchProvider()
_nexusops = NexusOpsProvider()

_WEB_HINT = re.compile(
    r"(天气|新闻|股价|汇率|搜一下|搜一搜|搜索|网上|联网|最新消息|"
    r"公开信息|wikipedia|维基|what is|who is|latest|news|weather|google|duckduckgo)",
    re.I,
)
# AgentNexus：个人记忆 / 日程 / 待办
_MEMORY_HINT = re.compile(
    r"(我|记得|之前|偏好|兴趣|安排|日程|待办|会议|预约|负责|角色|记住|"
    r"笔记|提醒|今天有什么|有什么安排)",
    re.I,
)
# NexusOps：运营事实
_NEXUSOPS_HINT = re.compile(
    r"(产线|设备|订单|交期|延误|延期|进度|故障|停机|维修|工单|物料|质量|抽检|"
    r"OEE|排产|换型|备件|FAN-|SKU-|ORD-|WO-?\d+|M\d+|壳盖|托盘|支架)",
    re.I,
)


def _api_ok(data: Any = None, message: str | None = None) -> web.Response:
    body: dict[str, Any] = {"status": "success", "data": data if data is not None else {}}
    if message:
        body["message"] = message
    return web.json_response(body)


def _api_err(message: str, status: int = 400) -> web.Response:
    return web.json_response({"status": "error", "data": {}, "message": message}, status=status)


def _plan_providers(query: str) -> list[str]:
    providers: list[str] = []
    if _MEMORY_HINT.search(query):
        providers.append("memory")
    if _NEXUSOPS_HINT.search(query):
        providers.append("nexusops")
    if _WEB_HINT.search(query):
        providers.append("websearch")
    # 默认：无明确运营词时走 memory；纯运营词已进 nexusops
    if not providers:
        providers.append("memory")
    return providers


async def _load_agentnexus_memory(channel_id: str) -> list[dict[str, Any]]:
    client = agentnexus.get_client()
    from agentnexus.client import HttpClient

    if isinstance(client, HttpClient):
        # real 模式但没配 AGENTNEXUS_BASE_URL（比如只设了 PRODUCTION=1 忘了配 base_url）时，
        # client.config.base_url 是空字符串，aiohttp 对空 host 的 URL 直接抛
        # InvalidUrlClientError——之前这里没兜底，会把 session_bootstrap 和
        # context_query 一起打挂成 500，包括跟 AgentNexus 完全无关的纯 WebSearch 查询
        # （bootstrap/context_query 都无条件调用本函数，不看 providers 里有没有 "memory"）。
        # 降级成"这轮没有 AgentNexus 记忆"，让 NexusOps/WebSearch 结果照常返回。
        try:
            return await client.list_memory_remote(channel_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[agentnexus] list_memory_remote 失败（base_url={client.config.base_url!r}）：{exc}")
            return []
    return client.get_seed_memory(channel_id)


async def session_bootstrap(request: web.Request) -> web.Response:
    try:
        user = auth.require_user(request)
    except web.HTTPUnauthorized:
        return _api_err("未登录", 401)

    memory = get_memory_service()
    profile = user["profile"]
    memory.upsert_profile(user["user_id"], profile)

    an_entries = await _load_agentnexus_memory(user["channel_id"])
    memory.mirror_agentnexus_entries(user["user_id"], an_entries)

    # source_index：指针路由到 NexusOps（不深拷流水）
    memory.upsert_source_index(
        user["user_id"],
        "machine:M102",
        aliases=["M102", "产线A那台", "WO-8842"],
        providers=[
            {"provider": "nexusops", "resource": "machine", "hint": "q=M102"},
            {"provider": "nexusops", "resource": "work_order", "hint": "q=WO-8842"},
            {"provider": "memory", "resource": "semantic", "hint": "belongs_to=line_A"},
        ],
    )
    memory.upsert_source_index(
        user["user_id"],
        "line:A",
        aliases=["产线A", "产线 A", "line-A"],
        providers=[
            {"provider": "nexusops", "resource": "line", "hint": "q=产线A"},
            {"provider": "nexusops", "resource": "order", "hint": "q=ORD-20260915-01"},
        ],
    )
    memory.upsert_source_index(
        user["user_id"],
        "order:ORD-20260915-01",
        aliases=["ORD-20260915-01", "Shell-A7", "延误订单"],
        providers=[
            {"provider": "nexusops", "resource": "order", "hint": "q=ORD-20260915-01"},
        ],
    )

    hot_sids = {
        e.get("entry_id")
        for e in an_entries
        if e.get("include_in_hot", True) and e.get("entry_id")
    }
    hot_all = memory.list_hot_memory(user["user_id"], limit=24)
    hot = [
        h
        for h in hot_all
        if h.get("source") != "agentnexus" or h.get("source_id") in hot_sids
    ][:8]

    an_cfg = agentnexus.load_config()
    ops_cfg = nexusops.load_config()
    data = {
        "user_id": user["user_id"],
        "session_hint": f"S-{user['user_id']}",
        "user_profile": profile,
        "conversation_summary": {
            "topics": ["今日待办", "M102", "ORD-20260915-01交期"],
            "note": (
                "用户是产线主管；个人日程/待办在 AgentNexus；"
                "产线与订单实况在 NexusOps（M102 故障、ORD-20260915-01 延误风险）。"
            ),
        },
        "hot_memory": hot,
        "active_entities": [
            {"id": "machine:M102", "label": "M102", "system": "nexusops"},
            {"id": "order:ORD-20260915-01", "label": "ORD-20260915-01", "system": "nexusops"},
            {"id": "todo:today", "label": "今日待办", "system": "agentnexus"},
        ],
        "active_tasks": [
            {"id": "todo-wo-8842", "label": "跟进 WO-8842（待办→查 NexusOps）"},
            {"id": "todo-ord-01", "label": "确认 ORD-20260915-01 是否延误"},
        ],
        "phrase_stats": {"count": phrase_count()},
        "agentnexus_seed_count": len(an_entries),
        "integrations": {
            "agentnexus": {"mode": an_cfg.mode, "base_url": an_cfg.public_base_url},
            "nexusops": {"mode": ops_cfg.mode, "base_url": ops_cfg.public_base_url},
        },
    }
    return _api_ok(data)


async def context_query(request: web.Request) -> web.Response:
    try:
        user = auth.require_user(request)
    except web.HTTPUnauthorized:
        return _api_err("未登录", 401)

    try:
        body = await request.json()
    except Exception:
        return _api_err("无效 JSON")

    query = (body.get("query") or "").strip()
    if not query:
        return _api_err("query 必填")

    options = body.get("options") or {}
    latency_budget_ms = int(options.get("latency_budget_ms") or 1500)
    max_results = int(options.get("max_results") or 10)
    force_providers = options.get("providers")

    providers = force_providers or _plan_providers(query)
    if "websearch" in providers and latency_budget_ms < 6000:
        latency_budget_ms = 6000

    memory = get_memory_service()
    an_entries = await _load_agentnexus_memory(user["channel_id"])
    mem_provider = MemoryProvider(
        memory,
        agentnexus_entries=an_entries,
        channel_id=user["channel_id"],
    )

    async def run_memory() -> list[dict[str, Any]]:
        if "memory" not in providers:
            return []
        return await mem_provider.query(user["user_id"], query, max_results=max_results)

    async def run_nexusops() -> list[dict[str, Any]]:
        if "nexusops" not in providers:
            return []
        return await _nexusops.query(query, max_results=min(8, max_results))

    async def run_web() -> list[dict[str, Any]]:
        if "websearch" not in providers:
            return []
        return await _websearch.query(query, max_results=min(5, max_results))

    timed_out = False
    t0 = time.perf_counter()
    try:
        mem_res, ops_res, web_res = await asyncio.wait_for(
            asyncio.gather(run_memory(), run_nexusops(), run_web()),
            timeout=max(latency_budget_ms, 200) / 1000.0,
        )
    except asyncio.TimeoutError:
        timed_out = True
        mem_res, ops_res, web_res = [], [], []
        try:
            mem_res = await asyncio.wait_for(run_memory(), timeout=0.05)
        except Exception:
            pass

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    usable_web = []
    rejected_web = 0
    web_errors = []
    for item in web_res:
        if item.get("citation"):
            usable_web.append(item)
        else:
            rejected_web += 1
            if item.get("freshness") == "error" or item.get("meta", {}).get("error"):
                web_errors.append(str(item.get("text") or item.get("meta", {}).get("error") or "unknown"))

    # NexusOps 已在 Provider 内过滤无 citation
    results = list(mem_res) + list(ops_res) + usable_web
    search_notes: list[str] = []
    if "nexusops" in providers:
        search_notes.append(f"NexusOps 返回 {len(ops_res)} 条运营事实。" if ops_res else "已查 NexusOps，暂无命中。")
    if "websearch" in providers:
        if timed_out and not usable_web:
            search_notes.append("已尝试公开网页检索（Tavily），但在时限内未返回结果。")
        elif not usable_web:
            detail = web_errors[0] if web_errors else "无带来源的可用条目"
            search_notes.append(f"已尝试公开网页检索（Tavily），暂无可用结果：{detail}")
        else:
            search_notes.append(f"公开网页检索返回 {len(usable_web)} 条带来源的结果。")

    data = {
        "session_id": body.get("session_id"),
        "turn_id": body.get("turn_id"),
        "context_version": body.get("context_version"),
        "query": query,
        "providers_used": providers,
        "results": results[:max_results],
        "search_notes": search_notes,
        "timed_out": timed_out,
        "elapsed_ms": elapsed_ms,
        "citation_policy": "layer_C",
        "websearch_rejected_no_citation": rejected_web,
        "note_if_timeout": (
            "检索超时：客户端应只播 Immediate、取消 Refined（后续优化：部分结果早到先播）"
            if timed_out
            else None
        ),
    }
    return _api_ok(data)


async def memory_event(request: web.Request) -> web.Response:
    """写入/纠正长期记忆事件（save-intent、质疑回写）→ AgentNexus。"""
    try:
        user = auth.require_user(request)
    except web.HTTPUnauthorized:
        return _api_err("未登录", 401)

    try:
        body = await request.json()
    except Exception:
        return _api_err("无效 JSON")

    event_type = (body.get("type") or "upsert").strip()
    text = (body.get("text") or "").strip()
    if not text and event_type != "supersede":
        return _api_err("text 必填")

    memory = get_memory_service()
    if event_type == "supersede":
        old_id = body.get("entry_id")
        if not old_id:
            return _api_err("supersede 需要 entry_id")
        memory.supersede_semantic(user["user_id"], old_id)
        new_row = None
        if text:
            new_row = memory.add_semantic(
                user["user_id"],
                text,
                provenance=body.get("provenance") or "user_correction",
                source="local",
            )
        return _api_ok({"superseded": old_id, "new": _public_row(new_row) if new_row else None})

    row = memory.add_semantic(
        user["user_id"],
        text,
        provenance=body.get("provenance") or "session",
        source=body.get("source") or "local",
        source_id=body.get("source_id"),
    )

    layer = (body.get("layer") or "PROGRESS").upper()
    an_entry = None
    if layer in ("ANCHOR", "DECISIONS", "PROGRESS"):
        try:
            an_entry = agentnexus.create_memory_entry(user["channel_id"], layer, text)
        except Exception:
            an_entry = None

    return _api_ok({"entry": _public_row(row), "agentnexus": an_entry})


def _public_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": row.get("id"),
        "text": row.get("text"),
        "source": row.get("source"),
        "source_id": row.get("source_id") or None,
        "status": row.get("status"),
        "provenance": row.get("provenance"),
    }


async def phrase_pick(request: web.Request) -> web.Response:
    try:
        auth.require_user(request)
    except web.HTTPUnauthorized:
        return _api_err("未登录", 401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    scene = (body.get("scene") if isinstance(body, dict) else None) or "retrieve_generic"
    return _api_ok({"scene": scene, "phrase": pick_phrase(scene)})


def register(app: web.Application) -> None:
    app.router.add_post("/api/session/bootstrap", session_bootstrap)
    app.router.add_post("/api/context/query", context_query)
    app.router.add_post("/api/memory/event", memory_event)
    app.router.add_post("/api/phrase/pick", phrase_pick)
