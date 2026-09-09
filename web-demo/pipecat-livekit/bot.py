"""LiveKit + Pipecat voice bot for comparison with Qwen Realtime WebSocket demo.

Pipeline (modular STT -> LLM -> TTS):
  transport.input -> STT -> user_aggregator -> LLM -> TTS -> transport.output -> assistant_aggregator

Run:
  cd web-demo/pipecat-livekit
  pip install -r requirements.txt
  python bot.py --room voicechat-compare

Requires LiveKit server (see README) and QWEN_API_KEY in repo-root .env.
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
from pipecat.services.qwen.llm import QwenLLMService
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport
from pipecat.workers.runner import WorkerRunner

from dashscope_services import DashScopeSTTService, DashScopeTTSV2Service
from transcript_bridge import TranscriptBridge

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent.parent / ".env")

BASE_INSTRUCTIONS = """你是一个语音助手，正在和用户实时语音对话。

说话方式：
- 像日常聊天一样自然口语化，不要用书面语。
- 不要用任何视觉格式：不用列表符号、编号、加粗，也不要读网址或代码。
- 回答尽量简洁，适合语音播报。"""


def compatible_mode_base() -> str:
    workspace_id = os.environ.get("QWEN_WORKSPACE_ID", "")
    if workspace_id:
        return f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    return "https://dashscope.aliyuncs.com/compatible-mode/v1"


def build_services():
    api_key = os.environ.get("QWEN_API_KEY", "")
    if not api_key:
        raise RuntimeError("QWEN_API_KEY not set in .env")

    stt_model = os.environ.get("PIPECAT_STT_MODEL", "paraformer-realtime-v1")
    llm_model = os.environ.get("PIPECAT_LLM_MODEL", "qwen-plus")
    tts_model = os.environ.get("PIPECAT_TTS_MODEL", "cosyvoice-v3-flash")
    tts_voice = os.environ.get("PIPECAT_TTS_VOICE", os.environ.get("QWEN_VOICE", "longxiaochun_v2"))

    stt = DashScopeSTTService(api_key=api_key, model=stt_model)
    llm = QwenLLMService(
        api_key=api_key,
        base_url=compatible_mode_base(),
        settings=QwenLLMService.Settings(
            model=llm_model,
            system_instruction=BASE_INSTRUCTIONS,
            temperature=0.7,
            max_completion_tokens=512,
        ),
    )
    tts = DashScopeTTSV2Service(
        api_key=api_key,
        model=tts_model,
        voice=tts_voice,
        sample_rate=24000,
    )
    return stt, llm, tts


async def main():
    parser = argparse.ArgumentParser(description="LiveKit + Pipecat voice bot")
    url, token, room_name, args = await configure_with_args(parser)

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

    stt, llm, tts = build_services()
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

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport_obj, participant_id):
        logger.info(f"Participant joined: {participant_id}")
        await asyncio.sleep(0.5)
        await worker.queue_frame(
            TTSSpeakFrame("你好，我是 LiveKit 加 Pipecat 的语音助手。你可以开始说话了。")
        )

    logger.info(f"Bot joining LiveKit room={room_name} url={url}")
    await runner.run()


if __name__ == "__main__":
    logger.remove(0)
    logger.add(sys.stderr, level=os.environ.get("LOG_LEVEL", "INFO"))
    asyncio.run(main())
