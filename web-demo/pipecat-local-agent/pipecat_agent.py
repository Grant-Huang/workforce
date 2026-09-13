"""Mac Mini local Pipecat agent — outbound WebSocket to LiveKit Cloud.

Traffic path:
  [Phone/Browser] ──WebRTC──> LiveKit Cloud
                                    ▲
                                    │ outbound WS + media (Mac needs no public IP)
                                    ▼
  [Mac Mini LAN] Pipecat Agent
      SenseVoice (MPS) → LanceDB memory → Qwen2.5 (llama.cpp) → Qwen3-TTS (MPS)

Run (on Mac Mini):
  cd web-demo/pipecat-local-agent
  pip install -r requirements.txt
  # optional Mac ML stack: pip install -r requirements-local-mac.txt
  python pipecat_agent.py --room your-room

Configure via repo-root .env — see README.md and .env.example LOCAL_* keys.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.livekit import configure_with_args, generate_token_with_agent
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport
from pipecat.workers.runner import WorkerRunner

from local_services.config import LocalAgentConfig
from local_services.factory import build_pipeline_services
from transcript_bridge import TranscriptBridge

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent.parent / ".env")


def resolve_livekit_credentials(config: LocalAgentConfig, room_name: str | None):
    """Return (url, agent_token, room_name) for outbound LiveKit Cloud connect."""
    config.validate_livekit()
    room = room_name or config.livekit_room
    url = config.livekit_url

    if config.livekit_agent_token:
        token = config.livekit_agent_token
        logger.info("Using LIVEKIT_AGENT_TOKEN from environment")
    else:
        token = generate_token_with_agent(
            room,
            os.environ.get("LIVEKIT_AGENT_IDENTITY", "Pipecat Local Agent"),
            config.livekit_api_key,
            config.livekit_api_secret,
        )
    return url, token, room


async def entrypoint(room_url: str, token: str, room_name: str, config: LocalAgentConfig):
    transport = LiveKitTransport(
        url=room_url,
        token=token,
        room_name=room_name,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    services = build_pipeline_services(config)
    stt = services["stt"]
    llm = services["llm"]
    tts = services["tts"]
    memory = services["memory"]

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    transcript = TranscriptBridge()

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            transcript,
            memory,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
            # Pipecat pipeline heartbeats: detect stalls while agent waits idle in room.
            # (LiveKitParams has no separate heartbeat flag — keep WorkerRunner alive.)
            enable_heartbeats=config.pipeline_enable_heartbeats,
            heartbeats_period_secs=config.heartbeats_period_secs,
            heartbeats_monitor_secs=config.heartbeats_monitor_secs,
            # Don't cancel the pipeline if no user shows up within 5 minutes.
            # Default pipecat 1.9 cancels after IDLE_TIMEOUT_SECS=300s, but our
            # agents are meant to wait indefinitely in the room for the user to
            # connect (user might have the page open on phone, refresh, etc.).
            # The heartbeat monitor above still detects stalls — we just don't
            # kill the worker for "no activity yet".
            cancel_on_idle_timeout=False,
            cancel_runner_on_idle_timeout=False,
        ),
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )

    runner = WorkerRunner()
    await runner.add_workers(worker)

    @transport.event_handler("on_disconnected")
    async def on_disconnected(_transport):
        logger.warning("LiveKit transport disconnected — agent will exit or reconnect per LIVEKIT_AUTO_RECONNECT")

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(_transport, participant_id):
        logger.info(f"Participant joined: {participant_id}")
        await asyncio.sleep(0.5)
        await worker.queue_frame(
            TTSSpeakFrame("你好，我是本地 Mac 上的 Pipecat 助手，你可以开始说话了。")
        )

    logger.info(
        f"Local agent joining LiveKit Cloud room={room_name} "
        f"stt={config.stt_backend} llm={config.llm_backend} tts={config.tts_backend} "
        f"memory={'on' if config.memory_enabled else 'off'}"
    )
    await runner.run()


async def main():
    parser = argparse.ArgumentParser(description="Mac Mini local Pipecat agent (LiveKit Cloud)")
    parser.add_argument(
        "-r",
        "--room",
        type=str,
        default=None,
        help="LiveKit room (overrides LIVEKIT_ROOM_NAME)",
    )
    parser.add_argument(
        "--use-runner-config",
        action="store_true",
        help="Use pipecat.runner.livekit.configure_with_args (same as pipecat-livekit/bot.py)",
    )
    args, _unknown = parser.parse_known_args()
    config = LocalAgentConfig.from_env()

    config.validate_livekit_url_direct()
    for note in config.warn_mps_layout():
        logger.warning(note)

    while True:
        if args.use_runner_config:
            url, token, room_name, _cfg_args = await configure_with_args(parser)
        else:
            url, token, room_name = resolve_livekit_credentials(config, args.room)

        try:
            await entrypoint(url, token, room_name, config)
        except Exception as exc:
            logger.exception(f"Agent session ended with error: {exc}")
        else:
            logger.info("Agent session ended normally")

        if not config.livekit_auto_reconnect:
            break
        logger.info(f"Reconnecting to LiveKit in {config.livekit_reconnect_delay_secs}s…")
        await asyncio.sleep(config.livekit_reconnect_delay_secs)


if __name__ == "__main__":
    logger.remove(0)
    cfg = LocalAgentConfig.from_env()
    logger.add(sys.stderr, level=cfg.log_level)
    asyncio.run(main())
