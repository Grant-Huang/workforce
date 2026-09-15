"""NexusOps 连接配置：Mock / Real 切换。"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class NexusOpsConfig:
    mode: str  # "mock" | "real"
    base_url: str
    token: str
    mock_prefix: str = "/nexusops-mock"

    @property
    def use_mock_routes(self) -> bool:
        return self.mode == "mock"

    @property
    def public_base_url(self) -> str:
        if self.mode == "mock":
            return self.mock_prefix
        return self.base_url.rstrip("/")


def load_config() -> NexusOpsConfig:
    production = os.environ.get("PRODUCTION", "").lower() in ("1", "true", "yes")
    base_url = (os.environ.get("NEXUSOPS_BASE_URL") or "").strip()
    mode_env = (os.environ.get("NEXUSOPS_MODE") or "").strip().lower()

    if mode_env in ("mock", "real"):
        mode = mode_env
    elif production or base_url:
        mode = "real"
    else:
        mode = "mock"

    return NexusOpsConfig(
        mode=mode,
        base_url=base_url,
        token=(os.environ.get("NEXUSOPS_TOKEN") or "ops_mock_demo_token").strip(),
    )
