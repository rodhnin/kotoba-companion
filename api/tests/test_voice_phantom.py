"""Phantom-commit safety net — commits arriving while she is audibly streaming audio.

Every connection opens with a `ready` frame, so the first `ws.receive()` of a socket test is that
frame. ElevenLabs STT hallucinates short commits ("Yes.") on silence or echo and re-commits fragments
of the utterance it just committed; each used to start a turn that superseded (killed) hers
mid-speech. Covers: the _is_phantom_commit heuristic, phantom and straggler commits dropped during
playback, a substantial commit still superseding, and short commits honored whenever audio is NOT
active. No network.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import kotoba.core.voice.session as vs
import kotoba.server as main
from kotoba.core.voice.stt import SttCommitted, SttSessionStarted

COMMIT_MAGIC = b"__COMMIT__"


class FakeStt:
    """Minimal SttClient stand-in: a mic frame prefixed with __COMMIT__ emits that text as a committed
    transcript (like an EL VAD end-of-turn), so tests can land commits at exact moments of a turn."""

    instances: list["FakeStt"] = []
    script: list[str] = []

    def __init__(self, **kwargs) -> None:
        self._events: asyncio.Queue = asyncio.Queue()
        FakeStt.instances.append(self)

    async def connect(self) -> SttSessionStarted:
        return SttSessionStarted(session_id="fake", config={})

    async def send_audio(self, pcm: bytes, *, commit: bool = False) -> None:
        if pcm.startswith(COMMIT_MAGIC):
            await self._events.put(SttCommitted(text=pcm[len(COMMIT_MAGIC):].decode()))

    async def commit(self) -> None:
        text = FakeStt.script.pop(0) if FakeStt.script else "hello"
        await self._events.put(SttCommitted(text=text))

    async def events(self):
        while True:
            yield await self._events.get()

    async def close(self) -> None:
        pass


class FakeTts:
    instances: list["FakeTts"] = []

    def __init__(self, voice_id=None, **kwargs) -> None:
        self.text: list[str] = []
        self.ended = False
        FakeTts.instances.append(self)

    async def connect(self) -> None:
        pass

    async def send_text(self, text: str) -> None:
        if text:
            self.text.append(text)

    async def end(self) -> None:
        self.ended = True

    async def audio_chunks(self):
        while not self.ended:
            await asyncio.sleep(0.005)
        yield b"\x01\x02"

    async def close(self) -> None:
        pass


@pytest.fixture
def voice_env(monkeypatch):
    FakeStt.instances, FakeStt.script = [], []
    FakeTts.instances = []
    monkeypatch.setattr(vs, "SttClient", FakeStt)
    monkeypatch.setattr(vs, "TtsClient", FakeTts)

    async def no_memory(user_text, db):
        return None

    monkeypatch.setattr(vs, "extract_and_save_memory", no_memory)
    yield


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def recv_until(ws, stop_type: str, limit: int = 60):
    events = []
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("bytes") is not None:
            continue
        ev = json.loads(msg["text"])
        events.append(ev)
        if ev["type"] == stop_type:
            return events
    raise AssertionError(f"no {stop_type!r} within {limit} frames: {[e['type'] for e in events]}")


def wait_for(ws, stop_type: str) -> None:
    for _ in range(60):
        msg = ws.receive()
        if msg.get("bytes") is None and json.loads(msg["text"])["type"] == stop_type:
            return
    raise AssertionError(f"no {stop_type!r}")


# ---- the heuristic itself ----------------------------------------------------------------------


def make_session() -> vs.VoiceSession:
    return vs.VoiceSession(ws=None, session_id="unit", db=None, soul_patterns={})


def test_trivial_commit_is_phantom_only_while_audio_active():
    s = make_session()
    assert s._is_phantom_commit("Yes.") is False  # idle: even trivial text starts a turn
    s._audio_active = True
    assert s._is_phantom_commit("Yes.") is True
    assert s._is_phantom_commit("¿Qué hora es?") is True  # unicode letters counted, punctuation not
    assert s._is_phantom_commit("wait, that is not what I meant at all") is False


def test_straggler_fragment_of_last_commit_is_phantom():
    s = make_session()
    s._audio_active = True
    s._last_commit_text = "Tell me a long story about dragons, please!"
    assert s._is_phantom_commit("tell me a long story about dragons please") is True
    assert s._is_phantom_commit("long story about dragons") is True
    assert s._is_phantom_commit("actually make it about castles instead") is False


# ---- through the socket --------------------------------------------------------------------------


def test_phantom_commit_ignored_but_substantial_commit_supersedes(client, monkeypatch, voice_env):
    """A hallucinated "Yes." landing mid-playback must reach neither the transcript nor a turn, while a
    real interruption behind it still supersedes."""
    state = {"calls": 0, "cancelled": False}

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        state["calls"] += 1
        if state["calls"] == 1:
            await queue.put("Let me tell you all about it... ")
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise
        await queue.put("New answer.")
        return "New answer."

    monkeypatch.setattr(vs, "agentic_loop", fake_loop)
    with client.websocket_connect("/api/voice/vph-mixed") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "text", "text": "slow question"}))
        wait_for(ws, "audio_start")   # from here on she is audibly streaming
        ws.send_bytes(COMMIT_MAGIC + "Yes.".encode())
        ws.send_bytes(COMMIT_MAGIC + "wait stop, I need something else entirely".encode())
        events = recv_until(ws, "turn_end")

    committed = [e["text"] for e in events if e["type"] == "committed"]
    assert "Yes." not in committed
    assert committed == ["wait stop, I need something else entirely"]
    assert state["cancelled"] is True and state["calls"] == 2
    assert any(e["type"] == "interrupted" for e in events)
    assert next(e for e in events if e["type"] == "turn_end")["turn"] == 2


def test_straggler_duplicate_commit_does_not_kill_turn(client, monkeypatch, voice_env):
    """A re-commit of the utterance that started the turn is a straggler, not a second request."""
    state = {"calls": 0, "cancelled": False}

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        state["calls"] += 1
        await queue.put("Once upon a time... ")
        try:
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        return "Once upon a time..."

    monkeypatch.setattr(vs, "agentic_loop", fake_loop)
    FakeStt.script = ["tell me a long story about dragons please"]
    with client.websocket_connect("/api/voice/vph-straggler") as ws:
        ws.receive()
        ws.send_bytes(b"\x00\x01")  # opens the lazy STT session
        ws.send_text(json.dumps({"type": "commit"}))
        wait_for(ws, "audio_start")
        ws.send_bytes(COMMIT_MAGIC + "tell me a long story about dragons please".encode())
        events = recv_until(ws, "turn_end")

    assert state["calls"] == 1 and state["cancelled"] is False
    assert not any(e["type"] == "interrupted" for e in events)
    assert next(e for e in events if e["type"] == "turn_end")["turn"] == 1


def test_short_commit_while_idle_still_starts_turn(client, monkeypatch, voice_env):
    """The suspicion only applies during playback. With nothing playing, a one-word commit is a real
    request — here the Spanish stop word "para" ("stop"), which must be honoured."""
    seen = {}

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        seen["items"] = input_items
        await queue.put("Stopping.")
        return "Stopping."

    monkeypatch.setattr(vs, "agentic_loop", fake_loop)
    with client.websocket_connect("/api/voice/vph-idle") as ws:
        ws.receive()
        ws.send_bytes(COMMIT_MAGIC + "para".encode())
        events = recv_until(ws, "turn_end")

    assert any(e["type"] == "committed" and e["text"] == "para" for e in events)
    user_items = [i for i in seen["items"] if i.get("role") == "user"]
    assert "para" in str(user_items[-1]["content"])
