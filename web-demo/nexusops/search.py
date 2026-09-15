"""NexusOps 检索：把结构化目录打成可搜索文档，带 citation。"""
from __future__ import annotations

import re
from typing import Any

from .seeds.catalog import build_catalog

_catalog: dict[str, Any] | None = None


def get_catalog() -> dict[str, Any]:
    global _catalog
    if _catalog is None:
        _catalog = build_catalog()
    return _catalog


def reset_catalog() -> None:
    global _catalog
    _catalog = build_catalog()


def _tokens(query: str) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []
    tokens: list[str] = []
    for m in re.finditer(r"[A-Za-z0-9][\w\-]*", q):
        tokens.append(m.group(0))
    for m in re.finditer(r"[\u4e00-\u9fff]+", q):
        span = m.group(0)
        tokens.append(span)
        # 始终抽 2–4 字片，避免「产线情况」整词 miss「产线」
        if len(span) >= 2:
            for n in (4, 3, 2):
                if len(span) < n:
                    continue
                for i in range(0, len(span) - n + 1):
                    tokens.append(span[i : i + n])
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        tl = t.lower()
        if tl in seen or len(t) < 2:
            continue
        seen.add(tl)
        out.append(t)
    return out or ([q] if q else [])


def _docs() -> list[dict[str, Any]]:
    c = get_catalog()
    docs: list[dict[str, Any]] = []

    def add(resource: str, entity_id: str, title: str, text: str, extra: dict | None = None):
        docs.append(
            {
                "resource": resource,
                "entity_id": entity_id,
                "title": title,
                "text": text,
                "extra": extra or {},
                "citation": {
                    "system": "nexusops",
                    "resource": resource,
                    "id": entity_id,
                    "label": f"NexusOps {resource}/{entity_id}",
                },
            }
        )

    ov = c["overview"]
    add("overview", ov["id"], ov["title"], ov["summary"])
    ds = c["delay_summary"]
    add("delay_summary", ds["id"], ds["title"], ds["summary"])

    for line in c["lines"]:
        add("line", line["id"], line["name"], line["summary"], {"status": line["status"]})
    for m in c["machines"]:
        add("machine", m["id"], f"设备{m['id']}", m["summary"], {"status": m["status"]})
    for o in c["orders"]:
        add(
            "order",
            o["id"],
            f"订单{o['id']}",
            o["summary"],
            {"delayed": o["delayed"], "line": o["line_name"]},
        )
    for w in c["work_orders"]:
        add("work_order", w["id"], f"工单{w['id']}", w["summary"], {"status": w["status"]})
    for mat in c["materials"]:
        add("material", mat["sku"], f"物料{mat['sku']}", mat["summary"], {"status": mat["status"]})

    # 质量汇总（从订单派生）
    q_parts = []
    for o in c["orders"]:
        if o.get("quality_pass_rate") is not None:
            q_parts.append(f"{o['id']} 合格率 {o['quality_pass_rate']}%（{o['line_name']}）")
    add(
        "quality",
        "quality-today",
        "质量抽检",
        "质量情况：" + "；".join(q_parts) + "。M102 故障主要导致进度延误，未引入批量质量扣留。",
    )
    return docs


def search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    tokens = _tokens(query)
    if not tokens:
        return []
    q_l = (query or "").lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for d in _docs():
        blob = f"{d['title']} {d['text']} {d['entity_id']}".lower()
        score = 0
        for t in tokens:
            if t.lower() in blob:
                score += max(2, len(t))
        # 轻量意图加权，避免「产线情况」被质量汇总抢首条
        if "产线" in q_l and d["resource"] in ("overview", "line"):
            score += 8
        if ("设备" in q_l or "故障" in q_l) and d["resource"] in ("machine", "work_order", "overview"):
            score += 8
        if ("订单" in q_l or "延误" in q_l or "交期" in q_l) and d["resource"] in (
            "order",
            "delay_summary",
            "overview",
        ):
            score += 8
        if score > 0:
            scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:limit]]


def get_overview() -> dict[str, Any]:
    return dict(get_catalog()["overview"])


def get_order(order_id: str) -> dict[str, Any] | None:
    for o in get_catalog()["orders"]:
        if o["id"].lower() == order_id.lower():
            return dict(o)
    return None


def get_machine(machine_id: str) -> dict[str, Any] | None:
    for m in get_catalog()["machines"]:
        if m["id"].lower() == machine_id.lower():
            return dict(m)
    return None


def get_line(line_id: str) -> dict[str, Any] | None:
    for line in get_catalog()["lines"]:
        if line["id"].lower() == line_id.lower() or line["name"] == line_id:
            return dict(line)
    return None


def list_lines() -> list[dict[str, Any]]:
    return [dict(x) for x in get_catalog()["lines"]]


def list_orders() -> list[dict[str, Any]]:
    return [dict(x) for x in get_catalog()["orders"]]
