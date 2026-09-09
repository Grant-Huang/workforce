"""Streaming speech-to-text over DashScope's `api-ws/v1/inference` WebSocket API.

Protocol (Paraformer realtime ASR):

1. Connect with ``Authorization: Bearer <key>``.
2. Send a ``run-task`` JSON text frame; wait for the ``task-started`` event.
3. Stream raw mono PCM as binary frames; receive ``result-generated`` events, each
   carrying ``payload.output.sentence`` with the text so far and a ``sentence_end``
   flag that marks the final result for that sentence.
4. Send ``finish-task`` when done and read until ``task-finished``.

The whole conversation lives in one task on one connection: Paraformer keeps
segmenting sentence after sentence, so nothing needs to be restarted per turn.

Note on turn-taking: ``max_sentence_silence`` only controls when Paraformer decides a
*sentence* ended. What ends a conversational *turn* in this pipeline is the local
Silero VAD in the transport, which is the substantive difference from web-demo/, where
Qwen's server-side VAD owns both.
"""
import json
import uuid
from collections.abc import AsyncGenerator

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import WebsocketSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601
from websockets.protocol import State

# Enough room for the audio that arrives between connecting and `task-started`
# (normally a single round trip). Past this we drop the oldest audio rather than grow
# without bound -- a stuck handshake must not turn into a memory leak.
MAX_PENDING_AUDIO_BYTES = 16000 * 2 * 5  # ~5s of 16kHz PCM16

_LANGUAGE_HINTS = {
    Language.ZH: "zh",
    Language.EN: "en",
    Language.JA: "ja",
    Language.KO: "ko",
    Language.YUE: "yue",
    Language.DE: "de",
    Language.FR: "fr",
    Language.RU: "ru",
}


