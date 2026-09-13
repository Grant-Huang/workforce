"""Manage the Pipecat bot subprocess for the comparison UI."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOCAL_AGENT_DIR = BASE_DIR.parent / "pipecat-local-agent"

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

    preflight = _preflight_local_pipeline()
    if preflight:
        return preflight

    if is_running() and _room == room:
        return {"status": "running", "room": room, "pid": _process.pid}

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
    threading.Thread(target=_read_stderr, args=(_process.stderr,), daemon=True).start()

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
