"""Local SenseVoice STT (PyTorch / MPS on Mac Mini)."""

from __future__ import annotations

import asyncio
import tempfile
import wave
from pathlib import Path
from typing import Any

from loguru import logger

from pipecat.frames.frames import ErrorFrame, TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.utils.time import time_now_iso8601
from pipecat.utils.tracing.service_decorators import traced_stt

from .config import LocalAgentConfig


class SenseVoiceSTTService(SegmentedSTTService):
    """Segmented STT using FunASR SenseVoice on local GPU (MPS/CUDA/CPU)."""

    def __init__(self, config: LocalAgentConfig, **kwargs: Any) -> None:
        super().__init__(settings=self.Settings(model=config.stt_model), **kwargs)
        self._config = config
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise ImportError(
                "SenseVoice backend requires funasr. Install with: "
                "pip install -r requirements-local-mac.txt"
            ) from exc

        device = self._config.stt_device
        logger.info(f"Loading SenseVoice model={self._config.stt_model} device={device}")
        self._model = AutoModel(
            model=self._config.stt_model,
            device=device,
            disable_update=True,
        )
        return self._model

    @traced_stt
    async def _handle_transcription(self, transcript: str, is_final: bool):
        return None

    async def run_stt(self, audio: bytes):
        try:
            await self.start_processing_metrics()
            text = await asyncio.to_thread(self._transcribe_pcm, audio)
            await self.stop_processing_metrics()
            text = (text or "").strip()
            if not text:
                return
            await self._handle_transcription(text, True)
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601())
        except Exception as exc:
            yield ErrorFrame(error=f"SenseVoice STT error: {exc}")

    def _transcribe_pcm(self, audio: bytes) -> str:
        model = self._load_model()
        sample_rate = self.sample_rate or self._init_sample_rate or 16000

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = Path(handle.name)
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio)

        try:
            result = model.generate(
                input=str(path),
                language=self._config.stt_language,
                use_itn=True,
            )
        finally:
            path.unlink(missing_ok=True)

        if isinstance(result, list) and result:
            item = result[0]
            if isinstance(item, dict):
                return item.get("text") or item.get("sentence") or str(item)
            return str(item)
        if isinstance(result, dict):
            return result.get("text", "")
        return str(result or "")
