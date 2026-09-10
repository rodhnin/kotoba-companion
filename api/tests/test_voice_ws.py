"""Local voice mode — /api/voice/{session_id} WebSocket, end to end with fake EL clients + fake LLM.

Covers: auth gate (4401 close / query token / Bearer), the full duplex path (mic audio → committed
transcript → agentic loop → filtered assistant_text → TTS text-in → audio frames out), barge-in via
a new utterance and via the explicit interrupt control, mute semantics, TTS segment reopening across
tool gaps, spoken-text filters, STT reconnect-on-drop, and clean error surfacing. No network.

Every accepted socket opens with a `ready` frame, so the first `ws.receive()` of a test is that frame.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import kotoba.core.voice.session as vs
import kotoba.server as main
from kotoba.core.voice.config import VoiceAuthError, VoiceError, VoiceStreamClosed
from kotoba.core.voice.stt import SttCommitted, SttPartial, SttSessionStarted


class FakeStt:
    """Scripted stand-in for SttClient: audio frames are recorded; a commit (control-driven) emits a
    partial + committed pair with the next scripted text, like ElevenLabs VAD would on end-of-turn
    silence.

    Two hooks ride on the class attributes. The frame `b"__PARTIAL__"` records no audio and emits a bare
    partial instead — the user talking with nothing committed behind it. `drop_after_first_event` lets
    only the FIRST session yield one event before it dies, which is what a reconnect test needs."""

    instances: list["FakeStt"] = []
    script: list[str] = []
    fail_connect: Exception | None = None
    drop_after_first_event = False
    die_instantly = False

    def __init__(self, **kwargs) -> None:
        self.audio: list[bytes] = []
        self.connect_count = 0
        self._events: asyncio.Queue = asyncio.Queue()
        self._dropped = False
        FakeStt.instances.append(self)

    async def connect(self) -> SttSessionStarted:
        if FakeStt.fail_connect is not None:
            raise FakeStt.fail_connect
        self.connect_count += 1
        return SttSessionStarted(session_id="fake", config={})

    async def send_audio(self, pcm: bytes, *, commit: bool = False) -> None:
        if pcm == b"__PARTIAL__":
            await self._events.put(SttPartial(text="user talking over her"))
            return
        self.audio.append(pcm)
        if commit:
            text = FakeStt.script.pop(0) if FakeStt.script else "hello"
            await self._events.put(SttPartial(text=text))
            await self._events.put(SttCommitted(text=text))

    async def commit(self) -> None:
        await self.send_audio(b"", commit=True)

    async def events(self):
        if FakeStt.die_instantly:
            raise VoiceStreamClosed("fake instant death")
        while True:
            ev = await self._events.get()
            if FakeStt.drop_after_first_event and not self._dropped:
                self._dropped = True
                FakeStt.drop_after_first_event = False
                yield ev
                raise VoiceStreamClosed("fake drop")
            yield ev

    async def close(self) -> None:
        pass


class FakeTts:
    """Stand-in for TtsClient: records text; audio_chunks yields two frames once end() is called."""

    instances: list["FakeTts"] = []
    fail_connect: Exception | None = None

    def __init__(self, voice_id=None, **kwargs) -> None:
        self.voice_id = voice_id
        self.kwargs = kwargs
        self.text: list[str] = []
        self.ended = False
        self.closed = False
        FakeTts.instances.append(self)

    async def connect(self) -> None:
        if FakeTts.fail_connect is not None:
            raise FakeTts.fail_connect

    async def send_text(self, text: str) -> None:
        if text:
            self.text.append(text)

    async def end(self) -> None:
        self.ended = True

    async def audio_chunks(self):
        while not self.ended:
            await asyncio.sleep(0.005)
        yield b"\x01\x02"
        yield b"\x03\x04"

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def voice_env(monkeypatch):
    """Fake EL clients + a recording no-op memory extractor; each test supplies its own fake loop."""
    FakeStt.instances, FakeStt.script, FakeStt.fail_connect = [], [], None
    FakeStt.drop_after_first_event = False
    FakeStt.die_instantly = False
    FakeTts.instances, FakeTts.fail_connect = [], None
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


def set_loop(monkeypatch, fn):
    monkeypatch.setattr(vs, "agentic_loop", fn)


def recv_until(ws, stop_type: str, limit: int = 60):
    """Collect frames until a JSON event of stop_type arrives. Returns (json_events, binary_frames)."""
    events, blobs = [], []
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("bytes") is not None:
            blobs.append(msg["bytes"])
            continue
        ev = json.loads(msg["text"])
        events.append(ev)
        if ev["type"] == stop_type:
            return events, blobs
    raise AssertionError(f"no {stop_type!r} within {limit} frames: {[e['type'] for e in events]}")


def texts(events, of_type="assistant_text"):
    return "".join(e["text"] for e in events if e["type"] == of_type)


# ---- auth ------------------------------------------------------------------------------------


def test_ws_rejects_untokened_when_gate_on(client, monkeypatch):
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "letmein")
    with client.websocket_connect("/api/voice/vws-auth1") as ws:
        msg = ws.receive()
        assert msg["type"] == "websocket.close" and msg["code"] == 4401


def test_ws_accepts_query_token_and_bearer(client, monkeypatch, voice_env):
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "letmein")
    with client.websocket_connect("/api/voice/vws-auth2?token=letmein") as ws:
        assert json.loads(ws.receive()["text"])["type"] == "ready"
    with client.websocket_connect("/api/voice/vws-auth3", headers={"Authorization": "Bearer letmein"}) as ws:
        assert json.loads(ws.receive()["text"])["type"] == "ready"


def test_ws_open_when_gate_off(client, voice_env):
    with client.websocket_connect("/api/voice/vws-auth4") as ws:
        ready = json.loads(ws.receive()["text"])
        assert ready["type"] == "ready"
        assert ready["audio_out"]["format"] == "pcm_24000"
        assert ready["audio_in"]["sample_rate"] == 16000


# ---- the full duplex path --------------------------------------------------------------------


def test_audio_to_committed_to_loop_to_tts_to_audio(client, monkeypatch, voice_env):
    """The whole duplex path in one turn.

    Mic frames reach STT; the committed transcript reaches the loop through the real `load_context`,
    where it is the last user message; the loop's text reaches TTS; and the TTS audio comes back as
    binary frames bracketed by `audio_start` / `audio_end` before `turn_end`."""
    seen = {}

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        seen["items"] = input_items
        seen["session_id"] = session_id
        seen["mode"] = kw.get("mode")
        await queue.put("Hi there! ")
        await queue.put("I heard you.")
        return "Hi there! I heard you."

    set_loop(monkeypatch, fake_loop)
    FakeStt.script = ["what's the weather like"]

    with client.websocket_connect("/api/voice/vws-happy") as ws:
        ws.receive()
        ws.send_bytes(b"\x00\x01" * 320)
        ws.send_bytes(b"\x00\x02" * 320)
        ws.send_text(json.dumps({"type": "commit"}))
        events, blobs = recv_until(ws, "turn_end")

    kinds = [e["type"] for e in events]
    assert "partial" in kinds and "committed" in kinds
    committed = next(e for e in events if e["type"] == "committed")
    assert committed["text"] == "what's the weather like"

    assert seen["session_id"] == "vws-happy" and seen["mode"] == "companion"
    user_items = [i for i in seen["items"] if i.get("role") == "user"]
    assert "what's the weather like" in str(user_items[-1]["content"])

    stt = FakeStt.instances[0]
    assert len([a for a in stt.audio if a]) == 2
    assert "Hi there!" in texts(events) and "I heard you." in texts(events)
    assert "".join(FakeTts.instances[0].text).strip() != ""
    assert blobs == [b"\x01\x02", b"\x03\x04"]
    assert kinds.index("audio_start") < kinds.index("audio_end") < kinds.index("turn_end")


def test_typed_text_drives_turn_without_stt(client, monkeypatch, voice_env):
    """A typed message drives a whole turn, audio included, without ever opening an STT session: the
    STT connect is lazy, and only a mic frame trips it."""
    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        await queue.put("Typed reply.")
        return "Typed reply."

    set_loop(monkeypatch, fake_loop)
    with client.websocket_connect("/api/voice/vws-typed") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "text", "text": "hello from keyboard"}))
        events, blobs = recv_until(ws, "turn_end")
    assert "Typed reply." in texts(events)
    assert blobs
    assert FakeStt.instances == []


def test_spoken_text_filters_apply(client, monkeypatch, voice_env):
    """Code fences and URLs never reach the captions or the TTS client, and no audio tag reaches a
    caption.

    The caption stream is filtered by its own `AudioTagFilter(keep_valid=False)`, separate from the one
    in front of TTS: a tag is an instruction to a voice, so it is dropped on screen even in the
    expressive engine, where the spoken branch keeps it."""
    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        await queue.put("[happily] Look: ```py\nprint('x')\n``` done. ")
        await queue.put("See https://example.com/long/url for more. ")
        return "raw"

    set_loop(monkeypatch, fake_loop)
    with client.websocket_connect("/api/voice/vws-filter") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "text", "text": "show me code"}))
        events, _ = recv_until(ws, "turn_end")
    spoken = texts(events)
    assert "```" not in spoken and "print" not in spoken
    assert "https://" not in spoken and "example.com" not in spoken
    assert "[happily]" not in spoken
    assert "".join("".join(t.text) for t in FakeTts.instances).find("```") == -1


# ---- barge-in ---------------------------------------------------------------------------------


def test_new_utterance_supersedes_running_turn(client, monkeypatch, voice_env):
    """A second committed utterance supersedes the running turn.

    The barge-in is sent only once turn 1 is audibly running. The first loop call must see the
    cancellation, the browser must be told `interrupted`, and only the superseding turn may reach
    `turn_end`."""
    state = {"calls": 0, "cancelled": False}

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        state["calls"] += 1
        if state["calls"] == 1:
            await queue.put("One moment... ")
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise
        await queue.put("Second answer.")
        return "Second answer."

    set_loop(monkeypatch, fake_loop)
    FakeStt.script = ["first question", "actually never mind"]

    with client.websocket_connect("/api/voice/vws-barge") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "commit"}))
        while True:
            msg = ws.receive()
            if msg.get("bytes") is None and json.loads(msg["text"])["type"] == "assistant_text":
                break
        ws.send_text(json.dumps({"type": "commit"}))
        events, _ = recv_until(ws, "turn_end")

    kinds = [e["type"] for e in events]
    assert "interrupted" in kinds
    assert state["cancelled"] is True
    end = next(e for e in events if e["type"] == "turn_end")
    assert end["turn"] == 2
    assert "Second answer." in texts(events)


def test_partial_during_playback_never_interrupts(client, monkeypatch, voice_env):
    """A partial arriving mid-playback is forwarded but must NEVER kill the turn.

    The client gates the microphone off while she talks, so a partial landing then is a straggler or a
    hallucination of pre-gate audio. Treating one as speech silenced healthy turns, so the straggler
    here is timed to land after `audio_start`, while she is audibly talking."""
    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        await queue.put("Long answer coming... ")
        await asyncio.sleep(0.5)
        await queue.put("and here it ends.")
        return "Long answer coming... and here it ends."

    set_loop(monkeypatch, fake_loop)
    FakeStt.script = ["tell me everything"]
    with client.websocket_connect("/api/voice/vws-partial") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "commit"}))
        while True:
            msg = ws.receive()
            if msg.get("bytes") is None and json.loads(msg["text"])["type"] == "audio_start":
                break
        ws.send_bytes(b"__PARTIAL__")
        events, _ = recv_until(ws, "turn_end")
    assert any(e["type"] == "partial" for e in events)
    assert not any(e["type"] == "interrupted" for e in events)
    assert "and here it ends." in texts(events)


def test_partial_straggler_right_after_commit_is_harmless(client, monkeypatch, voice_env):
    """ElevenLabs re-emits partials of the utterance it has just committed. One landing inside the
    grace window that follows a commit must not cancel the turn that commit just started."""
    state = {"cancelled": False}

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        try:
            await queue.put("Survived the straggler.")
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        return "Survived the straggler."

    set_loop(monkeypatch, fake_loop)
    FakeStt.script = ["question"]
    with client.websocket_connect("/api/voice/vws-grace") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "commit"}))
        ws.send_bytes(b"__PARTIAL__")
        events, _ = recv_until(ws, "turn_end")
    assert state["cancelled"] is False
    assert not any(e["type"] == "interrupted" for e in events)
    assert "Survived the straggler." in texts(events)


def test_explicit_interrupt_control(client, monkeypatch, voice_env):
    """The `interrupt` control cancels the running turn, and the socket is usable straight after.

    The receive pump handles controls serially, so a follow-up turn sent right behind the interrupt is
    what proves the teardown finished rather than merely started."""
    state = {"calls": 0, "cancelled": False}

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        state["calls"] += 1
        if state["calls"] == 1:
            await queue.put("Let me think... ")
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise
        await queue.put("After interrupt.")
        return "After interrupt."

    set_loop(monkeypatch, fake_loop)
    with client.websocket_connect("/api/voice/vws-intr") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "text", "text": "slow question"}))
        while True:
            msg = ws.receive()
            if msg.get("bytes") is None and json.loads(msg["text"])["type"] == "assistant_text":
                break
        ws.send_text(json.dumps({"type": "interrupt"}))
        ws.send_text(json.dumps({"type": "text", "text": "next"}))
        events, _ = recv_until(ws, "turn_end")
    assert state["cancelled"] is True
    assert any(e["type"] == "interrupted" for e in events)
    assert "After interrupt." in texts(events)


# ---- mute -------------------------------------------------------------------------------------


def test_muted_drops_stt_turns_but_answers_typed(client, monkeypatch, voice_env):
    """The commit is landed through a session opened BEFORE the mute — the only way a transcript can
    still arrive once muted, since a muted session no longer opens one. It must not become a turn and
    must not reach the browser as a user line; a typed message must still be answered."""
    state = {"calls": 0}

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        state["calls"] += 1
        await queue.put("Answered.")
        return "Answered."

    set_loop(monkeypatch, fake_loop)
    FakeStt.script = ["background chatter"]
    with client.websocket_connect("/api/voice/vws-mute") as ws:
        ws.receive()
        ws.send_bytes(b"\x00\x01")  # opens the lazy STT session while still unmuted
        ws.send_text(json.dumps({"type": "mute", "muted": True}))
        ws.send_text(json.dumps({"type": "commit"}))
        events, _ = recv_until(ws, "skipped")
        assert state["calls"] == 0
        assert not any(e["type"] == "committed" for e in events)
        assert events[-1]["reason"] == "muted"
        ws.send_text(json.dumps({"type": "text", "text": "but answer this"}))
        events, _ = recv_until(ws, "turn_end")
        assert state["calls"] == 1 and "Answered." in texts(events)
        ws.send_text(json.dumps({"type": "mute", "muted": False}))


# ---- TTS segmenting across tool gaps -----------------------------------------------------------


def test_tool_gap_reopens_tts_segment(client, monkeypatch, voice_env):
    """A quiet gap on the TTS input closes the segment and the next text opens a fresh one: two
    segments, two clients, two `audio_start` / `audio_end` pairs, and two audio frames per segment."""
    monkeypatch.setattr(vs, "SEGMENT_IDLE_SECS", 0.05)

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        await queue.put("Before the tool. ")
        await asyncio.sleep(0.4)  # a "tool run" longer than the idle window
        await queue.put("After the tool.")
        return "Before the tool. After the tool."

    set_loop(monkeypatch, fake_loop)
    with client.websocket_connect("/api/voice/vws-gap") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "text", "text": "run a tool"}))
        events, blobs = recv_until(ws, "turn_end")

    kinds = [e["type"] for e in events]
    assert kinds.count("audio_start") == 2 and kinds.count("audio_end") == 2
    assert len(FakeTts.instances) == 2 and all(t.ended and t.closed for t in FakeTts.instances)
    assert len(blobs) == 4


# ---- errors ------------------------------------------------------------------------------------


def test_stt_auth_failure_surfaces_fatal_error(client, monkeypatch, voice_env):
    FakeStt.fail_connect = VoiceAuthError("No ElevenLabs API key configured")
    with client.websocket_connect("/api/voice/vws-sttfail") as ws:
        ws.receive()
        ws.send_bytes(b"\x00\x01" * 160)
        events, _ = recv_until(ws, "error")
    err = events[-1]
    assert err["code"] == "stt_auth" and err["fatal"] is True


def test_an_exhausted_quota_is_not_reported_as_a_bad_key(client, monkeypatch, voice_env):
    """The browser already has the right sentence — FATAL_TEXT["quota_exceeded"], "The ElevenLabs quota
    is used up" — and the handshake could never reach it: every non-`auth_error` opener became
    `stt_connect`, whose text sends the person to check their connection and their key. The key is fine
    and no amount of rotating it will help."""
    from kotoba.core.voice.config import VoiceQuotaError

    FakeStt.fail_connect = VoiceQuotaError("ElevenLabs quota exhausted")
    with client.websocket_connect("/api/voice/vws-sttquota") as ws:
        ws.receive()
        ws.send_bytes(b"\x00\x01" * 160)
        events, _ = recv_until(ws, "error")
    err = events[-1]
    assert err["code"] == "quota_exceeded" and err["fatal"] is True


def test_stt_drop_reopens_lazily_on_next_audio(client, monkeypatch, voice_env):
    """An STT stream that dies mid-session is not fatal: the first session yields its partial and then
    drops, and the next mic frame opens a fresh one that carries the turn through."""
    FakeStt.drop_after_first_event = True

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        await queue.put("Reply.")
        return "Reply."

    set_loop(monkeypatch, fake_loop)
    FakeStt.script = ["lost to the drop", "second try"]
    with client.websocket_connect("/api/voice/vws-drop") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "commit"}))
        ev = json.loads(ws.receive()["text"])
        assert ev["type"] == "partial"
        import time
        time.sleep(0.1)  # let the pump's teardown clear the dead session
        ws.send_bytes(b"\x00\x01" * 160)
        ws.send_text(json.dumps({"type": "commit"}))
        events, _ = recv_until(ws, "turn_end")
    assert len(FakeStt.instances) == 2
    assert any(e["type"] == "committed" and e["text"] == "second try" for e in events)


def test_stt_instant_death_loop_disables_mic(client, monkeypatch, voice_env):
    """Lazy reopening must not become an infinite reconnect loop.

    Every mic frame here opens a session that dies at once, so the strikes accumulate until the socket
    gives up and reports one fatal `stt_closed` instead of reconnecting forever."""
    FakeStt.die_instantly = True
    with client.websocket_connect("/api/voice/vws-strikes") as ws:
        ws.receive()
        import time
        for _ in range(8):
            ws.send_bytes(b"\x00\x01" * 160)
            time.sleep(0.05)
        events, _ = recv_until(ws, "error")
    err = events[-1]
    assert err["code"] == "stt_closed" and err["fatal"] is True
    assert len(FakeStt.instances) >= 3


def test_tts_unavailable_still_streams_captions(client, monkeypatch, voice_env):
    """A dead TTS costs the voice, not the turn: captions still stream, no audio comes back, and the
    failure is reported once as non-fatal rather than once per chunk."""
    FakeTts.fail_connect = VoiceError("TTS connect failed: boom")

    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        await queue.put("Words without sound.")
        return "Words without sound."

    set_loop(monkeypatch, fake_loop)
    with client.websocket_connect("/api/voice/vws-ttsfail") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "text", "text": "say something"}))
        events, blobs = recv_until(ws, "turn_end")
    assert "Words without sound." in texts(events)
    assert blobs == []
    err = next(e for e in events if e["type"] == "error")
    assert err["code"] == "tts_unavailable" and err["fatal"] is False
    assert [e["type"] for e in events].count("error") == 1


def test_loop_crash_speaks_graceful_line(client, monkeypatch, voice_env):
    async def fake_loop(input_items, session_id, db, queue, soul_patterns, **kw):
        raise RuntimeError("secret traceback detail")

    set_loop(monkeypatch, fake_loop)
    with client.websocket_connect("/api/voice/vws-crash") as ws:
        ws.receive()
        ws.send_text(json.dumps({"type": "text", "text": "trigger"}))
        events, _ = recv_until(ws, "turn_end")
    spoken = texts(events)
    assert "tripped up" in spoken
    assert "secret traceback detail" not in json.dumps(events)


def test_bad_control_message_is_nonfatal(client, monkeypatch, voice_env):
    with client.websocket_connect("/api/voice/vws-bad") as ws:
        ws.receive()
        ws.send_text("not json at all")
        ev = json.loads(ws.receive()["text"])
        assert ev["type"] == "error" and ev["fatal"] is False
        ws.send_text(json.dumps({"type": "wat"}))
        ev = json.loads(ws.receive()["text"])
        assert ev["type"] == "error" and ev["fatal"] is False
