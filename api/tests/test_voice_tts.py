"""Outbound voice layer — streaming TTS client against a fake WebSocket (no network).

Covers: the priming frame, incremental text feeding, the {"text":""} end-of-stream quirk
(empty tokens must never be forwarded), audio chunk decoding, isFinal termination, stall
timeout, dropped-socket handling, the synthesize() helper, and voice_id resolution.
"""
from __future__ import annotations

import asyncio
import base64
import json

import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

from kotoba.core.voice import config
from kotoba.core.voice.config import VoiceError, VoiceStreamClosed
from kotoba.core.voice.tts import TtsClient, synthesize

AUDIO_1 = base64.b64encode(b"fake-mp3-bytes-one").decode()
AUDIO_2 = base64.b64encode(b"fake-mp3-bytes-two").decode()


def closed_ok() -> ConnectionClosedOK:
    return ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True)


def closed_err() -> ConnectionClosedError:
    return ConnectionClosedError(Close(1006, "abnormal"), None)


class FakeWS:
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


def fake_connect(ws: FakeWS):
    calls: list[dict] = []

    async def _connect(url: str, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return ws

    return _connect, calls


@pytest.fixture(autouse=True)
def _test_key(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el_test_key_xyz")
    config.set_api_key(None)


def test_tts_url_shape_and_voice_id_quoting():
    url = config.tts_url("VoiceABC123")
    assert url.startswith("wss://api.elevenlabs.io/v1/text-to-speech/VoiceABC123/stream-input?")
    assert "model_id=eleven_flash_v2_5" in url
    assert "output_format" not in url
    pcm = config.tts_url("v/1", output_format="pcm_16000", inactivity_timeout=180)
    assert "/v%2F1/" in pcm and "output_format=pcm_16000" in pcm and "inactivity_timeout=180" in pcm


def test_default_voice_id_resolution(monkeypatch):
    monkeypatch.delenv("KOTOBA_VOICE_ID", raising=False)
    assert config.default_voice_id({"voice_id": "soul_voice"}) == "soul_voice"
    assert config.default_voice_id({"voice_id": ""}) == config.FALLBACK_VOICE_ID
    monkeypatch.setenv("KOTOBA_VOICE_ID", "env_voice")
    assert config.default_voice_id() == "env_voice"
    assert config.default_voice_id({"voice_id": "soul_voice"}) == "soul_voice"


def test_connect_sends_priming_frame_with_voice_settings():
    ws = FakeWS([])
    connect, calls = fake_connect(ws)

    async def go():
        client = TtsClient("v1", ws_connect=connect)
        await client.connect()

    asyncio.run(go())
    prime = json.loads(ws.sent[0])
    assert prime == {"text": " ", "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}}
    assert calls[0]["kwargs"]["additional_headers"] == {"xi-api-key": "el_test_key_xyz"}


def test_send_text_feeds_incrementally_and_skips_empty():
    ws = FakeWS([])
    connect, _ = fake_connect(ws)

    async def go():
        client = TtsClient("v1", ws_connect=connect)
        await client.connect()
        await client.send_text("Hola, ")
        await client.send_text("")  # empty = protocol EOS — must NOT be forwarded
        await client.send_text("soy Kotoba.")
        await client.end()
        await client.end()  # idempotent

    asyncio.run(go())
    frames = [json.loads(f) for f in ws.sent]
    assert frames[1] == {"text": "Hola, ", "try_trigger_generation": True}
    assert frames[2] == {"text": "soy Kotoba.", "try_trigger_generation": True}
    assert frames[3] == {"text": ""}  # end-of-stream, exactly once
    assert len(frames) == 4
    assert all("el_test_key_xyz" not in f for f in ws.sent)


def test_send_text_after_end_raises():
    ws = FakeWS([])
    connect, _ = fake_connect(ws)

    async def go():
        client = TtsClient("v1", ws_connect=connect)
        await client.connect()
        await client.end()
        await client.send_text("late")

    with pytest.raises(VoiceStreamClosed):
        asyncio.run(go())


def test_audio_chunks_stream_until_is_final():
    ws = FakeWS([
        json.dumps({"audio": AUDIO_1, "isFinal": None}),
        json.dumps({"audio": AUDIO_2}),
        json.dumps({"audio": None, "isFinal": True}),
    ])
    connect, _ = fake_connect(ws)

    async def go():
        client = TtsClient("v1", ws_connect=connect)
        await client.connect()
        return [c async for c in client.audio_chunks()]

    chunks = asyncio.run(go())
    assert chunks == [b"fake-mp3-bytes-one", b"fake-mp3-bytes-two"]


def test_final_frame_with_audio_yields_then_stops():
    ws = FakeWS([json.dumps({"audio": AUDIO_1, "isFinal": True})])
    connect, _ = fake_connect(ws)

    async def go():
        client = TtsClient("v1", ws_connect=connect)
        await client.connect()
        return [c async for c in client.audio_chunks()]

    assert asyncio.run(go()) == [b"fake-mp3-bytes-one"]


def test_error_frame_raises_voice_error():
    """An error frame ends the stream. This one carries a code we have no verdict about, so it stays
    the plain transient VoiceError; the codes that mean the KEY was refused are classified instead."""
    ws = FakeWS([json.dumps({"error": "invalid_input", "message": "voice_settings changed"})])
    connect, _ = fake_connect(ws)

    async def go():
        client = TtsClient("v1", ws_connect=connect)
        await client.connect()
        return [c async for c in client.audio_chunks()]

    with pytest.raises(VoiceError, match="invalid_input") as caught:
        asyncio.run(go())
    assert type(caught.value) is VoiceError


def test_stalled_stream_raises_instead_of_hanging():
    ws = FakeWS([])  # no frames ever arrive
    connect, _ = fake_connect(ws)

    async def go():
        client = TtsClient("v1", recv_timeout=0.05, ws_connect=connect)
        await client.connect()
        return [c async for c in client.audio_chunks()]

    with pytest.raises(VoiceError, match="stalled"):
        asyncio.run(go())


def test_dropped_socket_raises_voice_stream_closed():
    ws = FakeWS([json.dumps({"audio": AUDIO_1}), closed_err()])
    connect, _ = fake_connect(ws)

    async def go():
        client = TtsClient("v1", ws_connect=connect)
        await client.connect()
        return [c async for c in client.audio_chunks()]

    with pytest.raises(VoiceStreamClosed):
        asyncio.run(go())


def test_clean_server_close_ends_iteration():
    ws = FakeWS([json.dumps({"audio": AUDIO_1}), closed_ok()])
    connect, _ = fake_connect(ws)

    async def go():
        client = TtsClient("v1", ws_connect=connect)
        await client.connect()
        return [c async for c in client.audio_chunks()]

    assert asyncio.run(go()) == [b"fake-mp3-bytes-one"]


def test_synthesize_helper_round_trip():
    ws = FakeWS([
        json.dumps({"audio": AUDIO_1}),
        json.dumps({"isFinal": True}),
    ])
    connect, _ = fake_connect(ws)

    async def go():
        return [c async for c in synthesize("Hola Kotoba", voice_id="v1", ws_connect=connect)]

    chunks = asyncio.run(go())
    assert chunks == [b"fake-mp3-bytes-one"]
    frames = [json.loads(f) for f in ws.sent]
    assert frames[0]["text"] == " "  # prime
    assert frames[1] == {"text": "Hola Kotoba", "try_trigger_generation": True}
    assert frames[2] == {"text": ""}  # EOS
    assert ws.closed


def test_a_corrupt_frame_is_skipped_not_escaped():
    """One unparseable frame must not leak a JSONDecodeError past the VoiceError contract — the
    stream keeps going and the final frame still ends it."""
    ws = FakeWS(['{"audio":"cGNt"}', "NOT JSON {{{", '{"audio":"cGNt"}', '{"isFinal":true}'])
    connect, _calls = fake_connect(ws)

    async def go():
        client = TtsClient("v", ws_connect=connect)
        await client.connect()
        await client.send_text("hola")
        await client.end()
        return [c async for c in client.audio_chunks()]

    assert asyncio.run(go()) == [b"pcm", b"pcm"]
