"""Environment-driven configuration for the Mac Mini local Pipecat agent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ENV = BASE_DIR.parent.parent / ".env"

DEFAULT_SYSTEM_INSTRUCTION = """你是一个语音助手，正在和用户实时语音对话。
说话自然口语化，适合语音播报，不要用列表或 markdown 格式。"""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class LocalAgentConfig:
    # LiveKit Cloud (outbound from Mac — no public IP required on LAN)
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str
    livekit_room: str
    livekit_agent_token: str

    # STT
    stt_backend: str
    stt_model: str
    stt_device: str
    stt_language: str

    # LLM
    llm_backend: str
    llm_model: str
    llm_base_url: str
    llm_api_key: str
    llm_temperature: float
    llm_max_tokens: int

    # TTS
    tts_backend: str
    tts_model: str
    tts_device: str
    tts_voice: str
    tts_sample_rate: int

    # Memory (LanceDB on Mac)
    memory_enabled: bool
    lancedb_path: str
    memory_top_k: int
    memory_embedding_model: str

    # DashScope fallback (when backend=dashscope)
    dashscope_api_key: str

    system_instruction: str
    log_level: str

    # Pipeline / LiveKit session keep-alive
    pipeline_enable_heartbeats: bool
    heartbeats_period_secs: float
    heartbeats_monitor_secs: float
    livekit_auto_reconnect: bool
    livekit_reconnect_delay_secs: float

    @classmethod
    def from_env(cls) -> LocalAgentConfig:
        return cls(
            livekit_url=_env("LIVEKIT_URL", "wss://your-project.livekit.cloud"),
            livekit_api_key=_env("LIVEKIT_API_KEY"),
            livekit_api_secret=_env("LIVEKIT_API_SECRET"),
            livekit_room=_env("LIVEKIT_ROOM_NAME", "voicechat-local"),
            livekit_agent_token=_env("LIVEKIT_AGENT_TOKEN"),
            stt_backend=_env("LOCAL_STT_BACKEND", "sensevoice").lower(),
            stt_model=_env("LOCAL_STT_MODEL", "iic/SenseVoiceSmall"),
            stt_device=_env("LOCAL_STT_DEVICE", "mps"),
            stt_language=_env("LOCAL_STT_LANGUAGE", "auto"),
            # Default llamacpp: llama-server OpenAI-compatible HTTP (separate process).
            llm_backend=_env("LOCAL_LLM_BACKEND", "llamacpp").lower(),
            llm_model=_env("LOCAL_LLM_MODEL", "qwen2.5-7b-instruct"),
            llm_base_url=_env("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080/v1"),
            llm_api_key=_env("LOCAL_LLM_API_KEY", "local"),
            llm_temperature=float(_env("LOCAL_LLM_TEMPERATURE", "0.7")),
            llm_max_tokens=int(_env("LOCAL_LLM_MAX_TOKENS", "512")),
            tts_backend=_env("LOCAL_TTS_BACKEND", "qwen3_tts").lower(),
            tts_model=_env("LOCAL_TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"),
            tts_device=_env("LOCAL_TTS_DEVICE", "mps"),
            tts_voice=_env("LOCAL_TTS_VOICE", "Cherry"),
            tts_sample_rate=int(_env("LOCAL_TTS_SAMPLE_RATE", "24000")),
            memory_enabled=_env_bool("LOCAL_MEMORY_ENABLED", True),
            lancedb_path=_env("LANCEDB_PATH", str(BASE_DIR / "data" / "lancedb")),
            memory_top_k=int(_env("LOCAL_MEMORY_TOP_K", "5")),
            memory_embedding_model=_env(
                "LOCAL_MEMORY_EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
            ),
            dashscope_api_key=_env("QWEN_API_KEY") or _env("DASHSCOPE_API_KEY"),
            system_instruction=_env("LOCAL_SYSTEM_INSTRUCTION", DEFAULT_SYSTEM_INSTRUCTION),
            log_level=_env("LOG_LEVEL", "INFO"),
            pipeline_enable_heartbeats=_env_bool("PIPELINE_ENABLE_HEARTBEATS", True),
            heartbeats_period_secs=float(_env("PIPELINE_HEARTBEATS_PERIOD_SECS", "5")),
            heartbeats_monitor_secs=float(_env("PIPELINE_HEARTBEATS_MONITOR_SECS", "30")),
            livekit_auto_reconnect=_env_bool("LIVEKIT_AUTO_RECONNECT", True),
            livekit_reconnect_delay_secs=float(_env("LIVEKIT_RECONNECT_DELAY_SECS", "5")),
        )

    def validate_livekit(self) -> None:
        if self.livekit_agent_token:
            return
        if not self.livekit_api_key or not self.livekit_api_secret:
            raise ValueError(
                "Set LIVEKIT_AGENT_TOKEN or both LIVEKIT_API_KEY and LIVEKIT_API_SECRET "
                "(LiveKit Cloud project credentials)."
            )
        if not self.livekit_room:
            raise ValueError("LIVEKIT_ROOM_NAME is required.")

    def validate_livekit_url_direct(self) -> None:
        """LiveKit Cloud WSS must not be proxied through Cloudflare Tunnel."""
        url = self.livekit_url.lower()
        if any(marker in url for marker in ("cloudflare", "trycloudflare.com", "cfargotunnel.com")):
            raise ValueError(
                "LIVEKIT_URL must be the native LiveKit Cloud endpoint "
                "(wss://<project>.livekit.cloud). Do NOT route LiveKit signaling through Cloudflare Tunnel."
            )
        if "livekit.cloud" not in url and not url.startswith("ws://127.0.0.1"):
            raise ValueError(
                "LIVEKIT_URL should be wss://<your-project>.livekit.cloud for production. "
                "Frontends and Mac agent must connect to LiveKit Cloud directly."
            )

    def warn_mps_layout(self) -> list[str]:
        """Return warnings about LLM layout vs STT/TTS on MPS."""
        notes: list[str] = []
        if self.llm_backend in ("llamacpp", "ollama", "openai_compat") and self.stt_device == "mps" and self.tts_device == "mps":
            notes.append(
                "SenseVoice + Qwen3-TTS share MPS inside Pipecat; keep LLM on llama-server "
                "(LOCAL_LLM_BACKEND=llamacpp) as a separate process to reduce Metal contention."
            )
        return notes
