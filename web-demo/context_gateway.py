"""Context Gateway：bootstrap / context.query / memory.event。

Phase 1 Planner：
- 个人/偏好/「我之前说…」→ MemoryProvider
- 时效外部事实 → WebSearchProvider
- 可并行；超时标记 timed_out（客户端只播 Immediate、取消 Refined）
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from aiohttp import web

import agentnexus_mock
import auth
from memory_service import get_memory_service
from providers.memory_provider import MemoryProvider
from providers.websearch_provider import WebSearchProvider
from transition_phrases import pick_phrase, phrase_count

_websearch = WebSearchProvider()

# 粗粒度意图：需要联网的时效/公开查询
_WEB_HINT = re.compile(
    r"(天气|新闻|股价|汇率|搜一下|搜一搜|搜索|查一下|查一查|网上|联网|最新消息|"
    r"公开信息|wikipedia|维基|what is|who is|latest|news|weather|google|duckduckgo)",
    re.I,
)
_MEMORY_HINT = re.compile(
    r"(我|记得|之前|偏好|兴趣|安排|日程|负责|产线|设备|M\d+|OEE|排产|角色|记住|"
    r"工单|WO-?\d+|交接|备件|FAN-|SKU-|备注)",
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
    if _MEMORY_HINT.search(query) or not _WEB_HINT.search(query):
        providers.append("memory")
    if _WEB_HINT.search(query):
        providers.append("websearch")
    if not providers:
        providers.append("memory")
    return providers


async def session_bootstrap(request: web.Request) -> web.Response:
    try:
        user = auth.require_user(request)
    except web.HTTPUnauthorized:
        return _api_err("未登录", 401)

    memory = get_memory_service()
    profile = user["profile"]
    memory.upsert_profile(user["user_id"], profile)

    # 从 Mock 拉种子事实并镜像（真相源仍是 Mock）
    mock_entries = agentnexus_mock.get_seed_memory(user["channel_id"])
    memory.mirror_agentnexus_entries(user["user_id"], mock_entries)

    # 预置 source_index 指针示例（不深拷业务流水）
    memory.upsert_source_index(
        user["user_id"],
        "machine:M102",
        aliases=["M102", "产线A那台", "WO-8842"],
        providers=[
            {"provider": "mes", "resource": "machine_event_history", "hint": "machine_id=M102"},
            {"provider": "memory", "resource": "semantic", "hint": "belongs_to=line_A"},
            {"provider": "agentnexus", "resource": "channel_memory", "hint": "q=WO-8842"},
        ],
    )

    hot_sids = {
        e.get("entry_id")
        for e in mock_entries
        if e.get("include_in_hot", True) and e.get("entry_id")
    }
    hot_all = memory.list_hot_memory(user["user_id"], limit=24)
    hot = [
        h
        for h in hot_all
        if h.get("source") != "agentnexus" or h.get("source_id") in hot_sids
    ][:8]
    data = {
        "user_id": user["user_id"],
        "session_hint": f"S-{user['user_id']}",
        "user_profile": profile,
        "conversation_summary": {
            "topics": ["M102温度告警", "今日排产", "OEE"],
            "note": "用户是产线主管；昨天 M102 有温度告警并短暂停机，今天早班排产偏紧、想先确认设备再调产。",
        },
        "hot_memory": hot,
        "active_entities": [{"id": "machine:M102", "label": "M102"}],
        "active_tasks": [{"id": "WO-8842", "label": "冷却风扇更换（需查 AgentNexus）"}],
        "phrase_stats": {"count": phrase_count()},
        "agentnexus_seed_count": len(mock_entries),
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
    force_providers = options.get("providers")  # 质疑路径可强制

    providers = force_providers or _plan_providers(query)
    # 公开网页检索常 >1.5s；若规划了 websearch 且客户端预算过紧，抬到至少 6s
    if "websearch" in providers and latency_budget_ms < 6000:
        latency_budget_ms = 6000

    memory = get_memory_service()
    mock_entries = agentnexus_mock.get_seed_memory(user["channel_id"])
    mem_provider = MemoryProvider(
        memory,
        agentnexus_entries=mock_entries,
        channel_id=user["channel_id"],
    )

    async def run_memory() -> list[dict[str, Any]]:
        if "memory" not in providers:
            return []
        return await mem_provider.query(user["user_id"], query, max_results=max_results)

    async def run_web() -> list[dict[str, Any]]:
        if "websearch" not in providers:
            return []
        return await _websearch.query(query, max_results=min(5, max_results))

    timed_out = False
    t0 = time.perf_counter()
    try:
        mem_res, web_res = await asyncio.wait_for(
            asyncio.gather(run_memory(), run_web()),
            timeout=max(latency_budget_ms, 200) / 1000.0,
        )
    except asyncio.TimeoutError:
        timed_out = True
        mem_res, web_res = [], []
        try:
            mem_res = await asyncio.wait_for(run_memory(), timeout=0.05)
        except Exception:
            pass

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # Citation 分层 C：过滤无 citation 的 WebSearch「业务断言」
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

    results = list(mem_res) + usable_web
    search_notes: list[str] = []
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
    """写入/纠正长期记忆事件（save-intent、质疑回写）。"""
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

    # 同步尝试写入 AgentNexus Mock（结构化层）
    layer = (body.get("layer") or "PROGRESS").upper()
    mock_entry = None
    if layer in ("ANCHOR", "DECISIONS", "PROGRESS"):
        mock_entry = agentnexus_mock.create_memory_entry(user["channel_id"], layer, text)

    return _api_ok({"entry": _public_row(row), "agentnexus": mock_entry})


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
    """可选：服务端抽一条过渡语（前端也可本地库）。"""
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
