"""She must not hum AT the user while she is waiting FOR the user — and the mic must stay shut anyway.

`ctx.approval.confirm(...)` runs inside the heartbeat-wrapped `tool.execute`, so the filler kept
firing while an approval card sat on screen waiting for a human click; she is not working then.

Not humming is not enough: a filler opens a segment, and the gate only stays shut while a
segment is open. At the old cadence the beats were closer than `SEGMENT_IDLE_SECS`, so removing
the filler risked leaving the mic live instead. Measured at 1/20 scale, through a port of the
client's own MicGate.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

import kotoba.core.loop as loop
import kotoba.core.voice.session as vs
import kotoba.tools.registry as reg
from kotoba.core import events, interaction
from kotoba.core import stream as sse
from kotoba.tools.registry import ToolSpec

SCALE = 1 / 20.0
IDLE = 8.0 * SCALE
FIRST = 9.0 * SCALE
EVERY = 21.0 * SCALE
TICK = 1.0 * SCALE
POLL = 2.0 * SCALE
GATE_TAIL = 0.350 * SCALE
# The gate's own closing tail is the only leak that is real; the rest of this is the clock. A
# bound of exactly GATE_TAIL passed here and failed on Windows, whose timer granularity is most
# of a tail on its own at this scale — measured there at 0.4 against a budget of 0.35.
LEAK_OK = GATE_TAIL + 0.300 * SCALE

CARD_SECS = 15.0 * SCALE
WORK_SECS = 12.0 * SCALE


class FakeWS:
    """Records every frame with the clock reading at which it left the server."""

    def __init__(self) -> None:
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.frames: list = []
        self.t0 = 0.0

    def now(self) -> float:
        return asyncio.get_running_loop().time() - self.t0

    async def accept(self) -> None:
        self.t0 = asyncio.get_running_loop().time()

    async def receive(self):
        return await self.incoming.get()

    async def send_text(self, t: str) -> None:
        self.frames.append((self.now(), json.loads(t)))

    async def send_bytes(self, b: bytes) -> None:
        pass

    def types(self) -> list:
        return [f["type"] for _, f in self.frames]

    def audio_events(self) -> list:
        return [(t, f) for t, f in self.frames if f["type"] in ("audio_start", "audio_end", "interrupted")]


class FakeDB:
    async def ensure_session(self, sid):
        pass

    async def insert_turn(self, *a, **k):
        pass

    async def fetch_soul_config(self):
        return None


class RecordingTts:
    """Every send_text with its timestamp — what was actually synthesized, and when."""

    made: list["RecordingTts"] = []

    def __init__(self, voice_id=None, **kw) -> None:
        self.said: list[tuple[float, str]] = []
        self.ended = False
        RecordingTts.made.append(self)

    def _now(self) -> float:
        return asyncio.get_running_loop().time() - RecordingTts.t0

    async def connect(self) -> None:
        pass

    async def send_text(self, text: str) -> None:
        self.said.append((self._now(), text))

    async def end(self) -> None:
        self.ended = True

    async def audio_chunks(self):
        while not self.ended:
            await asyncio.sleep(0.005)
        yield b"\x01\x02"

    async def close(self) -> None:
        self.ended = True


class MicGatePort:
    """lib/voice-gate.ts MicGate, scaled. `isOpen` is the whole question: an open gate means every mic
    frame goes to ElevenLabs and a hallucinated commit can buy a whole agentic turn."""

    def __init__(self, tail: float = GATE_TAIL) -> None:
        self.tail = tail
        self.streaming = False
        self.playing = False
        self.tail_until = 0.0

    def apply(self, frame: dict, t: float) -> None:
        kind = frame["type"]
        if kind == "audio_start":
            self.streaming = True
        elif kind == "audio_end":
            self.streaming = False
            if not self.playing:
                self.tail_until = max(self.tail_until, t + self.tail)
        elif kind == "interrupted":
            self.streaming = False
            self.playing = False
            self.tail_until = 0.0

    def is_open(self, t: float) -> bool:
        return not self.streaming and not self.playing and t >= self.tail_until


def open_windows(events_at: list, end: float, step: float = 0.005) -> list:
    """Every interval in which the client's gate would have let mic audio through, in seconds."""
    gate = MicGatePort()
    out: list = []
    start = None
    i, t = 0, 0.0
    while t <= end:
        while i < len(events_at) and events_at[i][0] <= t:
            gate.apply(events_at[i][1], events_at[i][0])
            i += 1
        if gate.is_open(t):
            start = t if start is None else start
        elif start is not None:
            out.append((start, t))
            start = None
        t += step
    if start is not None:
        out.append((start, end))
    return out


