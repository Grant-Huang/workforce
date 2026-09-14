"""DashScope STT/TTS services for Pipecat (adapted from pipecat-dashscope, fixed for Pipecat 1.8+)."""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import dashscope
from dashscope.audio.asr import Recognition
from dashscope.audio.qwen_tts_realtime import (
    AudioFormat as QwenRealtimeAudioFormat,
    QwenTtsRealtime,
    QwenTtsRealtimeCallback,
)
from dashscope.audio.tts_v2 import AudioFormat as TTSV2AudioFormat
from dashscope.audio.tts_v2 import SpeechSynthesizer
from loguru import logger

from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame, TranscriptionFrame
from pipecat.services.settings import NOT_GIVEN, STTSettings, TTSSettings, is_given
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.services.tts_service import TTSService
from pipecat.transcriptions.language import Language, resolve_language
from pipecat.utils.time import time_now_iso8601
from pipecat.utils.tracing.service_decorators import traced_stt, traced_tts

DEFAULT_DASHSCOPE_STT_MODEL = "paraformer-realtime-v1"


def _resolve_api_key(api_key: str | None) -> str:
    resolved = api_key or os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")
    if not resolved:
        raise ValueError("DashScope API key is required. Set QWEN_API_KEY or DASHSCOPE_API_KEY.")
    return resolved


def language_to_dashscope_stt_language(language: Language) -> str | None:
    language_map = {
        Language.ZH: "zh",
        Language.ZH_CN: "zh",
        Language.EN: "en",
        Language.EN_US: "en",
        Language.EN_GB: "en",
        Language.JA: "ja",
        Language.KO: "ko",
        Language.DE: "de",
        Language.FR: "fr",
        Language.RU: "ru",
        Language.ES: "es",
    }
    return resolve_language(language, language_map, use_base_code=True)


@dataclass
class DashScopeSTTSettings(STTSettings):
    phrase_id: str | None = None
    disfluency_removal_enabled: bool | None = None


class DashScopeSTTService(SegmentedSTTService):
    """Segmented STT backed by DashScope ASR Recognition API."""

    Settings = DashScopeSTTSettings
    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        sample_rate: int | None = None,
        settings: Settings | None = None,
        **kwargs,
    ) -> None:
        default_settings = self.Settings(model=model or DEFAULT_DASHSCOPE_STT_MODEL, language=None)
        if settings is not None:
            default_settings.apply_update(settings)
        super().__init__(sample_rate=sample_rate, settings=default_settings, **kwargs)
        self._api_key = _resolve_api_key(api_key)

    def language_to_service_language(self, language: Language) -> str | None:
        return language_to_dashscope_stt_language(language)

    @traced_stt
    async def _handle_transcription(self, transcript: str, is_final: bool):
        return None

    async def run_stt(self, audio: bytes):
        tmp_path: Path | None = None
        try:
            await self.start_processing_metrics()
            tmp_path = await self._write_temp_wav(audio)
            result = await asyncio.to_thread(self._transcribe_file, tmp_path)
            await self.stop_processing_metrics()
            text = self._extract_text(result).strip()
            if not text:
                logger.warning(f"{self}: received empty transcription from DashScope")
                return
            await self._handle_transcription(text, True)
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), result=result)
        except Exception as e:
            yield ErrorFrame(error=f"DashScope STT error: {e}")
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    async def _write_temp_wav(self, audio: bytes) -> Path:
        def _write() -> Path:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as handle:
                handle.write(audio)
                return Path(handle.name)

        return await asyncio.to_thread(_write)

    def _transcribe_file(self, path: Path):
        effective_sample_rate = self.sample_rate or self._init_sample_rate
        recognition = Recognition(
            model=self._settings.model,
            callback=None,
            format="wav",
            sample_rate=effective_sample_rate,
            api_key=self._api_key,
        )
        return recognition.call(str(path))

    @staticmethod
    def _extract_text(result) -> str:
        sentence = result.get_sentence()
        if isinstance(sentence, dict):
            return sentence.get("text", "")
        if isinstance(sentence, list):
            return " ".join(item.get("text", "") for item in sentence if item.get("text"))
        return ""