class DashScopeSTTService(WebsocketSTTService):
    """Paraformer realtime ASR as a Pipecat streaming STT service."""

    def __init__(
        self,
        *,
        api_key: str,
        url: str,
        model: str = "paraformer-realtime-v2",
        workspace_id: str = "",
        language_hints: list[str] | None = None,
        max_sentence_silence_ms: int = 800,
        semantic_punctuation_enabled: bool = False,
        disfluency_removal_enabled: bool = False,
        vocabulary_id: str | None = None,
        sample_rate: int | None = None,
        **kwargs,
    ):
        """Initialize the service.

        Args:
            api_key: DashScope API key.
            url: `api-ws/v1/inference` WebSocket URL (shared or workspace-specific).
            model: Paraformer model name.
            workspace_id: Sent as `X-DashScope-WorkSpace` when set.
            language_hints: Language codes to bias recognition (v2 models only).
            max_sentence_silence_ms: Silence that ends a sentence, 200-6000ms.
            semantic_punctuation_enabled: Semantic segmentation instead of VAD
                segmentation. More accurate, higher latency -- off for conversation.
            disfluency_removal_enabled: Ask the ASR to drop filler words.
            vocabulary_id: Hotword list id. Note this is the feature that could *not*
                be made to work on the Realtime API in the other arm (web-demo's
                docs/app-design.md 7.1): it is accepted here, which is one of the
                things worth comparing.
            sample_rate: Input sample rate; taken from the pipeline when omitted.
            **kwargs: Passed through to `WebsocketSTTService`.
        """
        super().__init__(
            sample_rate=sample_rate,
            settings=STTSettings(model=model, language=None),
            # DashScope closes an inference task that goes 23s without input, so idle
            # silence has to be sent to hold the connection between turns.
            keepalive_timeout=15.0,
            keepalive_interval=5.0,
            **kwargs,
        )

        self._api_key = api_key
        self._url = url
        self._workspace_id = workspace_id
        self._language_hints = language_hints or []
        self._max_sentence_silence_ms = max_sentence_silence_ms
        self._semantic_punctuation_enabled = semantic_punctuation_enabled
        self._disfluency_removal_enabled = disfluency_removal_enabled
        self._vocabulary_id = vocabulary_id

        self._websocket = None
        self._receive_task = None
        self._task_id: str | None = None
        self._task_started = False
        self._pending_audio = bytearray()

    def can_generate_metrics(self) -> bool:
        """Report metrics (TTFB from end of speech to final transcript)."""
        return True

    def language_to_service_language(self, language: Language) -> str | None:
        """Map a pipecat language to a Paraformer `language_hints` code."""
        return _LANGUAGE_HINTS.get(language)

    async def setup(self, setup: FrameProcessorSetup):
        """Connect as soon as the pipeline is set up, before any audio arrives."""
        await super().setup(setup)
        await self._connect()

    async def stop(self, frame: EndFrame):
        """Close the task on a graceful pipeline end."""
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        """Close the task immediately on cancellation."""
        await super().cancel(frame)
        await self._disconnect()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        """Forward one chunk of PCM audio.

        Transcripts do not come back here -- they arrive asynchronously on the receive
        task and are pushed from there, which is the normal shape for a streaming STT
        service in pipecat.
        """
        if not self._task_started:
            # Still waiting for `task-started`: hold the audio so the beginning of an
            # utterance isn't lost if the user speaks immediately.
            self._pending_audio.extend(audio)
            if len(self._pending_audio) > MAX_PENDING_AUDIO_BYTES:
                del self._pending_audio[: len(self._pending_audio) - MAX_PENDING_AUDIO_BYTES]
            yield None
            return

        await self._send_audio(audio)
        yield None

    def _run_task_message(self) -> dict:
        parameters: dict = {
            "format": "pcm",
            "sample_rate": self.sample_rate,
            "semantic_punctuation_enabled": self._semantic_punctuation_enabled,
            "disfluency_removal_enabled": self._disfluency_removal_enabled,
        }
        if not self._semantic_punctuation_enabled:
            # Only honoured when semantic segmentation is off, per the API docs.
            parameters["max_sentence_silence"] = self._max_sentence_silence_ms
        if self._language_hints:
            parameters["language_hints"] = self._language_hints
        if self._vocabulary_id:
            parameters["vocabulary_id"] = self._vocabulary_id

        return {
            "header": {
                "action": "run-task",
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self._settings.model,
                "parameters": parameters,
                "input": {},
            },
        }

    def _finish_task_message(self) -> dict:
        return {
            "header": {
                "action": "finish-task",
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": {"input": {}},
        }

    async def _connect(self):
        await self._connect_websocket()
        await super()._connect()

        if self._websocket and not self._receive_task:
            self._receive_task = self.create_task(self._receive_task_handler(self._report_error))

    async def _disconnect(self):
        await super()._disconnect()

        if self._receive_task:
            await self.cancel_task(self._receive_task)
            self._receive_task = None

        await self._disconnect_websocket()

    async def _connect_websocket(self):
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                return

            logger.debug(f"{self}: connecting to DashScope ASR ({self._settings.model})")
            headers = {"Authorization": f"Bearer {self._api_key}"}
            if self._workspace_id:
                headers["X-DashScope-WorkSpace"] = self._workspace_id

            self._websocket = await self._websocket_connect(self._url, additional_headers=headers)
            self._task_id = str(uuid.uuid4())
            self._task_started = False
            self._pending_audio.clear()
            await self._websocket.send(json.dumps(self._run_task_message()))
            await self._call_event_handler("on_connected")
        except Exception as e:
            self._websocket = None
            await self._call_event_handler("on_connection_error", f"{e}")
            await self.push_error(error_msg=f"DashScope ASR connect failed: {e}", exception=e)

    async def _disconnect_websocket(self):
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                if self._task_started:
                    await self._websocket.send(json.dumps(self._finish_task_message()))
                await self._websocket.close()
        except Exception as e:
            logger.warning(f"{self}: error closing DashScope ASR connection: {e}")
        finally:
            self._websocket = None
            self._task_started = False
            self._task_id = None
            await self._call_event_handler("on_disconnected")

    async def _send_audio(self, audio: bytes):
        if not self._websocket or self._websocket.state is not State.OPEN:
            return
        try:
            await self._websocket.send(audio)
        except Exception as e:
            logger.warning(f"{self}: failed to send audio, requesting reconnect: {e}")
            self._task_started = False
            await self._request_reconnect()

    def _is_keepalive_ready(self) -> bool:
        return bool(self._task_started and self._websocket and self._websocket.state is State.OPEN)

    async def _send_keepalive(self, silence: bytes):
        """Hold the task open with silence; the protocol has no idle ping of its own."""
        await self._send_audio(silence)

    async def _receive_messages(self):
        async for message in self._websocket:
            if isinstance(message, bytes):
                # The ASR direction never sends binary frames.
                continue
            try:
                await self._handle_event(json.loads(message))
            except Exception as e:
                await self.push_error(error_msg=f"DashScope ASR message error: {e}", exception=e)

    async def _handle_event(self, event: dict):
        header = event.get("header") or {}
        name = header.get("event")

        if name == "task-started":
            self._task_started = True
            logger.debug(f"{self}: ASR task started ({self._task_id})")
            if self._pending_audio:
                buffered = bytes(self._pending_audio)
                self._pending_audio.clear()
                await self._send_audio(buffered)
        elif name == "result-generated":
            await self._handle_result(event)
        elif name == "task-finished":
            logger.debug(f"{self}: ASR task finished ({self._task_id})")
            self._task_started = False
        elif name == "task-failed":
            error = header.get("error_message") or header.get("error_code") or "unknown"
            logger.error(f"{self}: ASR task failed: {error}")
            self._task_started = False
            await self.push_error(error_msg=f"DashScope ASR task failed: {error}")
            await self._request_reconnect()

    async def _handle_result(self, event: dict):
        sentence = ((event.get("payload") or {}).get("output") or {}).get("sentence") or {}
        if sentence.get("heartbeat"):
            # Keepalive echo, not speech.
            return

        text = (sentence.get("text") or "").strip()
        if not text:
            return

        language = self._settings.language if isinstance(self._settings.language, str) else None
        if sentence.get("sentence_end"):
            await self.emit_stt_usage_metrics()
            await self.push_frame(
                TranscriptionFrame(
                    text,
                    self._user_id,
                    time_now_iso8601(),
                    language,
                    result=event,
                )
            )
        else:
            await self.push_frame(
                InterimTranscriptionFrame(
                    text,
                    self._user_id,
                    time_now_iso8601(),
                    language,
                    result=event,
                )
            )
