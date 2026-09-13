"""Manage Pipecat bot / local-agent subprocesses for the 8766 UI."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOCAL_AGENT_DIR = BASE_DIR.parent / "pipecat-local-agent"
AGENT_SCRIPT = LOCAL_AGENT_DIR / "pipecat_agent.py"

_process: subprocess.Popen | None = None
_room: str | None = None
_stderr_lines: list[str] = []
_stderr_lock = threading.Lock()

_agent_process: subprocess.Popen | None = None
_agent_room: str | None = None
_agent_stderr_lines: list[str] = []
_agent_stderr_lock = threading.Lock()


def _read_stderr(stream, lines: list[str], lock: threading.Lock) -> None:
    try:
        for line in iter(stream.readline, b""):
            text = line.decode("utf-8", errors="replace").rstrip()
            with lock:
                lines.append(text)
                if len(lines) > 80:
                    del lines[:-80]
    finally:
        stream.close()


def _tail(lines: list[str], lock: threading.Lock, limit: int = 8) -> str:
    with lock:
        return "\n".join(lines[-limit:])


def _preflight_livekit_cloud() -> dict | None:
    try:
        from livekit_env import load_livekit_config

        load_livekit_config()
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    if not os.environ.get("LIVEKIT_API_KEY") or not os.environ.get("LIVEKIT_API_SECRET"):
        return {
            "status": "error",
            "message": "LIVEKIT_API_KEY / LIVEKIT_API_SECRET 未配置（LiveKit Cloud 项目凭证）",
        }
    url = os.environ.get("LIVEKIT_URL", "").lower()
    if "livekit.cloud" not in url:
        return {
            "status": "error",
            "message": "LIVEKIT_URL 必须为 wss://<project>.livekit.cloud",
        }
    return None


def _preflight_local_pipeline() -> dict | None:
    """Return error dict if local pipeline prerequisites look missing."""
    cloud_err = _preflight_livekit_cloud()
    if cloud_err:
        return cloud_err

    stt = os.environ.get("LOCAL_STT_BACKEND", "sensevoice").lower()
    llm = os.environ.get("LOCAL_LLM_BACKEND", "llamacpp").lower()
    tts = os.environ.get("LOCAL_TTS_BACKEND", "qwen3_tts").lower()
    api_key = os.environ.get("QWEN_API_KEY", "")

    if stt == "dashscope" and not api_key:
        return {"status": "error", "message": "LOCAL_STT_BACKEND=dashscope 需要 QWEN_API_KEY"}
    if llm == "dashscope" and not api_key:
        return {"status": "error", "message": "LOCAL_LLM_BACKEND=dashscope 需要 QWEN_API_KEY"}
    if tts == "dashscope" and not api_key:
        return {"status": "error", "message": "LOCAL_TTS_BACKEND=dashscope 需要 QWEN_API_KEY"}

    if not LOCAL_AGENT_DIR.is_dir():
        return {
            "status": "error",
            "message": f"缺少本地服务目录: {LOCAL_AGENT_DIR}",
        }
    return None


def is_running() -> bool:
    return _process is not None and _process.poll() is None


def is_agent_running() -> bool:
    return _agent_process is not None and _agent_process.poll() is None


def is_agent_connected_to_livekit() -> bool:
    """Verify via LiveKit Cloud admin API that the agent is actually in the room.

    Popen process being alive doesn't mean the pipecat pipeline is healthy:
    pipecat 1.9 cancels pipeline worker on idle timeout but the Python process
    keeps running, so is_agent_running() returns True while the agent is
    effectively disconnected. Checking LiveKit Cloud for the participant
    gives ground truth.
    """
    if _agent_process is None or _agent_process.poll() is not None or not _agent_room:
        return False
    try:
        asyncio.run(_check_agent_in_room(_agent_room))
    except Exception:
        # If admin API fails (network etc.), fall back to process-alive heuristic.
        return _agent_process.poll() is None
    return True


async def _check_agent_in_room(room: str) -> bool:
    import os as _os

    from livekit.api import LiveKitAPI, ListParticipantsRequest

    url = _os.environ.get("LIVEKIT_URL", "").replace("wss://", "https://").rstrip("/")
    api_key = _os.environ.get("LIVEKIT_API_KEY", "")
    api_secret = _os.environ.get("LIVEKIT_API_SECRET", "")
    if not url or not api_key or not api_secret:
        return False
    api = LiveKitAPI(url, api_key, api_secret)
    try:
        resp = await api.room.list_participants(ListParticipantsRequest(room=room))
        agent_identity = _os.environ.get("LIVEKIT_AGENT_IDENTITY", "Pipecat Local Agent")
        for p in resp.participants:
            if p.identity == agent_identity and str(p.state) == "2":  # 2 = ACTIVE
                return True
        return False
    finally:
        await api.aclose()


def stop() -> None:
    global _process, _room
    if _process is None:
        return
    if _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _process.kill()
            _process.wait(timeout=3)
    _process = None
    _room = None


def stop_agent() -> None:
    global _agent_process, _agent_room
    if _agent_process is None:
        return
    if _agent_process.poll() is None:
        _agent_process.terminate()
        try:
            _agent_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _agent_process.kill()
            _agent_process.wait(timeout=3)
    _agent_process = None
    _agent_room = None


def stop_all() -> None:
    stop()
    stop_agent()


def ensure_started(room: str) -> dict:
    """Start compare-mode bot.py for room if not already running."""
    global _process, _room, _stderr_lines

    preflight = _preflight_local_pipeline()
    if preflight:
        return preflight

    if is_running() and _room == room:
        return {"status": "running", "room": room, "pid": _process.pid, "kind": "bot"}

    if is_running():
        stop()

    _stderr_lines = []
    env = os.environ.copy()
    env["LIVEKIT_ROOM_NAME"] = room
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(LOCAL_AGENT_DIR), env.get("PYTHONPATH", "")])
    )

    cmd = [sys.executable, str(BASE_DIR / "bot.py"), "--room", room]
    _process = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    _room = room
    threading.Thread(
        target=_read_stderr,
        args=(_process.stderr, _stderr_lines, _stderr_lock),
        daemon=True,
    ).start()

    time.sleep(0.8)
    if _process.poll() is not None:
        err = _tail(_stderr_lines, _stderr_lock) or f"bot 进程退出，code={_process.returncode}"
        _process = None
        _room = None
        return {"status": "error", "message": err}

    return {"status": "starting", "room": room, "pid": _process.pid, "kind": "bot"}


def ensure_agent_started(room: str) -> dict:
    """Start pipecat_agent.py (Mac local agent) for room if not already running."""
    global _agent_process, _agent_room, _agent_stderr_lines

    preflight = _preflight_local_pipeline()
    if preflight:
        return preflight

    if not AGENT_SCRIPT.is_file():
        return {"status": "error", "message": f"缺少 agent 脚本: {AGENT_SCRIPT}"}

    if is_agent_running() and _agent_room == room:
        return {
            "status": "running",
            "room": room,
            "pid": _agent_process.pid,
            "kind": "agent",
        }

    if is_agent_running():
        stop_agent()

    _agent_stderr_lines = []
    env = os.environ.copy()
    env["LIVEKIT_ROOM_NAME"] = room
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(LOCAL_AGENT_DIR), env.get("PYTHONPATH", "")])
    )

    cmd = [sys.executable, str(AGENT_SCRIPT), "--room", room]
    _agent_process = subprocess.Popen(
        cmd,
        cwd=str(LOCAL_AGENT_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    _agent_room = room
    threading.Thread(
        target=_read_stderr,
        args=(_agent_process.stderr, _agent_stderr_lines, _agent_stderr_lock),
        daemon=True,
    ).start()

    time.sleep(0.8)
    if _agent_process.poll() is not None:
        err = (
            _tail(_agent_stderr_lines, _agent_stderr_lock)
            or f"agent 进程退出，code={_agent_process.returncode}"
        )
        _agent_process = None
        _agent_room = None
        return {"status": "error", "message": err}

    return {
        "status": "starting",
        "room": room,
        "pid": _agent_process.pid,
        "kind": "agent",
    }


def get_status() -> dict:
    if not is_running():
        err = _tail(_stderr_lines, _stderr_lock, 12) if _stderr_lines else ""
        return {
            "status": "stopped",
            "room": _room,
            "kind": "bot",
            "error": err or None,
            "exit_code": _process.returncode if _process else None,
        }
    return {"status": "running", "room": _room, "pid": _process.pid, "kind": "bot"}


def get_agent_status() -> dict:
    global _agent_process, _agent_room
    if not is_agent_connected_to_livekit():
        err = (
            _tail(_agent_stderr_lines, _agent_stderr_lock, 12)
            if _agent_stderr_lines
            else ""
        )
        # If the Popen process is alive but the agent isn't actually in the room
        # (pipecat idle-timeout cancel or LiveKit SFU drop), return stopped so
        # /api/agent/wake knows to spawn a fresh one.
        exit_code = _agent_process.returncode if _agent_process else None
        # Force-clear the stale Popen reference so the next wake starts fresh.
        _agent_process = None
        _agent_room = None
        return {
            "status": "stopped",
            "room": _agent_room,
            "kind": "agent",
            "error": err or None,
            "exit_code": exit_code,
        }
    return {
        "status": "running",
        "room": _agent_room,
        "pid": _agent_process.pid,
        "kind": "agent",
    }
