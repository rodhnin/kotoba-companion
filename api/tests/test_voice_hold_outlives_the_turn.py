"""The card outlives the turn, so the microphone hold has to outlive it too.

Measured live: a typed request opened a blocking card and shut the mic correctly, but when the
user's voice ended the turn, teardown released the microphone with the card still on screen —
every word after bought a transcript, a turn and a reply. The hold protects one utterance.

Two paths differ: an INLINE card is parked on the turn and clears when it is cancelled. A
DETACHED card survives the turn on purpose, so the cancellation never reaches it and no
`clear` frame is ever born.
"""
from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest

import kotoba.core.loop as loop
import kotoba.core.voice.session as vs
import kotoba.tools.registry as reg
from kotoba.core import deferred_exec, events, interaction
from kotoba.core import stream as sse
from kotoba.tools.registry import ToolSpec

SNAPSHOT = Path(__file__).parent / "snapshots" / "voice_hold_across_turns.json"

SCALE = 1 / 20.0
IDLE = 8.0 * SCALE
POLL = 2.0 * SCALE
TICK = 1.0 * SCALE
FIRST = 9.0 * SCALE
EVERY = 21.0 * SCALE

COMMAND = "sleep 20 && echo 'terminado a los veinte'"


class FakeWS:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.frames: list[dict] = []

    async def accept(self) -> None:
        pass

    async def receive(self):
        return await self.incoming.get()

    async def send_text(self, t: str) -> None:
        self.frames.append(json.loads(t))

    async def send_bytes(self, b: bytes) -> None:
        pass

    def types(self) -> list[str]:
        return [f["type"] for f in self.frames]

    def brackets(self) -> list[dict]:
        return [f for f in self.frames if f["type"] in ("audio_start", "audio_end", "interrupted")]


class FakeDB:
    async def ensure_session(self, sid):
        pass

    async def insert_turn(self, *a, **k):
        pass

    async def fetch_soul_config(self):
        return None


class SlowTts:
    """Streams until end() — so a turn can be interrupted while she is genuinely speaking."""

    def __init__(self, **kw) -> None:
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
        self.ended = True


class Ctx:
    def __init__(self, session_id: str, channel: str) -> None:
        self.session_id = session_id
        self.channel = channel
        self.register = "voice"
        self.model_role = "companion"
        self.call_id = "call-1"
        self.user_text = "ejecuta eso"
        self.approval = None
        self.run_id = ""


@pytest.fixture
def clean_registry():
    saved = dict(reg._REGISTRY)
    try:
        yield
    finally:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(saved)


@pytest.fixture
def scaled(monkeypatch):
    monkeypatch.setenv("KOTOBA_VOICE_MODE", "local")
    monkeypatch.setattr(loop, "_HEARTBEAT_FIRST", FIRST)
    monkeypatch.setattr(loop, "_HEARTBEAT_EVERY", EVERY)
    monkeypatch.setattr(loop, "_HEARTBEAT_TICK", TICK, raising=False)
    monkeypatch.setattr(vs, "SEGMENT_IDLE_SECS", IDLE)
    monkeypatch.setattr(vs, "MIC_HOLD_POLL_SECS", POLL, raising=False)
    monkeypatch.setattr(vs, "TtsClient", SlowTts)
    monkeypatch.setattr(deferred_exec, "_DEFERRED_APPROVAL_TIMEOUT", 60.0 * SCALE, raising=False)

    async def fake_load_context(request, db, session_id, **kw):
        return [{"role": "user", "content": request.messages[-1]["content"]}]

    async def no_memory(user_text, db):
        return None

    monkeypatch.setattr(vs, "load_context", fake_load_context)
    monkeypatch.setattr(vs, "extract_and_save_memory", no_memory)
    yield monkeypatch


def register_tool(name: str, execute) -> None:
    mod = types.SimpleNamespace(SCHEMA={"type": "function", "name": name}, __name__=f"t.{name}",
                                execute=execute, TIMEOUT=600)
    reg.register(ToolSpec(name=name, module=mod, schema=mod.SCHEMA, toolset="terminal", risk="exec"))


def speaking_loop(tool_name: str, ctx: Ctx, line: str):
    """The shape of a real turn: announce, run the tool, report."""

    async def fake_loop(items, sid, db, queue, patterns, **kw):
        await queue.put("Vale. ")
        await queue.put(sse.FLUSH_SENTINEL)
        await loop.execute_with_heartbeat(tool_name, {}, queue, patterns, ctx)
        await queue.put(line)
        return "Vale. " + line

    return fake_loop


