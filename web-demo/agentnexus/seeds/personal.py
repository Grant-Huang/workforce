"""个人偏好 / 日程 / 待办种子（AgentNexus 职责）。

产线/订单/设备等运营事实在 NexusOps，不在此包。
待办里可引用 ORD-/WO- 作为指针，进度以 NexusOps 为准。
"""
from __future__ import annotations

from typing import Any


def personal_entries(channel_id: str, now: str) -> list[dict[str, Any]]:
    return [
        {
            "entry_id": "seed-anchor-1",
            "channel_id": channel_id,
            "layer": "ANCHOR",
            "title": None,
            "content": "用户是 A 厂产线主管，偏好中文交流，日常关注设备异常、排产与订单交期。",
            "sort_order": 1,
            "created_by": "mock-seed",
            "creator_type": "user",
            "created_at": now,
            "updated_at": now,
        },
        {
            "entry_id": "seed-anchor-2",
            "channel_id": channel_id,
            "layer": "ANCHOR",
            "title": None,
            "content": "M102 属于产线 A；用户习惯叫它「产线A那台」。运营详情查 NexusOps。",
            "sort_order": 2,
            "created_by": "mock-seed",
            "creator_type": "user",
            "created_at": now,
            "updated_at": now,
        },
        {
            "entry_id": "seed-anchor-3",
            "channel_id": channel_id,
            "layer": "ANCHOR",
            "title": None,
            "content": "用户负责早班产线稳定性，遇到停机希望先口头同步再补工单。",
            "sort_order": 3,
            "created_by": "mock-seed",
            "creator_type": "user",
            "created_at": now,
            "updated_at": now,
        },
        {
            "entry_id": "seed-schedule-today",
            "channel_id": channel_id,
            "layer": "DECISIONS",
            "title": "今日日程",
            "content": (
                "今天日程：09:00 早会巡线（重点产线 A / M102）；"
                "11:00 与计划员赵敏对齐 ORD-20260915-01 交期；"
                "15:00 跟智枢团队开会同步记忆集成；19:00 健身预约。"
            ),
            "sort_order": 1,
            "created_by": "mock-seed",
            "creator_type": "user",
            "created_at": now,
            "updated_at": now,
        },
        {
            "entry_id": "seed-todos-today",
            "channel_id": channel_id,
            "layer": "DECISIONS",
            "title": "今日待办",
            "content": (
                "今日待办：1）跟进维修工单 WO-8842 是否 14:00 前完工；"
                "2）确认订单 ORD-20260915-01 是否延误（进度以 NexusOps 为准）；"
                "3）催采购物料 SKU-PLATE-7；4）晚上确认健身预约。"
            ),
            "sort_order": 2,
            "created_by": "mock-seed",
            "creator_type": "user",
            "created_at": now,
            "updated_at": now,
        },
        {
            "entry_id": "seed-progress-personal-1",
            "channel_id": channel_id,
            "layer": "PROGRESS",
            "title": None,
            "content": (
                "用户个人跟进：昨天 M102 温度告警后短暂停机，怀疑冷却风扇；"
                "仍在待办里盯 WO-8842。具体设备/订单状态请查 NexusOps。"
            ),
            "sort_order": 1,
            "created_by": "mock-seed",
            "creator_type": "user",
            "created_at": now,
            "updated_at": now,
        },
        {
            "entry_id": "seed-progress-personal-2",
            "channel_id": channel_id,
            "layer": "PROGRESS",
            "title": None,
            "content": "今天早班用户想先确认设备异常再调排产；排产与产线实况以 NexusOps 为准。",
            "sort_order": 2,
            "created_by": "mock-seed",
            "creator_type": "user",
            "created_at": now,
            "updated_at": now,
        },
        {
            "entry_id": "seed-progress-personal-3",
            "channel_id": channel_id,
            "layer": "PROGRESS",
            "title": None,
            "content": "本周个人目标：盯住 M102 复发风险，并把语音助手接到日常晨会汇报里。",
            "sort_order": 3,
            "created_by": "mock-seed",
            "creator_type": "user",
            "created_at": now,
            "updated_at": now,
        },
        {
            "entry_id": "seed-decisions-pref-schedule",
            "channel_id": channel_id,
            "layer": "DECISIONS",
            "title": "排产偏好",
            "content": "用户希望早班优先保证 OEE，异常停机要先口头同步再写工单；订单延误要第一时间报交期风险。",
            "sort_order": 3,
            "created_by": "mock-seed",
            "creator_type": "user",
            "created_at": now,
            "updated_at": now,
        },
        {
            "entry_id": "seed-decisions-comm",
            "channel_id": channel_id,
            "layer": "DECISIONS",
            "title": "沟通偏好",
            "content": "开场喜欢直奔主题：先问设备/排产/订单或今日待办，不喜欢冗长客套。",
            "sort_order": 4,
            "created_by": "mock-seed",
            "creator_type": "user",
            "created_at": now,
            "updated_at": now,
        },
        # 冷：联系人等细节走 Retrieval
        {
            "entry_id": "seed-retrieve-contacts",
            "channel_id": channel_id,
            "layer": "ANCHOR",
            "title": "联系人",
            "content": (
                "设备异常升级：维修班长王强（尾号 6688）；"
                "订单交期升级：计划员赵敏；个人日程助手频道即本频道。"
            ),
            "sort_order": 10,
            "include_in_hot": False,
            "created_by": "mock-seed",
            "creator_type": "user",
            "created_at": now,
            "updated_at": now,
        },
        {
            "entry_id": "seed-retrieve-todo-detail",
            "channel_id": channel_id,
            "layer": "PROGRESS",
            "title": "待办备注",
            "content": (
                "待办备注：WO-8842 与 ORD-20260915-01 是今日最高优先级；"
                "问进度/是否延误时不要用本条猜测，应查 NexusOps。"
            ),
            "sort_order": 11,
            "include_in_hot": False,
            "created_by": "mock-seed",
            "creator_type": "user",
            "created_at": now,
            "updated_at": now,
        },
    ]
