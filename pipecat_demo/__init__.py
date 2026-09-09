"""LiveKit + Pipecat cascaded voice pipeline — the comparison arm to web-demo/.

web-demo/ talks to Qwen-Omni-Realtime end-to-end (speech in, speech out, one model,
one WebSocket). This package runs the same conversation as a classic cascade —
LiveKit WebRTC transport, local Silero VAD, DashScope Paraformer ASR, Qwen text LLM,
CosyVoice TTS — so the two architectures can be compared on the same account, the
same prompt and the same memory backend.

See README.md in this directory for how to run it, and
docs/livekit-pipecat-comparison.md for the architecture comparison itself.
"""
