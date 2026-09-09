"""The Pipecat agent: a cascaded voice pipeline joined to a LiveKit room.

Pipeline shape::

    LiveKit (WebRTC, mic in)
      → VADProcessor            Silero VAD, locally: decides start/stop of speech
      → DashScopeSTTService     Paraformer streaming ASR
      → LLMUserAggregator       turn assembly (stop strategy = VAD + debounce)
      → MemoryInjector          retrieved memory as an ordinary system message
      → QwenLLMService          Qwen text model over the OpenAI-compatible endpoint
      → DashScopeTTSService     CosyVoice streaming synthesis
      → LiveKit (WebRTC, audio out)
      → LLMAssistantAggregator  writes the reply back into the context

Compared with web-demo/, every box above is a separate, swappable service in this
process, and the whole turn happens under one local clock -- which is what makes the
per-turn breakdown in processors.TurnTimingObserver possible.

Run standalone (needs a LiveKit server and a room):

    python -m pipecat_demo.bot --room voicechat

or let token_server.py spawn it per session, which is what the web client does.
"""
import argparse
import asyncio
import json

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import EndFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.services.qwen.llm import QwenLLMService
from pipecat.transports.livekit.transport import (
    LiveKitOutputTransportMessageUrgentFrame,
    LiveKitParams,
    LiveKitTransport,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)

from pipecat_demo import config
from pipecat_demo.memory import AgentNexusMemory
from pipecat_demo.processors import MemoryInjector, TurnTimingObserver
from pipecat_demo.prompts import BASE_INSTRUCTIONS
from pipecat_demo.services.dashscope_stt import DashScopeSTTService
from pipecat_demo.services.dashscope_tts import DashScopeTTSService
from pipecat_demo.livekit_token import generate_bot_token


def build_transport(url: str, token: str, room_name: str) -> LiveKitTransport:
    """Create the LiveKit transport with this demo's audio settings."""
    return LiveKitTransport(
        url=url,
        token=token,
        room_name=room_name,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_in_sample_rate=config.AUDIO_IN_SAMPLE_RATE,
            audio_out_enabled=True,
            audio_out_sample_rate=config.TTS_SAMPLE_RATE,
        ),
    )


def build_services(mock: bool = False):
    """Create the STT / LLM / TTS trio.

    Args:
        mock: Use the offline stand-ins from mock_services instead of DashScope, so
            the transport and turn-taking can be exercised without an API key.

    Returns:
        Tuple of (stt, llm, tts). In mock mode `llm` is a plain processor rather than
        an LLM service, which the pipeline treats identically.
    """
    if mock:
        from pipecat_demo.mock_services import MockLLMProcessor, MockSTTService, MockTTSService

        return (
            MockSTTService(),
            MockLLMProcessor(),
            MockTTSService(sample_rate=config.TTS_SAMPLE_RATE),
        )

    if not config.QWEN_API_KEY:
        raise RuntimeError("QWEN_API_KEY is not set (see .env.example); or run with --mock")

    stt = DashScopeSTTService(
        api_key=config.QWEN_API_KEY,
        url=config.dashscope_inference_ws_url(),
        model=config.ASR_MODEL,
        workspace_id=config.QWEN_WORKSPACE_ID,
        language_hints=config.ASR_LANGUAGE_HINTS,
        max_sentence_silence_ms=config.ASR_MAX_SENTENCE_SILENCE_MS,
        sample_rate=config.AUDIO_IN_SAMPLE_RATE,
        ttfs_p99_latency=config.ASR_TTFS_P99_SECS,
    )

    # QwenLLMService defaults to the international DashScope host; this account is on
    # the mainland/workspace endpoint, resolved the same way web-demo/server.py does.
    llm = QwenLLMService(
        api_key=config.QWEN_API_KEY,
        base_url=config.compatible_mode_base(),
        settings=QwenLLMService.Settings(model=config.LLM_MODEL),
    )

    tts = DashScopeTTSService(
        api_key=config.QWEN_API_KEY,
        url=config.dashscope_inference_ws_url(),
        model=config.TTS_MODEL,
        voice=config.TTS_VOICE,
        workspace_id=config.QWEN_WORKSPACE_ID,
        sample_rate=config.TTS_SAMPLE_RATE,
    )

    return stt, llm, tts