def drain(sid: str) -> list[dict]:
    q = events.event_queues.get(sid)
    out: list[dict] = []
    while q is not None and not q.empty():
        out.append(q.get_nowait())
    return out


def clears(frames: list[dict]) -> list[dict]:
    return [f for f in frames
            if f.get("type") == "task" and f.get("kind") == "need_input" and f.get("mode") == "clear"]


def cards(frames: list[dict]) -> list[dict]:
    return [f for f in frames
            if f.get("type") == "task" and f.get("kind") == "need_input" and f.get("mode") == "approval"]


async def drained_clear(sid: str, limit: float = 5.0) -> list[dict]:
    """The card stops being pending one loop iteration BEFORE its clear frame is queued: the timeout
    cancels the waiting Future inside its own handle, and the detached task only reaches the emit when
    it next runs. Draining at the moment `has_pending` flips therefore reads an empty queue — rarely,
    and only when the poll's timer comes due just before the deadline."""
    frames: list[dict] = []
    end = asyncio.get_running_loop().time() + limit
    while asyncio.get_running_loop().time() < end:
        frames += drain(sid)
        if clears(frames):
            break
        await asyncio.sleep(0.005)
    return frames


async def wait_for(pred, limit: float = 5.0) -> bool:
    end = asyncio.get_running_loop().time() + limit
    while asyncio.get_running_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.005)
    return False


# ---- where the `clear` frame lives, and where it is never born -------------------------------------


def test_an_inline_cards_clear_frame_really_leaves_the_server(scaled, clean_registry):
    """The half the previous round got right. A card the TURN is parked on dies with the turn:
    supersede cancels it, `_await_response` raises out of its wait and clears its own card."""
    sid = "hold-inline"
    ctx = Ctx(sid, "text")

    async def asking_tool(args, c):
        return "ok " + await interaction.ask_approval(c, COMMAND)

    register_tool("probe", asking_tool)
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})
    scaled.setattr(vs, "agentic_loop", speaking_loop("probe", ctx, "Listo. "))

    async def go():
        events.register(sid)
        runner = asyncio.create_task(session.run())
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "ejecuta eso"})})
        assert await wait_for(lambda: interaction.has_pending(sid)), "no card opened"
        opened = cards(drain(sid))
        after: list[dict] = []
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "Hola."})})
        # The new turn re-emits the same call and opens its OWN card, so has_pending stays true —
        # the question is only whether the DEAD turn's card was named in a clear.
        assert await wait_for(
            lambda: bool(after.extend(drain(sid)) or True) and any(
                f.get("request_id") == opened[0]["request_id"] for f in clears(after)
            )
        ), "the dead turn's card was never cleared"
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, 10)
        events.unregister(sid)
        return opened, after

    opened, after = asyncio.run(go())

    assert len(opened) == 1, f"expected one card, got {opened!r}"
    ids = [f.get("request_id") for f in clears(after)]
    assert opened[0]["request_id"] in ids, (
        f"no clear named the dead turn's card {opened[0]['request_id']!r}; clears were {ids!r}"
    )


def test_a_detached_cards_clear_frame_is_never_born(scaled, clean_registry):
    """The half that was claimed and is false. core.deferred_exec runs the approval in a DETACHED task
    so the card can outlive the voice turn; `turns.supersede` cancels the turn and not that task, so
    `_await_response` is never cancelled and emits nothing. Nothing eats the frame — there is none.

    And the control it leaves on screen is live: pressing Yes on it runs the command, turns later."""
    sid = "hold-detached"
    ctx = Ctx(sid, "voice")
    ran: list[str] = []

    async def runner() -> str:
        ran.append(COMMAND)
        return "I ran it."

    async def deferring_tool(args, c):
        already = deferred_exec.schedule(c, COMMAND, runner, label=COMMAND, step_kind="shell")
        return already or "I asked for your permission on screen."

    register_tool("probe", deferring_tool)
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})
    scaled.setattr(vs, "agentic_loop", speaking_loop("probe", ctx, "Aprueba y lo hago. "))

    async def go():
        events.register(sid)
        runner_task = asyncio.create_task(session.run())
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "ejecuta eso"})})
        assert await wait_for(lambda: interaction.has_pending(sid)), "no card opened"
        assert await wait_for(lambda: "turn_end" in ws.types()), "turn 1 never ended"
        drain(sid)
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "Hola."})})
        assert await wait_for(lambda: ws.types().count("turn_end") >= 2), "turn 2 never ended"
        survived = interaction.has_pending(sid)
        after = drain(sid)
        pressed_yes = interaction.resolve(sid, {"approved": True, "always": False})
        assert await wait_for(lambda: bool(ran))
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner_task, 10)
        events.unregister(sid)
        return survived, after, pressed_yes

    survived, after, pressed_yes = asyncio.run(go())

    assert survived, "the detached card should outlive its turn — that is what deferring is for"
    assert clears(after) == [], f"a clear was emitted after all: {clears(after)!r}"
    assert pressed_yes, "the orphan's Yes button was still wired to a live Future"
    assert ran == [COMMAND], "pressing Yes on a dead turn's card ran nothing / ran it twice"


