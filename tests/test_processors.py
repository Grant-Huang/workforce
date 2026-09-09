"""Tests for memory injection into the LLM context and the per-turn timing observer."""
import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    LLMContextFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests.utils import run_test

from pipecat_demo.memory import AgentNexusMemory
from pipecat_demo.processors import MEMORY_MARKER, MemoryInjector, TurnTimingObserver
from pipecat_demo.prompts import BASE_INSTRUCTIONS


def make_memory(entries: list[str]) -> AgentNexusMemory:
    memory = AgentNexusMemory(
        base_url="http://127.0.0.1:1/unused", channel_id="demo-channel", token="t"
    )
    for text in entries:
        memory.store.add(text)
    return memory


def system_messages(context: LLMContext) -> list[str]:
    return [
        m["content"]
        for m in context.get_messages()
        if isinstance(m, dict) and m.get("role") == "system"
    ]


async def test_retrieved_memory_is_injected_before_the_question():
    """Grounding is a plain system message here -- no session.update, no ack to wait for."""
    memory = make_memory(["下午三点跟智枢团队开会，晚上七点健身"])
    context = LLMContext(
        [
            {"role": "system", "content": BASE_INSTRUCTIONS},
            {"role": "user", "content": "下午的开会安排是什么"},
        ]
    )

    await run_test(
        MemoryInjector(memory=memory),
        frames_to_send=[LLMContextFrame(context=context)],
        expected_down_frames=None,
    )

    messages = context.get_messages()
    injected = [
        i
        for i, m in enumerate(messages)
        if m.get("role") == "system" and MEMORY_MARKER in m["content"]
    ]
    assert injected, "expected an injected background-info message"

    last_user = max(i for i, m in enumerate(messages) if m.get("role") == "user")
    assert injected[0] == last_user - 1
    assert "开会" in messages[injected[0]]["content"]


async def test_injection_replaces_rather_than_stacks():
    """Each turn's background info replaces the previous turn's, like the other arm's patch."""
    memory = make_memory(["下午三点开会", "晚上七点健身"])
    context = LLMContext(
        [
            {"role": "system", "content": BASE_INSTRUCTIONS},
            {"role": "user", "content": "下午开会几点"},
        ]
    )
    injector = MemoryInjector(memory=memory)

    await run_test(
        injector,
        frames_to_send=[LLMContextFrame(context=context)],
        expected_down_frames=None,
    )
    context.add_message({"role": "user", "content": "晚上健身几点"})
    await run_test(
        injector,
        frames_to_send=[LLMContextFrame(context=context)],
        expected_down_frames=None,
    )

    memory_systems = [m for m in system_messages(context) if MEMORY_MARKER in m]
    assert len(memory_systems) == 1
    assert "健身" in memory_systems[0]


async def test_no_hits_means_no_injected_message():
    """An empty retrieval leaves the prompt alone, so the model can honestly say it doesn't know."""
    memory = make_memory(["下午三点开会"])
    context = LLMContext(
        [
            {"role": "system", "content": BASE_INSTRUCTIONS},
            {"role": "user", "content": "quarterly revenue forecast xyz"},
        ]
    )

    await run_test(
        MemoryInjector(memory=memory),
        frames_to_send=[LLMContextFrame(context=context)],
        expected_down_frames=None,
    )

    assert not [m for m in system_messages(context) if MEMORY_MARKER in m]


async def test_user_turn_is_added_to_memory():
    memory = make_memory([])
    context = LLMContext(
        [
            {"role": "system", "content": BASE_INSTRUCTIONS},
            {"role": "user", "content": "记一下：周四要交预算"},
        ]
    )

    await run_test(
        MemoryInjector(memory=memory),
        frames_to_send=[LLMContextFrame(context=context)],
        expected_down_frames=None,
    )

    assert [e.text for e in memory.store.all()] == ["记一下：周四要交预算"]


class _Dummy(FrameProcessor):
    pass


