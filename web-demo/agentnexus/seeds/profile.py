"""Demo 频道画像（与 auth.USERS[\"demo\"] 对齐）。"""
from __future__ import annotations

from typing import Any

SEED_PROFILES: dict[str, dict[str, Any]] = {
    "demo-channel": {
        "user_id": "user-demo",
        "language": "zh-CN",
        "timezone": "Asia/Shanghai",
        "response_style": "concise",
        "role": "产线主管",
        "interests": ["设备异常", "排产", "OEE", "订单交期"],
        "preferred_topics": ["今日待办", "今日日程", "M102跟进"],
        "greeting_style": "warm_brief",
        "workplace": "A厂",
    },
}
