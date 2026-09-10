"""Streaming TTS client — ElevenLabs stream-input WebSocket (eleven_flash_v2_5).

Sequence: prime with {"text":" ","voice_settings":...}, feed chunks with try_trigger_generation,
then send {"text":""} — an EMPTY text is the protocol's end-of-stream marker ({"flush":true} alone
stalls). Audio returns as base64 frames plus {"isFinal":true}.

A REFUSED credential arrives the same way the audio does: the socket accepts the upgrade and the
priming frame, then answers `invalid_api_key` and closes 1008. The verdict is therefore made in
audio_chunks(), not at connect(), and goes through config.key_rejection so both engines classify a
rejection identically — otherwise a permanent 401 wears a transient error's clothes forever."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, AsyncIterator, Awaitable, Callable

import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK

from kotoba.core.voice import config
from kotoba.core.voice.config import VoiceError, VoiceStreamClosed

log = logging.getLogger("kotoba.voice.tts")

RECV_TIMEOUT = 30.0


class TtsClient:
    """One synthesis stream. Feed text incrementally with send_text() (e.g. from the agentic
    loop's token stream) while iterating audio_chunks() concurrently — send and recv are
    independent WebSocket directions, so neither blocks the other."""

    def __init__(
        self,
        voice_id: str | None = None,
        *,
        model_id: str = config.TTS_MODEL_ID,
        voice_settings: dict | None = None,
        output_format: str | None = None,
        inactivity_timeout: int | None = None,
        recv_timeout: float = RECV_TIMEOUT,
        ws_connect: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self.voice_id = voice_id or config.default_voice_id()
        self._url = config.tts_url(
            self.voice_id, model_id=model_id, output_format=output_format,
            inactivity_timeout=inactivity_timeout,
        )
        self._voice_settings = dict(voice_settings or config.DEFAULT_VOICE_SETTINGS)
        self._recv_timeout = recv_timeout
        self._ws_connect = ws_connect or websockets.connect
        self._ws: Any = None
        self._ended = False
        self._final = False

    async def connect(self) -> None:
        headers = config.auth_headers()  # raises VoiceAuthError before any network I/O when keyless
        await self.close()
        self._ended = False
        self._final = False
        try:
            self._ws = await self._ws_connect(self._url, additional_headers=headers)
        except VoiceError:
            raise
        except Exception as exc:
            raise (
                config.handshake_rejection(exc)
                or VoiceError(f"TTS connect failed: {exc.__class__.__name__}: {exc}")
            ) from exc
        # The verified opening frame for this socket.
        await self._send({"text": " ", "voice_settings": self._voice_settings})

    async def send_text(self, text: str) -> None:
        # "" is the protocol's end-of-stream marker — forwarding an empty token would kill the
        # stream mid-utterance, so it is silently skipped here; end() is the only way to finish.
        if not text:
            return
        if self._ended:
            raise VoiceStreamClosed("TTS input already ended (end() was called)")
        await self._send({"text": text, "try_trigger_generation": True})

    async def end(self) -> None:
        """Close the INPUT side; audio_chunks() keeps yielding until the final frame arrives."""
        if self._ended:
            return
        self._ended = True
        await self._send({"text": ""})

    async def audio_chunks(self) -> AsyncIterator[bytes]:
        """Yield decoded audio bytes as they arrive, ending after {"isFinal":true}. A stalled
        stream raises VoiceError after recv_timeout instead of hanging forever."""
        if self._ws is None:
            raise VoiceStreamClosed("TTS stream is not connected")
        while not self._final:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), self._recv_timeout)
            except asyncio.TimeoutError:
                raise VoiceError(f"TTS stream stalled (no frame within {self._recv_timeout}s)")
            except ConnectionClosedOK:
                return
            except ConnectionClosed as exc:
                raise VoiceStreamClosed(f"TTS socket dropped (code={config.ws_close_code(exc)})") from exc
            try:
                msg = json.loads(raw) if isinstance(raw, (str, bytes)) else {}
            except ValueError:
                # A corrupt frame must not escape the VoiceError contract; a truly broken stream
                # still ends at the recv_timeout watchdog.
                log.debug("skipping unparseable TTS frame (%d bytes)", len(raw))
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("error"):
                code, detail = str(msg.get("error")), str(msg.get("message") or "")
                # This is where a refused key actually lands: EL takes the upgrade and the text,
                # then answers in-band and closes 1008. Flattened into a plain VoiceError it read
                # as "her voice cut out", so every later turn opened a segment and bought it again.
                raise config.key_rejection(code, detail) or VoiceError(f"TTS error: {code}: {detail}")
            audio = msg.get("audio")
            if audio:
                yield base64.b64decode(audio)
            if msg.get("isFinal"):
                self._final = True

    async def _send(self, payload: dict) -> None:
        if self._ws is None:
            raise VoiceStreamClosed("TTS stream is not connected")
        try:
            await self._ws.send(json.dumps(payload))
        except ConnectionClosed as exc:
            raise VoiceStreamClosed(f"TTS socket dropped while sending (code={config.ws_close_code(exc)})") from exc

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                log.debug("TTS close raced a dead socket", exc_info=True)

    async def __aenter__(self) -> "TtsClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()


class TagStrippingTts:
    """Fast-engine front: flash models read "[happily]" aloud literally, so every [tag] is scrubbed
    from the text before it reaches the WS stream. Streaming-safe (tags split across chunks)."""

    def __init__(self, inner: TtsClient) -> None:
        from kotoba.core.stream import AudioTagFilter

        self._inner = inner
        self._filter = AudioTagFilter(keep_valid=False)

    async def connect(self) -> None:
        await self._inner.connect()

    async def send_text(self, text: str) -> None:
        out = self._filter.feed(text)
        if out:
            await self._inner.send_text(out)

    async def end(self) -> None:
        rest = self._filter.flush()
        if rest:
            await self._inner.send_text(rest)
        await self._inner.end()

    def audio_chunks(self) -> AsyncIterator[bytes]:
        return self._inner.audio_chunks()

    async def close(self) -> None:
        await self._inner.close()


def create_tts_client(
    voice_id: str | None = None,
    *,
    output_format: str | None = None,
    inactivity_timeout: int | None = None,
    **kwargs: Any,
):
    """Engine dispatch for the local voice path, read live per segment: "expressive" = eleven_v3 REST
    (valid audio tags pass through and are performed), "fast" = flash WS (tags stripped)."""
    if config.tts_engine() == "fast":
        return TagStrippingTts(
            TtsClient(voice_id, output_format=output_format, inactivity_timeout=inactivity_timeout, **kwargs)
        )
    from kotoba.core.voice.tts_rest import ExpressiveTtsClient

    return ExpressiveTtsClient(voice_id, output_format=output_format, **kwargs)


# NO PRODUCTION CALLER: a one-shot convenience wrapper the session does not use (it drives TtsClient
# directly, per segment). Kept as the simple door for a script or a test.
async def synthesize(text: str, *, voice_id: str | None = None, **kwargs: Any) -> AsyncIterator[bytes]:
    """One-shot helper: a complete phrase in, audio chunks out — still streamed, never buffered."""
    client = TtsClient(voice_id=voice_id, **kwargs)
    await client.connect()
    try:
        await client.send_text(text)
        await client.end()
        async for chunk in client.audio_chunks():
            yield chunk
    finally:
        await client.close()
