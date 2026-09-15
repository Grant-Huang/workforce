"""WebSearchProvider：DuckDuckGo 公开网页检索。

第三方库理由：DDG 无稳定官方免费 JSON API；`ddgs`（原 duckduckgo_search）
封装了可用的文本检索与结果 URL，便于 Citation 分层 C（必须有 citation）。
"""
from __future__ import annotations

import asyncio
from typing import Any


class WebSearchProvider:
    name = "websearch"

    async def query(self, query: str, *, max_results: int = 5) -> list[dict[str, Any]]:
        def _search() -> list[dict[str, Any]]:
            try:
                from ddgs import DDGS
            except ImportError:
                # 兼容旧包名
                from duckduckgo_search import DDGS  # type: ignore

            out: list[dict[str, Any]] = []
            with DDGS() as ddgs:
                for item in ddgs.text(query, max_results=max_results):
                    title = item.get("title") or ""
                    body = item.get("body") or item.get("snippet") or ""
                    href = item.get("href") or item.get("link") or ""
                    if not href:
                        # Citation 分层 C：无 URL 不得当业务事实
                        continue
                    out.append(
                        {
                            "provider": self.name,
                            "source": "websearch",
                            "text": f"{title}。{body}".strip("。"),
                            "confidence": 0.6,
                            "freshness": "live",
                            "citation": {
                                "title": title,
                                "url": href,
                            },
                            "meta": {},
                        }
                    )
            return out

        try:
            return await asyncio.to_thread(_search)
        except Exception as exc:
            return [
                {
                    "provider": self.name,
                    "source": "websearch",
                    "text": f"公开检索暂不可用：{exc}",
                    "confidence": 0.0,
                    "freshness": "error",
                    # 无可靠来源：上层不得当业务事实播报
                    "citation": None,
                    "meta": {"error": str(exc)},
                }
            ]
