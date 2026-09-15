"""AgentNexus Client：InProcess（Mock）与 Http（真实）共用同一接口。"""
from __future__ import annotations

from typing import Any, Protocol

import aiohttp

from . import store
from .config import AgentNexusConfig, load_config
from .search import entry_to_text, search_entries


class AgentNexusClient(Protocol):
    def get_seed_memory(self, channel_id: str | None = None) -> list[dict[str, Any]]: ...

    def get_seed_profile(self, channel_id: str | None = None) -> dict[str, Any]: ...

    def search_memory(
        self, channel_id: str | None, query: str, limit: int = 8
    ) -> list[dict[str, Any]]: ...

    def create_memory_entry(
        self,
        channel_id: str | None,
        layer: str,
        content: str,
        title: str | None = None,
    ) -> dict[str, Any]: ...

    async def list_memory_remote(
        self, channel_id: str | None = None, *, q: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]: ...


class InProcessClient:
    """进程内 Mock：与 /agentnexus-mock REST 共用 store。"""

    def __init__(self, config: AgentNexusConfig):
        self.config = config

    def _cid(self, channel_id: str | None) -> str:
        return channel_id or self.config.channel_id

    def get_seed_memory(self, channel_id: str | None = None) -> list[dict[str, Any]]:
        return store.list_memory(self._cid(channel_id))

    def get_seed_profile(self, channel_id: str | None = None) -> dict[str, Any]:
        return store.get_profile(self._cid(channel_id))

    def search_memory(
        self, channel_id: str | None, query: str, limit: int = 8
    ) -> list[dict[str, Any]]:
        return search_entries(self.get_seed_memory(channel_id), query, limit=limit)

    def create_memory_entry(
        self,
        channel_id: str | None,
        layer: str,
        content: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        return store.create_memory_entry(self._cid(channel_id), layer, content, title)

    async def list_memory_remote(
        self, channel_id: str | None = None, *, q: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        cid = self._cid(channel_id)
        if q:
            return self.search_memory(cid, q, limit=limit)
        return self.get_seed_memory(cid)


class HttpClient:
    """真实 AgentNexus HTTP；契约见 docs/agentnexus-external-api.md。"""

    def __init__(self, config: AgentNexusConfig):
        self.config = config

    def _cid(self, channel_id: str | None) -> str:
        return channel_id or self.config.channel_id

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.token}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        base = self.config.base_url.rstrip("/")
        return f"{base}{path}"

    def get_seed_memory(self, channel_id: str | None = None) -> list[dict[str, Any]]:
        # 同步路径留给 bootstrap 镜像；真实模式建议走 async list_memory_remote
        # 这里返回空，避免在事件循环里阻塞；bootstrap 应 await list_memory_remote
        return []

    def get_seed_profile(self, channel_id: str | None = None) -> dict[str, Any]:
        return {}

    def search_memory(
        self, channel_id: str | None, query: str, limit: int = 8
    ) -> list[dict[str, Any]]:
        # 同步 search 在 real 模式不可用；MemoryProvider 应改用 async
        return []

    def create_memory_entry(
        self,
        channel_id: str | None,
        layer: str,
        content: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        raise RuntimeError("HttpClient.create_memory_entry 请使用 async create_memory_entry_remote")

    async def list_memory_remote(
        self, channel_id: str | None = None, *, q: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        cid = self._cid(channel_id)
        params: dict[str, str] = {}
        if q:
            params["q"] = q
            params["limit"] = str(limit)
        url = self._url(f"/api/v1/channels/{cid}/memory/")
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers(), params=params, timeout=10) as res:
                res.raise_for_status()
                data = await res.json()
                return list(data) if isinstance(data, list) else []

    async def search_memory_remote(
        self, channel_id: str | None, query: str, limit: int = 8
    ) -> list[dict[str, Any]]:
        return await self.list_memory_remote(channel_id, q=query, limit=limit)

    async def create_memory_entry_remote(
        self,
        channel_id: str | None,
        layer: str,
        content: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        cid = self._cid(channel_id)
        body: dict[str, Any] = {"layer": layer, "content": content}
        if title:
            body["title"] = title
        url = self._url(f"/api/v1/channels/{cid}/memory/")
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers(), json=body, timeout=10) as res:
                res.raise_for_status()
                return await res.json()


_client: InProcessClient | HttpClient | None = None


def get_client(config: AgentNexusConfig | None = None) -> InProcessClient | HttpClient:
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


# --- 模块级兼容函数（调用方 from agentnexus import search_memory）---

def get_seed_memory(channel_id: str = "demo-channel") -> list[dict[str, Any]]:
    return get_client().get_seed_memory(channel_id)


def get_seed_profile(channel_id: str = "demo-channel") -> dict[str, Any]:
    return get_client().get_seed_profile(channel_id)


def search_memory(channel_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
    return get_client().search_memory(channel_id, query, limit=limit)


def create_memory_entry(
    channel_id: str, layer: str, content: str, title: str | None = None
) -> dict[str, Any]:
    return get_client().create_memory_entry(channel_id, layer, content, title)


__all__ = [
    "AgentNexusClient",
    "InProcessClient",
    "HttpClient",
    "get_client",
    "reset_client",
    "get_seed_memory",
    "get_seed_profile",
    "search_memory",
    "create_memory_entry",
    "entry_to_text",
]