@dataclass
class DashScopeTTSV2Settings(TTSSettings):
    volume: int | None = 50
    speech_rate: float | None = 1.0
    pitch_rate: float | None = 1.0


class DashScopeTTSV2Service(TTSService):
    """TTS backed by DashScope audio.tts_v2.SpeechSynthesizer."""

    Settings = DashScopeTTSV2Settings
    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str,
        voice: str,
        sample_rate: int | None = None,
        settings: Settings | None = None,
        **kwargs,
    ) -> None:
        default_settings = self.Settings(model=model, voice=voice, language=None)
        if settings is not None:
            default_settings.apply_update(settings)
        super().__init__(
            sample_rate=sample_rate or 24000,
            push_start_frame=True,
            push_stop_frames=True,
            settings=default_settings,
            **kwargs,
        )
        self._api_key = _resolve_api_key(api_key)

    @property
    def _effective_sample_rate(self) -> int:
        return self.sample_rate or self._init_sample_rate

    def can_generate_metrics(self) -> bool:
        return True

    @traced_tts
    async def run_tts(self, text: str, context_id: str):
        try:
            await self.start_processing_metrics()
            audio = await asyncio.to_thread(self._synthesize, text)
            await self.stop_processing_metrics()
            if audio:
                yield TTSAudioRawFrame(
                    audio=audio,
                    sample_rate=self._effective_sample_rate,
                    num_channels=1,
                    context_id=context_id,
                )
        except Exception as e:
            yield ErrorFrame(error=f"DashScope TTS v2 error: {e}")

    def _synthesize(self, text: str) -> bytes:
        dashscope.api_key = self._api_key
        synthesizer = SpeechSynthesizer(
            model=self._settings.model,
            voice=self._settings.voice,
            format=self._dashscope_audio_format(self._effective_sample_rate),
            volume=self._settings.volume if is_given(self._settings.volume) else 50,
            speech_rate=self._settings.speech_rate if is_given(self._settings.speech_rate) else 1.0,
            pitch_rate=self._settings.pitch_rate if is_given(self._settings.pitch_rate) else 1.0,
        )
        audio = synthesizer.call(text)
        return bytes(audio or b"")

    @staticmethod
    def _dashscope_audio_format(sample_rate: int) -> TTSV2AudioFormat:
        mapping = {
            8000: TTSV2AudioFormat.PCM_8000HZ_MONO_16BIT,
            16000: TTSV2AudioFormat.PCM_16000HZ_MONO_16BIT,
            22050: TTSV2AudioFormat.PCM_22050HZ_MONO_16BIT,
            24000: TTSV2AudioFormat.PCM_24000HZ_MONO_16BIT,
            44100: TTSV2AudioFormat.PCM_44100HZ_MONO_16BIT,
            48000: TTSV2AudioFormat.PCM_48000HZ_MONO_16BIT,
        }
        if sample_rate not in mapping:
            raise ValueError(f"Unsupported TTS sample_rate={sample_rate}")
        return mapping[sample_rate]


class _QwenRealtimeCollector(QwenTtsRealtimeCallback):
    def __init__(self):
        self.audio_chunks: list[bytes] = []
        # Two independent "done" signals so we don't exit before audio arrives:
        #   - self.audio_done: set when server says "response.audio.done"
        #     (the audio stream has been fully delivered)
        #   - self.session_done: set when server says "response.done" /
        #     "session.finished" / socket closes
        # ``run_tts`` waits for audio_done FIRST, then for session_done with a
        # short grace period. Without this split, on_close fires almost
        # immediately after finish() on some server commits and we return
        # before any audio chunks have arrived — the user hears silence.
        self.audio_done = threading.Event()
        self.session_done = threading.Event()
        self.error: Exception | None = None
        self.error_msg: dict[str, Any] | None = None

    def on_close(self, close_status_code, close_msg) -> None:
        # Socket closed — mark session done. Don't trust this as the audio
        # boundary; it's only the "I have nothing more to send" signal.
        self.session_done.set()

    def on_event(self, message: dict[str, Any]) -> None:
        try:
            t = message.get("type")
            if t == "response.audio.delta":
                audio = (
                    message.get("delta")
                    or message.get("audio")
                    or message.get("data")
                    or message.get("response", {}).get("audio", {}).get("delta")
                )
                chunk = base64.b64decode(audio) if audio else b""
                if chunk:
                    self.audio_chunks.append(chunk)
            elif t == "response.audio.done":
                # Server explicitly says audio stream finished. This is the
                # reliable "no more chunks coming" signal we should wait for.
                self.audio_done.set()
            elif t in {"response.done", "session.finished"}:
                self.session_done.set()
            elif t == "error":
                self.error_msg = message
                self.audio_done.set()  # unblock waiters on error too
                self.session_done.set()
        except Exception as e:
            self.error = e
            self.audio_done.set()
            self.session_done.set()