# ---- the hold, across more than one utterance ------------------------------------------------------


def _run_two_utterance_turn(scaled, sid: str):
    """Turn 1 defers a card and speaks; he talks over her while she IS speaking, which kills the turn
    and correctly leaves the detached card up. Returns the frames the server sent."""
    ctx = Ctx(sid, "voice")
    ran: list[str] = []

    async def never() -> str:
        ran.append(COMMAND)
        return "ran"

    async def deferring_tool(args, c):
        already = deferred_exec.schedule(c, COMMAND, never, label=COMMAND, step_kind="shell")
        return already or "I asked for your permission on screen."

    register_tool("probe", deferring_tool)
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})
    scaled.setattr(vs, "agentic_loop", speaking_loop("probe", ctx, "Aprueba y lo hago. "))

    async def go():
        events.register(sid)
        runner_task = asyncio.create_task(session.run())
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "ejecuta eso"})})
        assert await wait_for(lambda: interaction.has_pending(sid)), "no card opened"
        assert await wait_for(lambda: "audio_start" in ws.types()), "she never started speaking"
        # Utterance 1 — over her actual voice, so the bracket it cuts is a segment's and not a hold.
        ws.incoming.put_nowait({"text": json.dumps({"type": "interrupt", "turn": 1})})
        assert await wait_for(lambda: "interrupted" in ws.types()), "the turn was not cut"
        held = await wait_for(
            lambda: any(f["type"] == "audio_start" and f.get("hold")
                        for f in ws.frames[ws.types().index("interrupted"):])
        )
        state = {"held_after_interrupt": held, "card_after_interrupt": interaction.has_pending(sid)}
        # Utterance 2 — behind the hold this time: it takes the MIC back and leaves the card standing.
        ws.incoming.put_nowait({"text": json.dumps({"type": "interrupt", "turn": 1})})
        assert await wait_for(lambda: session._mic_yielded), "his voice never took the mic back"
        state["card_survived_second_utterance"] = interaction.has_pending(sid)
        assert await wait_for(lambda: not session._mic_hold), "the hold never stepped aside"
        # The card ends on its OWN clock — the scaled deferred window — never on the user's voice.
        assert await wait_for(lambda: not interaction.has_pending(sid)), "the card's clock never ended it"
        state["clears"] = clears(await drained_clear(sid))
        state["yes_after"] = interaction.resolve(sid, {"approved": True, "always": False})
        await asyncio.sleep(0.05)
        state["ran"] = list(ran)
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner_task, 10)
        events.unregister(sid)
        return state

    return ws, asyncio.run(go())


def test_the_hold_comes_back_after_the_utterance_that_killed_the_turn(scaled, clean_registry):
    """The measured defect. His voice ends the turn; the card is still up; the mic must go straight
    back to being shut instead of being handed over with the card on screen."""
    ws, state = _run_two_utterance_turn(scaled, "hold-across")

    assert state["card_after_interrupt"], "the card should have survived the barge-in"
    assert state["held_after_interrupt"], (
        f"no hold followed the interrupt — the mic was live with a card up: {ws.brackets()!r}"
    )


def test_the_second_utterance_frees_the_mic_and_the_card_ends_on_its_own_clock(scaled, clean_registry):
    """The way out changed sides. This used to pin "behind a held bracket the user's words can only
    mean take that away" — that reading was retired, and for a while this test kept passing on the old
    wording only because the scaled deferred window expired inside its wait. What it actually guards is stated honestly here: the
    user's voice frees the MIC and nothing else; the card stands until its own clock ends it; and that
    ending keeps its hygiene — a NAMED clear, a dead Yes, and a command that never ran."""
    ws, state = _run_two_utterance_turn(scaled, "hold-escape")

    assert state["card_survived_second_utterance"], "his voice took the card away — retired behavior"
    assert state["clears"], "the expired card never cleared on screen"
    assert all(f.get("request_id") for f in state["clears"]), (
        f"a clear with no request_id wipes every card on screen: {state['clears']!r}"
    )
    assert state["yes_after"] is False, "the expired card's Yes was still wired to a live Future"
    assert state["ran"] == [], "an unanswered card still ran its command"


