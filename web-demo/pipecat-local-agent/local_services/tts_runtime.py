"""Local Qwen3-TTS runtime via the ``qwen-tts`` package (self-hosted on Mac).

Requires: pip install qwen-tts soundfile
Configure model path/voice via LOCAL_TTS_* in repo-root .env.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np

_model: Any = None
_model_key: tuple[str, str] | None = None


def _resolve_device(device: str) -> str:
    mapping = {"mps": "mps", "cuda": "cuda:0", "cpu": "cpu"}
    return mapping.get(device.lower(), device)


def _load_model(model: str, device: str):
    global _model, _model_key
    key = (model, device)
    if _model is not None and _model_key == key:
        return _model

    try:
        import torch
        from qwen_tts import Qwen3TTSModel
    except ImportError as exc:
        raise ImportError(
            "Local Qwen TTS requires: pip install qwen-tts soundfile torch"
        ) from exc

    model_path = os.environ.get("LOCAL_TTS_MODEL_PATH", model)
    device_map = _resolve_device(device)
    dtype = torch.float32 if device_map == "cpu" else torch.bfloat16

    _model = Qwen3TTSModel.from_pretrained(
        model_path,
        device_map=device_map,
        dtype=dtype,
    )
    _model_key = key
    return _model


def _wav_to_pcm16(wav: np.ndarray, source_rate: int, target_rate: int) -> bytes:
    audio = np.asarray(wav, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=-1)
    audio = np.clip(audio, -1.0, 1.0)

    if source_rate != target_rate and source_rate > 0:
        duration = audio.shape[0] / source_rate
        target_len = max(1, int(round(duration * target_rate)))
        source_times = np.linspace(0.0, duration, num=audio.shape[0], endpoint=False)
        target_times = np.linspace(0.0, duration, num=target_len, endpoint=False)
        audio = np.interp(target_times, source_times, audio).astype(np.float32)

    return (audio * 32767.0).astype(np.int16).tobytes()


def synthesize(
    text: str,
    *,
    model: str,
    voice: str,
    device: str,
    sample_rate: int,
) -> bytes:
    """Synthesize PCM16 mono audio with local Qwen3-TTS CustomVoice."""
    tts = _load_model(model, device)
    language = os.environ.get("LOCAL_TTS_LANGUAGE", "Chinese")

    wavs, sr = tts.generate_custom_voice(
        text=text,
        language=language,
        speaker=voice,
    )
    if not wavs:
        raise RuntimeError("Qwen3-TTS returned empty audio")

    return _wav_to_pcm16(wavs[0], int(sr), sample_rate)
