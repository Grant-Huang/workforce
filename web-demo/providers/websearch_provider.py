"""WebSearchProvider：DuckDuckGo / 多后端公开网页检索。

第三方库理由：DDG 无稳定官方免费 JSON API；`ddgs`（原 duckduckgo_search）
封装了可用的文本检索与结果 URL，便于 Citation 分层 C（必须有 citation）。
"""
from __future__ import annotations

import asyncio
import os
from typing import Any


class WebSearchProvider:
    name = "websearch"

    async def query(self, query: str, *, max_results: int = 5) -> list[dict[str, Any]]:
        def _search() -> list[dict[str, Any]]:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS  # type: ignore

            # 允许部署环境指定后端；默认先 duckduckgo 再 auto 兜底
            preferred = (os.environ.get("WEBSEARCH_BACKEND") or "duckduckgo,auto").split(",")
            backends = [b.strip() for b in preferred if b.strip()]
            last_err: Exception | None = None
            raw_items: list[dict[str, Any]] = []

            with DDGS() as ddgs:
                for backend in backends:
                    try:
                        raw_items = list(
                            ddgs.text(query, max_results=max_results, backend=backend)
                        )
                        if raw_items:
                            break
                    except Exception as exc:  # noqa: BLE001 — 后端轮询
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
                    "citation": None,
                    "meta": {"error": str(exc)},
                }
            ]
