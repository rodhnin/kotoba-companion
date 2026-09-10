"""A producer that dies mid-stream must not be presented as a finished answer.

The CLI's `_produce` used to swallow the exception: its finally put DONE, `_drain` returned the
half-sentence by the normal path, and the transcript held '…Guardé el resumen completo en' as if she
had finished — with the RuntimeError sitting unread in the task. The web path already apologises into
the queue; the CLI now mirrors it, and mirrors the other half too: on the web a failed turn leaves
holder["text"] empty, so nothing is marked delivered by it — the work announcement and the due
reminder both survive for the next turn. Cancellation is not failure: Ctrl+C keeps the partial and
keeps its silence.
"""
from __future__ import annotations

import asyncio

import pytest

import kotoba.cli.session as cli_session
from kotoba.core import pending_reminder, work_state

APOLOGY = "Sorry, something tripped up on my end — let's try that again."


async def _dying_loop(items, sid, db, stream, patterns, **kw):
    await stream.put("Ya quedó listo. Guardé el resumen completo en")
    raise RuntimeError("connection dropped mid-stream")


def test_a_dying_producer_apologises_and_the_transcript_stops_lying(monkeypatch):
    monkeypatch.setattr(cli_session, "agentic_loop", _dying_loop)

    async def go():
        session = await cli_session.Session.open()
        reply = await session.ask("resúmelo")
        async with session.engine.db.conn.execute(
            "SELECT content FROM turns WHERE role='assistant'"
        ) as cur:
            rows = [r["content"] for r in await cur.fetchall()]
        await session.close()
        return reply, rows

    reply, rows = asyncio.run(go())
    assert reply == f"Ya quedó listo. Guardé el resumen completo en\n\n{APOLOGY}"
    assert rows == [reply], "what the next turn knows must be what the user saw"


def test_a_failed_turn_marks_nothing_delivered(monkeypatch):
    """The announce turn that dies mid-sentence must not consume the announcement, and the due
    reminder it was carrying must survive — even though the apology itself was spoken."""
    monkeypatch.setattr(cli_session, "agentic_loop", _dying_loop)

    async def go():
        session = await cli_session.Session.open()
        sid = session.session_id
        work_state.start(sid, "resumir el informe")
        work_state.finish(sid, "hecho", [])
        pending_reminder.add(sid, "llamar a tu hermana")
        await session.ask("__work_done__")
        snap = work_state.get(sid)
        rem = pending_reminder.has_pending(sid)
        work_state.clear(sid)
        pending_reminder.clear(sid)
        await session.close()
        return snap, rem

    snap, rem = asyncio.run(go())
    assert snap["announced"] is False, "a truncated announcement was presented as delivered"
    assert rem is True, "the reminder died with the turn that never really said it"


def test_a_cancelled_turn_keeps_its_silence(monkeypatch):
    """Ctrl+C and a supersede are somebody's decision, not a failure — no apology may appear."""
    async def slow(items, sid, db, stream, patterns, **kw):
        await stream.put("Voy a explicarte esto ")
        await asyncio.sleep(5)
        return "unreached"

    monkeypatch.setattr(cli_session, "agentic_loop", slow)

    async def go():
        session = await cli_session.Session.open()
        task = asyncio.create_task(session.ask("cuéntame"))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.05)
        async with session.engine.db.conn.execute(
            "SELECT content FROM turns WHERE role='assistant'"
        ) as cur:
            rows = [r["content"] for r in await cur.fetchall()]
        await session.close()
        return rows

    rows = asyncio.run(go())
    assert rows and rows[0].startswith("Voy a explicarte"), rows
    assert APOLOGY not in rows[0], "a cut is not a stumble — she must not apologise for your Ctrl+C"
