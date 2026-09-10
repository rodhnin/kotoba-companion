"""Outbound voice layer — realtime STT client against a fake WebSocket (no network).

Covers: VAD commit strategy in the connect URL, session start, partial vs committed transcript
events, the VAD auto-commit path (committed WITHOUT a client commit), manual commit framing,
error events, dropped-socket handling, reconnect, and key-leakage guarantees.
"""
from __future__ import annotations

import asyncio
import base64
import json

import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

from kotoba.core.voice import config
from kotoba.core.voice.config import VoiceAuthError, VoiceError, VoiceStreamClosed
from kotoba.core.voice.stt import SttClient, SttCommitted, SttError, SttPartial

SESSION_STARTED = json.dumps({
    "message_type": "session_started",
    "session_id": "sess_123",
    "config": {"sample_rate": 16000, "audio_format": "pcm_16000", "commit_strategy": "vad"},
})


def closed_ok() -> ConnectionClosedOK:
    return ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True)


def closed_err() -> ConnectionClosedError:
    return ConnectionClosedError(Close(1006, "abnormal"), None)


class FakeWS:
    """Minimal stand-in for a websockets connection: queued incoming frames (an Exception
    entry is raised from recv), and every outgoing frame recorded in .sent."""

    def __init__(self, incoming: list) -> None:
        self.sent: list[str] = []
        self.closed = False
        self._incoming: asyncio.Queue = asyncio.Queue()
        for item in incoming:
            self._incoming.put_nowait(item)

    async def send(self, data: str) -> None:
        if self.closed:
            raise closed_err()
        self.sent.append(data)

    async def recv(self):
        item = await self._incoming.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


