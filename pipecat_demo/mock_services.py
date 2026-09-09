"""Offline stand-ins for the three cloud services, used by `bot.py --mock`.

They exist so the LiveKit half of this demo can be verified without a DashScope key:
WebRTC transport, Silero VAD, turn-taking, interruption handling, audio playback and
the timing observer all run for real, while ASR/LLM/TTS are replaced by local fakes.
That is exactly the check that was possible in this sandbox -- see
docs/livekit-pipecat-comparison.md for what is verified and what still needs a key.

Not a fallback path: nothing in the real pipeline imports this module, and `--mock` has
to be asked for explicitly.
"""
import math
import struct
from collections.abc import AsyncGenerator

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.time import time_now_iso8601


class MockSTTService(SegmentedSTTService):
    """Returns a fixed transcript for every detected speech segment.

    A `SegmentedSTTService` gets handed one VAD-delimited segment at a time, so this
    fires exactly once per user turn -- enough to drive the rest of the pipeline from
    real microphone audio without recognizing anything.
    """

    def __init__(self, *, transcript: str = "这是一句模拟转写的测试语音", **kwargs):
        """Initialize with the canned transcript to emit per turn."""
        super().__init__(settings=STTSettings(model="mock", language=None), **kwargs)
        self._transcript = transcript

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        """Emit the canned transcript for one speech segment."""
        logger.debug(f"{self}: mock transcript for {len(audio)} bytes of audio")
        yield TranscriptionFrame(self._transcript, self._user_id, time_now_iso8601(), None)


class MockLLMProcessor(FrameProcessor):
    """Answers every context frame with a canned reply that echoes the question."""

    def __init__(self, *, reply: str = "我听到你说：{text}。这是模拟回复，没有调用真实模型。", **kwargs):
        """Initialize with a reply template containing an optional `{text}` slot."""
        super().__init__(**kwargs)
        self._reply = reply

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Turn each inference request into a short canned response."""
        await super().process_frame(frame, direction)

        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return

        last_user = ""
        for message in reversed(frame.context.get_messages()):
            if isinstance(message, dict) and message.get("role") == "user":
                content = message.get("content")
                last_user = content if isinstance(content, str) else ""
                break

        await self.push_frame(LLMFullResponseStartFrame(), direction)
        await self.push_frame(LLMTextFrame(self._reply.format(text=last_user)), direction)
        await self.push_frame(LLMFullResponseEndFrame(), direction)


class MockTTSService(TTSService):
    """Synthesizes a plain tone, one 20ms chunk at a time, roughly at speech length."""

    def __init__(self, *, sample_rate: int | None = None, frequency: float = 220.0, **kwargs):
        """Initialize with the tone frequency to emit."""
        super().__init__(
            push_start_frame=True,
            push_stop_frames=True,
            sample_rate=sample_rate,
            settings=TTSSettings(model="mock", voice="tone", language=None),
            **kwargs,
        )
        self._frequency = frequency

    def can_generate_metrics(self) -> bool:
        """Report metrics so the timing observer sees the same shape as the real TTS."""
        return True

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        """Emit ~120ms of tone per character, in 20ms chunks."""
        await self.start_tts_usage_metrics(text)
        await self.stop_ttfb_metrics()

        chunk_samples = int(self.sample_rate * 0.02)
        total_chunks = max(len(text) * 6, 10)
        phase = 0.0
        step = 2 * math.pi * self._frequency / self.sample_rate

        for _ in range(total_chunks):
            samples = []
            for _ in range(chunk_samples):
                samples.append(int(12000 * math.sin(phase)))
                phase += step
            audio = struct.pack(f"<{len(samples)}h", *samples)
            yield TTSAudioRawFrame(audio, self.sample_rate, 1, context_id=context_id)
