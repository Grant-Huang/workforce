"""组装 AgentNexus demo-channel（仅个人记忆 / 日程 / 待办）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .personal import personal_entries
from .profile import SEED_PROFILES


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_seed_channel(channel_id: str = "demo-channel") -> dict[str, Any]:
    now = _now_iso()
    return {
        "profile": dict(SEED_PROFILES.get(channel_id) or SEED_PROFILES["demo-channel"]),
        "memory": personal_entries(channel_id, now),
        "messages": [
            {
                "msg_id": "seed-msg-1",
                "channel_id": channel_id,
                "content": "帮我记一下：明早对一下 WO-8842，并看 ORD-20260915-01 会不会延误。",
                "sender_type": "user",
                "created_at": now,
            },
            {
                "msg_id": "seed-msg-2",
                "channel_id": channel_id,
                "content": "已写入待办：跟进 WO-8842，并核对 ORD-20260915-01 交期（进度以 NexusOps 为准）。",
                "sender_type": "assistant",
                "created_at": now,
            },
        ],
    }
