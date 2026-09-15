"""兼容旧 import 路径：`import agentnexus_mock`。

实现已迁至 `agentnexus/` 包；新代码请 `from agentnexus import ...`。
"""
from agentnexus import (  # noqa: F401
    create_memory_entry,
    entry_to_text,
    get_seed_memory,
    get_seed_profile,
    register,
    search_memory,
)
from agentnexus.seeds.profile import SEED_PROFILES  # noqa: F401

__all__ = [
    "SEED_PROFILES",
    "create_memory_entry",
    "entry_to_text",
    "get_seed_memory",
    "get_seed_profile",
    "register",
    "search_memory",
]
