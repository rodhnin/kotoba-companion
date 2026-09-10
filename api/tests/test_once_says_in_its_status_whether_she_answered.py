"""`kotoba --once` is the half of her that goes in a script, and a script reads the status, not the prose.

Both ways a turn fails end with her saying a sentence, and `ask` hands back a string either way. So the
one-shot command printed the apology, exited 0, and the caller filed it as the answer: an unconfigured
install and a rejected key both looked like a successful run to cron, to CI, and to anything piping her
into another command.

The exit code carries what the text cannot. 0 means she answered, 1 means she did not, 130 means it was
cut short — and a real answer must never drift into 1, or the status stops meaning anything.
"""
from __future__ import annotations

import asyncio
import sys

import httpx
import pytest
from openai import AuthenticationError

from kotoba.core.loop import _OFFLINE_MSG


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def once(monkeypatch):
    from kotoba.cli import __main__ as entry
    from kotoba.cli import session as cli_session

    async def no_memory(user_msg, db):
        return None

    monkeypatch.setattr(cli_session, "extract_and_save_memory", no_memory)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(entry, "print", lambda *a, **k: None, raising=False)
    return entry, cli_session


def _loop_that(monkeypatch, cli_session, *, says: str = "", raises: Exception | None = None):
    async def fake_loop(items, sid, db, stream, patterns, **kw):
        if raises is not None:
            raise raises
        await stream.put(says)

    monkeypatch.setattr(cli_session, "agentic_loop", fake_loop)


def test_a_real_answer_still_exits_zero(once, monkeypatch):
    entry, cli_session = once
    _loop_that(monkeypatch, cli_session, says="pong")
    assert _run(entry._once("say pong")) == 0


def test_an_install_with_no_brain_does_not_report_success(once, monkeypatch):
    """The commonest one: no key anywhere. She says so pleasantly, and nothing raised, so this was
    invisible to every caller that only had the status to go on."""
    entry, cli_session = once
    _loop_that(monkeypatch, cli_session, says=_OFFLINE_MSG)
    assert _run(entry._once("say pong")) == 1


def test_a_rejected_key_does_not_report_success(once, monkeypatch):
    entry, cli_session = once
    rejected = AuthenticationError(
        "Incorrect API key provided",
        response=httpx.Response(401, request=httpx.Request("POST", "https://api.openai.com/v1")),
        body=None,
    )
    _loop_that(monkeypatch, cli_session, raises=rejected)
    assert _run(entry._once("say pong")) == 1


def test_a_turn_that_died_mid_thought_does_not_report_success(once, monkeypatch):
    """A crash apology reaches the person as words. It must not reach a script as a result."""
    entry, cli_session = once
    _loop_that(monkeypatch, cli_session, raises=RuntimeError("the provider hung up"))
    assert _run(entry._once("say pong")) == 1


def test_she_had_nothing_to_say_is_still_one(once, monkeypatch):
    entry, cli_session = once
    _loop_that(monkeypatch, cli_session, says="")
    assert _run(entry._once("say pong")) == 1


def test_a_failed_turn_does_not_poison_the_next_one(once, monkeypatch):
    """The flag belongs to the LAST turn. Left standing, an interactive session that recovered would
    keep reporting a failure it had already come back from."""
    entry, cli_session = once
    from kotoba.cli.session import Session

    _loop_that(monkeypatch, cli_session, says=_OFFLINE_MSG)

    async def two_turns():
        s = await Session.open()
        try:
            await s.ask("first")
            assert s.last_turn_failed is True
            _loop_that(monkeypatch, cli_session, says="Here you go.")
            await s.ask("second")
            return s.last_turn_failed
        finally:
            await s.close()

    assert _run(two_turns()) is False


def test_the_apology_still_reaches_the_person(once, monkeypatch, capsys):
    """The status is for the script. The words are for whoever is reading — a silent exit 1 would be
    a worse answer than the one this file set out to fix.

    Only the silenced `print` is put back. A blanket undo drops the whole test session's isolation with
    it, and this is the one test that then runs the real one-shot path: it read the machine's own saved
    servers and launched a package manager to fetch one."""
    entry, cli_session = once
    monkeypatch.delattr(entry, "print", raising=False)
    _loop_that(monkeypatch, cli_session, says=_OFFLINE_MSG)
    code = _run(entry._once("say pong"))
    assert code == 1
    assert "isn't connected yet" in capsys.readouterr().out
