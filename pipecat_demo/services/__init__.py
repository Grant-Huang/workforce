"""Pipecat services for DashScope's streaming ASR and TTS.

Pipecat 1.8.1 ships a `QwenLLMService` (the OpenAI-compatible DashScope endpoint) but
no DashScope speech services, so the two halves of the cascade that talk to
`api-ws/v1/inference` live here.
"""