@dataclass
class DashScopeQwenRealtimeTTSSettings(TTSSettings):
    mode: str | None = "server_commit"


class DashScopeQwenRealtimeTTSService(TTSService):
    """TTS backed by DashScope qwen_tts_realtime."""

    Settings = DashScopeQwenRealtimeTTSSettings
    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str,
        voice: str,
        sample_rate: int | None = None,
        settings: Settings | None = None,
        **kwargs,
    ) -> None:
        default_settings = self.Settings(model=model, voice=voice, language=None, mode="server_commit")
        if settings is not None:
            default_settings.apply_update(settings)
        super().__init__(
            sample_rate=sample_rate or 24000,
            push_start_frame=True,
            push_stop_frames=True,
            settings=default_settings,
            **kwargs,
        )
        self._api_key = _resolve_api_key(api_key)

    @property
    def _effective_sample_rate(self) -> int:
        return self.sample_rate or self._init_sample_rate

    def can_generate_metrics(self) -> bool:
        return True

    @traced_tts
    async def run_tts(self, text: str, context_id: str):
        logger.info(f"DashScopeQwenRealtimeTTSService.run_tts CALLED text={text!r} ctx={context_id[:8]}")
        try:
            await self.start_processing_metrics()
            audio = await asyncio.to_thread(self._synthesize, text)
            await self.stop_processing_metrics()
            logger.info(f"DashScopeQwenRealtimeTTSService._synthesize returned audio_len={len(audio) if audio else 0}")
            if audio:
                yield TTSAudioRawFrame(
                    audio=audio,
                    sample_rate=self._effective_sample_rate,
                    num_channels=1,
                    context_id=context_id,
                )
            else:
                logger.warning(f"DashScopeQwenRealtimeTTSService: empty audio for ctx={context_id[:8]}, yielding no frame")
        except Exception as e:
            logger.error(f"DashScopeQwenRealtimeTTSService.run_tts EXCEPTION: {e!r}")
            yield ErrorFrame(error=f"DashScope Qwen realtime TTS error: {e}")

    def _synthesize(self, text: str) -> bytes:
        dashscope.api_key = self._api_key
        collector = _QwenRealtimeCollector()
        realtime = QwenTtsRealtime(model=self._settings.model, callback=collector)
        try:
            realtime.connect()
            realtime.update_session(
                voice=self._settings.voice,
                response_format=QwenRealtimeAudioFormat.PCM_24000HZ_MONO_16BIT,
                sample_rate=self._effective_sample_rate,
                mode=self._settings.mode if is_given(self._settings.mode) else "server_commit",
            )
            realtime.append_text(text)
            realtime.finish()
            # Wait in two stages. First, block until server signals the audio
            # stream is done (``response.audio.done``). Then a short grace
            # period for the socket to close cleanly. Without this split,
            # on_close fires almost immediately after finish() on some
            # server commits and we return before any audio chunks arrive
            # — the user hears silence.
            if not collector.audio_done.wait(timeout=30.0):
                raise TimeoutError("Timed out waiting for Qwen realtime TTS audio")
            # Give the websocket a brief window to deliver any tail chunks.
            collector.session_done.wait(timeout=2.0)
            if collector.error_msg:
                # Surface server-reported error to caller
                code = collector.error_msg.get("error", {}).get("code", "unknown")
                msg = collector.error_msg.get("error", {}).get("message", "")
                raise RuntimeError(f"Qwen realtime TTS error {code}: {msg}")
            return b"".join(collector.audio_chunks)
        finally:
            try:
                realtime.close()
            except Exception:
                pass
