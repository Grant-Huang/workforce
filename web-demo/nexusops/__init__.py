"""NexusOps 集成包：工厂运营真相源（产线/设备/订单/物料/质量/维修）。

切换：NEXUSOPS_MODE=real + NEXUSOPS_BASE_URL + NEXUSOPS_TOKEN
契约见 docs/nexusops-external-api.md；能力地图见 docs/capability-map.md。
"""
from __future__ import annotations

from .client import get_client, reset_client, search
from .config import NexusOpsConfig, load_config
from .routes import register

__all__ = [
    "NexusOpsConfig",
    "get_client",
    "load_config",
    "register",
    "reset_client",
    "search",
]
