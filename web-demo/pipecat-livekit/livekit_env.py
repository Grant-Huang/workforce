"""Shared LiveKit Cloud settings for pipecat-livekit (8766)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOCAL_AGENT_DIR = BASE_DIR.parent / "pipecat-local-agent"
if str(LOCAL_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_AGENT_DIR))

from local_services.config import LocalAgentConfig


def load_livekit_config() -> LocalAgentConfig:
    cfg = LocalAgentConfig.from_env()
    cfg.validate_livekit()
    cfg.validate_livekit_url_direct()
    return cfg


def livekit_settings() -> dict[str, str]:
    cfg = load_livekit_config()
    return {
        "url": cfg.livekit_url,
        "api_key": cfg.livekit_api_key,
        "api_secret": cfg.livekit_api_secret,
        "room": cfg.livekit_room,
        "agent_identity": os.environ.get("LIVEKIT_AGENT_IDENTITY", "Pipecat Local Agent"),
    }
