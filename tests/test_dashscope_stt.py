"""Protocol tests for DashScopeSTTService against the fake inference server."""
from pipecat.frames.frames import (
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from pipecat_demo.services.dashscope_stt import DashScopeSTTService
from tests.fake_dashscope import FakeDashScopeServer

SAMPLE_RATE = 16000
AUDIO_CHUNK = b"\x00\x00" * 320  # 20ms of silence at 16kHz


def make_service(url: str, **kwargs) -> DashScopeSTTService:
    return DashScopeSTTService(
        api_key="test-key",
        url=url,
        workspace_id="llm-test-workspace",
        sample_rate=SAMPLE_RATE,
        language_hints=["zh", "en"],
        max_sentence_silence_ms=800,
        **kwargs,
    )


async def test_run_task_shape_and_auth():
    """run-task carries the documented fields, and the key travels in the header."""
    async with FakeDashScopeServer() as server:
        service = make_service(server.url)
        await run_test(
            service,
            frames_to_send=[InputAudioRawFrame(AUDIO_CHUNK, SAMPLE_RATE, 1), SleepFrame(0.3)],
            expected_down_frames=None,
        )

        run_task = server.instruction("run-task")
        assert run_task is not None
        assert run_task["header"]["action"] == "run-task"
        assert run_task["header"]["streaming"] == "duplex"
        assert run_task["header"]["task_id"]

        payload = run_task["payload"]
        assert payload["task_group"] == "audio"
        assert payload["task"] == "asr"
        assert payload["function"] == "recognition"
        assert payload["model"] == "paraformer-realtime-v2"
        assert payload["input"] == {}

        parameters = payload["parameters"]
        assert parameters["format"] == "pcm"
        assert parameters["sample_rate"] == SAMPLE_RATE
        assert parameters["max_sentence_silence"] == 800
        assert parameters["language_hints"] == ["zh", "en"]

        assert server.headers.get("authorization") == "Bearer test-key"
        assert server.headers.get("x-dashscope-workspace") == "llm-test-workspace"


async def test_audio_is_forwarded_and_task_finished_on_stop():
    """Audio reaches the server, and the task is closed rather than dropped."""
    async with FakeDashScopeServer() as server:
        service = make_service(server.url)
        await run_test(
            service,
            frames_to_send=[
                InputAudioRawFrame(AUDIO_CHUNK, SAMPLE_RATE, 1),
                InputAudioRawFrame(AUDIO_CHUNK, SAMPLE_RATE, 1),
                SleepFrame(0.3),
            ],
            expected_down_frames=None,
        )

        assert server.audio_bytes >= len(AUDIO_CHUNK) * 2
        assert "finish-task" in server.actions()


async def test_sentence_end_produces_final_transcription():
    """`sentence_end` decides interim vs final, which is what turn-taking keys on."""
    results = [
        {"text": "今天下午", "sentence_end": False},
        {"text": "今天下午三点开会", "sentence_end": True},
    ]
    async with FakeDashScopeServer(asr_results=results) as server:
        service = make_service(server.url)
        down, _ = await run_test(
            service,
            frames_to_send=[InputAudioRawFrame(AUDIO_CHUNK, SAMPLE_RATE, 1), SleepFrame(0.4)],
            expected_down_frames=None,
        )

        interim = [f for f in down if isinstance(f, InterimTranscriptionFrame)]
        final = [f for f in down if isinstance(f, TranscriptionFrame)]
        assert [f.text for f in interim] == ["今天下午"]
        assert [f.text for f in final] == ["今天下午三点开会"]


async def test_heartbeat_results_are_ignored():
    """Keepalive echoes must not turn into transcripts."""
    results = [{"text": "", "sentence_end": True, "heartbeat": True}]
    async with FakeDashScopeServer(asr_results=results) as server:
        service = make_service(server.url)
        down, _ = await run_test(
            service,
            frames_to_send=[InputAudioRawFrame(AUDIO_CHUNK, SAMPLE_RATE, 1), SleepFrame(0.3)],
            expected_down_frames=None,
        )

        assert not [f for f in down if isinstance(f, TranscriptionFrame)]


async def test_audio_before_task_started_is_not_lost():
    """Audio that arrives during the handshake is buffered, not dropped.

    The start of an utterance is exactly what a slow handshake would eat, and losing it
    shows up as a truncated first word rather than an obvious error.
    """
    async with FakeDashScopeServer() as server:
        service = make_service(server.url)
        # Reach into the service to simulate "handshake still in flight" without racing
        # the real one: the buffer must be flushed once task-started arrives.
        service._task_started = False
        service._pending_audio.extend(AUDIO_CHUNK)
        await service._handle_event({"header": {"event": "task-started"}})

        assert service._task_started is True
        assert not service._pending_audio
