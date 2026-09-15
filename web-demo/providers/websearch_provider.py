"""WebSearchProvider：公开网页检索。

主路径：Tavily Search API（需 `TAVILY_API_KEY`）。
兜底：Google News RSS（无 Key 时的新闻时效兜底）。
Citation 分层 C：可用结果必须带 url。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener, urlopen


def _http_open(req: Request, *, timeout: int = 15):
    try:
        opener = build_opener(ProxyHandler({}))
        return opener.open(req, timeout=timeout)
    except Exception:
        return urlopen(req, timeout=timeout)


def _http_get(url: str, *, timeout: int = 12) -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; WorkforceWebSearch/1.0)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
        method="GET",
    )
    with _http_open(req, timeout=timeout) as resp:
        return resp.read()


def _http_post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: int = 15,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; WorkforceWebSearch/1.0)",
            **(headers or {}),
        },
        method="POST",
    )
    with _http_open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw else {}


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


def _search_tavily(query: str, max_results: int, api_key: str) -> list[dict[str, Any]]:
    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": os.environ.get("TAVILY_SEARCH_DEPTH", "basic"),
        "include_answer": False,
        "include_images": False,
        "include_raw_content": False,
    }
    # 新闻/时效问用 advanced 更稳（可被环境变量覆盖）
    if re.search(r"(新闻|最新|消息|报道|news|latest)", query, re.I):
        if not os.environ.get("TAVILY_SEARCH_DEPTH"):
            payload["search_depth"] = "advanced"
        payload["topic"] = "news"

    try:
        # Tavily 已弃用把 key 放进请求体的 api_key 字段——部分（尤其 dev tier）key 直接拒绝这种
        # 写法，只认 Authorization: Bearer 头，这也是本次要修的 bug 本身（该函数之前一直用旧的
        # body 字段）。用假 key 实测过：换成 Bearer 头之后请求能正常打到 api.tavily.com 并拿到
        # 结构化的 401（而不是被拒绝在别的层面），说明请求格式本身是对的。
        data = _http_post_json(
            "https://api.tavily.com/search",
            payload,
            timeout=18,
            headers={"Authorization": f"Bearer {api_key}"},
        )
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:240] if exc.fp else str(exc)
        raise RuntimeError(f"Tavily HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Tavily network error: {exc}") from exc

    out: list[dict[str, Any]] = []
    for item in data.get("results") or []:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        content = (item.get("content") or item.get("snippet") or "").strip()
        if not url:
            continue
        text = f"{title}。{content}".strip("。") if title else content
        if not text:
            text = title or url
        score = item.get("score")
        conf = 0.72
        try:
            if score is not None:
                conf = max(0.55, min(0.92, float(score)))
        except (TypeError, ValueError):
            pass
        out.append(
            _row(
                text=text,
                title=title or url,
                url=url,
                confidence=conf,
                meta={"backend": "tavily", "score": score},
            )
        )
        if len(out) >= max_results:
            break
    return out


def _search_google_news_rss(query: str, max_results: int) -> list[dict[str, Any]]:
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
        snippet = title
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
            api_key = (os.environ.get("TAVILY_API_KEY") or "").strip()

            # 1) Tavily（主路径）
            if api_key:
                try:
                    hits = _search_tavily(query, max_results, api_key)
                    if hits:
                        return hits
                    errors.append("tavily: empty results")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"tavily: {exc}")
            else:
                errors.append("tavily: TAVILY_API_KEY not set")

            # 2) Google News RSS 兜底
            try:
                hits = _search_google_news_rss(query, max_results)
                if hits:
                    return hits
                if _NEWSISH.search(query):
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
