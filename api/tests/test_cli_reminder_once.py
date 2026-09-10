"""A due reminder is voiced ONCE per occurrence in the CLI.

load_context peeks (consume=False — the F2 guarantee: a superseded or dead turn must not lose the
reminder) and the TRANSPORT clears after the turn that actually spoke. The web and the voice paths
carry both halves; the CLI's finally ported work_state.mark_announced's twin and forgot this one, so
the note re-injected on every turn forever ('me sigue recordando lo de mi hermana'). Driven here the
way it happens live: real engine, real cron tick, real DB — only the LLM stubbed.
"""
from __future__ import annotations

import asyncio

import kotoba.cli.session as cli_session
from kotoba.core import cron, pending_reminder


def test_cli_voices_a_due_reminder_once_then_clears(monkeypatch):
    seen = []

    async def fake_loop(items, sid, db, stream, patterns, **kw):
        seen.append(any(m.get("role") == "developer" and "DUE REMINDER" in str(m.get("content", ""))
                        for m in items))
        await stream.put("Oye, acuérdate de llamar a tu hermana." if seen[-1] else "Claro.")
        return "dicho"

    monkeypatch.setattr(cli_session, "agentic_loop", fake_loop)

    async def go():
        session = await cli_session.Session.open()
        await session.engine.db.insert_cronjob(
            message="llamar a tu hermana", due_at="2026-08-09 09:00:00",
            session_id="stale-browser-uuid", recurring="daily")
        await cron._tick(session.engine.db)
        assert pending_reminder.has_pending(session.session_id)
        for i in range(3):
            await session.ask(f"turno {i}")
        left = pending_reminder.has_pending(session.session_id)
        await session.close()
        return left

    assert asyncio.run(go()) is False
    assert seen == [True, False, False], "once, in the turn after it came due — then never again"


def test_cli_keeps_the_reminder_when_the_turn_never_really_spoke(monkeypatch):
    """consume=False exists for exactly this — and the transport's apology for a dead producer does
    not count as having voiced it. The retry says it, and only the retry clears it."""
    calls = {"n": 0}

    async def dying_then_fine(items, sid, db, stream, patterns, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("provider died before a single token")
        await stream.put("Oye, acuérdate de tomar la medicina.")
        return "dicho"

    monkeypatch.setattr(cli_session, "agentic_loop", dying_then_fine)

    async def go():
        session = await cli_session.Session.open()
        pending_reminder.add(session.session_id, "tomar la medicina")
        await session.ask("hola")
        survived = pending_reminder.has_pending(session.session_id)
        await session.ask("hola de nuevo")
        cleared = not pending_reminder.has_pending(session.session_id)
        await session.close()
        return survived, cleared

    survived, cleared = asyncio.run(go())
    assert survived is True, "the failed turn ate the reminder — that is the F2 regression"
    assert cleared is True, "the spoken retry is the one that consumes it"
