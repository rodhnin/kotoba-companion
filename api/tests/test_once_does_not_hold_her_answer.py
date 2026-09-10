"""The one-shot command's teardown, and what it is allowed to make the user wait for.

`ask()` starts a memory extraction in the turn's finally and `close()` drains it, since this process
does not outlive its own detached tasks the way the server does. That drain is bounded but not free:
the provider client sets no read timeout, so a slow extractor spends the whole limit — measured at ten
seconds of silence after the answer had already printed, with Ctrl+C unable to shorten it because the
handler pointed at the turn's task, which was already done. The interrupted path also exited on a blank
screen: the partial answer was persisted and read back by the next turn, but never shown to the person
who typed the question.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time

import pytest
from conftest import posix_only


def _run(coro):
    return asyncio.run(coro)


def _record(monkeypatch, module):
    """Every print the entry point makes, with the moment it made it. A module-global `print` shadows
    the builtin, so this is the whole of its output and none of it reaches the suite's terminal."""
    printed: list[tuple[float, str]] = []

    def fake(*args, **kw):
        printed.append((time.monotonic(), " ".join(str(a) for a in args)))

    monkeypatch.setattr(module, "print", fake, raising=False)
    return printed


@pytest.fixture
def once(monkeypatch):
    """`--once` with a stubbed loop and a memory extraction that never returns, on a short drain."""
    from kotoba.cli import __main__ as entry
    from kotoba.cli import session as cli_session

    async def fake_loop(items, sid, db, stream, patterns, **kw):
        await stream.put("Ya está.")

    async def hung_extract(user_msg, db):
        await asyncio.sleep(3600)

    monkeypatch.setattr(cli_session, "agentic_loop", fake_loop)
    monkeypatch.setattr(cli_session, "extract_and_save_memory", hung_extract)
    monkeypatch.setattr(cli_session, "_DRAIN_LIMIT", 1.0)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    return entry


def test_her_answer_is_printed_before_the_drain(once, monkeypatch):
    printed = _record(monkeypatch, once)
    start = time.monotonic()
    code = _run(once._once("vivo en Barcelona"))
    end = time.monotonic()

    assert code == 0
    assert [text for _t, text in printed] == ["Ya está."]
    # Measured against the RETURN, not the launch: starting the engine is a real cost and not the one
    # under test. The whole drain has to come after her answer, not before it.
    assert end - start >= 1.0, "the drain never ran — this test would prove nothing otherwise"
    held = end - printed[0][0]
    assert held >= 0.9, f"her answer was withheld until the drain was over, {held:.2f}s before the exit"


def test_ctrl_c_during_the_drain_ends_it(once, monkeypatch):
    """The turn's task is done by then, so the handler installed for it is a no-op. Something else has
    to answer the second press or the terminal is hung for the whole limit."""
    printed = _record(monkeypatch, once)
    sessions = []
    opened = once.Session.open

    async def remember(*a, **kw):
        session = await opened(*a, **kw)
        sessions.append(session)
        return session

    monkeypatch.setattr(once.Session, "open", remember)

    async def go():
        task = asyncio.create_task(once._once("vivo en Barcelona"))
        while not printed:
            await asyncio.sleep(0.01)
        sessions[0].abandon_extractions()      # what SIGINT is pointed at once the turn is done
        return await task

    assert _run(go()) == 0
    # From her answer to the exit, not from launch: starting the engine is not what this measures, and
    # a loaded machine would make that reading say anything.
    waited = time.monotonic() - printed[0][0]
    assert waited < 0.9, f"abandoning the extraction did not shorten the {waited:.2f}s drain"


def test_an_interrupted_turn_shows_what_she_had_said_and_does_not_wait(once, monkeypatch):
    """Cancellation eats ask()'s return value, so the buffered chunks are the only copy the screen can
    get — and Ctrl+C means give me my prompt back, not ten more silent seconds."""
    from kotoba.cli.session import Session

    printed = _record(monkeypatch, once)

    async def cut_off(self, text, on_text=None, on_face=None):
        # What the real ask() hands a renderer: already filtered of tags, and still carrying the hole
        # the stripped one left, which only _tidy closes.
        on_text("Voy a  explicarte")
        hung = asyncio.create_task(asyncio.sleep(3600))
        self._extractions.add(hung)
        hung.add_done_callback(self._extractions.discard)
        raise asyncio.CancelledError

    monkeypatch.setattr(Session, "ask", cut_off)
    code = _run(once._once("cuéntame"))
    after = time.monotonic() - printed[-1][0]

    assert code == 130
    said = [text for _t, text in printed]
    assert said[0] == "Voy a explicarte", said
    assert any("interrupted" in s for s in said), said
    assert after < 0.9, f"the prompt came back {after:.2f}s after «— interrupted —»"


@posix_only("the Unix selector event loop (asyncio.unix_events)")
def test_a_loop_without_signal_handlers_still_runs_and_still_closes(once, monkeypatch):
    """Only the Unix selector loop implements add_signal_handler. Unguarded, `kotoba --once` is a
    traceback on Windows; unguarded in the teardown it skips close(), and the database's worker thread
    is not a daemon, so the process would never exit."""
    printed = _record(monkeypatch, once)
    closed = []
    real_close = once.Session.close

    async def note_close(self):
        closed.append(True)
        await real_close(self)

    def refuse(self, sig, callback, *args):
        raise NotImplementedError

    monkeypatch.setattr(once.Session, "close", note_close)
    monkeypatch.setattr(asyncio.unix_events._UnixSelectorEventLoop, "add_signal_handler", refuse)

    assert _run(once._once("hola")) == 0
    assert [text for _t, text in printed] == ["Ya está."]
    assert closed == [True], "the engine was never stopped"