def fake_connect(*ws_sequence: FakeWS):
    """A ws_connect stub handing out the given sockets in order; records each call's url/kwargs."""
    calls: list[dict] = []

    async def _connect(url: str, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return ws_sequence[len(calls) - 1]

    return _connect, calls


@pytest.fixture(autouse=True)
def _test_key(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el_test_key_xyz")
    config.set_api_key(None)


def test_stt_url_enables_vad_commit_strategy():
    url = config.stt_url()
    assert "commit_strategy=vad" in url
    assert "model_id=scribe_v2_realtime" in url
    assert "vad_silence_threshold_secs=1.5" in url and "vad_threshold=0.4" in url
    manual = config.stt_url(commit_strategy="manual")
    assert "commit_strategy=manual" in manual and "vad_threshold" not in manual


def test_background_filter_and_language_ride_the_query_string(monkeypatch):
    """ElevenLabs' own noise defence, off until the audit: an idle room committed its own noise and
    every commit bought a full agentic turn. EL refuses the filter alongside include_timestamps, so
    asking for both must drop the filter rather than send a request the server rejects."""
    url = config.stt_url(filter_background_audio=True, language_code="es")
    assert "filter_background_audio=true" in url and "language_code=es" in url
    assert "filter_background_audio" not in config.stt_url(
        filter_background_audio=True, include_timestamps=True
    )
    assert "filter_background_audio" not in config.stt_url()
    assert "language_code" not in config.stt_url()

    monkeypatch.setenv("KOTOBA_STT_LANGUAGE", "ES")
    assert config.stt_language() == "es"
    monkeypatch.setenv("KOTOBA_STT_LANGUAGE", "espanol")  # junk is refused, never sent
    assert config.stt_language() == ""
    monkeypatch.delenv("KOTOBA_STT_LANGUAGE")
    assert config.stt_filter_background_audio() is True
    monkeypatch.setenv("KOTOBA_STT_FILTER_BACKGROUND", "0")
    assert config.stt_filter_background_audio() is False


def test_detected_language_survives_to_the_caller():
    """_to_event kept only text/words, so include_language_detection could never have told anyone
    anything — the signal was discarded before it reached a decision."""
    ws = FakeWS([
        SESSION_STARTED,
        json.dumps({"message_type": "committed_transcript", "text": "嗯", "language_code": "zho"}),
        closed_ok(),
    ])
    connect, _ = fake_connect(ws)

    async def go():
        client = SttClient(ws_connect=connect, include_language_detection=True)
        await client.connect()
        return [e async for e in client.events()]

    events = asyncio.run(go())
    assert events[0].language_code == "zho"
    assert SttCommitted(text="listo") == SttCommitted(text="listo"), "the new field keeps equality"


def test_connect_waits_for_session_started_and_authenticates():
    ws = FakeWS([SESSION_STARTED])
    connect, calls = fake_connect(ws)

    async def go():
        client = SttClient(ws_connect=connect)
        return await client.connect()

    session = asyncio.run(go())
    assert session.session_id == "sess_123"
    assert session.config["commit_strategy"] == "vad"
    assert calls[0]["kwargs"]["additional_headers"] == {"xi-api-key": "el_test_key_xyz"}


def test_connect_without_key_raises_auth_error_before_network(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    config.set_api_key(None)
    connect, calls = fake_connect()

    async def go():
        await SttClient(ws_connect=connect).connect()

    with pytest.raises(VoiceAuthError):
        asyncio.run(go())
    assert calls == []  # never even dialed


def test_connect_surfaces_server_auth_error():
    ws = FakeWS([json.dumps({"message_type": "auth_error", "error": "invalid api key"})])
    connect, _ = fake_connect(ws)

    async def go():
        await SttClient(ws_connect=connect).connect()

    with pytest.raises(VoiceAuthError):
        asyncio.run(go())


def test_audio_chunk_framing_and_manual_commit():
    ws = FakeWS([SESSION_STARTED])
    connect, _ = fake_connect(ws)
    pcm = b"\x01\x02\x03\x04"

    async def go():
        client = SttClient(ws_connect=connect)
        await client.connect()
        await client.send_audio(pcm)
        await client.commit()
        return client

    asyncio.run(go())
    chunk = json.loads(ws.sent[0])
    assert chunk["message_type"] == "input_audio_chunk"
    assert chunk["audio_base_64"] == base64.b64encode(pcm).decode("ascii")
    assert chunk["sample_rate"] == 16000
    assert "commit" not in chunk  # plain streaming chunk
    commit = json.loads(ws.sent[1])
    assert commit["commit"] is True and commit["audio_base_64"] == ""
    # The API key must never appear in any outbound frame.
    assert all("el_test_key_xyz" not in frame for frame in ws.sent)


def test_partial_vs_committed_events():
    ws = FakeWS([
        SESSION_STARTED,
        json.dumps({"message_type": "partial_transcript", "text": "hol"}),
        json.dumps({"message_type": "partial_transcript", "text": "hola ko"}),
        json.dumps({"message_type": "committed_transcript_with_timestamps",
                    "text": "hola kotoba", "words": [{"text": "hola", "start": 0.1, "end": 0.4}]}),
        closed_ok(),
    ])
    connect, _ = fake_connect(ws)

    async def go():
        client = SttClient(ws_connect=connect)
        await client.connect()
        return [e async for e in client.events()]

    events = asyncio.run(go())
    assert events[0] == SttPartial(text="hol")
    assert events[1] == SttPartial(text="hola ko")
    assert isinstance(events[2], SttCommitted)
    assert events[2].text == "hola kotoba" and events[2].words[0]["text"] == "hola"


def test_vad_commit_arrives_without_client_commit():
    """The VAD path: the server commits on silence — the client never sends commit:true."""
    ws = FakeWS([
        SESSION_STARTED,
        json.dumps({"message_type": "partial_transcript", "text": "listo"}),
        json.dumps({"message_type": "committed_transcript", "text": "listo"}),
        closed_ok(),
    ])
    connect, _ = fake_connect(ws)

    async def go():
        client = SttClient(ws_connect=connect)
        await client.connect()
        await client.send_audio(b"\x00" * 320)
        return [e async for e in client.events()]

    events = asyncio.run(go())
    assert SttCommitted(text="listo") in events
    assert all('"commit"' not in frame for frame in ws.sent)


def test_error_message_yields_stt_error_event():
    ws = FakeWS([
        SESSION_STARTED,
        json.dumps({"message_type": "commit_throttled", "error": "too many commits"}),
        json.dumps({"message_type": "quota_exceeded", "error": "quota reached"}),
        closed_ok(),
    ])
    connect, _ = fake_connect(ws)

    async def go():
        client = SttClient(ws_connect=connect)
        await client.connect()
        return [e async for e in client.events()]

    events = asyncio.run(go())
    assert events[0] == SttError(code="commit_throttled", message="too many commits", fatal=False)
    assert events[1] == SttError(code="quota_exceeded", message="quota reached", fatal=True)


def test_dropped_socket_raises_voice_stream_closed():
    ws = FakeWS([SESSION_STARTED, closed_err()])
    connect, _ = fake_connect(ws)

    async def go():
        client = SttClient(ws_connect=connect)
        await client.connect()
        return [e async for e in client.events()]

    with pytest.raises(VoiceStreamClosed):
        asyncio.run(go())


def test_send_after_close_raises_cleanly():
    ws = FakeWS([SESSION_STARTED])
    connect, _ = fake_connect(ws)

    async def go():
        client = SttClient(ws_connect=connect)
        await client.connect()
        await client.close()
        await client.send_audio(b"\x00")

    with pytest.raises(VoiceStreamClosed):
        asyncio.run(go())


def test_reconnect_after_close_opens_fresh_session():
    ws1, ws2 = FakeWS([SESSION_STARTED]), FakeWS([json.dumps({
        "message_type": "session_started", "session_id": "sess_456", "config": {},
    })])
    connect, calls = fake_connect(ws1, ws2)

    async def go():
        client = SttClient(ws_connect=connect)
        first = await client.connect()
        await client.close()
        second = await client.connect()
        return first, second

    first, second = asyncio.run(go())
    assert first.session_id == "sess_123" and second.session_id == "sess_456"
    assert len(calls) == 2 and ws1.closed


def test_keyless_reconnect_closes_the_previous_socket(monkeypatch):
    """connect() on a live client must not leave the old session behind when the key check raises:
    a stale _ws would let send_audio keep streaming to a session the caller believes is gone."""
    ws = FakeWS([SESSION_STARTED])
    connect, _ = fake_connect(ws)

    async def go():
        client = SttClient(ws_connect=connect)
        await client.connect()
        monkeypatch.delenv("ELEVENLABS_API_KEY")
        config.set_api_key(None)
        with pytest.raises(VoiceAuthError):
            await client.connect()
        assert ws.closed
        with pytest.raises(VoiceStreamClosed):
            await client.send_audio(b"\x00")

    asyncio.run(go())


def test_close_between_events_raises_the_declared_error():
    """A consumer parked between events when close() lands must see VoiceStreamClosed — never an
    AttributeError off the nulled socket."""
    ws = FakeWS([SESSION_STARTED, json.dumps({"message_type": "partial_transcript", "text": "hola"})])
    connect, _ = fake_connect(ws)

    async def go():
        client = SttClient(ws_connect=connect)
        await client.connect()
        it = client.events()
        assert isinstance(await anext(it), SttPartial)
        await client.close()
        with pytest.raises(VoiceStreamClosed):
            await anext(it)

    asyncio.run(go())


def test_handshake_non_session_message_is_clean_error():
    ws = FakeWS([json.dumps({"message_type": "unheard_of", "error": "who knows"})])
    connect, _ = fake_connect(ws)

    async def go():
        await SttClient(ws_connect=connect).connect()

    with pytest.raises(VoiceError):
        asyncio.run(go())


def test_a_quota_opener_is_a_quota_error_not_a_generic_one():
    """An exhausted quota answers the handshake with its own message_type, and only `auth_error` was
    read. Everything else fell to a bare `VoiceError` → `stt_connect` → "Check your connection and API
    key" — so the one person whose key is fine is the one told to go and look at it. `VoiceQuotaError`
    exists for exactly this ("distinguished by detail.status == quota_exceeded … so it does not
    masquerade as a rejected key") and the TTS half already raises it; this half did not, and
    `SttError.code`'s own comment names `quota_exceeded` as an STT message_type."""
    from kotoba.core.voice.config import VoiceQuotaError

    ws = FakeWS([json.dumps({"message_type": "quota_exceeded", "error": "no quota"})])
    connect, _ = fake_connect(ws)

    async def go():
        await SttClient(ws_connect=connect).connect()

    with pytest.raises(VoiceQuotaError):
        asyncio.run(go())
