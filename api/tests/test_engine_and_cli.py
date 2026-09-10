"""Startup and the terminal entry point.

Every step here used to live inside FastAPI's lifespan, so a second entry point silently skipped it.
Costliest: without the LLM key preload she tells a user who configured a key in Settings that she
has none (it lives encrypted in the database, never the environment); without apply_on_startup a
fresh process offers exactly the tool families the app hides.

The cron ticker must NOT start twice: the claim is a compare-and-swap so only one ticker wins the
job, but delivery reads a per-process queue — the winner posts into its own empty queue and the
user never hears it."""
from __future__ import annotations

import asyncio
import tempfile

import pytest

from kotoba.core import engine


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _own_state(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + tempfile.mktemp(suffix=".db"))


def test_starting_without_tickers_starts_no_cron():
    async def go():
        eng = await engine.start(tickers=False)
        names = [t.get_coro().__qualname__ for t in eng.tasks]
        await engine.stop(eng)
        return names

    names = _run(go())
    assert not any("cron" in n for n in names), f"the CLI must not tick: {names}"
    assert not any("refresh_loop" in n for n in names), "nor refresh someone else's OAuth token"


def test_starting_with_tickers_starts_them():
    async def go():
        eng = await engine.start(tickers=True)
        names = [t.get_coro().__qualname__ for t in eng.tasks]
        await engine.stop(eng)
        return names

    names = _run(go())
    assert any("cron" in n for n in names), f"the server owns the user's channel and must tick: {names}"


def test_a_key_saved_in_the_panel_reaches_the_client():
    """It lives ENCRYPTED in the database, never in the environment — skip the preload and she reports
    having no key to someone who configured one."""
    async def go():
        eng = await engine.start(tickers=False)
        await eng.db.save_key("llm:openai:api_key", "sk-from-the-panel")
        from kotoba.core import llm

        llm._provider_keys.clear()
        assert llm.get_client() is None, "the fixture needs no ambient key for this to mean anything"
        await engine.stop(eng)

        eng2 = await engine.start(tickers=False)
        client = llm.get_client()
        await engine.stop(eng2)
        return client

    assert _run(go()) is not None


def test_the_toolsets_the_user_switched_off_stay_off():
    async def go():
        from kotoba.core import app_settings
        from kotoba.tools import registry

        app_settings.set_toolset_enabled("web", False)
        registry._disabled_toolsets.clear()          # a fresh process starts with none
        eng = await engine.start(tickers=False)
        names = {s.get("name") for s in registry.schemas_for(mode="companion")}
        await engine.stop(eng)
        app_settings.set_toolset_enabled("web", True)
        return names

    assert "web_extract" not in _run(go()), "a fresh process offered a tool the panel hides"


def test_stop_is_idempotent():
    async def go():
        eng = await engine.start(tickers=False)
        await engine.stop(eng)
        await engine.stop(eng)      # the CLI calls it from a signal handler AND from its finally

    _run(go())


def test_the_cli_registers_an_event_queue():
    """Without one, `events._put` drops every frame and both askers refuse to open a card — so every
    approval is denied instantly with the model told it asked."""
    from kotoba.cli.session import Session
    from kotoba.core import events

    async def go():
        session = await Session.open()
        listening = events.has_listener(session.session_id)
        await session.close()
        return listening, events.has_listener(session.session_id)

    during, after = _run(go())
    assert during is True
    assert after is False, "and it must give the queue back on the way out"


def test_the_cli_answers_and_persists(monkeypatch):
    from kotoba.cli.session import Session
    import kotoba.cli.session as cli_session

    async def fake_loop(items, sid, db, stream, patterns, **kw):
        assert kw.get("channel") == "text", "a terminal answers its cards in place"
        await stream.put("[warmly] Ya está.")
        return "[warmly] Ya está."

    monkeypatch.setattr(cli_session, "agentic_loop", fake_loop)

    async def go():
        session = await Session.open()
        reply = await session.ask("hola")
        async with session.engine.db.conn.execute(
            "SELECT role, content FROM turns ORDER BY rowid"
        ) as cur:
            rows = [(r["role"], r["content"]) for r in await cur.fetchall()]
        await session.close()
        return reply, rows

    reply, rows = _run(go())
    assert reply == "Ya está.", "an audio tag must never be printed to a terminal"
    assert ("user", "hola") in rows and ("assistant", "Ya está.") in rows


