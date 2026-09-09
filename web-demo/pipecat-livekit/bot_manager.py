"""Manage the Pipecat bot subprocess for the comparison UI."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

_process: subprocess.Popen | None = None
_room: str | None = None
_stderr_lines: list[str] = []
_stderr_lock = threading.Lock()


def _read_stderr(stream) -> None:
    global _stderr_lines
    try:
        for line in iter(stream.readline, b""):
            text = line.decode("utf-8", errors="replace").rstrip()
            with _stderr_lock:
                _stderr_lines.append(text)
                if len(_stderr_lines) > 80:
                    _stderr_lines = _stderr_lines[-80:]
    finally:
        stream.close()


def _tail_stderr(limit: int = 8) -> str:
    with _stderr_lock:
        return "\n".join(_stderr_lines[-limit:])


def is_running() -> bool:
    return _process is not None and _process.poll() is None


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


def ensure_started(room: str) -> dict:
    """Start bot for room if not already running. Returns status dict."""
    global _process, _room, _stderr_lines

    if not os.environ.get("QWEN_API_KEY"):
        return {
            "status": "error",
            "message": "QWEN_API_KEY 未配置。请在仓库根目录 .env 中设置后重启 server.py",
        }

    if is_running() and _room == room:
        return {"status": "running", "room": room, "pid": _process.pid}

    if is_running():
        stop()

    _stderr_lines = []
    env = os.environ.copy()
    env.setdefault("LIVEKIT_URL", os.environ.get("LIVEKIT_URL", "ws://127.0.0.1:7880"))
    env.setdefault("LIVEKIT_API_KEY", os.environ.get("LIVEKIT_API_KEY", "devkey"))
    env.setdefault("LIVEKIT_API_SECRET", os.environ.get("LIVEKIT_API_SECRET", "secret"))
    env["LIVEKIT_ROOM_NAME"] = room

    cmd = [sys.executable, str(BASE_DIR / "bot.py"), "--room", room]
    _process = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    _room = room
    threading.Thread(target=_read_stderr, args=(_process.stderr,), daemon=True).start()

    # Give the bot a moment to fail fast (missing deps, bad key format, etc.)
    time.sleep(0.8)
    if _process.poll() is not None:
        err = _tail_stderr() or f"bot 进程退出，code={_process.returncode}"
        _process = None
        _room = None
        return {"status": "error", "message": err}

    return {"status": "starting", "room": room, "pid": _process.pid}


def get_status() -> dict:
    if not is_running():
        err = _tail_stderr(12) if _stderr_lines else ""
        return {
            "status": "stopped",
            "room": _room,
            "error": err or None,
            "exit_code": _process.returncode if _process else None,
        }
    return {"status": "running", "room": _room, "pid": _process.pid}
