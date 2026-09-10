"""What a THIRD caller — the CLI — exposed in the two existing turn orchestrators.

1. The WS persisted the reply inside its try body; barge-in dropped it, and mark_announced never ran.
2. Only `produce()` had an exception net; a raise outside it killed the turn in silence.
3. An announce sentinel arrived as a text frame, so the one-shot attachment take ate an upload.
4. An emptied reply made the WS persist raw text instead of what she said, so search misquoted her.
5. Six of seven askers skipped the listener check and blocked into a dead queue — a false refusal.
6. `ask_user` gated secrets on `mode`, not channel, refusing them only in work mode.
7. An inline approval denial still logged an audit row saying the command ran.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from kotoba.core import events, interaction


@pytest.fixture(autouse=True)
def _clean_queues():
    events.event_queues.clear()
    yield
    events.event_queues.clear()


# --- 5. no channel, no card -------------------------------------------------------------------------

def test_an_approval_with_no_listener_returns_at_once():
    async def go():
        started = asyncio.get_running_loop().time()
        got = await interaction.request_approval("nobody", "rm -rf build", channel="text")
        return got, asyncio.get_running_loop().time() - started

    got, elapsed = asyncio.run(go())
    assert got == (False, False)
    assert elapsed < 1.0, f"blocked {elapsed:.1f}s into a dead channel (the window is 180s on text)"


def test_an_input_request_with_no_listener_returns_at_once():
    async def go():
        started = asyncio.get_running_loop().time()
        got = await interaction.request_input("nobody", "your token?", "text", timeout=180.0)
        return got, asyncio.get_running_loop().time() - started

    got, elapsed = asyncio.run(go())
    assert got is None
    assert elapsed < 1.0, f"blocked {elapsed:.1f}s"


def test_a_registered_listener_still_gets_its_card():
    """The guard must not break the path it protects."""
    async def go():
        q = events.register("s1")
        task = asyncio.create_task(interaction.request_approval("s1", "npm test", channel="text"))
        frame = None
        while frame is None or frame.get("kind") != "need_input":
            frame = await asyncio.wait_for(q.get(), timeout=2)
        interaction.resolve("s1", {"approved": True, "always": False}, frame["request_id"])
        return await asyncio.wait_for(task, timeout=2)

    assert asyncio.run(go()) == (True, False)


# --- 6. ask_user ------------------------------------------------------------------------------------

def _ctx(**kw):
    from kotoba.db.database import Database

    base = {"session_id": "s-ask", "mode": "companion", "channel": "voice", "db": Database("sqlite:///:memory:")}
    base.update(kw)
    return types.SimpleNamespace(**base)


@pytest.mark.parametrize("kind", ["key", "secret"])
@pytest.mark.parametrize("mode,channel", [("companion", "text"), ("work", "voice"), ("work", "text")])
def test_ask_user_refuses_a_secret_on_every_blocking_path(kind, mode, channel):
    """The blocking branch hands the typed value straight back to the model, so a secret must never take
    it. The refusal used to live inside the work check alone — a CLI turn (companion + text) walked
    right past it and would have put the raw credential in the transcript and the scrollback."""
    from kotoba.tools.builtin import ask_user

    out = asyncio.run(ask_user.execute({"prompt": "your API key?", "kind": kind}, _ctx(mode=mode, channel=channel)))
    assert "Don't collect secrets with ask_user" in out


def test_the_web_key_card_still_works_over_voice():
    """Not a leak and must not be broken: the non-blocking card's value goes to POST /input and is stored
    backend-only — the model is told a confirmation is coming, never the value.

    The listener is what makes this a real call: the guidance is now written from whether the card was
    drawn, and this fixture clears every queue."""
    from kotoba.tools.builtin import ask_user

    events.register("s-ask")
    out = asyncio.run(ask_user.execute(
        {"prompt": "your API key?", "kind": "key", "name": "my_openai_key"}, _ctx(channel="voice")
    ))
    assert "never shown to me" in out and "do NOT wait here" in out


def test_ask_user_blocks_on_a_text_channel_and_not_on_voice():
    """A terminal answers the card in place; a voice turn must stay non-blocking or the socket dies."""
    from kotoba.tools.builtin import ask_user

    events.register("s-ask")  # a live call always has one; the card's guidance depends on it
    voice = asyncio.run(ask_user.execute({"prompt": "which file?"}, _ctx(channel="voice")))
    assert "do NOT wait here" in voice
    events.event_queues.clear()

    async def text_turn():
        q = events.register("s-ask")
        task = asyncio.create_task(ask_user.execute({"prompt": "which file?"}, _ctx(channel="text")))
        frame = None
        while frame is None or frame.get("kind") != "need_input":
            frame = await asyncio.wait_for(q.get(), timeout=2)
        assert frame.get("wait") is True, "a blocking card must tell the client to answer it"
        interaction.resolve("s-ask", {"value": "notes.md"}, frame["request_id"])
        return await asyncio.wait_for(task, timeout=2)

    assert "notes.md" in asyncio.run(text_turn())


# --- 7. a denial is not an execution ----------------------------------------------------------------

def test_an_inline_denial_is_not_audited_as_executed():
    from kotoba.core import deferred_exec
    from kotoba.tools.action import execute_code, shell

    class _Gate:
        async def confirm(self, *a, **kw):
            return False

        def would_auto_allow(self, *a, **kw):
            return False

    for mod, args in ((shell, {"command": "rm -rf build"}), (execute_code, {"code": "import os"})):
        ctx = _ctx(channel="text", approval=_Gate(), call_id=f"call-{mod.__name__}")
        out = asyncio.run(mod.execute(args, ctx))
        assert "held off" in out
        assert deferred_exec.ran_nothing(ctx, ctx.call_id), (
            f"{mod.__name__}: the loop would write an audit row saying a refused command ran"
        )


# --- 1-4. the WebSocket orchestrator, driven for real ------------------------------------------------

class _FakeWS:
    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, payload):
        self.sent.append(payload)

    async def accept(self):
        pass


def _session(monkeypatch, *, reply="Here you go.", raise_in_context=False, spoke=True):
    """A VoiceSession with the loop, TTS and context build faked — nothing but the orchestration is real."""
    import kotoba.core.voice.session as vs
    from kotoba.db.database import Database

    async def _loop(*a, **kw):
        return reply

    async def _ctxbuild(*a, **kw):
        if raise_in_context:
            raise RuntimeError("context build blew up")
        return [{"role": "user", "content": "hi"}]

    monkeypatch.setattr(vs, "agentic_loop", _loop)
    monkeypatch.setattr(vs, "load_context", _ctxbuild)

    db = Database("sqlite:///:memory:")
    s = vs.VoiceSession(_FakeWS(), "s-ws", db=db, soul_patterns={}, mcp=None)

    spoken: list[str] = []

    async def _pump(queue, turn_no):
        while True:
            item = await queue.get()
            if item is vs.sse.DONE_SENTINEL:
                break
        return spoke

    async def _speak(line, turn_no):
        spoken.append(line)

    monkeypatch.setattr(s, "_pump_speech", _pump)
    monkeypatch.setattr(s, "_speak_line", _speak)
    return s, db, spoken


def _frames(ws):
    import json
    return [json.loads(x).get("type") for x in ws.sent]


async def _rows(db):
    async with db.conn.execute("SELECT role, content FROM turns ORDER BY rowid") as cur:
        return [(r["role"], r["content"]) for r in await cur.fetchall()]


def test_a_barge_in_after_the_model_finished_keeps_the_reply(monkeypatch):
    """The producer routinely finishes before the speech drain does — the pump below is still speaking
    when the user barges in. Cancelling there used to drop a fully generated turn out of her own
    history."""
    async def go():
        s, db, _ = _session(monkeypatch, reply="I found three of them.")
        await db.connect()

        async def _slow_pump(queue, turn_no):
            await asyncio.sleep(30)
            return True

        monkeypatch.setattr(s, "_pump_speech", _slow_pump)
        task = asyncio.create_task(s._run_turn("find them", False, 1))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        rows = await _rows(db)
        await db.close()
        return rows

    rows = asyncio.run(go())
    assert ("assistant", "I found three of them.") in rows, rows


def test_a_failure_outside_the_producer_still_answers(monkeypatch):
    async def go():
        s, db, spoken = _session(monkeypatch, raise_in_context=True)
        await db.connect()
        await s._run_turn("hola", False, 1)
        await db.close()
        return spoken, _frames(s.ws)

    spoken, frames = asyncio.run(go())
    assert spoken, "the user got total silence"
    assert "turn_end" in frames, "the client was never told the turn was over"


def test_an_emptied_reply_persists_the_line_she_actually_said(monkeypatch):
    async def go():
        s, db, spoken = _session(monkeypatch, reply="https://example.com/only-a-url", spoke=False)
        await db.connect()
        await s._run_turn("link?", False, 1)
        rows = await _rows(db)
        await db.close()
        return spoken, rows

    spoken, rows = asyncio.run(go())
    assert spoken, "a filtered-empty reply must still say something"
    stored = [c for r, c in rows if r == "assistant"]
    assert stored == spoken, f"the DB kept text nobody heard: {stored!r} vs said {spoken!r}"


def test_an_announce_sentinel_leaves_a_pending_attachment_alone(monkeypatch):
    """take() is one-shot: the file the user just uploaded must not be eaten by a '__work_done__'
    turn, which reaches the socket as a TEXT frame like any typed message.

    A sentinel writes no user row, so it also has to say `persisted=False` — the builder appends the
    message off that alone, and a sentinel that claimed otherwise would hand the model a turn with
    nothing being asked. The typed turn at the end is the other half: it DID write its row, and says
    so, or the message reaches the model twice."""
    async def go(text):
        import kotoba.core.voice.session as vs
        from kotoba.core import attachments

        seen = {}

        async def _ctxbuild(request, db, sid, consume_attachments=True, connected_mcp=None,
                            persisted=False):
            seen.update(consume=consume_attachments, persisted=persisted)
            return [{"role": "user", "content": "x"}]

        monkeypatch.setattr(vs, "load_context", _ctxbuild)
        s, db, _ = _session(monkeypatch)
        monkeypatch.setattr(vs, "load_context", _ctxbuild)   # _session re-stubs it; ours must win
        await db.connect()
        attachments.add("s-ws", [{"type": "input_image", "image_url": "data:image/png;base64,AA"}])
        await s._run_turn(text, True, 1)
        left = attachments.take("s-ws")
        await db.close()
        return seen, left

    seen, left = asyncio.run(go("__work_done__"))
    assert seen["consume"] is False, "the sentinel turn consumed the attachment"
    assert seen["persisted"] is False, "a sentinel wrote no row and must not claim one"
    assert left, "the user's upload was swallowed by an announce turn"

    spoken, _ = asyncio.run(go("hola"))
    assert spoken["persisted"] is True, "a turn that persisted his message must say so"


def test_hanging_up_fires_two_cancels_and_still_keeps_everything(monkeypatch):
    """endCall() fires POST /leave (→ turns.supersede) AND the socket close in one tick, so the task that
    persists is cancelled TWICE. The second one used to land inside the finally's first await, skipping
    mark_announced, the memory extraction and turns.clear — the announce flag being exactly what
    work_state re-injects as "ANNOUNCE the result now" on every following turn."""
    import kotoba.core.voice.session as vs_mod
    from kotoba.core import turns, work_state

    async def _no_memory(*a, **kw):
        return None

    async def go(gap):
        s, db, _ = _session(monkeypatch, reply="Ya lo tengo.")
        await db.connect()

        async def slow_pump(queue, turn_no):
            await asyncio.sleep(30)      # still speaking when they hang up
            return True

        monkeypatch.setattr(s, "_pump_speech", slow_pump)
        monkeypatch.setattr(vs_mod, "extract_and_save_memory", _no_memory)
        work_state.start("s-ws", "una tarea")
        work_state.finish("s-ws", "hecho", [])
        task = asyncio.create_task(s._run_turn("hola", False, 1))
        turns.register("s-ws", task)
        await asyncio.sleep(0.05)
        task.cancel()                    # 1: supersede from /leave
        await asyncio.sleep(gap)
        task.cancel()                    # 2: the socket teardown
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0.15)        # let the shielded write finish
        rows = await _rows(db)
        state = work_state.get("s-ws")
        await db.close()
        return rows, state, turns._active.get("s-ws")

    for gap in (0.0, 0.0002, 0.0005, 0.001):
        work_state.clear("s-ws")
        turns._active.pop("s-ws", None)
        rows, state, active = asyncio.run(go(gap))
        assert ("assistant", "Ya lo tengo.") in rows, f"gap={gap}: the reply was lost"
        assert state["announced"], f"gap={gap}: the announce flag stayed stuck → she re-announces forever"
        assert active is None, f"gap={gap}: turns registry kept a dead task"
