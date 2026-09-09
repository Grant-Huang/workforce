"""Streaming text-to-speech over DashScope's `api-ws/v1/inference` WebSocket API.

Protocol (CosyVoice streaming synthesis):

1. Connect with ``Authorization: Bearer <key>``.
2. Send ``run-task`` (voice, format, sample rate; ``input`` must be empty) and wait for
   ``task-started``.
3. Send one or more ``continue-task`` events carrying text. The server splits on
   sentence boundaries, synthesizes complete sentences immediately and buffers
   incomplete ones. Audio comes back as binary frames.
4. Send ``finish-task`` to force-synthesize whatever is still buffered, then read until
   ``task-finished``.

That maps onto pipecat's turn model cleanly: one DashScope task per bot turn.
``run_tts`` is called once per aggregated sentence and sends a ``continue-task``;
``flush_audio`` (which pipecat calls when the turn's audio context completes) sends
``finish-task``. Keeping the task open across the sentences of one turn avoids a
run-task round trip per sentence.

Interruptions are handled by the base class: ``InterruptibleTTSService`` reconnects the
socket when the user barges in while the bot is speaking, which also abandons the
server-side task -- no need to drain audio for a turn nobody is listening to any more.
"""
import asyncio
import json
import uuid
from collections.abc import AsyncGenerator

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import InterruptibleTTSService
from websockets.protocol import State