def test_markdown_survives_the_terminal_filters(monkeypatch):
    """The voice chain deletes fences, URLs and paths on purpose. A terminal needs every one of them."""
    from kotoba.cli.session import Session
    import kotoba.cli.session as cli_session

    body = "[warmly] Mira:\n\n```python\nx = 1\n```\n\n[excited] Y en https://example.com hay más."

    async def fake_loop(items, sid, db, stream, patterns, **kw):
        await stream.put(body)
        return body

    monkeypatch.setattr(cli_session, "agentic_loop", fake_loop)

    async def go():
        session = await Session.open()
        reply = await session.ask("dame el código")
        await session.close()
        return reply

    out = _run(go())
    assert out.count("```") == 2, out
    assert "https://example.com" in out
    assert "[" not in out, "no tag may reach the screen"
    assert "  " not in out, "a stripped tag must not leave its spaces behind"


def test_code_indentation_survives(monkeypatch):
    """Closing the gap a stripped tag leaves must not touch INDENTATION: collapsing leading whitespace
    turned a four-space Python body into one space, which is not the code she was asked for."""
    from kotoba.cli.session import _tidy

    body = "[warmly] Toma:\n\n```python\ndef f(a, b):\n    x = a + b\n    return x\n```"
    out = _tidy(body.replace("[warmly] ", " "))
    assert "\n    x = a + b" in out, out
    assert "Lo hice y funciono." == _tidy("Lo hice  y funciono.")


# --- Ctrl+C ------------------------------------------------------------------------------------------

def test_an_interrupted_turn_keeps_what_she_already_said(monkeypatch):
    """A half-answer she gave is still something the next turn has to know she said. The write is
    shielded because it runs while the task unwinds the cancellation — unshielded, it is cancelled at
    once and loses the very thing the finally exists to keep."""
    from kotoba.cli.session import Session
    import kotoba.cli.session as cli_session

    async def slow(items, sid, db, stream, patterns, **kw):
        for word in ("Voy ", "a ", "explicarte ", "esto ", "con ", "calma"):
            await stream.put(word)
            await asyncio.sleep(0.05)
        return "unreached"

    monkeypatch.setattr(cli_session, "agentic_loop", slow)

    async def go():
        from kotoba.core import session_sandbox, turns

        session = await Session.open()
        task = asyncio.create_task(session.ask("cuéntame"))
        await asyncio.sleep(0.13)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.05)
        async with session.engine.db.conn.execute(
            "SELECT role, content FROM turns ORDER BY rowid"
        ) as cur:
            rows = [(r["role"], r["content"]) for r in await cur.fetchall()]
        leftovers = (turns._active.get(session.session_id),
                     session.session_id in getattr(session_sandbox, "_live", {}))
        await session.close()
        return rows, leftovers

    rows, (live_turn, live_sandbox) = _run(go())
    said = [c for r, c in rows if r == "assistant"]
    assert said and said[0].startswith("Voy a"), rows
    assert said[0] != "unreached", "the producer's return value never arrived — only the stream did"
    assert live_turn is None, "a cancelled turn must not stay in the registry"
    assert live_sandbox is False, "nor leave its sandbox behind"


def test_a_finished_turn_is_persisted_exactly_once(monkeypatch):
    """The same finally persists both paths, so the normal one must not write a second row."""
    from kotoba.cli.session import Session
    import kotoba.cli.session as cli_session

    async def fake(items, sid, db, stream, patterns, **kw):
        await stream.put("Ya está.")
        return "Ya está."

    monkeypatch.setattr(cli_session, "agentic_loop", fake)

    async def go():
        session = await Session.open()
        await session.ask("hola")
        async with session.engine.db.conn.execute(
            "SELECT count(*) AS n FROM turns WHERE role='assistant'"
        ) as cur:
            n = (await cur.fetchone())["n"]
        await session.close()
        return n

    assert _run(go()) == 1


