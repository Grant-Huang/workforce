"""进程内频道存储（Mock 真相源）。"""
from __future__ import annotations

import itertools
from datetime import datetime, timezone
from typing import Any

from .seeds import build_seed_channel
from .seeds.profile import SEED_PROFILES

_id_counter = itertools.count(100)
_store: dict[str, dict[str, Any]] | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_store() -> dict[str, dict[str, Any]]:
    global _store
    if _store is None:
        _store = {"demo-channel": build_seed_channel("demo-channel")}
    return _store


def reset_store() -> None:
    """测试用：重建种子。"""
    global _store
    _store = {"demo-channel": build_seed_channel("demo-channel")}


def get_channel(channel_id: str) -> dict[str, Any]:
    store = _ensure_store()
    return store.setdefault(channel_id, {"memory": [], "messages": [], "profile": {}})


def list_memory(channel_id: str) -> list[dict[str, Any]]:
    return list(get_channel(channel_id).get("memory") or [])


def get_profile(channel_id: str) -> dict[str, Any]:
    ch = get_channel(channel_id)
    return dict(ch.get("profile") or SEED_PROFILES.get(channel_id) or {})


def list_messages(channel_id: str) -> list[dict[str, Any]]:
    return list(get_channel(channel_id).get("messages") or [])


def create_memory_entry(
    channel_id: str,
    layer: str,
    content: str,
    title: str | None = None,
) -> dict[str, Any]:
    channel = get_channel(channel_id)
    layer = layer.upper()
    now = _now_iso()
    entry = {
        "entry_id": f"mock-{next(_id_counter)}",
        "channel_id": channel_id,
        "layer": layer,
        "title": title,
        "content": content,
        "sort_order": len([e for e in channel["memory"] if e["layer"] == layer]) + 1,
        "created_by": "voice-app-mock-user",
        "creator_type": "user",
        "created_at": now,
        "updated_at": now,
    }
    channel["memory"].append(entry)
    return entry


def create_message(channel_id: str, content: str, sender_type: str = "user") -> dict[str, Any]:
    channel = get_channel(channel_id)
    message = {
        "msg_id": f"mock-msg-{next(_id_counter)}",
        "channel_id": channel_id,
        "content": content,
        "sender_type": sender_type,
        "created_at": _now_iso(),
    }
    channel["messages"].append(message)
    return message
