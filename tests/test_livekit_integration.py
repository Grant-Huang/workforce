"""End-to-end wiring check against a real LiveKit server.

Covers the parts that cannot be faked in-process: the bot's token grants, joining a
room, publishing its audio track, receiving a participant's audio, and shutting down
when that participant leaves. Runs in mock mode, so no DashScope key is involved.

Skipped unless a LiveKit server is reachable at LIVEKIT_URL. Start one with:

    pipecat_demo/scripts/run_livekit_dev.sh
"""
import asyncio
import contextlib
import secrets
import socket
import sys
from urllib.parse import urlparse

import pytest
from livekit import rtc

from pipecat_demo import config
from pipecat_demo.livekit_token import BOT_IDENTITY, generate_user_token

BOT_JOIN_TIMEOUT = 45.0
SAMPLE_RATE = 16000
NUM_CHANNELS = 1


def _livekit_running() -> bool:
    parsed = urlparse(config.LIVEKIT_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    with contextlib.closing(socket.socket()) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((host, port)) == 0


pytestmark = pytest.mark.skipif(
    not _livekit_running(),
    reason=f"no LiveKit server at {config.LIVEKIT_URL}; run scripts/run_livekit_dev.sh",
)


async def _publish_silence(room: rtc.Room) -> tuple[rtc.AudioSource, asyncio.Task]:
    """Publish a silent microphone track so the bot has audio to receive."""
    source = rtc.AudioSource(SAMPLE_RATE, NUM_CHANNELS)
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )

    async def pump():
        samples = SAMPLE_RATE // 100  # 10ms
        frame = rtc.AudioFrame.create(SAMPLE_RATE, NUM_CHANNELS, samples)
        while True:
            await source.capture_frame(frame)
            await asyncio.sleep(0.01)

    return source, asyncio.create_task(pump())


@pytest.mark.asyncio
async def test_bot_joins_publishes_audio_and_exits_with_the_participant():
    room_name = f"smoke-{secrets.token_hex(4)}"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pipecat_demo.bot",
        "--room",
        room_name,
        "--mock",
        cwd=str(config.REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    room = rtc.Room()
    bot_joined = asyncio.Event()
    bot_published = asyncio.Event()

    @room.on("participant_connected")
    def _on_participant(participant: rtc.RemoteParticipant):
        if participant.identity == BOT_IDENTITY:
            bot_joined.set()

    @room.on("track_published")
    def _on_track(publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        if participant.identity == BOT_IDENTITY and publication.kind == rtc.TrackKind.KIND_AUDIO:
            bot_published.set()

    pump = None
    try:
        await room.connect(config.LIVEKIT_URL, generate_user_token(room_name))
        _, pump = await _publish_silence(room)

        try:
            await asyncio.wait_for(bot_joined.wait(), timeout=BOT_JOIN_TIMEOUT)
            await asyncio.wait_for(bot_published.wait(), timeout=BOT_JOIN_TIMEOUT)
        except asyncio.TimeoutError:
            process.terminate()
            output = (await process.stdout.read()).decode(errors="replace")
            pytest.fail(f"bot never joined/published. Bot output:\n{output}")
    finally:
        if pump:
            pump.cancel()
        await room.disconnect()

    # Leaving the room ends the session: the bot must not linger as an orphan process.
    try:
        await asyncio.wait_for(process.wait(), timeout=30)
    except asyncio.TimeoutError:
        process.terminate()
        pytest.fail("bot did not exit after the participant left")

    assert process.returncode == 0
