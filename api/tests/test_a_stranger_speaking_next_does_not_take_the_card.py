from __future__ import annotations

import asyncio

import kotoba.tools  # noqa: F401
from kotoba.discord import authority, bridge, cards

OWNER = 9000000000000000001
STRANGER = 9000000000000000002


class _Db:
    async def ensure_session(self, sid):
        return None

    async def insert_turn(self, sid, role, text):
        return None


class _Engine:
    db = _Db()
    mcp = None
    soul_patterns = None


class _Channel:
    id = 55


def _actor(uid):
    return authority.Actor(user_id=uid, guild_id=None, display="x", handle="x",
                           is_owner=False, is_guild_admin=False, is_guild_owner=False)


def test_the_card_belongs_to_the_turn_that_is_running_not_the_last_to_speak(monkeypatch):
    seen = []

    async def load_context(*a, **k):
        return []

    monkeypatch.setattr(bridge, "load_context", load_context)

    async def drive():
        from kotoba.core import events

        asking = cards.DiscordCards(_Channel(), asker=OWNER, owner=OWNER)
        session = bridge.ChannelSession(_Engine(), None, 55, ask=asking.ask,
                                        on_turn=lambda who: setattr(asking, "asker", who.user_id))

        async def one_pass(items, said, excluded, outcome, who, register, on_text, on_face):
            await asyncio.sleep(0.05)
            seen.append((who.user_id, asking.asker))
            return "ok"

        monkeypatch.setattr(session, "_one_pass", one_pass)
        try:
            first = asyncio.create_task(session.ask("hola", _actor(OWNER)))
            await asyncio.sleep(0.01)
            second = asyncio.create_task(session.ask("hola", _actor(STRANGER)))
            await asyncio.gather(first, second)
        finally:
            events.unregister(session.session_id, session.queue)

    asyncio.run(drive())
    assert seen == [(OWNER, OWNER), (STRANGER, STRANGER)]


def test_looking_up_the_session_no_longer_moves_the_asker():
    from kotoba.discord.client import KotobaClient

    surface = object.__new__(KotobaClient)
    asking = cards.DiscordCards(_Channel(), asker=OWNER, owner=OWNER)
    surface.sessions = {55: object()}
    surface.askers = {55: asking}
    msg = type("M", (), {"channel": _Channel(), "guild": None})()
    asyncio.run(surface._session(msg, _actor(STRANGER)))
    assert asking.asker == OWNER
