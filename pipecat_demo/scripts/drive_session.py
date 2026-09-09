"""Drive a bot session from the command line, using a wav file as the microphone.

A headless stand-in for the browser: it joins a LiveKit room, publishes a wav as the
microphone track in real time, and prints every event the bot sends on the data channel
(the same events the web client renders). Useful for two things a browser is bad at:
reading the per-turn timing numbers as plain JSON, and reproducing a turn exactly the
same way twice.

    python -m pipecat_demo.scripts.drive_session --wav question.wav --mock

The wav must be 16-bit PCM mono; any sample rate works (LiveKit resamples). A file that
is all speech with no pauses will get the bot interrupted on every turn -- that is the
barge-in path working, not a bug. Leave a few seconds of silence after the speech to
see a complete answer.
"""
import argparse
import asyncio
import json
import secrets
import sys
import wave
from pathlib import Path

from livekit import rtc

from pipecat_demo import config
from pipecat_demo.livekit_token import generate_user_token

FRAME_MS = 10


async def drive_session(
    *,
    wav_path: Path,
    duration: float,
    mock: bool,
    room_name: str | None = None,
    log_path: Path | None = None,
) -> list[dict]:
    """Run one session and return the bot's data-channel events."""
    room_name = room_name or f"drive-{secrets.token_hex(4)}"
    log_file = open(log_path, "wb") if log_path else asyncio.subprocess.DEVNULL

    args = [sys.executable, "-m", "pipecat_demo.bot", "--room", room_name]
    if mock:
        args.append("--mock")
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(config.REPO_ROOT),
        stdout=log_file,
        stderr=asyncio.subprocess.STDOUT,
    )

    events: list[dict] = []
    room = rtc.Room()

    @room.on("data_received")
    def _on_data(packet: rtc.DataPacket):
        try:
            event = json.loads(packet.data.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        # pipecat publishes its own RTVI telemetry on this channel; this demo's events
        # are the ones without that label.
        if event.get("label") == "rtvi-ai":
            return
        events.append(event)
        print(json.dumps(event, ensure_ascii=False), flush=True)

    pump = None
    try:
        await room.connect(config.LIVEKIT_URL, generate_user_token(room_name))
        pump = await _publish_wav(room, wav_path)
        await asyncio.sleep(duration)
    finally:
        if pump:
            pump.cancel()
        await room.disconnect()
        try:
            await asyncio.wait_for(process.wait(), timeout=20)
        except asyncio.TimeoutError:
            process.terminate()
        if log_path:
            log_file.close()

    return events


async def _publish_wav(room: rtc.Room, wav_path: Path) -> asyncio.Task:
    reader = wave.open(str(wav_path))
    if reader.getnchannels() != 1 or reader.getsampwidth() != 2:
        raise ValueError(f"{wav_path} must be 16-bit mono PCM")

    sample_rate = reader.getframerate()
    pcm = reader.readframes(reader.getnframes())
    samples_per_frame = sample_rate * FRAME_MS // 1000
    bytes_per_frame = samples_per_frame * 2

    source = rtc.AudioSource(sample_rate, 1)
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )

    async def pump():
        position = 0
        while True:
            chunk = pcm[position:position + bytes_per_frame]
            if len(chunk) < bytes_per_frame:
                position = 0  # loop the file
                continue
            position += bytes_per_frame
            await source.capture_frame(
                rtc.AudioFrame(chunk, sample_rate, 1, samples_per_frame)
            )
            # Real time, not as fast as possible: VAD timings are only meaningful if the
            # audio arrives at the rate a microphone would produce it.
            await asyncio.sleep(FRAME_MS / 1000)

    return asyncio.create_task(pump())


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Drive a bot session with a wav file")
    parser.add_argument("--wav", required=True, type=Path, help="16-bit mono PCM wav")
    parser.add_argument("--duration", type=float, default=40.0, help="Seconds to run")
    parser.add_argument("--mock", action="store_true", help="Run the bot in mock mode")
    parser.add_argument("--room", default=None, help="Room name (random by default)")
    parser.add_argument("--log", type=Path, default=None, help="Write the bot log here")
    args = parser.parse_args()

    asyncio.run(
        drive_session(
            wav_path=args.wav,
            duration=args.duration,
            mock=args.mock,
            room_name=args.room,
            log_path=args.log,
        )
    )


if __name__ == "__main__":
    main()
