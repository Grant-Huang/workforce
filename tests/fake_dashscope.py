"""A fake DashScope `api-ws/v1/inference` server for testing.

Speaks the documented instruction/event protocol (run-task → task-started →
continue-task / binary audio → result-generated → finish-task → task-finished) and
records everything it receives, so the ASR and TTS services can be verified without a
DashScope account. It is a protocol double, not a simulator: it does not recognize or
synthesize anything.
"""
import asyncio
import json

from websockets.asyncio.server import serve


class FakeDashScopeServer:
    """Records client instructions and replies with scripted events."""

    def __init__(
        self,
        *,
        asr_results: list[dict] | None = None,
        tts_chunks: int = 3,
        tts_chunk_bytes: int = 960,
        fail_task: bool = False,
    ):
        """Initialize the fake server.

        Args:
            asr_results: `payload.output.sentence` dicts to emit after audio arrives.
            tts_chunks: How many binary audio chunks to emit per continue-task.
            tts_chunk_bytes: Size of each fake audio chunk.
            fail_task: Reply `task-failed` instead of `task-started`.
        """
        self._asr_results = asr_results or []
        self._tts_chunks = tts_chunks
        self._tts_chunk_bytes = tts_chunk_bytes
        self._fail_task = fail_task

        self.instructions: list[dict] = []
        self.audio_bytes = 0
        self.headers: dict = {}
        self._server = None
        self._audio_seen = asyncio.Event()

    @property
    def url(self) -> str:
        """WebSocket URL of the running server."""
        host, port = self._server.sockets[0].getsockname()[:2]
        return f"ws://{host}:{port}"

    def actions(self) -> list[str]:
        """The `header.action` of every instruction received, in order."""
        return [i["header"]["action"] for i in self.instructions]

    def instruction(self, action: str) -> dict | None:
        """The first instruction with the given action."""
        for item in self.instructions:
            if item["header"]["action"] == action:
                return item
        return None

    async def wait_for_audio(self, timeout: float = 2.0) -> None:
        """Wait until at least one binary audio frame has arrived."""
        await asyncio.wait_for(self._audio_seen.wait(), timeout=timeout)

    async def __aenter__(self) -> "FakeDashScopeServer":
        self._server = await serve(self._handler, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc_info) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _handler(self, websocket) -> None:
        self.headers = dict(websocket.request.headers)
        task_id = None
        async for message in websocket:
            if isinstance(message, bytes):
                self.audio_bytes += len(message)
                self._audio_seen.set()
                await self._emit_asr_results(websocket, task_id)
                continue

            instruction = json.loads(message)
            self.instructions.append(instruction)
            action = instruction["header"]["action"]
            task_id = instruction["header"]["task_id"]

            if action == "run-task":
                event = "task-failed" if self._fail_task else "task-started"
                await self._send_event(websocket, event, task_id)
            elif action == "continue-task":
                await self._send_event(
                    websocket, "result-generated", task_id, {"output": {"sentence_index": 0}}
                )
                for _ in range(self._tts_chunks):
                    await websocket.send(b"\x00\x01" * (self._tts_chunk_bytes // 2))
            elif action == "finish-task":
                await self._send_event(websocket, "task-finished", task_id)

    async def _emit_asr_results(self, websocket, task_id) -> None:
        while self._asr_results:
            sentence = self._asr_results.pop(0)
            await self._send_event(
                websocket,
                "result-generated",
                task_id,
                {"output": {"sentence": sentence}},
            )

    @staticmethod
    async def _send_event(websocket, event: str, task_id, payload: dict | None = None) -> None:
        message = {
            "header": {"event": event, "task_id": task_id},
            "payload": payload or {},
        }
        if event == "task-failed":
            message["header"]["error_code"] = "TestError"
            message["header"]["error_message"] = "fake failure"
        await websocket.send(json.dumps(message))
