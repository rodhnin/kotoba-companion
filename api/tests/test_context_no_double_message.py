"""His message reaches the model exactly once, whatever else lands while the turn is being built.

Every transport writes the user's row and then reads it back, with awaits in between. The duplicate
guard used to INFER that the row it wrote was `recent[-1]`; any row arriving in that window — a
superseded turn's partial, a second ElevenLabs post — took the last slot, the guess said "not mine",
and the message was appended on top of itself.

`persisted` replaces the guess with the fact the caller already has. The concurrent case is driven
through `cli/session.Session.ask` itself, in the exact window between the write and the read.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace


import kotoba.cli.session as cli_session
from kotoba.core import events
from kotoba.core.context import load_context
from kotoba.db.database import Database
from kotoba.models.schemas import ChatRequest


class _DB:
    def __init__(self, rows):
        self.rows = rows

    async def fetch_recent_turns(self, *a, **k):
        return list(self.rows)

    async def fetch_soul_config(self):
        return {"name": "Kotoba", "language": "auto"}

    async def fetch_user_profile_as_markdown(self):
        return ""


def _users(items):
    return [i["content"] for i in items if i.get("role") == "user"]


def test_a_row_landing_between_the_write_and_the_read_does_not_double_his_message():
    rows = [{"role": "user", "content": "hola"},
            {"role": "assistant", "content": "…perdona, seguía."}]
    req = ChatRequest(messages=[{"role": "user", "content": "hola"}], session_id="s1")
    items = asyncio.run(load_context(req, _DB(rows), "s1", register="text", persisted=True))
    assert _users(items).count("hola") == 1


def test_a_caller_that_did_not_persist_still_gets_its_message_appended():
    """The sentinel turns and anything else that writes no row: the message is not in `recent`, so it
    has to be added or the model is handed a turn with nothing being asked."""
    rows = [{"role": "user", "content": "antes"}, {"role": "assistant", "content": "ya"}]
    req = ChatRequest(messages=[{"role": "user", "content": "ahora"}], session_id="s2")
    items = asyncio.run(load_context(req, _DB(rows), "s2", register="text", persisted=False))
    assert _users(items) == ["antes", "ahora"]


def test_the_cli_turn_survives_a_concurrent_insert_in_the_very_window(tmp_path, monkeypatch):
    """The window is real: `ask` writes his row, then builds the context, and the two are separate
    awaits. A concurrent write is driven into exactly that gap."""
    captured: dict = {}

    async def fake_loop(items, *a, **k):
        captured["items"] = items
        return ""

    real_load = cli_session.load_context

    async def racing_load(request, database, session_id, **kw):
        await database.insert_turn(session_id, "assistant", "…perdona, seguía.")
        return await real_load(request, database, session_id, **kw)

    monkeypatch.setattr(cli_session, "agentic_loop", fake_loop)
    monkeypatch.setattr(cli_session, "load_context", racing_load)

    async def go():
        db = Database("sqlite:///" + str(tmp_path / "turns.db"))
        await db.connect()
        session = cli_session.Session(SimpleNamespace(db=db, mcp=None, soul_patterns={}))
        try:
            await session.ask("hola")
        finally:
            events.unregister(session.session_id, session.queue)
            await db.close()

    asyncio.run(go())
    assert _users(captured["items"]).count("hola") == 1, captured["items"]
