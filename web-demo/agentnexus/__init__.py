"""AgentNexus 集成包：Mock 可整体切换为真实 HTTP。

用法：
    from agentnexus import get_seed_memory, search_memory, register, load_config

真实环境设 AGENTNEXUS_MODE=real + AGENTNEXUS_BASE_URL + AGENTNEXUS_TOKEN。
契约见 docs/agentnexus-external-api.md。
"""
from __future__ import annotations

from .client import (
    HttpClient,
    InProcessClient,
    create_memory_entry,
    get_client,
    get_seed_memory,
    get_seed_profile,
    reset_client,
    search_memory,
)
from .config import AgentNexusConfig, load_config
from .routes import register
from .search import entry_to_text

__all__ = [
    "AgentNexusConfig",
    "HttpClient",
    "InProcessClient",
    "create_memory_entry",
    "entry_to_text",
    "get_client",
    "get_seed_memory",
    "get_seed_profile",
    "load_config",
    "register",
    "reset_client",
    "search_memory",
]