def overlap(windows: list, lo: float, hi: float) -> float:
    return sum(max(0.0, min(hi, b) - max(lo, a)) for a, b in windows)


@pytest.fixture
def clean_registry():
    saved = dict(reg._REGISTRY)
    savedc = dict(reg._check_cache)
    try:
        yield
    finally:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(saved)
        reg._check_cache.clear()
        reg._check_cache.update(savedc)


class Ctx:
    """What the loop puts on a real ToolContext before running a tool."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.channel = "voice"
        self.register = "voice"
        self.model_role = "companion"
        self.call_id = "call-1"


@pytest.fixture
def scaled(monkeypatch):
    monkeypatch.setenv("KOTOBA_VOICE_MODE", "local")
    monkeypatch.setattr(loop, "is_reasoning_model", lambda role=None: True)
    monkeypatch.setattr(loop, "_HEARTBEAT_FIRST", FIRST)
    monkeypatch.setattr(loop, "_HEARTBEAT_EVERY", EVERY)
    monkeypatch.setattr(loop, "_HEARTBEAT_TICK", TICK, raising=False)
    monkeypatch.setattr(vs, "SEGMENT_IDLE_SECS", IDLE)
    monkeypatch.setattr(vs, "MIC_HOLD_POLL_SECS", POLL, raising=False)
    monkeypatch.setattr(vs, "TtsClient", RecordingTts)

    async def fake_load_context(request, db, session_id, **kw):
        return [{"role": "user", "content": request.messages[-1]["content"]}]

    async def no_memory(user_text, db):
        return None

    monkeypatch.setattr(vs, "load_context", fake_load_context)
    monkeypatch.setattr(vs, "extract_and_save_memory", no_memory)
    RecordingTts.made = []
    yield monkeypatch


def register_tool(name: str, execute) -> None:
    mod = types.SimpleNamespace(SCHEMA={"type": "function", "name": name}, __name__=f"t.{name}",
                                execute=execute, TIMEOUT=600)
    reg.register(ToolSpec(name=name, module=mod, schema=mod.SCHEMA, toolset="terminal", risk="exec"))


async def run_turn(ws, session, text="ejecuta eso por mi"):
    runner = asyncio.create_task(session.run())
    ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": text})})
    for _ in range(600):
        if "turn_end" in ws.types():
            break
        await asyncio.sleep(0.01)
    ws.incoming.put_nowait({"type": "websocket.disconnect"})
    await asyncio.wait_for(runner, 10)


def announce_run_report(tool_name: str, ctx: Ctx):
    """The shape of a real tool turn: she says what she is about to do, the loop flushes at the tool
    boundary, the tool runs under the heartbeat, then she reports."""

    async def fake_loop(items, sid, db, queue, patterns, **kw):
        await queue.put("Vale, lo ejecuto ahora mismo. ")
        await queue.put(sse.FLUSH_SENTINEL)
        await loop.execute_with_heartbeat(tool_name, {}, queue, patterns, ctx)
        await queue.put("Listo, ya está hecho. ")
        return "Vale, lo ejecuto ahora mismo. Listo, ya está hecho."

    return fake_loop


# ---- the cadence, measured ------------------------------------------------------------------------


@pytest.mark.parametrize("first,every,name", [(3.0 * SCALE, 6.0 * SCALE, "old 3s/+6s"),
                                              (FIRST, EVERY, "new 9s/+21s")])
def test_what_the_new_heartbeat_cadence_did_to_the_microphone(scaled, clean_registry, first, every,
                                                              name):
    """The undocumented second job of the filler, measured on both cadences.

    A beat opens a segment, and a segment shuts the client's mic. The old beats were 6s apart under an
    8s idle budget, so the bracket never closed and the mic stayed shut across the whole tool run. The
    new ones are 21s apart, so the bracket closes at 8s and nothing reopens it until 30s.

    The hole itself is not the bug this file fixes: while she is genuinely WORKING the user has to be
    able to talk over her, and that is what SEGMENT_IDLE_SECS closing the bracket buys. What must never
    be open is the stretch where she is waiting on the human instead — the next test.
    """
    scaled.setattr(loop, "_HEARTBEAT_FIRST", first)
    scaled.setattr(loop, "_HEARTBEAT_EVERY", every)
    sid = f"card-cadence-{name}"
    ctx = Ctx(sid)

    async def slow_tool(args, c):
        await asyncio.sleep(40.0 * SCALE)
        return "done"

    register_tool("probe", slow_tool)
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})
    scaled.setattr(vs, "agentic_loop", announce_run_report("probe", ctx))
    RecordingTts.t0 = 0.0

    async def go():
        RecordingTts.t0 = asyncio.get_running_loop().time()
        await run_turn(ws, session)

    asyncio.run(go())

    end = ws.frames[-1][0]
    live = open_windows(ws.audio_events(), end)
    longest = max((b - a for a, b in live), default=0.0)
    if first < IDLE:
        # Both branches say it in HER seconds, so the contrast is legible: a second of open mic against
        # the six the other one needs. Said as a bare 0.02 it was one float bit under the quantum the
        # clock actually reports, and a coarser clock landed exactly on it: 0.020000000000000018.
        assert longest < 1.0 * SCALE, f"the old cadence held the mic shut; got {longest / SCALE:.1f}s open"
    else:
        assert longest > 6.0 * SCALE, (
            f"the new cadence should leave a long unguarded stretch; got {longest / SCALE:.1f}s"
        )


# ---- the fix: silence while the card is up, without opening the mic --------------------------------


def drive_card_turn(scaled, sid: str, work: float = WORK_SECS):
    """One tool that asks for approval and then works. Returns (ws, session, marks)."""
    ctx = Ctx(sid)
    marks: dict = {}

    async def asking_tool(args, c):
        verdict = await interaction.ask_approval(c, "rm -rf /tmp/algo")
        marks["answered"] = asyncio.get_running_loop().time() - RecordingTts.t0
        marks["verdict"] = verdict
        await asyncio.sleep(work)
        return "hecho"

    register_tool("probe", asking_tool)
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})
    scaled.setattr(vs, "agentic_loop", announce_run_report("probe", ctx))

    async def answer_later():
        # The window opens when the card EXISTS, not when the tool is entered: entering it is still
        # before `ask_approval`, and the announcement she owes the turn is synthesized by a consumer
        # task whose first run lands on either side of that instant depending on the interpreter.
        while not interaction.has_pending(sid):
            await asyncio.sleep(0.001)
        marks["card"] = asyncio.get_running_loop().time() - RecordingTts.t0
        await asyncio.sleep(CARD_SECS)
        interaction.resolve(sid, {"approved": True, "always": False})

    async def watch(session, samples):
        while True:
            samples.append((asyncio.get_running_loop().time() - RecordingTts.t0, session._audio_active))
            await asyncio.sleep(0.01)

    async def go():
        RecordingTts.t0 = asyncio.get_running_loop().time()
        events.register(sid)
        samples: list = []
        answering = asyncio.create_task(answer_later())
        watching = asyncio.create_task(watch(session, samples))
        try:
            await run_turn(ws, session)
        finally:
            for t in (answering, watching):
                t.cancel()
            events.unregister(sid)
        marks["armed"] = samples
        return marks

    return ws, session, go


def test_nothing_is_synthesized_while_the_card_waits_on_a_human(scaled, clean_registry):
    """The defect heard live: ~10s to read an approval card, fillers the whole time. She is not
    working then — the card is on screen and the next move is the user's."""
    ws, session, go = drive_card_turn(scaled, "card-quiet")

    marks = asyncio.run(go())

    assert marks["verdict"] == interaction.APPROVED
    lo, hi = marks["card"], marks["answered"]
    # Whitespace is not speech: the separator held back at the tool boundary is fed to a sentence
    # splitter that keeps it until a sentence closes, so it costs no request and says nothing.
    said = [(t, s) for tts in RecordingTts.made for t, s in tts.said if lo <= t <= hi and s.strip()]
    assert said == [], f"synthesized while waiting on the human: {said!r}"


