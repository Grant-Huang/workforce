"""NexusOpsProvider：产线/设备/订单等运营事实；必须带 citation。"""
from __future__ import annotations

from typing import Any

import nexusops
from nexusops.client import HttpClient


class NexusOpsProvider:
    name = "nexusops"

    async def query(self, query: str, *, max_results: int = 8) -> list[dict[str, Any]]:
        client = nexusops.get_client()
        if isinstance(client, HttpClient):
            docs = await client.search_remote(query, limit=max_results)
        else:
            docs = client.search(query, limit=max_results)

        results: list[dict[str, Any]] = []
        for d in docs:
            text = d.get("text") or ""
            if not text.strip():
                continue
            citation = d.get("citation")
            if not citation:
                # 分层 C：无 citation 的业务结果丢弃
                continue
            title = d.get("title")
            display = f"{title}：{text}" if title else text
            results.append(
                {
                    "provider": self.name,
                    "source": "nexusops",
                    "text": display,
                    "confidence": 0.9,
                    "freshness": "live_mock" if not isinstance(client, HttpClient) else "live",
                    "citation": citation,
                    "meta": {
                        "resource": d.get("resource"),
                        "entity_id": d.get("entity_id"),
                        "extra": d.get("extra") or {},
                    },
                }
            )
        return results[:max_results]
