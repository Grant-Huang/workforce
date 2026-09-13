"""LiveKit + Pipecat voice bot for comparison with Qwen Realtime WebSocket demo.

Pipeline (local STT -> LLM -> TTS):
  SenseVoice STT -> LLM (llama-server / Qwen2.5) -> Qwen3-TTS (local)

Run:
  cd web-demo/pipecat-livekit
  pip install -r requirements.txt
  pip install -r ../pipecat-local-agent/requirements-local-mac.txt
  python bot.py --room voicechat-compare

Requires LiveKit Cloud (LIVEKIT_* in .env) and local models (LOCAL_*).
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
from pipecat.runner.livekit import configure_with_args
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport
from pipecat.workers.runner import WorkerRunner

from transcript_bridge import TranscriptBridge

BASE_DIR = Path(__file__).resolve().parent
LOCAL_AGENT_DIR = BASE_DIR.parent / "pipecat-local-agent"
if str(LOCAL_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_AGENT_DIR))

from local_services.config import LocalAgentConfig
from local_services.factory import build_llm_service, build_stt, build_tts

load_dotenv(BASE_DIR.parent.parent / ".env")


def build_services(config: LocalAgentConfig):
    for note in config.warn_mps_layout():
        logger.warning(note)
    stt = build_stt(config)
    llm = build_llm_service(config)
    tts = build_tts(config)
    logger.info(
        f"Pipeline backends: stt={config.stt_backend} llm={config.llm_backend} tts={config.tts_backend}"
    )
    return stt, llm, tts


async def main():
    parser = argparse.ArgumentParser(description="LiveKit + Pipecat voice bot (local pipeline)")
    url, token, room_name, args = await configure_with_args(parser)

    config = LocalAgentConfig.from_env()
    config.validate_livekit()
    config.validate_livekit_url_direct()

    transport = LiveKitTransport(
        url=url,
        token=token,
        room_name=room_name,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    stt, llm, tts = build_services(config)
    transcript_bridge = TranscriptBridge()

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            transcript_bridge,
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
        ),
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )

    runner = WorkerRunner()
    await runner.add_workers(worker)

    # Use on_participant_connected (fires for every new join) instead of
    # on_first_participant_joined (fires once). Fixes: opening the page hours
    # later still plays the welcome TTS, and reconnects after a network blip
    # also replay it. Filter out the agent's own identity.
    @transport.event_handler("on_participant_connected")
    async def on_participant_connected(_transport, participant_id):
        own_identity = os.environ.get("LIVEKIT_AGENT_IDENTITY", "Pipecat Agent")
        if participant_id == own_identity:
            return
        logger.info(f"Participant joined: {participant_id}")
        await asyncio.sleep(0.5)
        await worker.queue_frame(
            TTSSpeakFrame("你好，我是 LiveKit 加 Pipecat 的本地语音助手。你可以开始说话了。")
        )

    logger.info(
        f"Bot joining LiveKit room={room_name} url={url} "
        f"stt={config.stt_backend} llm={config.llm_backend} tts={config.tts_backend}"
    )
    await runner.run()


if __name__ == "__main__":
    logger.remove(0)
    logger.add(sys.stderr, level=os.environ.get("LOG_LEVEL", "INFO"))
    asyncio.run(main())
