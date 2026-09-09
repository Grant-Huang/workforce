"""Wiring tests for the conversational half of the pipeline.

The transport and VAD are left out (they need a LiveKit room and real audio); what is
exercised here is everything between "a transcript exists" and "audio comes out":
turn-taking, memory grounding, the LLM step and the TTS step, using the offline
stand-ins from mock_services.
"""
import pytest
from pipecat.frames.frames import (
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from pipecat_demo import bot, config
from pipecat_demo.memory import AgentNexusMemory
from pipecat_demo.mock_services import MockLLMProcessor, MockTTSService
from pipecat_demo.processors import MEMORY_MARKER, MemoryInjector
from pipecat_demo.prompts import BASE_INSTRUCTIONS

DEBOUNCE = 0.3


def build_conversation_pipeline(memory: AgentNexusMemory, context: LLMContext):
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=DEBOUNCE)]
            ),
        ),
    )
    return Pipeline(
        [
            aggregators.user(),
            MemoryInjector(memory=memory),
            MockLLMProcessor(),
            MockTTSService(sample_rate=24000),
            aggregators.assistant(),
        ]
    )


async def test_a_transcript_produces_grounded_reply_audio():
    """One turn end to end: transcript → context → grounded LLM call → synthesized audio."""
    memory = AgentNexusMemory(base_url="http://127.0.0.1:1/unused", channel_id="c", token="t")
    memory.store.add("下午三点跟智枢团队开会")

    context = LLMContext([{"role": "system", "content": BASE_INSTRUCTIONS}])
    pipeline = build_conversation_pipeline(memory, context)

    down, _ = await run_test(
        pipeline,
        frames_to_send=[
            VADUserStartedSpeakingFrame(start_secs=0.2),
            TranscriptionFrame("下午的开会是几点", "user", "now"),
            VADUserStoppedSpeakingFrame(stop_secs=0.9),
            SleepFrame(DEBOUNCE + 0.7),
        ],
        expected_down_frames=None,
    )

    audio = [f for f in down if isinstance(f, TTSAudioRawFrame)]
    assert audio, "the turn should end in synthesized audio"

    roles = [m.get("role") for m in context.get_messages() if isinstance(m, dict)]
    assert "assistant" in roles, "the reply should be written back into the context"

    grounded = [
        m
        for m in context.get_messages()
        if isinstance(m, dict) and m.get("role") == "system" and MEMORY_MARKER in m["content"]
    ]
    assert grounded and "开会" in grounded[0]["content"]


async def test_build_services_mock_mode_needs_no_key():
    stt, llm, tts = bot.build_services(mock=True)
    assert type(stt).__name__ == "MockSTTService"
    assert isinstance(llm, MockLLMProcessor)
    assert isinstance(tts, MockTTSService)


async def test_build_services_without_key_fails_loudly(monkeypatch):
    """A missing key should be an explicit error, not a silent mock fallback."""
    monkeypatch.setattr(config, "QWEN_API_KEY", "")
    with pytest.raises(RuntimeError, match="QWEN_API_KEY"):
        bot.build_services()


async def test_build_services_uses_configured_endpoints(monkeypatch):
    monkeypatch.setattr(config, "QWEN_API_KEY", "test-key")
    monkeypatch.setattr(config, "QWEN_WORKSPACE_ID", "llm-abc")

    stt, llm, tts = bot.build_services()

    expected_ws = "wss://llm-abc.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
    assert stt._url == expected_ws
    assert tts._url == expected_ws
    # The LLM half speaks HTTP to the OpenAI-compatible endpoint on the same workspace.
    assert "llm-abc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1" in str(llm._client.base_url)
