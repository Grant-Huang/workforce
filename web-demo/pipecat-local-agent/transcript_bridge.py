"""Forward STT/LLM text to the browser via LiveKit data channel."""

from __future__ import annotations

import json

from pipecat.frames.frames import (
    Frame,
    LLMTextFrame,
    OutputTransportMessageFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class TranscriptBridge(FrameProcessor):
    """Emit user/assistant transcript events to the LiveKit client."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            payload = json.dumps(
                {"type": "transcript", "role": "user", "text": frame.text},
                ensure_ascii=False,
            )
            await self.push_frame(OutputTransportMessageFrame(message=payload), direction)
        elif isinstance(frame, LLMTextFrame):
            payload = json.dumps(
                {"type": "transcript", "role": "assistant", "text": frame.text, "final": False},
                ensure_ascii=False,
            )
            await self.push_frame(OutputTransportMessageFrame(message=payload), direction)

        await self.push_frame(frame, direction)
