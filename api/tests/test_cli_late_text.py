"""Nothing of hers may arrive after the turn has been closed out.

Ctrl+C is a signal — ISIG stays on — so KeyboardInterrupt lands on `_turn`'s OUTER
await and `asyncio.shield` leaves her task running. `_last_frames` then waits half a second for the
frames that teardown owes and returns whether or not the task has finished. A block echoed after that
goes into `Screen.held` and is printed above his next prompt line: the transcript out of order.

The shape here is exactly that state — the outer await raises while the task lives on, and the task
speaks once more during a teardown that outlasts the ceiling.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from test_cli_app import wired


LATE = "Y esto es lo que encontré."


@pytest.fixture()
def interrupted(monkeypatch):
    """SIGINT during `await asyncio.shield(task)`: the outer await raises, the inner task runs on."""
    async def outer(task):
        await asyncio.sleep(0.02)
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "shield", outer)


def _app_with_a_slow_tail():
    app, buf = wired()
    held: dict = {}

    async def ask(text, on_text=None, on_face=None):
        held["task"] = asyncio.current_task()
        on_text("Ya voy.")
        try:
            await asyncio.sleep(5)
        finally:
            await asyncio.sleep(0.7)
            on_text(f"{LATE}\n\n")
        return ""

    app.session.ask = ask
    return app, buf, held


def test_a_block_echoed_after_the_ceiling_never_reaches_the_next_prompt(interrupted):
    app, buf, held = _app_with_a_slow_tail()

    async def go():
        started = time.monotonic()
        await app._turn("hola")
        closed = time.monotonic() - started
        at_close = buf.getvalue()
        await asyncio.gather(held["task"], return_exceptions=True)
        app.screen.commit_user("otra pregunta")
        return closed, at_close

    closed, at_close = asyncio.run(go())

    assert closed < 1.0, "the turn waited for the tail instead of closing out on its ceiling"
    assert "Ya voy." in at_close, "what she said before the cut still belongs on the glass"
    assert app.caps.g["cut"] in at_close, "a cut turn says it stopped"
    assert LATE not in buf.getvalue(), "her late block printed above his next line"


def test_a_turn_that_finishes_normally_still_prints_every_block():
    """The sink closes at the end of the turn, not before it: the ordinary path is untouched."""
    app, buf = wired(["Primero.", "\n\nY segundo."])
    asyncio.run(app._turn("hola"))
    out = buf.getvalue()
    assert "Primero." in out and "Y segundo." in out
