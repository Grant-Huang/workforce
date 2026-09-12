"""Local Pipecat services for Mac Mini (SenseVoice / llama.cpp / Qwen3-TTS / LanceDB)."""

from .config import LocalAgentConfig
from .factory import build_pipeline_services
from .memory_lancedb import LanceDBMemoryProcessor
from .stt_sensevoice import SenseVoiceSTTService
from .llm_local import build_llm_service
from .tts_qwen_local import QwenLocalTTSService

__all__ = [
    "LocalAgentConfig",
    "SenseVoiceSTTService",
    "QwenLocalTTSService",
    "LanceDBMemoryProcessor",
    "build_llm_service",
    "build_pipeline_services",
]
