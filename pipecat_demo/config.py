"""Configuration for the LiveKit + Pipecat pipeline.

Reads the same `.env` at the repo root that `web-demo/server.py` reads, and reuses the
same `QWEN_API_KEY` / `QWEN_WORKSPACE_ID` on purpose: the point of this pipeline is to
be compared against the existing one, so both arms have to run on the same account and
the same region. Everything that is specific to the cascade (which ASR, which TTS
voice, VAD timings) gets its own `PIPECAT_*` variable so tuning one arm never silently
changes the other.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent

load_dotenv(REPO_ROOT / ".env")

QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")
# Same lesson as web-demo/server.py: the workspace-specific domain is what made the
# shared dashscope.aliyuncs.com reliability problems go away (see web-demo/README.md,
# "重大突破", 2026-08-23). Both DashScope endpoints this pipeline uses -- the
# OpenAI-compatible HTTP one for the LLM and the api-ws WebSocket one for ASR/TTS --
# have a workspace-specific form, so the same variable switches all of them at once.
QWEN_WORKSPACE_ID = os.environ.get("QWEN_WORKSPACE_ID", "")

# Text LLM for the cascade. Deliberately configurable and deliberately NOT the same
# kind of model as the other arm: web-demo talks to qwen3.5-omni-flash-realtime
# (speech-in/speech-out), this one needs a plain text chat model. qwen-plus is the
# balanced default; qwen-turbo/qwen-flash trade some quality for latency (the
# dictation-cleanup path in web-demo/server.py measured ~10x faster on qwen-turbo than
# on a heavier model, so it is worth trying here too when comparing turnaround).
LLM_MODEL = os.environ.get("PIPECAT_LLM_MODEL", "qwen-plus")

# Streaming ASR. paraformer-realtime-v2 accepts any sample rate and supports
# max_sentence_silence / semantic_punctuation_enabled, which are the knobs that decide
# how this arm segments speech.
ASR_MODEL = os.environ.get("PIPECAT_ASR_MODEL", "paraformer-realtime-v2")
ASR_LANGUAGE_HINTS = [
    hint.strip()
    for hint in os.environ.get("PIPECAT_ASR_LANGUAGE_HINTS", "zh,en").split(",")
    if hint.strip()
]
# VAD endpointing threshold inside the ASR itself (ms). This is *not* what ends a
# conversational turn here -- Silero VAD in the transport does that locally (see
# VAD_STOP_SECS) -- it only decides when Paraformer calls a sentence finished and sends
# a final transcript. Kept slightly below the local VAD window so the final transcript
# is usually already in hand when the turn closes.
ASR_MAX_SENTENCE_SILENCE_MS = int(os.environ.get("PIPECAT_ASR_MAX_SENTENCE_SILENCE_MS", "800"))

# P99 time from end of speech to a final transcript. pipecat feeds this to the turn
# stop strategy as a safety net for slow ASR. This is an *estimate*, not a measurement
# -- no key was available to profile Paraformer here -- so it is a config knob rather
# than a constant; lower it once you have real numbers from the timing log.
ASR_TTFS_P99_SECS = float(os.environ.get("PIPECAT_ASR_TTFS_P99_SECS", "0.6"))

# How long to wait after the user pauses before answering -- the exact counterpart of
# app.js's RESPONSE_DEBOUNCE_MS (500ms), which exists so a speaker who resumes after a
# short thinking pause isn't answered mid-thought. Same default so the two arms feel
# the same when compared.
RESPONSE_DEBOUNCE_SECS = float(os.environ.get("PIPECAT_RESPONSE_DEBOUNCE_SECS", "0.5"))

# TTS. cosyvoice-v2 voices are version-specific (`longxiaochun_v2`, not
# `longxiaochun`) -- switching the model without switching the voice is a documented
# way to get a rejected request.
TTS_MODEL = os.environ.get("PIPECAT_TTS_MODEL", "cosyvoice-v2")
TTS_VOICE = os.environ.get("PIPECAT_TTS_VOICE", "longxiaochun_v2")
TTS_SAMPLE_RATE = int(os.environ.get("PIPECAT_TTS_SAMPLE_RATE", "24000"))

# Microphone-side sample rate. Paraformer takes any rate, so this is 16k to match what
# web-demo sends upstream and to keep the ASR bill comparable between the two arms.
AUDIO_IN_SAMPLE_RATE = int(os.environ.get("PIPECAT_AUDIO_IN_SAMPLE_RATE", "16000"))

# Local turn-taking. 0.9s mirrors the `silence_duration_ms: 900` the other arm asks
# Qwen's server-side VAD for, so "how long a pause ends my turn" is not an accidental
# difference when comparing perceived speed.
VAD_STOP_SECS = float(os.environ.get("PIPECAT_VAD_STOP_SECS", "0.9"))
VAD_START_SECS = float(os.environ.get("PIPECAT_VAD_START_SECS", "0.2"))
VAD_CONFIDENCE = float(os.environ.get("PIPECAT_VAD_CONFIDENCE", "0.7"))

# LiveKit. The defaults are `livekit-server --dev`'s built-in credentials, so a local
# run needs no configuration at all; point these at LiveKit Cloud (or your own
# deployment) to test over a real network.
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "ws://127.0.0.1:7880")
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "secret")

# This demo's own web server (web-demo/server.py owns 8765).
HOST = os.environ.get("PIPECAT_HOST", "127.0.0.1")
PORT = int(os.environ.get("PIPECAT_PORT", "8766"))

# Memory comes from the AgentNexus mock that web-demo/server.py already serves, so both
# arms retrieve from the same store instead of each keeping its own copy (see
# memory.py). If web-demo isn't running, retrieval degrades to "no background info"
# instead of failing the conversation.
AGENTNEXUS_BASE_URL = os.environ.get(
    "AGENTNEXUS_BASE_URL", "http://127.0.0.1:8765/agentnexus-mock"
)
AGENTNEXUS_CHANNEL_ID = os.environ.get("AGENTNEXUS_CHANNEL_ID", "demo-channel")
AGENTNEXUS_TOKEN = os.environ.get("AGENTNEXUS_TOKEN", "pt_mock_demo_token")

# Idle hangup, matching the other arm's IDLE_TIMEOUT_MS (docs/app-design.md's
# connection lifecycle rule (c)).
IDLE_TIMEOUT_SECS = float(os.environ.get("PIPECAT_IDLE_TIMEOUT_SECS", "300"))


def dashscope_inference_ws_url() -> str:
    """WebSocket endpoint for DashScope's ASR/TTS (`api-ws/v1/inference`).

    Note this is a different path from the Realtime API the other arm uses
    (`api-ws/v1/realtime`): same host, different protocol family. Paraformer is
    Beijing-only, which is why the workspace form is pinned to cn-beijing.
    """
    if QWEN_WORKSPACE_ID:
        return f"wss://{QWEN_WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
    return "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


def compatible_mode_base() -> str:
    """OpenAI-compatible HTTP base URL, same resolution as web-demo/server.py."""
    if QWEN_WORKSPACE_ID:
        return f"https://{QWEN_WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    return "https://dashscope.aliyuncs.com/compatible-mode/v1"


def summary() -> dict:
    """Config snapshot for startup logging and `/api/config` (never includes the key)."""
    return {
        "llm_model": LLM_MODEL,
        "asr_model": ASR_MODEL,
        "tts_model": TTS_MODEL,
        "tts_voice": TTS_VOICE,
        "asr_max_sentence_silence_ms": ASR_MAX_SENTENCE_SILENCE_MS,
        "vad_stop_secs": VAD_STOP_SECS,
        "response_debounce_secs": RESPONSE_DEBOUNCE_SECS,
        "livekit_url": LIVEKIT_URL,
        "inference_ws_url": dashscope_inference_ws_url(),
        "workspace_domain": bool(QWEN_WORKSPACE_ID),
        "has_key": bool(QWEN_API_KEY),
    }
