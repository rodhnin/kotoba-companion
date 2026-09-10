"""The noise cost cap has to ESCALATE, or a forgotten call bills forever.

The burst/window limits are unchanged — 12 turns a minute is already 2-3x a human rate. What
was missing is that the window ROLLS and nothing ever latched: a noisy room could buy up to
~720 turns an hour, each one an agentic turn, a memory extraction, and a TTS bill.

The latch copies the STT-strikes discipline: it needs `NOISE_LATCH_WINDOWS` CONSECUTIVE
saturated windows, so a healthy session or a single quiet window never counts. ElevenLabs' own
`commit_throttled` is the same evidence from the party that bills us — it used to reach nothing.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import kotoba.core.voice.session as vs
from kotoba.core.voice.stt import SttCommitted, SttError, SttSessionStarted


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

    def latch(self) -> list:
        return [f for f in self.frames if f["type"] == "error" and f["code"] == "mic_noise"]


class FakeDB:
    async def ensure_session(self, sid):
        pass

    async def insert_turn(self, *a, **k):
        pass

    async def fetch_soul_config(self):
        return None


class RecordingStt:
    """Records every byte handed to ElevenLabs, so "the room stopped leaving this machine" is
    measured at the seam. `commit` emits one transcript, as EL's VAD would on end-of-turn silence."""

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
        await self._events.put(SttCommitted(text="ruido constante de la habitacion"))

    def throttle(self) -> None:
        self._events.put_nowait(
            SttError(code="commit_throttled", message="commits are arriving too fast", fatal=False)
        )

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
    # Scaled so a "window" is a fraction of a second; the ARITHMETIC under test is windows, not seconds.
    monkeypatch.setattr(vs, "VOICE_TURN_BURST", 2)
    monkeypatch.setattr(vs, "VOICE_TURN_WINDOW_SECS", 0.2)

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


async def drive(ws, session, script, *, gap: float = 0.02, settle: float = 0.1):
    runner = asyncio.create_task(session.run())
    for item in script:
        ws.incoming.put_nowait(item)
        await asyncio.sleep(gap)
    await asyncio.sleep(settle)
    ws.incoming.put_nowait({"type": "websocket.disconnect"})
    await asyncio.wait_for(runner, 10)


def mute(muted: bool) -> dict:
    return {"text": json.dumps({"type": "mute", "muted": muted})}


COMMIT = {"text": json.dumps({"type": "commit"})}
AUDIO = {"bytes": b"\x11" * 400}
TYPED = {"text": json.dumps({"type": "text", "text": "sigo aqui"})}


# ---- the escalation ------------------------------------------------------------------------------


def test_a_room_that_never_stops_stops_costing_money(env):
    """The headline. A source that keeps committing past the cap for two windows running is a room,
    not a person — the mic latches off and not one more byte reaches ElevenLabs."""
    sid = "latch-room"
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})

    asyncio.run(drive(ws, session, [AUDIO] + [COMMIT] * 45 + [AUDIO, AUDIO, AUDIO]))

    bought = env["loop"]
    assert ws.latch(), (
        f"the cap never escalated: the room bought {bought} turns in "
        f"{(45 * 0.02) / vs.VOICE_TURN_WINDOW_SECS:.0f} windows and was still going"
    )
    assert bought <= 4 * vs.VOICE_TURN_BURST, f"{bought} turns bought before the mic latched off"
    assert [len(b) for b in RecordingStt.opened[0].sent] == [400], (
        "audio kept leaving the machine after the latch"
    )
    after = ws.types()[ws.types().index("error"):]
    assert "committed" not in after, "a latched mic must buy no further turns"


def test_the_latch_is_the_last_word_and_says_how_to_undo_it(env):
    """Told once, not per commit, and fatal so the notice survives on screen — it is a session-long
    state, not the transient `skipped` the individual refusals already carry."""
    ws = FakeWS()
    session = vs.VoiceSession(ws, "latch-once", db=FakeDB(), soul_patterns={})

    asyncio.run(drive(ws, session, [AUDIO] + [COMMIT] * 45))

    assert len(ws.latch()) == 1, f"said {len(ws.latch())} times"
    assert ws.latch()[0]["fatal"] is True
    assert ws.of("skipped"), "the individual refusals still say why they were dropped"


def test_one_saturated_window_is_a_burst_and_never_latches(env, monkeypatch):
    """A burst inside a single window is exactly what the rolling cap is FOR: a laugh, a list read
    aloud, someone else in the room for ten seconds. It is already capped, it decays on its own, and
    silencing the call for it would punish a conversation."""
    monkeypatch.setattr(vs, "VOICE_TURN_WINDOW_SECS", 1.0)
    ws = FakeWS()
    session = vs.VoiceSession(ws, "latch-burst", db=FakeDB(), soul_patterns={})

    asyncio.run(drive(ws, session, [AUDIO] + [COMMIT] * 20 + [AUDIO]))

    assert ws.of("skipped"), "the burst was still capped"
    assert not ws.latch(), "a single saturated window is not a room"
    assert len(RecordingStt.opened[0].sent) == 2, "and the mic is still live afterwards"


def test_a_conversation_at_a_human_pace_never_even_reaches_the_cap(env):
    """The arithmetic the cap is built on, exercised rather than asserted: EL's VAD needs 1.5s of
    silence to commit at all, so turns arrive spaced out — and each accepted one makes her speak,
    which shuts the mic again. Nothing here is refused, so nothing here can escalate."""
    ws = FakeWS()
    session = vs.VoiceSession(ws, "latch-human", db=FakeDB(), soul_patterns={})

    asyncio.run(drive(ws, session, [AUDIO] + [COMMIT] * 8, gap=0.12))

    assert env["loop"] == 8, "every turn of a paced exchange is answered"
    assert not ws.of("skipped") and not ws.latch()


