"""WebSearchProvider：公开网页检索。

主路径：`ddgs`（DuckDuckGo 等多后端）。
兜底：Google News RSS（服务器上比 DDG HTML 抓取更稳，适合「最新新闻」类问题）。
Citation 分层 C：可用结果必须带 url。
"""
from __future__ import annotations

import asyncio
import os
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener, urlopen


def _http_get(url: str, *, timeout: int = 12) -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; WorkforceWebSearch/1.0)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
        method="GET",
    )
    # 优先直连：部分部署/沙箱代理会拦搜索站；失败再走系统代理
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()


def _row(
    *,
    text: str,
    title: str,
    url: str,
    freshness: str = "live",
    confidence: float = 0.6,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "provider": "websearch",
        "source": "websearch",
        "text": text,
        "confidence": confidence,
        "freshness": freshness,
        "citation": {"title": title, "url": url},
        "meta": meta or {},
    }


def _search_ddgs(query: str, max_results: int) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS  # type: ignore

    preferred = (os.environ.get("WEBSEARCH_BACKEND") or "duckduckgo,auto").split(",")
    backends = [b.strip() for b in preferred if b.strip()]
    last_err: Exception | None = None
    raw_items: list[dict[str, Any]] = []

    with DDGS(timeout=20) as ddgs:
        for backend in backends:
            try:
                raw_items = list(ddgs.text(query, max_results=max_results, backend=backend))
                if raw_items:
                    break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue

    if not raw_items and last_err is not None:
        raise last_err

    out: list[dict[str, Any]] = []
    for item in raw_items:
        title = item.get("title") or ""
        body = item.get("body") or item.get("snippet") or ""
        href = item.get("href") or item.get("link") or ""
        if not href:
            continue
        out.append(
            _row(
                text=f"{title}。{body}".strip("。"),
                title=title,
                url=href,
                meta={"backend": "ddgs"},
            )
        )
    return out


def _search_google_news_rss(query: str, max_results: int) -> list[dict[str, Any]]:
    """Google News RSS：无 API Key，对新闻时效问更稳。"""
    params = urlencode(
        {
            "q": query,
            "hl": "zh-CN",
            "gl": "US",
            "ceid": "US:zh-Hans",
        }
    )
    url = f"https://news.google.com/rss/search?{params}"
    raw = _http_get(url, timeout=12)

    root = ET.fromstring(raw)
    out: list[dict[str, Any]] = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source = (item.findtext("source") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if not title or not link:
            continue
        snippet = f"{title}"
        if source:
            snippet += f"（{source}）"
        if pub:
            snippet += f"。发布时间：{pub}"
        out.append(
            _row(
                text=snippet,
                title=title,
                url=link,
                confidence=0.62,
                meta={"backend": "google_news_rss", "source": source, "pubDate": pub},
            )
        )
        if len(out) >= max_results:
            break
    return out


_NEWSISH = re.compile(r"(新闻|最新|消息|报道|打电话|致电|峰会|today|news|latest)", re.I)


class WebSearchProvider:
    name = "websearch"

    async def query(self, query: str, *, max_results: int = 5) -> list[dict[str, Any]]:
        def _search() -> list[dict[str, Any]]:
            errors: list[str] = []
            # 1) ddgs（含 DDG）
            try:
                hits = _search_ddgs(query, max_results)
                if hits:
                    return hits
            except Exception as exc:  # noqa: BLE001
                errors.append(f"ddgs: {exc}")

            # 2) Google News RSS 兜底（尤其新闻类；浏览器能搜到、服务器 ddgs 常被拦）
            try:
                hits = _search_google_news_rss(query, max_results)
                if hits:
                    return hits
                if _NEWSISH.search(query):
                    # 再试一次更短的关键词（去掉口语词）
                    compact = re.sub(
                        r"(那你|帮我|搜一下|搜一搜|搜索|查一下|查一查|最近有没有|有没有)",
                        " ",
                        query,
                    )
                    compact = re.sub(r"\s+", " ", compact).strip()
                    if compact and compact != query:
                        hits = _search_google_news_rss(compact, max_results)
                        if hits:
                            return hits
            except Exception as exc:  # noqa: BLE001
                errors.append(f"google_news_rss: {exc}")

            detail = "；".join(errors) if errors else "无命中"
            return [
                {
                    "provider": self.name,
                    "source": "websearch",
                    "text": f"公开检索暂不可用：{detail}",
                    "confidence": 0.0,
                    "freshness": "error",
                    "citation": None,
                    "meta": {"error": detail},
                }
            ]

        return await asyncio.to_thread(_search)