def test_a_cli_turn_feeds_the_memory_extractor_like_every_other_client(monkeypatch):
    """`/v1` and the voice WebSocket both run `extract_and_save_memory` in the turn's finally; the CLI
    ran nothing, so a fact typed at the terminal — «vivo en Barcelona», "I live in Barcelona" — was
    never remembered unless the model happened to volunteer a `memory_write`. Same contract now,
    pinned: the user's own words reach the extractor whichever client typed them."""
    from kotoba.cli.session import Session
    import kotoba.cli.session as cli_session

    async def fake(items, sid, db, stream, patterns, **kw):
        await stream.put("Anotado.")
        return "Anotado."

    fed: list[str] = []

    async def fake_extract(user_msg, db):
        fed.append(user_msg)

    monkeypatch.setattr(cli_session, "agentic_loop", fake)
    monkeypatch.setattr(cli_session, "extract_and_save_memory", fake_extract)

    async def go():
        session = await Session.open()
        await session.ask("vivo en Barcelona")
        await session.close()

    _run(go())
    assert fed == ["vivo en Barcelona"]


def test_a_trigger_sentinel_turn_is_not_extracted(monkeypatch):
    """The WORK_DONE sentinel is nobody's words — /v1 gates its extraction on the same condition."""
    from kotoba.cli.session import WORK_DONE, Session
    import kotoba.cli.session as cli_session

    async def fake(items, sid, db, stream, patterns, **kw):
        await stream.put("El trabajo terminó bien.")

    fed: list[str] = []

    async def fake_extract(user_msg, db):
        fed.append(user_msg)

    monkeypatch.setattr(cli_session, "agentic_loop", fake)
    monkeypatch.setattr(cli_session, "extract_and_save_memory", fake_extract)

    async def go():
        session = await Session.open()
        await session.ask(WORK_DONE)
        await session.close()

    _run(go())
    assert fed == []


def test_close_waits_for_a_pending_extraction(monkeypatch):
    """`kotoba --once` exits right after the turn; a purely detached extraction would lose every fact
    it was ever told. close() drains the set (bounded) before stopping the engine."""
    from kotoba.cli.session import Session
    import kotoba.cli.session as cli_session

    async def fake(items, sid, db, stream, patterns, **kw):
        await stream.put("Ya.")

    landed = []

    async def slow_extract(user_msg, db):
        await asyncio.sleep(0.15)
        landed.append(user_msg)

    monkeypatch.setattr(cli_session, "agentic_loop", fake)
    monkeypatch.setattr(cli_session, "extract_and_save_memory", slow_extract)

    async def go():
        session = await Session.open()
        await session.ask("me mudé, ahora vivo en Madrid")
        await session.close()

    _run(go())
    assert landed == ["me mudé, ahora vivo en Madrid"], "the fact died with the process"


def test_a_wheel_install_does_not_write_into_site_packages(tmp_path, monkeypatch):
    """From a clone the database belongs beside the code in api/. From a wheel there is no repo, and
    counting parents from site-packages lands in /usr/lib — not where a conversation history goes."""
    import importlib
    import shutil
    import sys

    fake_site = tmp_path / "site"
    (fake_site / "kotoba").mkdir(parents=True)
    (fake_site / "kotoba" / "__init__.py").write_text("", encoding="utf-8")
    import kotoba.paths as real

    shutil.copy(real.__file__, fake_site / "kotoba" / "paths.py")

    monkeypatch.setenv("KOTOBA_HOME", str(tmp_path / "home"))
    monkeypatch.syspath_prepend(str(fake_site))
    for name in [n for n in sys.modules if n == "kotoba" or n.startswith("kotoba.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    paths = importlib.import_module("kotoba.paths")

    assert paths.API_DIR == tmp_path / "home", paths.API_DIR
    assert paths.REPO_ROOT == tmp_path / "home", paths.REPO_ROOT


def test_a_clone_keeps_the_database_beside_the_code():
    from kotoba import paths

    assert paths.API_DIR.name == "api"
    assert (paths.REPO_ROOT / "soul").is_dir(), "the clone root is the one holding soul/"
