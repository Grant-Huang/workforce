"""Pipeline pieces specific to this demo: memory injection and turn timing.

Both exist to make the comparison against web-demo/ meaningful rather than to add
features. `MemoryInjector` grounds each turn from the same memory backend the other
arm uses; `TurnTimingObserver` produces the same kind of per-turn breakdown app.js
logs as `[turn timing]`, with the segments that actually exist in a cascade.
"""
import time
from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from pipecat_demo.memory import AgentNexusMemory
from pipecat_demo.prompts import memory_context_message

# Identifies the system message this processor owns, so each turn replaces the previous
# turn's background info instead of stacking another copy into the context.
MEMORY_MARKER = "以下是用户过去说过、可能相关的内容"


class MemoryInjector(FrameProcessor):
    """Grounds each user turn with retrieved memory before the LLM sees the context.

    This is the piece that looks trivial here and was hard in the other arm. web-demo
    has to patch `session.update.instructions` on a live Realtime WebSocket and wait for
    the `session.updated` ack before it may ask for a response -- Qwen ignores memory
    delivered as a conversation item, and firing two updates without waiting produced
    empty replies (web-demo/README.md, "一个关键的实测发现"). A cascade has an ordinary
    message list, so grounding is a local edit to that list with no round trip, no ack
    to wait for, and no failure mode where a turn answers on stale instructions.
    """

    def __init__(
        self,
        *,
        memory: AgentNexusMemory,
        limit: int = 5,
        on_retrieval: Callable[[str, list[str]], Awaitable[None]] | None = None,
        **kwargs,
    ):
        """Initialize the injector.

        Args:
            memory: Memory backend to search and to record turns into.
            limit: Maximum number of entries to inject.
            on_retrieval: Optional async callback with (query, hits), used to surface
                what was retrieved in the demo UI.
            **kwargs: Passed to `FrameProcessor`.
        """
        super().__init__(**kwargs)
        self._memory = memory
        self._limit = limit
        self._on_retrieval = on_retrieval

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Rewrite the context's background-info message on every inference request."""
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame):
            try:
                await self._ground(frame.context)
            except Exception as e:
                # Grounding is an enhancement; never let it break the turn.
                logger.warning(f"{self}: memory grounding failed: {e}")

        await self.push_frame(frame, direction)

    async def _ground(self, context: LLMContext):
        query = _last_user_text(context)
        if not query:
            return

        hits = self._memory.search(query, self._limit)
        messages = [m for m in context.get_messages() if not _is_memory_message(m)]

        if hits:
            injected = {"role": "system", "content": memory_context_message(hits)}
            # Placed directly before the question it grounds rather than at the top of
            # the context: with the base prompt at index 0 and history in between, the
            # retrieved facts are what the model should be looking at last.
            insert_at = _last_user_index(messages)
            messages.insert(insert_at, injected)

        context.set_messages(messages)

        # The user's own words become memory too, same as the other arm's
        # LocalMemory.add(userText) on every turn.
        self._memory.store.add(query)
        if self._on_retrieval:
            await self._on_retrieval(query, hits)


def _is_memory_message(message) -> bool:
    return (
        isinstance(message, dict)
        and message.get("role") == "system"
        and isinstance(message.get("content"), str)
        and message["content"].startswith(MEMORY_MARKER)
    )


def _message_text(message) -> str:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _last_user_text(context: LLMContext) -> str:
    for message in reversed(context.get_messages()):
        if isinstance(message, dict) and message.get("role") == "user":
            return _message_text(message).strip()
    return ""


def _last_user_index(messages: list) -> int:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, dict) and message.get("role") == "user":
            return index
    return len(messages)


class TurnTimingObserver(BaseObserver):
    """Per-turn latency breakdown for the cascade.

    app.js logs `[turn timing]` for the other arm, but its first segment is marked
    "(服务端,不可测)": with an end-to-end speech model, everything between "the user
    went quiet" and "a transcript arrived" happens inside Qwen. In a cascade every hop
    is local to this process, so the same wall clock covers all of it:

    * 停止说话 → 最终转写: local Silero VAD closing the turn, then Paraformer's final
      sentence result.
    * 转写 → LLM 首字: the chat-completions call's time to first token.
    * LLM 首字 → TTS 首个音频: CosyVoice's time to first byte.
    * TTS 首个音频 → 开始播放: transport/WebRTC queueing before the user hears it.

    Being able to attribute a slow turn to one of those four is the main practical
    reason to run this arm at all, so this observer is part of the demo, not debug
    scaffolding.
    """

    def __init__(
        self,
        *,
        on_turn: Callable[[dict], Awaitable[None]] | None = None,
        **kwargs,
    ):
        """Initialize the observer.

        Args:
            on_turn: Optional async callback receiving the finished turn's breakdown.
            **kwargs: Passed to `BaseObserver`.
        """
        super().__init__(**kwargs)
        self._on_turn = on_turn
        self._marks: dict[str, float] = {}
        self._seen: set[int] = set()

    async def on_push_frame(self, data: FramePushed):
        """Record the first time each interesting frame appears in a turn."""
        frame = data.frame

        # Observers see a frame once per processor hop; only the first sighting is the
        # timestamp we want. Keyed on pipecat's own frame id rather than id(frame),
        # which CPython reuses as soon as a frame is collected.
        if frame.id in self._seen:
            return

        if isinstance(frame, UserStartedSpeakingFrame):
            self._marks.clear()
            self._seen.clear()
            self._mark(frame, "speech_started")
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._mark(frame, "speech_stopped")
        elif isinstance(frame, TranscriptionFrame):
            self._mark(frame, "transcript")
        elif isinstance(frame, LLMTextFrame):
            self._mark(frame, "llm_first_token")
        elif isinstance(frame, TTSAudioRawFrame):
            self._mark(frame, "tts_first_audio")
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._mark(frame, "bot_started_speaking")
            await self._report()
        elif isinstance(frame, InterruptionFrame):
            self._marks.clear()
            self._seen.clear()

    def _mark(self, frame: Frame, label: str):
        self._seen.add(frame.id)
        self._marks.setdefault(label, time.monotonic())

    def _segment(self, start: str, end: str) -> float | None:
        if start in self._marks and end in self._marks:
            return round((self._marks[end] - self._marks[start]) * 1000)
        return None

    async def _report(self):
        breakdown = {
            "vad_to_transcript_ms": self._segment("speech_stopped", "transcript"),
            "transcript_to_llm_ms": self._segment("transcript", "llm_first_token"),
            "llm_to_tts_audio_ms": self._segment("llm_first_token", "tts_first_audio"),
            "tts_audio_to_playback_ms": self._segment("tts_first_audio", "bot_started_speaking"),
            "total_ms": self._segment("speech_stopped", "bot_started_speaking"),
        }
        logger.info(
            "[turn timing] 停止说话→转写: {vad}ms | 转写→LLM首字: {llm}ms | "
            "LLM首字→TTS首音: {tts}ms | TTS首音→开始播放: {play}ms | 总计: {total}ms".format(
                vad=breakdown["vad_to_transcript_ms"],
                llm=breakdown["transcript_to_llm_ms"],
                tts=breakdown["llm_to_tts_audio_ms"],
                play=breakdown["tts_audio_to_playback_ms"],
                total=breakdown["total_ms"],
            )
        )
        if self._on_turn:
            await self._on_turn(breakdown)
        self._marks.clear()
        self._seen.clear()
