"""LanceDB 长期记忆服务（Phase 1）。

设计原则（Runtime §7）：
- 存 profile / semantic / source_index
- 不深拷 MES/业务系统流水；第三方系统是业务真相源
- Phase 1 用简易 hash embedding，避免引入重型向量模型依赖
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa

EMBED_DIM = 64
DATA_DIR = Path(__file__).resolve().parent / "data" / "lancedb"


def _simple_embed(text: str) -> list[float]:
    """字符 n-gram 哈希到固定维度，仅用于 Phase 1 近邻检索，非生产语义模型。"""
    vec = [0.0] * EMBED_DIM
    t = (text or "").lower().strip()
    if not t:
        return vec
    grams = [t[i : i + 2] for i in range(max(len(t) - 1, 1))]
    grams.append(t[:1])
    for g in grams:
        h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
        vec[h % EMBED_DIM] += 1.0
    norm = sum(v * v for v in vec) ** 0.5
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


class MemoryService:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(self.data_dir))
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        if "profiles" not in self.db.table_names():
            schema = pa.schema(
                [
                    ("user_id", pa.string()),
                    ("profile_json", pa.string()),
                    ("updated_at", pa.float64()),
                ]
            )
            self.db.create_table("profiles", schema=schema)

        if "semantic" not in self.db.table_names():
            schema = pa.schema(
                [
                    ("id", pa.string()),
                    ("user_id", pa.string()),
                    ("text", pa.string()),
                    ("vector", pa.list_(pa.float32(), EMBED_DIM)),
                    ("provenance", pa.string()),
                    ("source", pa.string()),
                    ("source_id", pa.string()),
                    ("status", pa.string()),
                    ("created_at", pa.float64()),
                    ("updated_at", pa.float64()),
                ]
            )
            self.db.create_table("semantic", schema=schema)

        if "source_index" not in self.db.table_names():
            schema = pa.schema(
                [
                    ("entity_id", pa.string()),
                    ("user_id", pa.string()),
                    ("aliases_json", pa.string()),
                    ("providers_json", pa.string()),
                    ("last_verified_at", pa.float64()),
                ]
            )
            self.db.create_table("source_index", schema=schema)

    # --- profile ---

    def upsert_profile(self, user_id: str, profile: dict[str, Any]) -> None:
        table = self.db.open_table("profiles")
        # LanceDB 无原生 upsert：先删后加（Phase 1 用户量极小）
        try:
            table.delete(f'user_id = "{user_id}"')
        except Exception:
            pass
        table.add(
            [
                {
                    "user_id": user_id,
                    "profile_json": json.dumps(profile, ensure_ascii=False),
                    "updated_at": time.time(),
                }
            ]
        )

    def get_profile(self, user_id: str) -> dict[str, Any] | None:
        table = self.db.open_table("profiles")
        try:
            df = table.to_pandas()
            hit = df[df["user_id"] == user_id]
            if hit.empty:
                return None
            return json.loads(hit.iloc[0]["profile_json"])
        except Exception:
            return None

    # --- semantic ---

    def add_semantic(
        self,
        user_id: str,
        text: str,
        *,
        provenance: str = "session",
        source: str = "local",
        source_id: str | None = None,
        status: str = "active",
        entry_id: str | None = None,
    ) -> dict[str, Any]:
        eid = entry_id or str(uuid.uuid4())
        now = time.time()
        row = {
            "id": eid,
            "user_id": user_id,
            "text": text,
            "vector": _simple_embed(text),
            "provenance": provenance,
            "source": source,
            "source_id": source_id or "",
            "status": status,
            "created_at": now,
            "updated_at": now,
        }
        self.db.open_table("semantic").add([row])
        return row

    def supersede_semantic(self, user_id: str, entry_id: str) -> None:
        table = self.db.open_table("semantic")
        try:
            df = table.to_pandas()
            mask = (df["user_id"] == user_id) & (df["id"] == entry_id)
            if mask.any():
                # 重写整表该行状态：Phase 1 简化为删+加
                row = df[mask].iloc[0].to_dict()
                table.delete(f'id = "{entry_id}"')
                row["status"] = "superseded"
                row["updated_at"] = time.time()
                row["vector"] = _simple_embed(str(row.get("text") or ""))
                table.add([row])
        except Exception:
            pass

    def search_semantic(self, user_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        table = self.db.open_table("semantic")
        try:
            hits = (
                table.search(_simple_embed(query))
                .where(f'user_id = "{user_id}" AND status = "active"')
                .limit(limit)
                .to_list()
            )
        except Exception:
            # 回退：关键词扫描
            try:
                df = table.to_pandas()
                df = df[(df["user_id"] == user_id) & (df["status"] == "active")]
                q = query.lower()
                scored = []
                for _, r in df.iterrows():
                    text = str(r["text"])
                    score = sum(1 for ch in q if ch in text.lower())
                    if score > 0:
                        scored.append((score, r.to_dict()))
                scored.sort(key=lambda x: -x[0])
                hits = [s[1] for s in scored[:limit]]
            except Exception:
                hits = []
        out = []
        for h in hits:
            out.append(
                {
                    "id": h.get("id"),
                    "text": h.get("text"),
                    "source": h.get("source"),
                    "source_id": h.get("source_id") or None,
                    "provenance": h.get("provenance"),
                    "status": h.get("status"),
                    "confidence": 0.75,
                }
            )
        return out

    def list_hot_memory(self, user_id: str, limit: int = 12) -> list[dict[str, Any]]:
        table = self.db.open_table("semantic")
        try:
            df = table.to_pandas()
            df = df[(df["user_id"] == user_id) & (df["status"] == "active")]
            df = df.sort_values("updated_at", ascending=False).head(limit)
            return [
                {
                    "id": r["id"],
                    "text": r["text"],
                    "source": r["source"],
                    "source_id": r["source_id"] or None,
                }
                for _, r in df.iterrows()
            ]
        except Exception:
            return []

    def mirror_agentnexus_entries(self, user_id: str, entries: list[dict[str, Any]]) -> int:
        """把 Mock 拉到的条目镜像进 semantic（带 source=agentnexus）；不以 LanceDB 为真相源。

        bootstrap 时全量刷新 agentnexus 镜像，避免种子内容更新后仍被旧 source_id 挡住。
        """
        table = self.db.open_table("semantic")
        try:
            table.delete(f'user_id = "{user_id}" AND source = "agentnexus"')
        except Exception:
            pass
        added = 0
        for e in entries:
            sid = e.get("entry_id") or e.get("source_id")
            if not sid:
                continue
            content = e.get("content") or e.get("text") or ""
            title = e.get("title")
            text = f"{title}：{content}" if title else content
            if not text.strip():
                continue
            self.add_semantic(
                user_id,
                text,
                provenance="agentnexus",
                source="agentnexus",
                source_id=sid,
                entry_id=f"agentnexus:{sid}",
            )
            added += 1
        return added

    # --- source_index ---

    def upsert_source_index(
        self,
        user_id: str,
        entity_id: str,
        aliases: list[str],
        providers: list[dict[str, Any]],
        last_verified_at: float | None = None,
    ) -> None:
        table = self.db.open_table("source_index")
        try:
            table.delete(f'user_id = "{user_id}" AND entity_id = "{entity_id}"')
        except Exception:
            pass
        table.add(
            [
                {
                    "entity_id": entity_id,
                    "user_id": user_id,
                    "aliases_json": json.dumps(aliases, ensure_ascii=False),
                    "providers_json": json.dumps(providers, ensure_ascii=False),
                    "last_verified_at": last_verified_at or 0.0,
                }
            ]
        )

    def lookup_source_index(self, user_id: str, hint: str) -> list[dict[str, Any]]:
        table = self.db.open_table("source_index")
        try:
            df = table.to_pandas()
            df = df[df["user_id"] == user_id]
            hint_l = hint.lower()
            out = []
            for _, r in df.iterrows():
                aliases = json.loads(r["aliases_json"] or "[]")
                blob = " ".join([r["entity_id"], *aliases]).lower()
                if any(tok in blob for tok in hint_l.replace("，", " ").split() if tok) or any(
                    a.lower() in hint_l for a in aliases
                ):
                    out.append(
                        {
                            "entity_id": r["entity_id"],
                            "aliases": aliases,
                            "providers": json.loads(r["providers_json"] or "[]"),
                            "last_verified_at": r["last_verified_at"] or None,
                        }
                    )
            return out
        except Exception:
            return []


_service: MemoryService | None = None


def get_memory_service() -> MemoryService:
    global _service
    if _service is None:
        _service = MemoryService()
    return _service
