"""Realtime STT client — ElevenLabs scribe_v2_realtime over an outbound WebSocket.

Connect with commit_strategy=vad so the server auto-commits a transcript on end-of-turn silence;
stream `input_audio_chunk` messages in, `partial_transcript` / `committed_transcript` come back.

`language_code` is echoed back in `session_started.config`, so the connect log is cheap live proof
that EL accepted the pin; `filter_background_audio` is NOT echoed, which is why stt_url refuses to
pair it with include_timestamps instead of hoping. A committed transcript can also carry the
detected `language_code` — dropping it made language detection unable to tell anyone anything."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK

from kotoba.core.voice import config
from kotoba.core.voice.config import VoiceAuthError, VoiceError, VoiceQuotaError, VoiceStreamClosed

log = logging.getLogger("kotoba.voice.stt")

SESSION_START_TIMEOUT = 10.0

# Per-message rejections the session survives; every other EL error message is session-fatal
# (the server closes the socket right after — events() then ends or raises).
_NONFATAL_ERRORS = frozenset({
    "commit_throttled", "insufficient_audio_activity", "input_error", "chunk_size_exceeded",
})


@dataclass(frozen=True)
class SttSessionStarted:
    session_id: str
    config: dict


@dataclass(frozen=True)
class SttPartial:
    text: str


@dataclass(frozen=True)
class SttCommitted:
    text: str
    words: tuple | None = None  # word timings when include_timestamps was requested
    language_code: str | None = None  # detected language when include_language_detection was requested


@dataclass(frozen=True)
class SttError:
    code: str  # EL message_type: auth_error, quota_exceeded, commit_throttled, ...
    message: str
    fatal: bool = True


SttEvent = SttSessionStarted | SttPartial | SttCommitted | SttError


def _parse(raw: str | bytes) -> dict:
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return {}
    return msg if isinstance(msg, dict) else {}


def _to_event(msg: dict) -> SttEvent | None:
    mtype = str(msg.get("message_type") or "")
    if mtype in ("partial_transcript", "partial_transcript_with_timestamps"):
        return SttPartial(text=msg.get("text") or "")
    if mtype in ("committed_transcript", "committed_transcript_with_timestamps"):
        words = msg.get("words")
        lang = msg.get("language_code")
        return SttCommitted(
            text=msg.get("text") or "",
            words=tuple(words) if words else None,
            language_code=str(lang) if lang else None,
        )
    if mtype == "error" or msg.get("error") is not None:
        return SttError(
            code=mtype or "error",
            message=str(msg.get("error") or msg.get("message") or ""),
            fatal=mtype not in _NONFATAL_ERRORS,
        )
    return None  # unknown/informational message types are ignored (forward-compat)


class SttClient:
    """One realtime transcription session. Reconnect-safe: after close() or a drop, calling
    connect() again opens a fresh socket/session on the same client instance."""

    def __init__(
        self,
        *,
        language_code: str | None = None,
        include_timestamps: bool = False,
        include_language_detection: bool = False,
        filter_background_audio: bool = False,
        commit_strategy: str = config.COMMIT_STRATEGY,
        vad_silence_threshold_secs: float = config.VAD_SILENCE_THRESHOLD_SECS,
        vad_threshold: float = config.VAD_THRESHOLD,
        min_speech_duration_ms: int = config.MIN_SPEECH_DURATION_MS,
        min_silence_duration_ms: int = config.MIN_SILENCE_DURATION_MS,
        keyterms: list[str] | None = None,
        sample_rate: int = config.SAMPLE_RATE,
        ws_connect: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self._url = config.stt_url(
            commit_strategy=commit_strategy,
            language_code=language_code,
            include_timestamps=include_timestamps,
            include_language_detection=include_language_detection,
            filter_background_audio=filter_background_audio,
            vad_silence_threshold_secs=vad_silence_threshold_secs,
            vad_threshold=vad_threshold,
            min_speech_duration_ms=min_speech_duration_ms,
            min_silence_duration_ms=min_silence_duration_ms,
            keyterms=keyterms,
        )
        self._ws_connect = ws_connect or websockets.connect
        self._ws: Any = None
        self.session: SttSessionStarted | None = None

    async def connect(self) -> SttSessionStarted:
        # Close any previous socket BEFORE the key check: a keyless raise must not leave a live
        # session behind that send_audio would keep feeding. Still no dial toward EL when keyless.
        await self.close()
        headers = config.auth_headers()  # raises VoiceAuthError before any network I/O when keyless
        try:
            self._ws = await self._ws_connect(self._url, additional_headers=headers)
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceError(f"STT connect failed: {exc.__class__.__name__}: {exc}") from exc
        try:
            raw = await asyncio.wait_for(self._ws.recv(), SESSION_START_TIMEOUT)
        except asyncio.TimeoutError:
            await self.close()
            raise VoiceError(f"STT session_started not received within {SESSION_START_TIMEOUT}s")
        except ConnectionClosed as exc:
            await self.close()
            raise VoiceStreamClosed(f"STT socket closed during handshake (code={config.ws_close_code(exc)})") from exc
        msg = _parse(raw)
        if msg.get("message_type") != "session_started":
            await self.close()
            code = str(msg.get("message_type") or "unknown")
            detail = str(msg.get("error") or "")
            if code == "quota_exceeded":   # not a rejected key — see VoiceQuotaError
                raise VoiceQuotaError(f"ElevenLabs quota exhausted: {detail}")
            if code == "auth_error":
                raise VoiceAuthError(f"ElevenLabs rejected the API key: {detail}")
            raise VoiceError(f"STT session failed to start: {code}: {detail}")
        self.session = SttSessionStarted(
            session_id=str(msg.get("session_id") or ""), config=msg.get("config") or {}
        )
        log.debug("STT session started: %s config=%s", self.session.session_id, self.session.config)
        return self.session

    async def send_audio(self, pcm: bytes, *, commit: bool = False) -> None:
        if self._ws is None:
            raise VoiceStreamClosed("STT session is not connected")
        payload: dict = {
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(pcm).decode("ascii"),
            "sample_rate": self.sample_rate,
        }
        if commit:
            payload["commit"] = True
        try:
            await self._ws.send(json.dumps(payload))
        except ConnectionClosed as exc:
            if self._ws is None:
                return          # we closed it; a clean 1000 is not a fault
            raise VoiceStreamClosed(f"STT socket dropped while sending (code={config.ws_close_code(exc)})") from exc

    async def commit(self) -> None:
        """Manual commit escape hatch — VAD mode normally commits on silence by itself."""
        await self.send_audio(b"", commit=True)

    async def events(self) -> AsyncIterator[SttEvent]:
        """Yield transcript events until the session ends. A clean server close ends iteration;
        an abnormal drop raises VoiceStreamClosed."""
        while True:
            # Re-read each iteration: close() nulls _ws, and a consumer parked between events must
            # see the declared VoiceStreamClosed, not an AttributeError off a vanished socket.
            ws = self._ws
            if ws is None:
                raise VoiceStreamClosed("STT session is not connected")
            try:
                raw = await ws.recv()
            except ConnectionClosedOK:
                return
            except ConnectionClosed as exc:
                if self._ws is None:
                    return      # we closed it; a consumer parked in recv() sees a clean 1000
                raise VoiceStreamClosed(f"STT socket dropped (code={config.ws_close_code(exc)})") from exc
            event = _to_event(_parse(raw))
            if event is not None:
                yield event

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        self.session = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                log.debug("STT close raced a dead socket", exc_info=True)

    async def __aenter__(self) -> "SttClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
