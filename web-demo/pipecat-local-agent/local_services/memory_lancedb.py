"""LanceDB vector memory — retrieve before LLM, save after assistant turn."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import LLMUpdateSettingsFrame

from .config import LocalAgentConfig


class LanceDBMemoryProcessor(FrameProcessor):
    """Local RAG memory using LanceDB on Mac Mini."""

    def __init__(self, config: LocalAgentConfig, base_instruction: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._base_instruction = base_instruction
        self._db = None
        self._table = None
        self._embedder = None
        self._last_user_text = ""
        self._assistant_buffer: list[str] = []
        self._memory_active = config.memory_enabled

        if config.memory_enabled:
            self._init_store()

    def _init_store(self) -> None:
        try:
            import lancedb
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            logger.warning(
                "LOCAL_MEMORY_ENABLED but lancedb/sentence-transformers missing — memory disabled. "
                f"({exc})"
            )
            self._memory_active = False
            return

        path = self._config.lancedb_path
        logger.info(f"LanceDB memory path={path}")
        self._db = lancedb.connect(path)
        self._embedder = SentenceTransformer(self._config.memory_embedding_model)
        if "memories" not in self._db.table_names():
            self._table = self._db.create_table(
                "memories",
                data=[{
                    "id": str(uuid.uuid4()),
                    "text": "bootstrap",
                    "vector": self._embed([ "bootstrap" ])[0],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }],
            )
            self._table.delete("text = 'bootstrap'")
        else:
            self._table = self._db.open_table("memories")

    def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._embedder.encode(texts, normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]

    def _search(self, query: str) -> list[str]:
        if not self._table or not query.strip():
            return []
        vector = self._embed([query])[0]
        rows = (
            self._table.search(vector)
            .limit(self._config.memory_top_k)
            .to_list()
        )
        return [row["text"] for row in rows if row.get("text")]

    def _save(self, text: str) -> None:
        if not self._table or not text.strip():
            return
        self._table.add([{
            "id": str(uuid.uuid4()),
            "text": text.strip(),
            "vector": self._embed([text.strip()])[0],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }])

    def _instruction_with_memory(self, user_text: str) -> str:
        hits = self._search(user_text)
        if not hits:
            return self._base_instruction
        lines = "\n".join(f"- {line}" for line in hits)
        return (
            f"{self._base_instruction}\n\n"
            f"以下是本地 LanceDB 检索到的相关记忆，如有帮助请参考：\n{lines}"
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if not self._memory_active:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            self._last_user_text = frame.text
            instruction = self._instruction_with_memory(frame.text)
            await self.push_frame(
                LLMUpdateSettingsFrame(settings={"system_instruction": instruction}),
                direction,
            )
        elif isinstance(frame, LLMTextFrame):
            self._assistant_buffer.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            user_text = self._last_user_text.strip()
            assistant_text = "".join(self._assistant_buffer).strip()
            self._assistant_buffer.clear()
            if user_text and assistant_text:
                self._save(f"用户：{user_text}\n助手：{assistant_text}")

        await self.push_frame(frame, direction)