async def run_bot(
    *,
    room_name: str,
    url: str | None = None,
    token: str | None = None,
    mock: bool = False,
) -> None:
    """Join a LiveKit room and run the pipeline until the participant leaves."""
    url = url or config.LIVEKIT_URL
    token = token or generate_bot_token(room_name)

    transport = build_transport(url, token, room_name)
    stt, llm, tts = build_services(mock=mock)

    memory = AgentNexusMemory(
        base_url=config.AGENTNEXUS_BASE_URL,
        channel_id=config.AGENTNEXUS_CHANNEL_ID,
        token=config.AGENTNEXUS_TOKEN,
    )

    context = LLMContext([{"role": "system", "content": BASE_INSTRUCTIONS}])
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                # Plain silence-based endpointing, on purpose: the point is to compare
                # against web-demo's `silence_duration_ms` + response debounce, not to
                # win with pipecat's default smart-turn model. `user_speech_timeout` is
                # the direct counterpart of app.js's RESPONSE_DEBOUNCE_MS.
                stop=[
                    SpeechTimeoutUserTurnStopStrategy(
                        user_speech_timeout=config.RESPONSE_DEBOUNCE_SECS
                    )
                ],
            ),
        ),
    )

    task_holder: dict = {}

    async def publish(payload: dict) -> None:
        """Send a JSON event to the browser over LiveKit's data channel."""
        task = task_holder.get("task")
        if not task:
            return
        await task.queue_frame(LiveKitOutputTransportMessageUrgentFrame(message=payload))

    async def on_retrieval(query: str, hits: list[str]) -> None:
        await publish({"type": "memory", "query": query, "hits": hits})

    memory_injector = MemoryInjector(memory=memory, on_retrieval=on_retrieval)

    async def on_turn(breakdown: dict) -> None:
        await publish({"type": "timing", **breakdown})

    pipeline = Pipeline(
        [
            transport.input(),
            VADProcessor(
                vad_analyzer=SileroVADAnalyzer(
                    params=VADParams(
                        confidence=config.VAD_CONFIDENCE,
                        start_secs=config.VAD_START_SECS,
                        stop_secs=config.VAD_STOP_SECS,
                    )
                )
            ),
            stt,
            aggregators.user(),
            memory_injector,
            llm,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[TurnTimingObserver(on_turn=on_turn)],
        idle_timeout_secs=config.IDLE_TIMEOUT_SECS,
    )
    task_holder["task"] = task

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(_transport, participant_id):
        logger.info(f"Participant joined: {participant_id}")
        pulled = await memory.pull()
        logger.info(f"Pulled {pulled} memory entries from AgentNexus")
        await publish({"type": "ready", "config": config.summary(), "memory_entries": pulled})

    @transport.event_handler("on_participant_disconnected")
    async def on_participant_disconnected(_transport, participant_id):
        logger.info(f"Participant left: {participant_id}, ending session")
        await task.queue_frame(EndFrame())

    @aggregators.user().event_handler("on_user_turn_message_added")
    async def on_user_message(_aggregator, message):
        await publish({"type": "user", "text": message.content})
        asyncio.create_task(memory.push_message(message.content, "user"))

    @aggregators.assistant().event_handler("on_assistant_turn_stopped")
    async def on_assistant_message(_aggregator, message):
        if not message.content:
            return
        await publish(
            {"type": "assistant", "text": message.content, "interrupted": message.interrupted}
        )
        memory.store.add(message.content)
        asyncio.create_task(memory.push_message(message.content, "assistant"))

    logger.info(f"Bot config: {json.dumps(config.summary(), ensure_ascii=False)}")
    runner = PipelineRunner(handle_sigint=False)
    try:
        await runner.run(task)
    finally:
        await memory.close()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="LiveKit + Pipecat cascaded voice bot")
    parser.add_argument("-r", "--room", default="voicechat", help="LiveKit room to join")
    parser.add_argument("-u", "--url", default=None, help="LiveKit server URL")
    parser.add_argument("-t", "--token", default=None, help="Pre-minted bot token")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run with offline stand-ins for ASR/LLM/TTS (no DashScope key needed)",
    )
    args = parser.parse_args()

    asyncio.run(run_bot(room_name=args.room, url=args.url, token=args.token, mock=args.mock))


if __name__ == "__main__":
    main()
