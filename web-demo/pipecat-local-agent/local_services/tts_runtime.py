"""Default TTS runtime hook — replace with your Qwen3-TTS (MPS) implementation on Mac.

Install your Qwen3-TTS package, then either:
  - edit this file, or
  - set LOCAL_TTS_MODULE=your.module.path in .env
"""

from __future__ import annotations


def synthesize(
    text: str,
    *,
    model: str,
    voice: str,
    device: str,
    sample_rate: int,
) -> bytes:
    """Synthesize PCM16 mono audio. Override on Mac Mini with real Qwen3-TTS."""
    raise NotImplementedError(
        "Local Qwen3-TTS is not configured. On Mac Mini, implement synthesize() in "
        "local_services/tts_runtime.py or set LOCAL_TTS_MODULE to your module. "
        "For cloud fallback, set LOCAL_TTS_BACKEND=dashscope in .env."
    )
