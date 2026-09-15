"""NexusOps Client：InProcess Mock / Http Real。"""
from __future__ import annotations

from typing import Any

import aiohttp

from . import search as ops_search
from .config import NexusOpsConfig, load_config


class InProcessClient:
    def __init__(self, config: NexusOpsConfig):
        self.config = config

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        return ops_search.search(query, limit=limit)

    def get_overview(self) -> dict[str, Any]:
        return ops_search.get_overview()

    async def search_remote(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        return self.search(query, limit=limit)


class HttpClient:
    def __init__(self, config: NexusOpsConfig):
        self.config = config

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.token}",
            "Content-Type": "application/json",
        }

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        return []

    def get_overview(self) -> dict[str, Any]:
        return {}

    async def search_remote(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        base = self.config.base_url.rstrip("/")
        url = f"{base}/api/v1/search"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=self._headers(),
                params={"q": query, "limit": str(limit)},
                timeout=10,
            ) as res:
                res.raise_for_status()
                data = await res.json()
                if isinstance(data, dict):
                    return list(data.get("results") or data.get("data") or [])
                return list(data) if isinstance(data, list) else []


_client: InProcessClient | HttpClient | None = None


def get_client(config: NexusOpsConfig | None = None) -> InProcessClient | HttpClient:
    global _client
    if config is not None:
        _client = InProcessClient(config) if config.mode == "mock" else HttpClient(config)
        return _client
    if _client is None:
        cfg = load_config()
        _client = InProcessClient(cfg) if cfg.mode == "mock" else HttpClient(cfg)
    return _client


def reset_client() -> None:
    global _client
    _client = None


def search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    return get_client().search(query, limit=limit)
