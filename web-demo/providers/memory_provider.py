"""MemoryProvider：从 LanceDB + AgentNexus 取个人/偏好类上下文。

Citation 分层 C：Memory/Profile 可无 citation。
主动查询：优先 live 检索 AgentNexus（真相源），再补 LanceDB 镜像命中。
镜像只在 bootstrap/启动时做，查询路径不再全量 delete+add（避免拖慢 / 阻塞事件循环）。
"""
from __future__ import annotations

from typing import Any

import agentnexus
from agentnexus.client import HttpClient
from memory_service import MemoryService


class MemoryProvider:
    name = "memory"

    def __init__(
        self,
        memory: MemoryService,
        agentnexus_entries: list[dict[str, Any]] | None = None,
        *,
        channel_id: str = "demo-channel",
    ):
        self.memory = memory
        self.agentnexus_entries = agentnexus_entries or []
        self.channel_id = channel_id

    async def query(
        self,
        user_id: str,
        query: str,
        *,
        max_results: int = 8,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen_texts: set[str] = set()

        client = agentnexus.get_client()
        # 1) 主动查 AgentNexus（Mock 进程内 / Real HTTP ?q=）
        if isinstance(client, HttpClient):
            live_entries = await client.search_memory_remote(
                self.channel_id, query, limit=max_results
            )
        else:
            live_entries = client.search_memory(self.channel_id, query, limit=max_results)

        for e in live_entries:
            text = agentnexus.entry_to_text(e)
            if not text.strip() or text in seen_texts:
                continue
            seen_texts.add(text)
            results.append(
                {
                    "provider": self.name,
                    "source": "agentnexus",
                    "text": text,
                    "confidence": 0.88,
                    "freshness": "live_mock" if not isinstance(client, HttpClient) else "live",
                    "citation": None,
                    "meta": {
                        "id": e.get("entry_id"),
                        "source_id": e.get("entry_id"),
                        "provenance": "agentnexus",
                        "layer": e.get("layer"),
                        "include_in_hot": e.get("include_in_hot", True),
                    },
                }
            )

        # 2) LanceDB 语义/关键词补召（可能含用户本地写入）
        for hit in self.memory.search_semantic(user_id, query, limit=max_results):
            text = hit.get("text") or ""
            if not text.strip() or text in seen_texts:
                continue
            seen_texts.add(text)
            results.append(
                {
                    "provider": self.name,
                    "source": hit.get("source") or "memory",
                    "text": text,
                    "confidence": hit.get("confidence", 0.7),
                    "freshness": "session_or_cached",
                    "citation": None,
                    "meta": {
                        "id": hit.get("id"),
                        "source_id": hit.get("source_id"),
                        "provenance": hit.get("provenance"),
                    },
                }
            )

        for idx in self.memory.lookup_source_index(user_id, query):
            text = (
                f"实体 {idx['entity_id']} 可经 "
                f"{[p.get('provider') for p in idx.get('providers', [])]} "
                f"查询（指针，非业务流水拷贝）"
            )
            if text in seen_texts:
                continue
            seen_texts.add(text)
            results.append(
                {
                    "provider": self.name,
                    "source": "source_index",
                    "text": text,
                    "confidence": 0.55,
                    "freshness": "index",
                    "citation": None,
                    "meta": idx,
                }
            )
        return results[:max_results]
