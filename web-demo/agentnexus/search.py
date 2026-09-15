"""频道记忆检索：与 REST ?q= 同一套分词/打分。"""
from __future__ import annotations

import re
from typing import Any


def query_tokens(query: str) -> list[str]:
    """拆查询词。纯中文连续句不能整段当一个 token，否则 live 检索会 miss。"""
    q = (query or "").strip()
    if not q:
        return []
    tokens: list[str] = []
    for m in re.finditer(r"[A-Za-z0-9][\w\-]*", q):
        tokens.append(m.group(0))
    for m in re.finditer(r"[\u4e00-\u9fff]+", q):
        span = m.group(0)
        tokens.append(span)
        if len(span) >= 2:
            for n in (4, 3, 2):
                if len(span) < n:
                    continue
                for i in range(0, len(span) - n + 1):
                    tokens.append(span[i : i + n])
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        tl = t.lower()
        if tl in seen or len(t) < 2:
            continue
        seen.add(tl)
        out.append(t)
    return out or ([q] if q else [])


def entry_to_text(entry: dict[str, Any]) -> str:
    title = entry.get("title")
    content = entry.get("content") or ""
    return f"{title}：{content}" if title else content


def search_entries(entries: list[dict[str, Any]], query: str, limit: int = 8) -> list[dict[str, Any]]:
    tokens = query_tokens(query)
    if not tokens:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for e in entries:
        text = entry_to_text(e)
        text_l = text.lower()
        score = 0
        for t in tokens:
            tl = t.lower()
            if tl in text_l:
                score += max(2, len(t))
        if score > 0:
            scored.append((score, e))
    scored.sort(key=lambda x: (-x[0], x[1].get("sort_order") or 0))
    return [e for _, e in scored[:limit]]
