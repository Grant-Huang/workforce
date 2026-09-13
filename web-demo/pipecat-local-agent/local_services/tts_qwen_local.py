"""Local Qwen3-TTS (MPS) and pluggable TTS backends."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService
from pipecat.utils.tracing.service_decorators import traced_tts

from .config import LocalAgentConfig


class QwenLocalTTSService(TTSService):
    """Local Qwen3-TTS synthesis on Mac (MPS/CUDA/CPU).

    Expects a Python module path in LOCAL_TTS_MODULE (default: local_services.tts_runtime)
    exposing ``synthesize(text, *, model, voice, device, sample_rate) -> bytes``.
    """

    def __init__(self, config: LocalAgentConfig, **kwargs: Any) -> None:
        # pipecat 1.9: settings moved to pipecat.services.settings.TTSSettings.
        # Earlier (<1.9) this used `self.Settings(...)` (a per-class inner dataclass
        # on TTSService); that attribute no longer exists in 1.9.
        super().__init__(
            sample_rate=config.tts_sample_rate,
            push_start_frame=True,
            push_stop_frames=True,
            settings=TTSSettings(model=config.tts_model, voice=config.tts_voice, language=None),
            **kwargs,
        )
        self._config = config
        self._runtime = None

    def _runtime_module(self):
        if self._runtime is not None:
            return self._runtime
        import importlib
        import os

        module_path = os.environ.get("LOCAL_TTS_MODULE", "local_services.tts_runtime")
        self._runtime = importlib.import_module(module_path)
        if not hasattr(self._runtime, "synthesize"):
            raise ImportError(f"{module_path} must define synthesize(text, **kwargs) -> bytes")
        return self._runtime

    @traced_tts
    async def run_tts(self, text: str, context_id: str):
        try:
            await self.start_processing_metrics()
            audio = await asyncio.to_thread(self._synthesize, text)
            await self.stop_processing_metrics()
            if audio:
                yield TTSAudioRawFrame(
                    audio=audio,
                    sample_rate=self.sample_rate or self._init_sample_rate,
                    num_channels=1,
                    context_id=context_id,
                )
        except Exception as exc:
            yield ErrorFrame(error=f"Local Qwen TTS error: {exc}")

    def _synthesize(self, text: str) -> bytes:
        runtime = self._runtime_module()
        logger.debug(
            f"TTS synthesize model={self._config.tts_model} voice={self._config.tts_voice} "
            f"device={self._config.tts_device}"
        )
        return runtime.synthesize(
            text,
            model=self._config.tts_model,
            voice=self._config.tts_voice,
            device=self._config.tts_device,
            sample_rate=self._config.tts_sample_rate,
        )