def test_elevenlabs_own_throttle_is_the_same_evidence(env):
    """`commit_throttled` is EL saying WE are committing too fast — the same fact our cap measures,
    from the party that bills us. _NONFATAL_ERRORS absorbs it and it used to reach nothing at all."""
    sid = "latch-throttle"
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})

    async def go():
        runner = asyncio.create_task(session.run())
        ws.incoming.put_nowait(AUDIO)
        for _ in range(200):
            if RecordingStt.opened:
                break
            await asyncio.sleep(0.005)
        stt = RecordingStt.opened[0]
        for _ in range(45):
            stt.throttle()
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.1)
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)

    asyncio.run(go())

    assert ws.latch(), "EL's own throttle must be able to escalate on its own"


def test_a_quiet_window_ends_the_episode_the_way_a_healthy_session_ends_a_strike(env):
    """_stt_strikes' discipline, copied: a strike is only counted for a QUICK death, and a session
    that lived is not one. Here a full window with nothing over the cap in it ends the episode, so
    two bursts an hour apart are two bursts and not the start of a latch."""
    s = vs.VoiceSession(ws=None, session_id="latch-unit", db=None, soul_patterns={})
    w = vs.VOICE_TURN_WINDOW_SECS
    span = vs.NOISE_LATCH_WINDOWS * w
    beats = [i * w / 2 for i in range(vs.NOISE_LATCH_WINDOWS * 2)]

    for t in beats:
        assert s._note_saturation(t) is False, f"latched {t}s into a run that never spans {span}s"
    restart = beats[-1] + w * 1.5
    assert s._note_saturation(restart) is False, "a whole quiet window must end the episode"
    for i in range(1, vs.NOISE_LATCH_WINDOWS * 2):
        assert s._note_saturation(restart + i * w / 2) is False
    assert s._note_saturation(restart + span + w / 4) is True, "the fresh episode has its own clock"


def test_the_trigger_is_consecutive_saturated_windows(env):
    """Named in windows, not in seconds or in a count of commits: the thing that separates a room
    from a person is DURATION at the cap, and the cap's own window is the only unit that means
    anything here."""
    s = vs.VoiceSession(ws=None, session_id="latch-arith", db=None, soul_patterns={})
    w = vs.VOICE_TURN_WINDOW_SECS

    assert vs.NOISE_LATCH_WINDOWS >= 2, "one window is a burst; the latch must need more"
    for step in range(vs.NOISE_LATCH_WINDOWS):
        assert s._note_saturation(step * w) is False, f"latched after {step + 1} window(s)"
    assert s._note_saturation(vs.NOISE_LATCH_WINDOWS * w) is True


def test_a_human_at_the_keyboard_restarts_the_clock(env):
    """A second belt, cheap and one-sided: typing is a hand on a keyboard, which no amount of room
    noise can produce. It restarts the episode rather than clearing a latch — someone typing has not
    told us anything about their microphone."""
    s = vs.VoiceSession(ws=None, session_id="latch-human-unit", db=None, soul_patterns={})
    w = vs.VOICE_TURN_WINDOW_SECS

    s._note_saturation(0.0)
    s._note_human()
    for step in range(vs.NOISE_LATCH_WINDOWS):
        assert s._note_saturation(step * w) is False
    assert s._note_saturation(vs.NOISE_LATCH_WINDOWS * w) is True


def test_typing_through_the_noise_keeps_her_listening(env):
    """The same belt, driven: a call with a person in it is a call, whatever the room is doing."""
    ws = FakeWS()
    session = vs.VoiceSession(ws, "latch-typed", db=FakeDB(), soul_patterns={})
    script = [AUDIO] + [COMMIT] * 12 + [TYPED] + [COMMIT] * 12 + [TYPED] + [COMMIT] * 12

    asyncio.run(drive(ws, session, script))

    assert not ws.latch(), "a person answering in writing must not have their mic taken away"


def test_unmuting_is_the_deliberate_way_back(env):
    """Recovery has to be an ACT or the latch is decorative. The mute button is the one gesture that
    already means "my microphone and I are here" — and it is the natural response to a room that got
    loud, so coming back from it has to work."""
    sid = "latch-back"
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})

    asyncio.run(drive(ws, session, [AUDIO] + [COMMIT] * 45 + [AUDIO] + [mute(True), mute(False)]
                      + [AUDIO, COMMIT]))

    assert ws.latch()
    sent = [len(b) for b in RecordingStt.opened[0].sent]
    assert sent == [400, 400], f"the mic never came back: {sent}"
    types = ws.types()
    assert "committed" in types[types.index("error"):], "a turn after the recovery must be heard"


def test_the_cap_itself_is_untouched(env, monkeypatch):
    """The rate was never the defect. Restore the real numbers and the per-minute behaviour is the
    one already pinned for the mute path."""
    monkeypatch.setattr(vs, "VOICE_TURN_BURST", 12)
    monkeypatch.setattr(vs, "VOICE_TURN_WINDOW_SECS", 60.0)
    ws = FakeWS()
    session = vs.VoiceSession(ws, "latch-cap", db=FakeDB(), soul_patterns={})

    asyncio.run(drive(ws, session, [AUDIO] + [COMMIT] * 20, settle=0.2))

    assert env["loop"] == 12
    assert {f["reason"] for f in ws.of("skipped")} == {"too_many_turns"}
    assert not ws.latch(), "20 commits in half a second is a burst, not two minutes of a room"