def test_the_microphone_stays_shut_for_the_whole_card(scaled, clean_registry):
    """The trap: the filler's second job was gating the mic. Take the filler away and the gate must be
    held by something else, or a 15s card is 15s of every mic frame going to ElevenLabs with
    _is_phantom_commit disarmed — the whole-turn cost the burst cap exists to stop."""
    ws, session, go = drive_card_turn(scaled, "card-gate")

    marks = asyncio.run(go())

    lo, hi = marks["card"], marks["answered"]
    end = ws.frames[-1][0]
    live = open_windows(ws.audio_events(), end)
    leaked = overlap(live, lo, hi)
    assert leaked < LEAK_OK, (
        f"the mic was live {leaked / SCALE:.1f}s of the {(hi - lo) / SCALE:.1f}s card: {live}"
    )


def test_the_phantom_commit_filter_stays_armed_for_the_whole_card(scaled, clean_registry):
    """A shut gate is the client's half. _is_phantom_commit is the server's, and it is armed only while
    `_audio_active` — so a straggler arriving from pre-gate audio during the card must still be
    dropped, exactly as it would be while she speaks."""
    ws, session, go = drive_card_turn(scaled, "card-armed")

    marks = asyncio.run(go())

    lo, hi = marks["card"], marks["answered"]
    # Both edges get the same settling margin. The closing instant is written by the TOOL and sampled
    # by another task, so at `hi` the two interleave either way and a sample landing there reads the
    # state after the answer, not during the card — measured: 47 samples True across the card and one
    # False at exactly `hi`, which is the gate already handed back.
    during = [armed for t, armed in marks["armed"] if lo + 0.02 <= t <= hi - 0.02]
    assert during and all(during), "the phantom filter was disarmed while the card was open"