# The debounce sits in its own segment on purpose: rolled into the LLM segment it would
# look like the model took half a second.
SEGMENTS = (
    "vad_to_transcript_ms",
    "transcript_to_turn_end_ms",
    "turn_end_to_llm_ms",
    "llm_to_tts_audio_ms",
    "tts_audio_to_playback_ms",
)


async def push(observer: TurnTimingObserver, frame) -> None:
    processor = _Dummy()
    await observer.on_push_frame(
        FramePushed(
            source=processor,
            destination=processor,
            frame=frame,
            direction=FrameDirection.DOWNSTREAM,
            timestamp=0,
        )
    )


async def test_turn_timing_breakdown_covers_every_hop():
    """The cascade can attribute a slow turn; the end-to-end arm cannot see these segments."""
    reported: list[dict] = []

    observer = TurnTimingObserver(on_turn=lambda b: _collect(reported, b))

    await push(observer, UserStartedSpeakingFrame())
    await push(observer, VADUserStoppedSpeakingFrame(stop_secs=0.9))
    await asyncio.sleep(0.02)
    await push(observer, TranscriptionFrame("下午开会几点", "user", "now"))
    await asyncio.sleep(0.02)
    await push(observer, UserStoppedSpeakingFrame())
    await asyncio.sleep(0.02)
    await push(observer, LLMTextFrame("下午"))
    await asyncio.sleep(0.02)
    await push(observer, TTSAudioRawFrame(b"\x00\x00", 24000, 1))
    await asyncio.sleep(0.02)
    await push(observer, BotStartedSpeakingFrame())

    assert len(reported) == 1
    breakdown = reported[0]
    for key in SEGMENTS + ("total_ms",):
        assert breakdown[key] is not None, key
        assert breakdown[key] >= 0

    segments = sum(breakdown[key] for key in SEGMENTS)
    # Rounding to whole milliseconds can shift the sum by a few ms either way.
    assert abs(segments - breakdown["total_ms"]) <= 4


async def test_broadcast_copies_do_not_produce_empty_reports():
    """One turn must report once, even though speech frames arrive once per processor.

    `BotStartedSpeakingFrame` is broadcast, so each processor gets a *distinct* object;
    without a guard the copies each fire a report with every segment empty, and in the
    UI those overwrite the real numbers with dashes.
    """
    reported: list[dict] = []
    observer = TurnTimingObserver(on_turn=lambda b: _collect(reported, b))

    await push(observer, UserStartedSpeakingFrame())
    await push(observer, VADUserStoppedSpeakingFrame(stop_secs=0.9))
    await push(observer, TranscriptionFrame("测试", "user", "now"))
    await push(observer, LLMTextFrame("测"))
    await push(observer, TTSAudioRawFrame(b"\x00\x00", 24000, 1))
    for _ in range(5):
        await push(observer, BotStartedSpeakingFrame())

    assert len(reported) == 1
    assert reported[0]["total_ms"] is not None


async def test_repeated_frames_do_not_reset_marks():
    """Observers see a frame once per hop; only the first sighting may count."""
    reported: list[dict] = []
    observer = TurnTimingObserver(on_turn=lambda b: _collect(reported, b))

    stopped = VADUserStoppedSpeakingFrame(stop_secs=0.9)
    await push(observer, UserStartedSpeakingFrame())
    await push(observer, stopped)
    await asyncio.sleep(0.03)
    await push(observer, stopped)  # same frame, next processor hop
    await push(observer, TranscriptionFrame("测试", "user", "now"))
    await push(observer, LLMTextFrame("测"))
    await push(observer, TTSAudioRawFrame(b"\x00\x00", 24000, 1))
    await push(observer, BotStartedSpeakingFrame())

    assert reported[0]["vad_to_transcript_ms"] >= 30


async def _collect(sink: list[dict], breakdown: dict) -> None:
    sink.append(breakdown)
