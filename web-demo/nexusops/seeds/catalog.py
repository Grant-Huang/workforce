"""NexusOps A 厂早班运营种子（产线/设备/订单/物料/质量/维修）。"""
from __future__ import annotations

from typing import Any


def build_catalog() -> dict[str, Any]:
    """结构化目录；检索时渲染为带 citation 的文本。"""
    lines = [
        {
            "id": "line-A",
            "name": "产线A",
            "status": "受阻",
            "oee_night": 82.4,
            "summary": (
                "产线 A 受阻：关键设备 M102 冷却风扇故障停机，维修工单 WO-8842 进行中；"
                "在制订单 ORD-20260915-01（Shell-A7）进度约 58%，已判定延误。"
            ),
            "machine_ids": ["M101", "M102"],
            "order_ids": ["ORD-20260915-01"],
        },
        {
            "id": "line-B",
            "name": "产线B",
            "status": "正常",
            "oee_night": 91.0,
            "summary": (
                "产线 B 正常运行：设备 M201 正常；在制订单 ORD-20260915-02（Tray-B3）"
                "进度约 76%，预计今天 16:00 准点交付，无延误。"
            ),
            "machine_ids": ["M201"],
            "order_ids": ["ORD-20260915-02"],
        },
        {
            "id": "line-C",
            "name": "产线C",
            "status": "换型中",
            "oee_night": None,
            "summary": (
                "产线 C 换型中，预计 10:30 恢复投产；计划订单 ORD-20260915-03 尚未开线，"
                "物料 SKU-PLATE-7 短缺有交期风险。"
            ),
            "machine_ids": [],
            "order_ids": ["ORD-20260915-03"],
        },
    ]

    machines = [
        {
            "id": "M102",
            "line_id": "line-A",
            "line_name": "产线A",
            "status": "故障停机",
            "fault": "冷却风扇异常，温度告警后停机",
            "work_order_id": "WO-8842",
            "affected_orders": ["ORD-20260915-01"],
            "summary": (
                "设备 M102（产线 A）故障停机：冷却风扇异常。关联维修 WO-8842（进行中，李工，预计今天 14:00 前完工）；"
                "影响订单 ORD-20260915-01。"
            ),
        },
        {
            "id": "M101",
            "line_id": "line-A",
            "line_name": "产线A",
            "status": "正常待机",
            "fault": None,
            "work_order_id": None,
            "affected_orders": [],
            "summary": "设备 M101（产线 A）正常待机，无告警；不能完全替代 M102 工序。",
        },
        {
            "id": "M201",
            "line_id": "line-B",
            "line_name": "产线B",
            "status": "运行正常",
            "fault": None,
            "work_order_id": None,
            "affected_orders": ["ORD-20260915-02"],
            "summary": "设备 M201（产线 B）运行正常，支撑订单 ORD-20260915-02 准点生产。",
        },
    ]

    orders = [
        {
            "id": "ORD-20260915-01",
            "product": "壳盖总成 Shell-A7",
            "qty": 500,
            "done": 290,
            "progress_pct": 58,
            "line_id": "line-A",
            "line_name": "产线A",
            "machine_ids": ["M102"],
            "plan_due": "今天 18:00",
            "eta": "明天 11:00",
            "delayed": True,
            "delay_reason": "产线 A 设备 M102 故障停机，维修工单 WO-8842 未完工",
            "quality_pass_rate": 98.2,
            "materials": [{"sku": "SKU-SHELL-A7", "status": "充足"}],
            "summary": (
                "订单 ORD-20260915-01：产品 Shell-A7，500 套，在产线 A 生产；完成 290/500（58%）；"
                "质检合格率 98.2%；计划交期今天 18:00，预计明天 11:00，已延误。"
                "原因：M102 故障，WO-8842 进行中。"
            ),
        },
        {
            "id": "ORD-20260915-02",
            "product": "托盘组件 Tray-B3",
            "qty": 320,
            "done": 243,
            "progress_pct": 76,
            "line_id": "line-B",
            "line_name": "产线B",
            "machine_ids": ["M201"],
            "plan_due": "今天 16:00",
            "eta": "今天 16:00",
            "delayed": False,
            "delay_reason": None,
            "quality_pass_rate": 99.1,
            "materials": [{"sku": "SKU-TRAY-B3", "status": "充足"}],
            "summary": (
                "订单 ORD-20260915-02：产品 Tray-B3，320 套，在产线 B 生产；完成 243/320（76%）；"
                "质检合格率 99.1%；计划交期今天 16:00，预计准点，无延误。设备 M201 正常。"
            ),
        },
        {
            "id": "ORD-20260915-03",
            "product": "支架 Bracket-C1",
            "qty": 200,
            "done": 0,
            "progress_pct": 0,
            "line_id": "line-C",
            "line_name": "产线C",
            "machine_ids": [],
            "plan_due": "明天 12:00",
            "eta": "明天 12:00（物料风险）",
            "delayed": False,
            "delay_reason": None,
            "quality_pass_rate": None,
            "materials": [{"sku": "SKU-PLATE-7", "status": "短缺，预计明天上午到货"}],
            "summary": (
                "订单 ORD-20260915-03：产品 Bracket-C1，200 套，计划产线 C；尚未开线（换型中）；"
                "进度 0%；计划交期明天 12:00，暂无延误判定，但物料 SKU-PLATE-7 短缺有风险。"
            ),
        },
    ]

    work_orders = [
        {
            "id": "WO-8842",
            "machine_id": "M102",
            "line_name": "产线A",
            "status": "进行中",
            "title": "冷却风扇更换",
            "assignee": "李工",
            "ext": "203",
            "eta": "今天 14:00 前",
            "spare_part": "FAN-M102",
            "bin": "A-03-12",
            "affected_orders": ["ORD-20260915-01"],
            "summary": (
                "维修工单 WO-8842：M102（产线 A）冷却风扇更换，进行中；负责人李工（分机 203）；"
                "预计今天 14:00 前完工；备件 FAN-M102 @ A-03-12。完工前 ORD-20260915-01 持续延误风险。"
            ),
        },
        {
            "id": "WO-8710",
            "machine_id": "M201",
            "line_name": "产线B",
            "status": "已关闭",
            "title": "润滑保养",
            "assignee": "钱工",
            "ext": "210",
            "eta": "已完成",
            "spare_part": None,
            "bin": None,
            "affected_orders": ["ORD-20260915-02"],
            "summary": "维修工单 WO-8710（已关闭）：上周 M201 润滑保养已完成，不影响今日 ORD-20260915-02。",
        },
    ]

    materials = [
        {
            "sku": "SKU-PLATE-7",
            "name": "支架板材",
            "status": "短缺",
            "eta": "明天上午到货",
            "affects_orders": ["ORD-20260915-03"],
            "affects_lines": ["产线C"],
            "summary": (
                "物料 SKU-PLATE-7 短缺：影响产线 C / ORD-20260915-03；已催采购，预计明天上午到货；"
                "夜班曾从产线 B 挪 20 片应急。"
            ),
        },
        {
            "sku": "SKU-SHELL-A7",
            "name": "壳体坯料",
            "status": "充足",
            "eta": None,
            "affects_orders": ["ORD-20260915-01"],
            "affects_lines": ["产线A"],
            "summary": "物料 SKU-SHELL-A7 库存充足，不影响 ORD-20260915-01。",
        },
    ]

    overview = {
        "id": "plant-A-shift-morning",
        "title": "今日产线总览",
        "summary": (
            "A 厂早班：产线 A 受阻（M102 故障 / WO-8842，订单 ORD-20260915-01 延误）；"
            "产线 B 正常（ORD-20260915-02 准点）；产线 C 换型中（ORD-20260915-03 待开线，SKU-PLATE-7 短缺风险）。"
        ),
    }

    delay_summary = {
        "id": "delay-summary-today",
        "title": "订单延误汇总",
        "summary": (
            "今日订单延误：ORD-20260915-01（产线 A）已延误，预计交期明天 11:00，原因 M102/WO-8842；"
            "ORD-20260915-02（产线 B）准点；ORD-20260915-03（产线 C）暂无延误但有物料风险。"
        ),
    }

    return {
        "overview": overview,
        "delay_summary": delay_summary,
        "lines": lines,
        "machines": machines,
        "orders": orders,
        "work_orders": work_orders,
        "materials": materials,
    }
