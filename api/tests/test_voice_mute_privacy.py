"""Mute must stop the MICROPHONE, and it must have one owner.

Drives the real VoiceSession through a fake WS with a recording STT stand-in — "no bytes left
the machine" is measured at the ElevenLabs seam, not asserted. Two bugs fixed: mute was
checked only in `_start_turn`, so a muted client's audio still reached ElevenLabs and came
back as a user message; and `session_state._muted` is process-global with no TTL, so leaving
without unmuting deafened the next, unmuted caller — the socket now clears it at accept and at
teardown. The burst cap exists because one hallucinated commit used to buy a full agentic turn
plus a memory extraction.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import kotoba.core.voice.session as vs
from kotoba.core import session_state
from kotoba.core.voice.stt import SttCommitted, SttSessionStarted


class FakeWS:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.frames: list = []

    async def accept(self) -> None:
        pass

    async def receive(self):
        return await self.incoming.get()

    async def send_text(self, t: str) -> None:
        self.frames.append(json.loads(t))

    async def send_bytes(self, b: bytes) -> None:
        pass

    def types(self) -> list:
        return [f["type"] for f in self.frames]

    def of(self, kind: str) -> list:
        return [f for f in self.frames if f["type"] == kind]


class FakeDB:
    def __init__(self) -> None:
        self.rows: list = []

    async def ensure_session(self, sid):
        pass

    async def insert_turn(self, sid, role, content):
        self.rows.append((role, content))

    async def fetch_soul_config(self):
        return None


class RecordingStt:
    """Records every byte handed to ElevenLabs and every session opened, so the privacy claim is
    measured at the seam. A commit emits one committed transcript, as EL VAD would on silence."""

    opened: list["RecordingStt"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.sent: list[bytes] = []
        self._events: asyncio.Queue = asyncio.Queue()
        RecordingStt.opened.append(self)

    async def connect(self) -> SttSessionStarted:
        return SttSessionStarted(session_id="fake", config={})

    async def send_audio(self, pcm: bytes, *, commit: bool = False) -> None:
        self.sent.append(pcm)

    async def commit(self) -> None:
        await self._events.put(SttCommitted(text="ruido de la habitacion"))

    async def events(self):
        while True:
            yield await self._events.get()

    async def close(self) -> None:
        pass


class FakeTts:
    def __init__(self, voice_id=None, **kwargs) -> None:
        self.ended = False

    async def connect(self) -> None:
        pass

    async def send_text(self, text: str) -> None:
        pass

    async def end(self) -> None:
        self.ended = True

    async def audio_chunks(self):
        while not self.ended:
            await asyncio.sleep(0.005)
        yield b"\x01\x02"

    async def close(self) -> None:
        pass


@pytest.fixture
def env(monkeypatch):
    RecordingStt.opened = []
    monkeypatch.setattr(vs, "SttClient", RecordingStt)
    monkeypatch.setattr(vs, "TtsClient", FakeTts)

    async def fake_load_context(request, db, session_id, **kw):
        return [{"role": "user", "content": request.messages[-1]["content"]}]

    monkeypatch.setattr(vs, "load_context", fake_load_context)
    calls = {"loop": 0, "memory": 0}

    async def fake_loop(items, sid, db, queue, patterns, **kw):
        calls["loop"] += 1
        await queue.put("Dicho.")
        return "Dicho."

    async def fake_memory(user_text, db):
        calls["memory"] += 1

    monkeypatch.setattr(vs, "agentic_loop", fake_loop)
    monkeypatch.setattr(vs, "extract_and_save_memory", fake_memory)
    yield calls


async def drive(ws, session, script, *, stop: str | None = "turn_end", limit: int = 60):
    runner = asyncio.create_task(session.run())
    for item in script:
        ws.incoming.put_nowait(item)
        await asyncio.sleep(0.02)
    for _ in range(limit):
        if stop is not None and stop in ws.types():
            break
        await asyncio.sleep(0.02)
    ws.incoming.put_nowait({"type": "websocket.disconnect"})
    await asyncio.wait_for(runner, 10)


def mute(muted: bool) -> dict:
    return {"text": json.dumps({"type": "mute", "muted": muted})}


COMMIT = {"text": json.dumps({"type": "commit"})}
AUDIO = {"bytes": b"\x11" * 400}


# ---- the microphone ------------------------------------------------------------------------------


def test_muted_mic_sends_zero_bytes_and_opens_no_stt_session(env):
    sid = "vmp-privacy"
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})

    asyncio.run(drive(ws, session, [mute(True), AUDIO, AUDIO, COMMIT], stop="skipped"))

    assert RecordingStt.opened == [], "a muted session must not open a transcription session at all"
    assert env["loop"] == 0
    assert "committed" not in ws.types(), "room audio must never come back as a user line"
    assert session_state.is_muted(sid) is False, "teardown leaves no sticky mute behind"


def test_a_transcript_from_before_the_mute_is_dropped_not_shown(env):
    """The one race the cut cannot close: audio that left before the mute still gets transcribed."""
    sid = "vmp-race"
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})

    asyncio.run(drive(ws, session, [AUDIO, mute(True), COMMIT], stop="skipped"))

    assert env["loop"] == 0 and env["memory"] == 0
    assert "committed" not in ws.types()
    assert ws.of("skipped")[0]["reason"] == "muted"


def test_unmuting_restores_the_mic(env):
    sid = "vmp-unmute"
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})

    asyncio.run(drive(ws, session, [mute(True), AUDIO, mute(False), AUDIO, COMMIT]))

    assert len(RecordingStt.opened) == 1, "the session opens on the first frame AFTER unmuting"
    assert [len(b) for b in RecordingStt.opened[0].sent] == [400], "only the unmuted frame was sent"
    assert env["loop"] == 1


def test_typed_turn_is_still_answered_while_muted(env):
    sid = "vmp-typed"
    ws, db = FakeWS(), FakeDB()
    session = vs.VoiceSession(ws, sid, db=db, soul_patterns={})

    asyncio.run(drive(ws, session, [
        mute(True), AUDIO, {"text": json.dumps({"type": "text", "text": "contesta a esto"})},
    ]))

    assert env["loop"] == 1, "muting the MIC must never silence an explicit typed message"
    assert ("user", "contesta a esto") in db.rows


def test_stt_client_is_built_with_the_background_filter(env, monkeypatch):
    monkeypatch.setenv("KOTOBA_STT_LANGUAGE", "es")
    ws = FakeWS()
    session = vs.VoiceSession(ws, "vmp-knobs", db=FakeDB(), soul_patterns={})

    asyncio.run(drive(ws, session, [AUDIO], stop=None, limit=3))

    assert RecordingStt.opened[0].kwargs == {"language_code": "es", "filter_background_audio": True}


# ---- the noisy room ------------------------------------------------------------------------------


def test_a_commit_storm_is_capped_and_says_why(env):
    sid = "vmp-storm"
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})

    asyncio.run(drive(ws, session, [AUDIO] + [COMMIT] * 20, stop=None, limit=40))

    assert env["loop"] == vs.VOICE_TURN_BURST, "every commit past the cap is refused"
    assert env["memory"] == vs.VOICE_TURN_BURST, "and refused before it can rewrite durable memory"
    assert {f["reason"] for f in ws.of("skipped")} == {"too_many_turns"}


def test_the_cap_decays_so_a_quiet_room_gets_its_voice_back(env, monkeypatch):
    monkeypatch.setattr(vs, "VOICE_TURN_WINDOW_SECS", 0.05)
    sid = "vmp-decay"
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})

    asyncio.run(drive(ws, session, [AUDIO] + [COMMIT] * (vs.VOICE_TURN_BURST + 4), stop=None, limit=40))

    assert env["loop"] > vs.VOICE_TURN_BURST, "the window rolls; the cap is not a session-long latch"


def test_a_short_idle_command_still_starts_a_turn(env):
    """`para` is a whole sentence. Length is not a noise heuristic while idle — the reason
    _is_phantom_commit stays armed only during playback."""
    s = vs.VoiceSession(ws=None, session_id="vmp-short", db=None, soul_patterns={})
    assert s._is_phantom_commit("para") is False
    s._audio_active = True
    assert s._is_phantom_commit("para") is True


# ---- one owner -----------------------------------------------------------------------------------


def test_mute_does_not_survive_a_hang_up(env):
    sid = "vmp-sticky"
    ws1 = FakeWS()
    asyncio.run(drive(ws1, vs.VoiceSession(ws1, sid, db=FakeDB(), soul_patterns={}),
                      [mute(True)], stop=None, limit=2))
    assert session_state.is_muted(sid) is False, "hanging up muted must not deafen the next call"

    ws2 = FakeWS()
    asyncio.run(drive(ws2, vs.VoiceSession(ws2, sid, db=FakeDB(), soul_patterns={}), [AUDIO, COMMIT]))

    assert env["loop"] == 1, "the second call hears the user"
    assert "committed" in ws2.types() and "skipped" not in ws2.types()


def test_a_stale_mute_is_cleared_when_the_socket_opens(env):
    sid = "vmp-stale"
    session_state.set_muted(sid, True)  # e.g. left by the /v1 path or a crashed process
    ws = FakeWS()

    asyncio.run(drive(ws, vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={}), [AUDIO, COMMIT]))

    assert env["loop"] == 1
    session_state.set_muted(sid, False)


def test_a_disagreement_is_announced_once_not_dropped_in_silence(env):
    """A muted CLIENT sends no audio, so a frame arriving while the server thinks we are muted is the
    two sides disagreeing. It must be visible — and repeat itself once per episode, not per frame."""
    sid = "vmp-desync"
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})

    asyncio.run(drive(ws, session, [mute(True), AUDIO, AUDIO, AUDIO], stop=None, limit=4))

    assert [f["reason"] for f in ws.of("skipped")] == ["muted"]


def test_re_declaring_unmuted_reopens_the_mic_and_rearms_the_notice(env):
    sid = "vmp-reheal"
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})

    asyncio.run(drive(ws, session, [mute(True), AUDIO, mute(False), AUDIO, mute(True), AUDIO],
                      stop=None, limit=4))

    assert len(ws.of("skipped")) == 2, "the latch re-arms on unmute, so a later episode is not swallowed"
    assert len(RecordingStt.opened) == 1


def test_leave_clears_the_mute(env):
    from fastapi.testclient import TestClient

    import kotoba.server as main

    sid = "vmp-leave"
    session_state.set_muted(sid, True)
    with TestClient(main.app) as c:
        assert c.post(f"/api/session/{sid}/leave").status_code == 200
    assert session_state.is_muted(sid) is False
