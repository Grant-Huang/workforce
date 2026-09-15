"""AgentNexus 连接配置：Mock / Real 切换。

环境变量：
- AGENTNEXUS_MODE=mock|real（默认 mock；设了 AGENTNEXUS_BASE_URL 且非空时倾向 real）
- AGENTNEXUS_BASE_URL：真实智枢根地址，如 https://agentnexus.example.com
- AGENTNEXUS_TOKEN：Bearer token（pt_... PersonalToken）
- AGENTNEXUS_CHANNEL_ID：频道 ID（默认 demo-channel）
- PRODUCTION=1：与 real 一样不注册本地 mock 路由
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentNexusConfig:
    mode: str  # "mock" | "real"
    base_url: str
    token: str
    channel_id: str
    mock_prefix: str = "/agentnexus-mock"

    @property
    def use_mock_routes(self) -> bool:
        return self.mode == "mock"

    @property
    def public_base_url(self) -> str:
        """前端/HTTP 客户端应使用的 base URL。"""
        if self.mode == "mock":
            return self.mock_prefix
        return self.base_url.rstrip("/")


def load_config() -> AgentNexusConfig:
    production = os.environ.get("PRODUCTION", "").lower() in ("1", "true", "yes")
    base_url = (os.environ.get("AGENTNEXUS_BASE_URL") or "").strip()
    mode_env = (os.environ.get("AGENTNEXUS_MODE") or "").strip().lower()

    if mode_env in ("mock", "real"):
        mode = mode_env
    elif production or base_url:
        mode = "real"
    else:
        mode = "mock"

    # real 但未配 base_url 时仍用 mock 前缀占位，避免空字符串；启动日志会提示
    if mode == "real" and not base_url:
        base_url = ""

    return AgentNexusConfig(
        mode=mode,
        base_url=base_url,
        token=(os.environ.get("AGENTNEXUS_TOKEN") or "pt_mock_demo_token").strip(),
        channel_id=(os.environ.get("AGENTNEXUS_CHANNEL_ID") or "demo-channel").strip(),
    )