def test_she_starts_humming_again_once_the_work_is_really_hers(scaled, clean_registry):
    """The suppression is scoped to the wait, not to the tool. Once the human has answered, the tool is
    hers again and a long one must still prove she has not gone away."""
    ws, session, go = drive_card_turn(scaled, "card-resume", work=40.0 * SCALE)

    marks = asyncio.run(go())

    after = [s.strip() for tts in RecordingTts.made for t, s in tts.said if t > marks["answered"]]
    assert any(h in after for h in loop._NEUTRAL_FILLER), f"nothing was heard after the card: {after!r}"


def test_the_bracket_is_always_closed_by_the_end_of_the_turn(scaled, clean_registry):
    """The gate has one catastrophic failure mode and it is the opposite of this fix: an audio_start
    nobody answers leaves the mic shut for the rest of the call. Whatever holds it during a card must
    hand it back."""
    ws, session, go = drive_card_turn(scaled, "card-closes")

    asyncio.run(go())

    end = ws.frames[-1][0]
    live = open_windows(ws.audio_events(), end + 1.0)
    assert live and live[-1][1] >= end, f"the mic never reopened after the turn: {live}"
    assert session._audio_open is False
    assert getattr(session, "_mic_hold", False) is False


def test_a_card_with_no_announcement_still_shuts_the_mic(scaled, clean_registry):
    """The gate must not depend on her having spoken first. A tool called with no preamble used to
    leave the mic live for the whole card, because nothing had opened a bracket to inherit."""
    sid = "card-silent-start"
    ctx = Ctx(sid)
    marks: dict = {}

    async def asking_tool(args, c):
        marks["card"] = asyncio.get_running_loop().time() - RecordingTts.t0
        await interaction.ask_approval(c, "rm -rf /tmp/algo")
        marks["answered"] = asyncio.get_running_loop().time() - RecordingTts.t0
        return "hecho"

    register_tool("probe", asking_tool)

    async def fake_loop(items, sid_, db, queue, patterns, **kw):
        await loop.execute_with_heartbeat("probe", {}, queue, patterns, ctx)
        await queue.put("Ya está. ")
        return "Ya está."

    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})
    scaled.setattr(vs, "agentic_loop", fake_loop)

    async def answer_later():
        while not interaction.has_pending(sid):
            await asyncio.sleep(0.001)
        marks["card"] = asyncio.get_running_loop().time() - RecordingTts.t0
        await asyncio.sleep(CARD_SECS)
        interaction.resolve(sid, {"approved": True})

    async def go():
        RecordingTts.t0 = asyncio.get_running_loop().time()
        events.register(sid)
        answering = asyncio.create_task(answer_later())
        try:
            await run_turn(ws, session)
        finally:
            answering.cancel()
            events.unregister(sid)

    asyncio.run(go())

    live = open_windows(ws.audio_events(), ws.frames[-1][0])
    leaked = overlap(live, marks["card"] + POLL, marks["answered"])
    assert leaked < LEAK_OK, f"the mic was live {leaked / SCALE:.1f}s of a card she never spoke before"


def test_barge_in_still_kills_the_turn_through_a_held_gate(scaled, clean_registry):
    """Barge-in runs on the CLIENT's own VAD and the `interrupt` control, never on STT (see the module
    docstring), so holding the gate must leave it untouched: the user talking over an open card still
    ends the turn — now without buying a transcript on the way."""
    sid = "card-barge"
    ctx = Ctx(sid)

    async def asking_tool(args, c):
        await interaction.ask_approval(c, "rm -rf /tmp/algo")
        return "hecho"

    register_tool("probe", asking_tool)
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})
    scaled.setattr(vs, "agentic_loop", announce_run_report("probe", ctx))

    async def go():
        RecordingTts.t0 = asyncio.get_running_loop().time()
        events.register(sid)
        runner = asyncio.create_task(session.run())
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "ejecuta eso"})})
        for _ in range(600):
            if interaction.has_pending(sid):
                break
            await asyncio.sleep(0.005)
        await asyncio.sleep(POLL * 2)
        ws.incoming.put_nowait({"text": json.dumps({"type": "interrupt", "turn": session._turn_no})})
        for _ in range(600):
            if "interrupted" in ws.types():
                break
            await asyncio.sleep(0.01)
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)
        events.unregister(sid)

    asyncio.run(go())

    assert "interrupted" in ws.types(), "a barge-in through a held gate must still reach the turn"
    assert session._turn_task is None or session._turn_task.done()


