"""Forward STT/LLM text to the browser via LiveKit data channel.

Two-stage protocol so the frontend can render cleanly:

1. While the LLM streams, forward each ``LLMTextFrame`` delta as
   ``{"type": "transcript", "role": "assistant", "text": "<delta>", "final": False}``
   — same shape as before, no behavior change for mid-stream UI.
2. When ``LLMFullResponseEndFrame`` fires, forward the **cleaned** full text
   as ``{"type": "transcript", "role": "assistant", "text": "<cleaned>",
        "final": True, "replace": True}``. The frontend uses ``replace=True``
   to overwrite the in-progress bubble with the sanitized text (strips Qwen
   special tokens like ``<|im_end|>`` / stray ```` / ``system\n``),
   and uses ``final=True`` to reset its ``assistantBubble`` reference so the
   next user turn lands in a fresh bubble instead of being appended to the
   previous assistant bubble.

Sanitization is applied only to the final message, not each delta — the
deltas are mostly OK (Qwen 2.5 only occasionally leaks special tokens) and
applying regex per delta would slow streaming for no real win.

Why this fixes three user-reported bugs at once:
  - Bug 'format codes': sanitization strips ``<|...|>`` and other Qwen 2.5
    internal tokens that sometimes leak into the very first delta.
  - Bug 'only first turn heard': the previous bridge never sent ``final=True``,
    so the frontend kept pointing ``assistantBubble`` at the first bubble and
    later user messages got appended to it. Now ``final=True`` clears it.
  - Bug 'welcome missing after long gap': unrelated to this file (fixed in
    pipecat_agent.py by switching to ``on_participant_connected``).
"""

from __future__ import annotations

import json
import re

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputTransportMessageFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Qwen 2.5 Instruct special tokens + control strings that occasionally leak
# into the first delta before the model settles into normal Chinese output.
# Examples observed in the wild: ``<|im_end|>``, ``<|im_start|>``,
# ``<|endoftext|>``, ``system\n...``, stray backticks opening a code block.
_SPECIAL_TOKEN_RE = re.compile(r"<\|[^>]*\|>")
_LEAKED_SYSTEM_RE = re.compile(r"^system\s*\n", flags=re.IGNORECASE)
# A leading/trailing `` ` `` from an unclosed code fence the LLM briefly
# started and abandoned — visually ugly, no semantic content. Only strip
# when the backtick count is ODD (i.e. unmatched — a pair of inline ``code``
# has even count and should stay intact).
def _strip_dangle_backticks(text: str) -> str:
    if not text:
        return text
    if text.count("`") % 2 != 1:
        return text
    return text.strip("`").lstrip("\n")
# Collapsed whitespace from the model's chain-of-thought that sometimes
# prints before settling.
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def _sanitize_assistant_text(text: str) -> str:
    if not text:
        return ""
    cleaned = _SPECIAL_TOKEN_RE.sub("", text)
    cleaned = _LEAKED_SYSTEM_RE.sub("", cleaned)
    cleaned = _strip_dangle_backticks(cleaned)
    cleaned = _MULTI_NEWLINE_RE.sub("\n\n", cleaned)
    return cleaned.strip()


class TranscriptBridge(FrameProcessor):
    """Emit user/assistant transcript events to the LiveKit client.

    Mid-stream: forwards each LLM delta unchanged so the frontend can do
    word-by-word streaming. End-of-turn: forwards the **full** cleaned text
    once so the frontend can replace the bubble with the sanitized version.
    """

    def __init__(self) -> None:
        super().__init__()
        self._assistant_buffer: str = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            payload = json.dumps(
                {"type": "transcript", "role": "user", "text": frame.text},
                ensure_ascii=False,
            )
            await self.push_frame(OutputTransportMessageFrame(message=payload), direction)

        elif isinstance(frame, LLMFullResponseStartFrame):
            # Clear any leftover text from a previous turn before accumulating.
            self._assistant_buffer = ""

        elif isinstance(frame, LLMTextFrame):
            # Stream delta to the frontend unchanged — the cleanup is applied
            # once on the end-of-response message below.
            self._assistant_buffer += frame.text
            payload = json.dumps(
                {
                    "type": "transcript",
                    "role": "assistant",
                    "text": frame.text,
                    "final": False,
                },
                ensure_ascii=False,
            )
            await self.push_frame(OutputTransportMessageFrame(message=payload), direction)

        elif isinstance(frame, LLMFullResponseEndFrame):
            cleaned = _sanitize_assistant_text(self._assistant_buffer)
            # Compare cleaned vs raw — if sanitization actually changed the
            # text, ask the frontend to overwrite the in-progress bubble with
            # the cleaned version. Otherwise just signal ``final=True`` so the
            # frontend clears its ``assistantBubble`` pointer for the next turn.
            needs_replace = cleaned != self._assistant_buffer
            payload = json.dumps(
                {
                    "type": "transcript",
                    "role": "assistant",
                    "text": cleaned,
                    "final": True,
                    "replace": needs_replace,
                },
                ensure_ascii=False,
            )
            await self.push_frame(OutputTransportMessageFrame(message=payload), direction)
            self._assistant_buffer = ""

        await self.push_frame(frame, direction)