class DashScopeTTSService(InterruptibleTTSService):
    """CosyVoice streaming synthesis as a Pipecat websocket TTS service."""

    def __init__(
        self,
        *,
        api_key: str,
        url: str,
        model: str = "cosyvoice-v2",
        voice: str = "longxiaochun_v2",
        workspace_id: str = "",
        sample_rate: int | None = None,
        rate: float = 1.0,
        pitch: float = 1.0,
        volume: int = 50,
        **kwargs,
    ):
        """Initialize the service.

        Args:
            api_key: DashScope API key.
            url: `api-ws/v1/inference` WebSocket URL (shared or workspace-specific).
            model: CosyVoice model name.
            voice: Voice id. Version-specific -- a `cosyvoice-v2` model needs a `_v2`
                voice; mixing versions is rejected by the service.
            workspace_id: Sent as `X-DashScope-WorkSpace` when set.
            sample_rate: Output sample rate; taken from the pipeline when omitted.
            rate: Speech rate, 0.5-2.0.
            pitch: Pitch, 0.5-2.0.
            volume: Volume, 0-100.
            **kwargs: Passed through to `InterruptibleTTSService`.
        """
        super().__init__(
            # The base class opens the audio context and pushes TTSStartedFrame /
            # TTSStoppedFrame around each turn, so run_tts only has to send text.
            push_start_frame=True,
            push_stop_frames=True,
            sample_rate=sample_rate,
            settings=TTSSettings(model=model, voice=voice, language=None),
            **kwargs,
        )

        self._api_key = api_key
        self._url = url
        self._workspace_id = workspace_id
        self._rate = rate
        self._pitch = pitch
        self._volume = volume

        self._websocket = None
        self._receive_task = None
        self._task_id: str | None = None
        # A task is "active" from the moment run-task is sent until task-finished /
        # task-failed comes back. `_task_started_event` is the narrower signal that the
        # server acked run-task, which is when continue-task becomes legal.
        self._task_active = False
        self._task_started_event = asyncio.Event()
        self._context_id: str | None = None

    def can_generate_metrics(self) -> bool:
        """Report metrics (TTFB from text sent to first audio byte)."""
        return True

    async def setup(self, setup: FrameProcessorSetup):
        """Connect at pipeline setup so the first turn doesn't pay for a handshake."""
        await super().setup(setup)
        await self._connect()

    async def stop(self, frame: EndFrame):
        """Close the connection on a graceful pipeline end."""
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        """Close the connection immediately on cancellation."""
        await super().cancel(frame)
        await self._disconnect()

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        """Send one sentence for synthesis.

        Audio is not yielded here: it arrives on the receive task and is appended to the
        turn's audio context, which is what lets a later sentence's audio queue up
        behind an earlier one without blocking this call.
        """
        logger.debug(f"{self}: generating TTS [{text}]")
        try:
            if not self._websocket or self._websocket.state is not State.OPEN:
                await self._connect()

            self._context_id = context_id
            await self._ensure_task_ready()
            await self._send(self._continue_task_message(text))
            await self.start_tts_usage_metrics(text)
        except Exception as e:
            logger.error(f"{self}: TTS error: {e}")
            yield ErrorFrame(error=f"DashScope TTS error: {e}")
            await self._disconnect()
            await self._connect()

        yield None

    async def on_turn_context_created(self, context_id: str):
        """Open the synthesis task as soon as the LLM starts responding.

        run-task has to be acked before any text may be sent, and this hook fires on
        `LLMFullResponseStartFrame` -- before the first sentence has even been
        aggregated -- so that round trip overlaps with the LLM generating its first
        sentence instead of adding to the time before the user hears anything.
        """
        self._context_id = context_id
        try:
            if not self._websocket or self._websocket.state is not State.OPEN:
                await self._connect()
            await self._start_task()
        except Exception as e:
            logger.warning(f"{self}: could not pre-open TTS task: {e}")

    async def flush_audio(self, context_id: str | None = None):
        """End the current turn's task so the server flushes buffered text.

        Called by the base class when the turn's audio context completes. Without this
        the last, possibly incomplete sentence would sit in the server's buffer and
        never be spoken.
        """
        if not self._task_active:
            return
        logger.trace(f"{self}: finishing TTS task {self._task_id}")
        await self._send(self._finish_task_message())

    def _run_task_message(self) -> dict:
        return {
            "header": {
                "action": "run-task",
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "model": self._settings.model,
                "parameters": {
                    "text_type": "PlainText",
                    "voice": self._settings.voice,
                    "format": "pcm",
                    "sample_rate": self.sample_rate,
                    "volume": self._volume,
                    "rate": self._rate,
                    "pitch": self._pitch,
                },
                # Must be empty on run-task; text only travels on continue-task.
                "input": {},
            },
        }

    def _continue_task_message(self, text: str) -> dict:
        return {
            "header": {
                "action": "continue-task",
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": {"input": {"text": text}},
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

    async def _start_task(self):
        """Send run-task for a new turn, if one isn't already open."""
        if self._task_active:
            return
        self._task_id = str(uuid.uuid4())
        self._task_started_event.clear()
        await self._send(self._run_task_message())
        self._task_active = True

    async def _ensure_task_ready(self):
        """Make sure run-task has been acked before sending text.

        Usually already true thanks to `on_turn_context_created`. The timeout is a
        backstop: continuing without the ack risks a rejected continue-task, but
        hanging here would freeze the turn, and a failed send reconnects anyway.
        """
        await self._start_task()
        if self._task_started_event.is_set():
            return
        try:
            await asyncio.wait_for(self._task_started_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(f"{self}: no task-started ack after 5s, sending text anyway")

    async def _connect(self):
        await self._connect_websocket()

        if self._websocket and not self._receive_task:
            self._receive_task = self.create_task(self._receive_task_handler(self._report_error))

    async def _disconnect(self):
        if self._receive_task:
            await self.cancel_task(self._receive_task)
            self._receive_task = None

        await self._disconnect_websocket()

    async def _connect_websocket(self):
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                return

            logger.debug(f"{self}: connecting to DashScope TTS ({self._settings.model})")
            headers = {"Authorization": f"Bearer {self._api_key}"}
            if self._workspace_id:
                headers["X-DashScope-WorkSpace"] = self._workspace_id

            self._websocket = await self._websocket_connect(self._url, additional_headers=headers)
            self._task_active = False
            self._task_started_event.clear()
            self._task_id = None
        except Exception as e:
            self._websocket = None
            await self._call_event_handler("on_connection_error", f"{e}")
            await self.push_error(error_msg=f"DashScope TTS connect failed: {e}", exception=e)

    async def _disconnect_websocket(self):
        try:
            await self.stop_all_metrics()
            if self._websocket and self._websocket.state is State.OPEN:
                logger.debug(f"{self}: disconnecting from DashScope TTS")
                await self._websocket.close()
        except Exception as e:
            logger.warning(f"{self}: error closing DashScope TTS connection: {e}")
        finally:
            self._websocket = None
            self._task_active = False
            self._task_started_event.clear()
            self._task_id = None

    async def _send(self, message: dict):
        if not self._websocket or self._websocket.state is not State.OPEN:
            raise RuntimeError("DashScope TTS websocket is not connected")
        await self._websocket.send(json.dumps(message, ensure_ascii=False))

    async def _receive_messages(self):
        async for message in self._websocket:
            if isinstance(message, bytes):
                await self._handle_audio(message)
                continue
            try:
                await self._handle_event(json.loads(message))
            except Exception as e:
                await self.push_error(error_msg=f"DashScope TTS message error: {e}", exception=e)

    async def _handle_audio(self, audio: bytes):
        if not audio:
            return
        await self.stop_ttfb_metrics()
        context_id = self.get_active_audio_context_id() or self._context_id
        frame = TTSAudioRawFrame(audio, self.sample_rate, 1, context_id=context_id)
        await self.append_to_audio_context(context_id, frame)

    async def _handle_event(self, event: dict):
        header = event.get("header") or {}
        name = header.get("event")

        if name == "task-started":
            logger.trace(f"{self}: TTS task started ({self._task_id})")
            self._task_started_event.set()
        elif name == "result-generated":
            # Sentence-level bookkeeping only; the audio itself is in the binary frames.
            pass
        elif name == "task-finished":
            logger.trace(f"{self}: TTS task finished ({self._task_id})")
            await self._close_turn()
        elif name == "task-failed":
            error = header.get("error_message") or header.get("error_code") or "unknown"
            logger.error(f"{self}: TTS task failed: {error}")
            await self.push_error(error_msg=f"DashScope TTS task failed: {error}")
            await self._close_turn()

    async def _close_turn(self):
        """Close the audio context for the turn that just finished synthesizing.

        Websocket TTS services own their context lifetime (the base class only flushes),
        so the end of the provider's task is what closes it -- and closing it is what
        makes the base class emit TTSStoppedFrame.
        """
        self._task_active = False
        self._task_started_event.clear()
        self._task_id = None
        if self._context_id:
            await self.remove_audio_context(self._context_id)
            self._context_id = None