def test_the_held_bracket_is_marked_so_the_client_can_tell_it_apart(scaled, clean_registry):
    """The client cannot gate on a bracket it cannot recognise, and it was sent the same frame as real
    speech. Measured live: he waited out the card in silence, then said "Hola" — his own VAD barged in,
    force-opened the gate and replayed the pre-roll ring, and "Hola" came back as a user turn. `hold`
    is the difference between "she is talking, cut her off" and "a card is open, there is nothing to
    cut"; a real segment's audio_start must never carry it."""
    ws, session, go = drive_card_turn(scaled, "card-marked")

    marks = asyncio.run(go())

    hi = marks["answered"]
    starts = [(t, f) for t, f in ws.frames if f["type"] == "audio_start"]
    assert starts[-1][0] > hi, "she must speak again after the card, or this proves nothing"
    during = [f for t, f in starts if t <= hi]
    assert during[-1].get("hold") is True, f"the hold's bracket is unmarked: {during[-1]!r}"
    after = [f for t, f in starts if t > hi]
    assert all(not f.get("hold") for f in after), f"a real segment was marked as a hold: {after!r}"


def test_a_cancelled_turn_hands_a_held_microphone_back(scaled, clean_registry):
    """The client no longer force-opens its own gate behind a hold, so every server exit path must
    close the bracket — and cancellation was the one with no owner. `interrupted` used to be the
    reopen, but the poll can re-hold in the window between that frame and the cancel landing, and
    then nothing answers the last audio_start ever again."""
    sid = "card-cancel"
    ctx = Ctx(sid)

    async def asking_tool(args, c):
        await interaction.ask_approval(c, "rm -rf /tmp/algo")
        return "hecho"

    register_tool("probe", asking_tool)
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})
    scaled.setattr(vs, "agentic_loop", announce_run_report("probe", ctx))

    async def go():
        RecordingTts.t0 = asyncio.get_running_loop().time()
        events.register(sid)
        runner = asyncio.create_task(session.run())
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "ejecuta eso"})})
        for _ in range(600):
            if session._mic_hold:  # the announcement idled out and the hold took the bracket over
                break
            await asyncio.sleep(0.005)
        assert session._mic_hold, "the hold never took the bracket — nothing to cancel through"
        ws.incoming.put_nowait({"text": json.dumps({"type": "interrupt", "turn": session._turn_no})})
        for _ in range(600):
            if session._turn_task is not None and session._turn_task.done():
                break
            await asyncio.sleep(0.01)
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)
        events.unregister(sid)

    asyncio.run(go())

    assert session._audio_open is False, "the cancelled turn left its held bracket open"
    assert session._mic_hold is False
    live = open_windows(ws.audio_events(), ws.frames[-1][0] + 1.0)
    assert live and live[-1][1] >= ws.frames[-1][0], f"the mic never reopened after the cut: {live}"


def test_a_card_that_wants_a_spoken_answer_does_not_shut_the_mic(scaled, clean_registry):
    """Where the line is drawn. `ask_user` over voice opens a card and returns at once — the answer is
    meant to arrive as an ordinary spoken turn, and it registers no pending request, so the hold
    excludes it by construction rather than by a special case. Only a card whose answer must come back
    through POST /input (approvals, ask_secret, request_credential, OAuth) closes the mic."""
    sid = "card-openended"
    ctx = Ctx(sid)

    async def open_ended(args, c):
        await interaction.open_input_card(sid, "¿cuál es la url?")
        await asyncio.sleep(30.0 * SCALE)
        return "hecho"

    register_tool("probe", open_ended)
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})
    scaled.setattr(vs, "agentic_loop", announce_run_report("probe", ctx))

    async def go():
        RecordingTts.t0 = asyncio.get_running_loop().time()
        events.register(sid)
        try:
            await run_turn(ws, session)
        finally:
            events.unregister(sid)

    asyncio.run(go())

    live = open_windows(ws.audio_events(), ws.frames[-1][0])
    assert max((b - a for a, b in live), default=0.0) > 4.0 * SCALE, (
        f"a non-blocking card must leave the mic open so she can be answered out loud: {live}"
    )