def test_the_trace_the_client_test_replays_is_written_from_this_run(scaled, clean_registry):
    """The bridge to tests/voice-hold.test.mjs. It replays THIS sequence through the real MicUplink,
    which is the only thing that can say whether a hold the server sent actually shut a microphone."""
    ws, _ = _run_two_utterance_turn(scaled, "hold-trace")

    trace = ws.brackets()
    SNAPSHOT.write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")
    # WRITTEN, and the assertions below are what stop the client test replaying something vacuous. It
    # cannot be a frozen comparison: the count of hold brackets follows the clock, and the same code
    # yields six of them here and four on a slower machine — measured. What must hold either way is
    # the SHAPE, so that is what is asserted: an interrupt, a hold after it, and nothing left open.
    kinds = [(f["type"], bool(f.get("hold"))) for f in trace]
    assert ("interrupted", False) in kinds, f"no interrupt in the trace: {kinds!r}"
    assert kinds.index(("audio_start", True)) > kinds.index(("interrupted", False)), (
        f"the hold must come after the interrupt or there is nothing to replay: {kinds!r}"
    )
    after = kinds[kinds.index(("interrupted", False)) + 1:]
    assert all(held for kind, held in after if kind == "audio_start"), (
        f"a bracket opened after the interrupt without the hold, so the mic was let go: {kinds!r}")
    # The catastrophic shape, named directly. Counted instead, the interrupt's own allowance absorbed a
    # dangling hold and a trace that ends with the microphone SHUT passed — the exact failure this file
    # exists to catch, admitted by the arithmetic meant to catch it.
    assert kinds[-1][0] != "audio_start", (
        f"the trace ends on an open bracket, so the replay leaves the microphone shut: {kinds!r}")


# ---- the owner closes on every exit ----------------------------------------------------------------


@pytest.mark.parametrize("ending", ["answered", "expired", "socket_dropped"])
def test_the_hold_is_handed_back_however_the_card_ends(scaled, clean_registry, ending):
    """The invariant with a name: a mic left shut with no card open is the catastrophic failure. The
    owner is the socket's, not the turn's, so each ending is measured against the same predicate —
    `interaction.has_pending` going false, which is the same `finally` that pops the request."""
    sid = f"hold-close-{ending}"
    ctx = Ctx(sid, "voice")

    async def never() -> str:
        return "ran"

    async def deferring_tool(args, c):
        already = deferred_exec.schedule(c, COMMAND, never, label=COMMAND, step_kind="shell")
        return already or "I asked for your permission on screen."

    register_tool("probe", deferring_tool)
    ws = FakeWS()
    session = vs.VoiceSession(ws, sid, db=FakeDB(), soul_patterns={})
    scaled.setattr(vs, "agentic_loop", speaking_loop("probe", ctx, "Aprueba y lo hago. "))

    async def go():
        events.register(sid)
        runner_task = asyncio.create_task(session.run())
        ws.incoming.put_nowait({"text": json.dumps({"type": "text", "text": "ejecuta eso"})})
        assert await wait_for(lambda: interaction.has_pending(sid)), "no card opened"
        assert await wait_for(lambda: session._mic_hold, 8.0), "the hold never engaged"
        if ending == "answered":
            interaction.resolve(sid, {"approved": False, "always": False})
        elif ending == "expired":
            pass  # _DEFERRED_APPROVAL_TIMEOUT is scaled down; just wait it out
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner_task, 15)
        if ending == "expired":
            assert await wait_for(lambda: not interaction.has_pending(sid), 8.0)
        events.unregister(sid)
        deferred_exec.cancel(sid)

    asyncio.run(go())

    assert session._mic_hold is False, "the hold outlived the socket that owned it"
    starts = [f for f in ws.brackets() if f["type"] == "audio_start" and f.get("hold")]
    ends = [f for f in ws.brackets() if f["type"] in ("audio_end", "interrupted")]
    assert starts and ends, f"nothing to balance: {ws.brackets()!r}"
    assert ws.brackets()[-1]["type"] != "audio_start", (
        f"the last thing the client heard was a bracket opening: {ws.brackets()!r}"
    )
