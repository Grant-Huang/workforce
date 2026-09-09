"""Protocol tests for DashScopeTTSService against the fake inference server."""
from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from pipecat_demo.services.dashscope_tts import DashScopeTTSService

from tests.fake_dashscope import FakeDashScopeServer

SAMPLE_RATE = 24000
SENTENCE = "你好，这是一句测试。"


def make_service(url: str, **kwargs) -> DashScopeTTSService:
    return DashScopeTTSService(
        api_key="test-key",
        url=url,
        workspace_id="llm-test-workspace",
        sample_rate=SAMPLE_RATE,
        **kwargs,
    )


async def speak(server: FakeDashScopeServer, service: DashScopeTTSService):
    return await run_test(
        service,
        frames_to_send=[
            LLMFullResponseStartFrame(),
            TextFrame(SENTENCE),
            LLMFullResponseEndFrame(),
            SleepFrame(0.5),
        ],
        expected_down_frames=None,
    )


async def test_task_lifecycle_order_and_ids():
    """One task per turn: run-task, then text, then finish-task, all one task_id."""
    async with FakeDashScopeServer() as server:
        await speak(server, make_service(server.url))

        actions = server.actions()
        assert actions.count("run-task") == 1
        assert "continue-task" in actions
        assert actions.index("run-task") < actions.index("continue-task")
        assert actions[-1] == "finish-task"

        task_ids = {i["header"]["task_id"] for i in server.instructions}
        assert len(task_ids) == 1


async def test_run_task_shape_and_auth():
    """run-task carries the documented synthesis parameters and an empty input."""
    async with FakeDashScopeServer() as server:
        await speak(server, make_service(server.url))

        payload = server.instruction("run-task")["payload"]
        assert payload["task_group"] == "audio"
        assert payload["task"] == "tts"
        assert payload["function"] == "SpeechSynthesizer"
        assert payload["model"] == "cosyvoice-v2"
        # Text may only travel on continue-task; a non-empty input here is rejected.
        assert payload["input"] == {}

        parameters = payload["parameters"]
        assert parameters["text_type"] == "PlainText"
        assert parameters["voice"] == "longxiaochun_v2"
        assert parameters["format"] == "pcm"
        assert parameters["sample_rate"] == SAMPLE_RATE

        assert server.headers.get("authorization") == "Bearer test-key"
        assert server.headers.get("x-dashscope-workspace") == "llm-test-workspace"


async def test_text_is_sent_and_audio_is_emitted():
    """The sentence reaches the server and the returned binary becomes audio frames."""
    async with FakeDashScopeServer(tts_chunks=3, tts_chunk_bytes=960) as server:
        down, _ = await speak(server, make_service(server.url))

        text_sent = "".join(
            i["payload"]["input"]["text"]
            for i in server.instructions
            if i["header"]["action"] == "continue-task"
        )
        assert SENTENCE.strip("。") in text_sent

        audio = [f for f in down if isinstance(f, TTSAudioRawFrame)]
        assert len(audio) == 3
        assert all(f.sample_rate == SAMPLE_RATE for f in audio)
        assert all(len(f.audio) == 960 for f in audio)


async def test_started_and_stopped_frames_bracket_the_audio():
    """The turn is framed by TTSStarted/TTSStopped, which drive bot speaking state."""
    async with FakeDashScopeServer() as server:
        down, _ = await speak(server, make_service(server.url))

        kinds = [type(f).__name__ for f in down]
        assert "TTSStartedFrame" in kinds
        assert "TTSStoppedFrame" in kinds

        first_audio = next(i for i, f in enumerate(down) if isinstance(f, TTSAudioRawFrame))
        started = next(i for i, f in enumerate(down) if isinstance(f, TTSStartedFrame))
        stopped = next(i for i, f in enumerate(down) if isinstance(f, TTSStoppedFrame))
        assert started < first_audio < stopped


async def test_second_turn_starts_a_new_task():
    """A finished task is not reused: the next turn opens its own task_id."""
    async with FakeDashScopeServer() as server:
        service = make_service(server.url)
        await run_test(
            service,
            frames_to_send=[
                LLMFullResponseStartFrame(),
                TextFrame(SENTENCE),
                LLMFullResponseEndFrame(),
                SleepFrame(0.4),
                LLMFullResponseStartFrame(),
                TextFrame("第二句话。"),
                LLMFullResponseEndFrame(),
                SleepFrame(0.4),
            ],
            expected_down_frames=None,
        )

        assert server.actions().count("run-task") == 2
        task_ids = {i["header"]["task_id"] for i in server.instructions}
        assert len(task_ids) == 2
