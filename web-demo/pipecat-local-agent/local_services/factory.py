"""Build STT / LLM / TTS services from LOCAL_* environment variables."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from .config import LocalAgentConfig
from .llm_local import build_llm_service
from .memory_lancedb import LanceDBMemoryProcessor
from .stt_sensevoice import SenseVoiceSTTService
from .tts_qwen_local import QwenLocalTTSService

# Reuse DashScope cloud services from the comparison demo when backend=dashscope.
_PIPECAT_LIVEKIT = Path(__file__).resolve().parents[1].parent / "pipecat-livekit"
if _PIPECAT_LIVEKIT.is_dir() and str(_PIPECAT_LIVEKIT) not in sys.path:
    sys.path.insert(0, str(_PIPECAT_LIVEKIT))


def build_stt(config: LocalAgentConfig):
    backend = config.stt_backend
    if backend == "sensevoice":
        return SenseVoiceSTTService(config)
    if backend == "dashscope":
        from dashscope_services import DashScopeSTTService

        if not config.dashscope_api_key:
            raise ValueError("LOCAL_STT_BACKEND=dashscope requires QWEN_API_KEY")
        logger.info(f"STT backend=dashscope model={config.stt_model}")
        return DashScopeSTTService(api_key=config.dashscope_api_key, model=config.stt_model)
    raise ValueError(f"Unknown LOCAL_STT_BACKEND={backend!r}. Use sensevoice or dashscope.")


def build_tts(config: LocalAgentConfig):
    backend = config.tts_backend
    if backend in ("qwen3_tts", "qwen_tts", "local"):
        return QwenLocalTTSService(config)
    if backend == "dashscope":
        from dashscope_services import DashScopeTTSV2Service

        if not config.dashscope_api_key:
            raise ValueError("LOCAL_TTS_BACKEND=dashscope requires QWEN_API_KEY")
        logger.info(f"TTS backend=dashscope model={config.tts_model} voice={config.tts_voice}")
        return DashScopeTTSV2Service(
            api_key=config.dashscope_api_key,
            model=config.tts_model or "cosyvoice-v3-flash",
            voice=config.tts_voice,
            sample_rate=config.tts_sample_rate,
        )
    raise ValueError(f"Unknown LOCAL_TTS_BACKEND={backend!r}. Use qwen3_tts or dashscope.")


def build_memory(config: LocalAgentConfig) -> LanceDBMemoryProcessor:
    return LanceDBMemoryProcessor(config, base_instruction=config.system_instruction)


def build_pipeline_services(config: LocalAgentConfig):
    return {
        "stt": build_stt(config),
        "llm": build_llm_service(config),
        "tts": build_tts(config),
        "memory": build_memory(config),
    }
