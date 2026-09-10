"""A key ElevenLabs refused must not come back as the transient "her voice couldn't start".

The TTS socket accepts the handshake, takes the priming frame and the text, and only then
sends an in-band `invalid_api_key` error before closing with 1008 — so `audio_chunks()` must
read the rejection out of the frame, not the handshake status.

Unclassified, `_tts_failed` never latches and the next turn dials ElevenLabs again for the
same 401. Pins both paths: the in-band error (what actually happens) and a 401 handshake
(defensive), with unrelated codes staying transient either way.
"""
from __future__ import annotations

import asyncio
import base64
import json

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

import kotoba.core.voice.session as vs
from kotoba.core.voice import config, tts as tts_mod
from kotoba.core.voice.config import VoiceAuthError, VoiceError, VoiceQuotaError
from kotoba.core.voice.tts import TtsClient

# Byte-for-byte what ElevenLabs sent the probe on a refused key.
EL_INVALID_KEY = json.dumps({"message": "Invalid API key", "error": "invalid_api_key", "code": 1008})
EL_NO_AUTH = json.dumps({
    "message": "None of the authentication methods (xi-api-key, authorization header, single-use "
               "token) were found.",
    "error": "authentication_required",
    "code": 1008,
})
EL_QUOTA = json.dumps({"message": "Quota exceeded", "error": "quota_exceeded", "code": 1008})
EL_OTHER = json.dumps({"message": "voice_settings must not change", "error": "invalid_input",
                       "code": 1008})


class FakeWS:
    def __init__(self, incoming: list) -> None:
        self.sent: list[str] = []
        self.closed = False
        self._incoming: asyncio.Queue = asyncio.Queue()
        for item in incoming:
            self._incoming.put_nowait(item)

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def recv(self):
        item = await self._incoming.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


def rejecting(status: int, body: bytes):
    async def _connect(url: str, **kwargs):
        raise InvalidStatus(Response(status, "Rejected", Headers(), body))

    return _connect


def serving(ws: FakeWS):
    async def _connect(url: str, **kwargs):
        return ws

    return _connect


@pytest.fixture(autouse=True)
def _test_key(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el_test_key_xyz")
    config.set_api_key(None)


async def _drain(frame: str):
    ws = FakeWS([frame])
    client = TtsClient(ws_connect=serving(ws))
    await client.connect()
    async for _ in client.audio_chunks():
        pass


def test_a_refused_key_arrives_as_an_auth_error_not_a_blip():
    with pytest.raises(VoiceAuthError) as caught:
        asyncio.run(_drain(EL_INVALID_KEY))
    assert "Invalid API key" in str(caught.value)


def test_a_missing_credential_is_the_same_verdict():
    with pytest.raises(VoiceAuthError):
        asyncio.run(_drain(EL_NO_AUTH))


def test_an_exhausted_quota_keeps_its_own_class():
    with pytest.raises(VoiceQuotaError):
        asyncio.run(_drain(EL_QUOTA))


def test_an_unrelated_stream_error_stays_transient():
    """A code we have no evidence about must NOT mute her for the rest of the call."""
    with pytest.raises(VoiceError) as caught:
        asyncio.run(_drain(EL_OTHER))
    assert not isinstance(caught.value, VoiceAuthError)


def test_a_401_handshake_is_an_auth_error_too():
    client = TtsClient(ws_connect=rejecting(401, b'{"detail":{"status":"invalid_api_key"}}'))
    with pytest.raises(VoiceAuthError):
        asyncio.run(client.connect())


def test_a_401_handshake_naming_the_quota_is_a_quota_error():
    client = TtsClient(ws_connect=rejecting(401, b'{"detail":{"status":"quota_exceeded"}}'))
    with pytest.raises(VoiceQuotaError):
        asyncio.run(client.connect())


def test_a_503_handshake_stays_transient():
    """websockets' own process_exception calls 5xx retryable; so do we."""
    client = TtsClient(ws_connect=rejecting(503, b"upstream down"))
    with pytest.raises(VoiceError) as caught:
        asyncio.run(client.connect())
    assert not isinstance(caught.value, VoiceAuthError)


# ── The whole point: the session stops re-dialling ────────────────────────────────────────────

class BrowserWS:
    def __init__(self):
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.frames: list = []

    async def accept(self):
        pass

    async def receive(self):
        return await self.incoming.get()

    async def send_text(self, t):
        self.frames.append(json.loads(t))

    async def send_bytes(self, b):
        pass

    def errors(self):
        return [(e["code"], e["fatal"]) for e in self.frames if e["type"] == "error"]


class FakeDB:
    async def ensure_session(self, sid):
        pass

    async def insert_turn(self, *a, **k):
        pass

    async def fetch_soul_config(self):
        return None


def test_the_session_stops_dialling_elevenlabs_after_the_key_is_refused(monkeypatch):
    """Two turns on the `fast` engine. Before the fix: two handshakes, two `tts_stream` blips,
    `_tts_failed` still False. After: one handshake, one fatal `tts_auth`, voice latched off."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el_test_key_xyz")
    monkeypatch.setenv("KOTOBA_TTS_ENGINE", "fast")
    config.set_api_key(None)

    dials: list[str] = []

    async def _connect(url: str, **kwargs):
        dials.append(url)
        return FakeWS([EL_INVALID_KEY])

    monkeypatch.setattr(tts_mod.websockets, "connect", _connect)

    async def fake_load_context(request, db, session_id, **kw):
        return [{"role": "user", "content": request.messages[-1]["content"]}]

    async def no_memory(user_text, db):
        return None

    async def loop(items, sid, db, queue, patterns, **kw):
        await queue.put("Hola, buenas tardes. ")
        await asyncio.sleep(0.1)
        return "Hola, buenas tardes."

    monkeypatch.setattr(vs, "load_context", fake_load_context)
    monkeypatch.setattr(vs, "extract_and_save_memory", no_memory)
    monkeypatch.setattr(vs, "agentic_loop", loop)

    async def drive():
        ws = BrowserWS()
        session = vs.VoiceSession(ws, "sid-auth", db=FakeDB(), soul_patterns={})
        runner = asyncio.create_task(session.run())
        for text in ("primera", "segunda"):
            ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": text})})
            for _ in range(200):
                if sum(1 for f in ws.frames if f["type"] == "turn_end") >= (1 if text == "primera" else 2):
                    break
                await asyncio.sleep(0.02)
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)
        return ws, session

    ws, session = asyncio.run(drive())

    assert session._tts_failed is True, "a refused key must latch the voice off for the session"
    assert ("tts_auth", True) in ws.errors(), f"expected a fatal tts_auth, got {ws.errors()}"
    assert len(dials) == 1, f"re-dialled ElevenLabs {len(dials)} times on a permanently refused key"


def test_the_audio_path_is_unchanged_by_the_new_branch():
    """The mapping must not disturb an ordinary stream."""
    audio = base64.b64encode(b"pcm").decode()
    frames = [json.dumps({"audio": audio}), json.dumps({"isFinal": True})]

    async def go():
        ws = FakeWS(frames)
        client = TtsClient(ws_connect=serving(ws))
        await client.connect()
        return [c async for c in client.audio_chunks()]

    assert asyncio.run(go()) == [b"pcm"]