# --- what the drain is allowed to cost the turn -------------------------------------------------------

def test_a_failing_extraction_costs_neither_the_reply_nor_the_history(monkeypatch):
    """A memory failure must never take a turn that already produced a good answer. It runs detached
    for exactly that reason, and close() must not turn the failure back into an exception here."""
    from kotoba.cli import session as cli_session
    from kotoba.cli.session import Session

    async def fake(items, sid, db, stream, patterns, **kw):
        await stream.put("Ya está.")

    async def boom(user_msg, db):
        raise RuntimeError("the memory store is unwritable")

    monkeypatch.setattr(cli_session, "agentic_loop", fake)
    monkeypatch.setattr(cli_session, "extract_and_save_memory", boom)

    async def go():
        session = await Session.open()
        reply = await session.ask("vivo en Barcelona")
        async with session.engine.db.conn.execute(
            "SELECT role, content FROM turns ORDER BY rowid"
        ) as cur:
            rows = [(r["role"], r["content"]) for r in await cur.fetchall()]
        await session.close()
        return reply, rows

    reply, rows = _run(go())
    assert reply == "Ya está."
    assert rows == [("user", "vivo en Barcelona"), ("assistant", "Ya está.")]


def test_one_turn_extracts_once_even_when_it_was_cut_short(monkeypatch):
    """Duplicate facts are the failure this system tracks, and the drain reads the same set the turn's
    finally writes to. A cancelled turn still extracts — its words were still typed — but only once."""
    from kotoba.cli import session as cli_session
    from kotoba.cli.session import Session

    async def slow(items, sid, db, stream, patterns, **kw):
        for word in ("Voy ", "a ", "explicarte"):
            await stream.put(word)
            await asyncio.sleep(0.05)

    fed: list[str] = []

    async def counting(user_msg, db):
        fed.append(user_msg)

    monkeypatch.setattr(cli_session, "agentic_loop", slow)
    monkeypatch.setattr(cli_session, "extract_and_save_memory", counting)

    async def go():
        session = await Session.open()
        task = asyncio.create_task(session.ask("me mudé a Madrid"))
        await asyncio.sleep(0.08)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await session.close()

    _run(go())
    assert fed == ["me mudé a Madrid"], fed


def test_the_extractor_writes_where_the_environment_points(monkeypatch, tmp_path):
    """Proof of isolation, not of plumbing: every path user_memory uses is resolved per call from
    KOTOBA_MEMORY_DIR, so a scratch env cannot reach the store the running Kotoba reads."""
    from kotoba.cli import session as cli_session
    from kotoba.cli.session import Session
    from kotoba.core import llm, memory, user_memory

    store = tmp_path / "scratch-memory"
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(store))

    async def fake(items, sid, db, stream, patterns, **kw):
        await stream.put("Anotado.")

    async def fake_extract(prompt, **kw):
        return ('{"user_name": null, "companion_name": null, '
                '"facts": [{"fact": "Lives in Barcelona", "topic": "places"}]}')

    monkeypatch.setattr(cli_session, "agentic_loop", fake)
    monkeypatch.setattr(cli_session, "extract_and_save_memory", memory.extract_and_save_memory)
    monkeypatch.setattr(llm, "utility_extract", fake_extract)

    async def go():
        session = await Session.open()
        await session.ask("vivo en Barcelona")
        await session.close()

    _run(go())
    assert user_memory.existing_facts() == ["Lives in Barcelona"]
    assert store.is_dir(), "the fact landed somewhere else entirely"


def test_a_cli_session_id_can_never_be_a_browsers():
    """`events.register` REPLACES whatever queue a session id already held, so a collision would not be
    shared state — it would silence the other client. The browser mints crypto.randomUUID(), which is
    dashed; a bare 32-hex uuid4 cannot land in that space whatever the draw."""
    from kotoba.cli.session import Session
    from kotoba.core import events

    async def go():
        made = [Session(engine=None) for _ in range(64)]
        try:
            return [s.session_id for s in made]
        finally:
            for s in made:
                events.unregister(s.session_id, s.queue)

    ids = _run(go())
    assert len(set(ids)) == len(ids), "two sessions in one process shared an id"
    assert all(len(i) == 32 and set(i) <= set("0123456789abcdef") for i in ids), ids
    assert not any("-" in i for i in ids), "a dashed id is the shape the browser hands out"


# --- `kotoba serve` ------------------------------------------------------------------------------------

def test_serve_leaves_no_backend_behind_when_the_web_spawn_fails(monkeypatch):
    """The backend is spawned first and into its OWN session, precisely so a terminal signal cannot
    reach it — which is also why a raise between the two spawns left it holding its port for good."""
    from kotoba.cli import serve

    children: list[subprocess.Popen] = []
    calls = {"n": 0}

    def spawn(command, cwd, env):
        calls["n"] += 1
        if calls["n"] == 1:
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True,
            )
            children.append(child)
            return child
        raise FileNotFoundError("npm vanished between the check and the spawn")

    monkeypatch.setattr(serve, "_spawn", spawn)
    monkeypatch.setattr(serve, "frontend_blocker", lambda root: None)
    monkeypatch.setattr(serve, "_installed", lambda module: True)
    survivors: list[int] = []
    try:
        with pytest.raises(FileNotFoundError):
            serve.run(port=18123, web_port=13123)
    finally:
        # Read liveness BEFORE reaping, or this test's own cleanup is what makes it pass.
        survivors = [c.pid for c in children if c.poll() is None]
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()

    assert children, "nothing was spawned — the test proved nothing"
    assert not survivors, f"the backend outlived `kotoba serve`: {survivors}"
