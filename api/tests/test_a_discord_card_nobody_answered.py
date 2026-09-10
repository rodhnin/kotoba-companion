from __future__ import annotations

import asyncio

from kotoba.cli.approvals import Approvals, Card
from kotoba.core import interaction
from kotoba.discord import cards

OWNER = 4242
ASKER = 77


class _Msg:
    def __init__(self) -> None:
        self.edits: list[dict] = []

    async def edit(self, **kw) -> None:
        self.edits.append(kw)


class _Channel:
    id = 1
    guild = None

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.msg = _Msg()

    async def send(self, content=None, **kw):
        self.sent.append({"content": content, **kw})
        return self.msg


def _card(rid: str = "r1") -> Card:
    return Card.from_frame({"request_id": rid, "mode": "approval", "label": "Delete #general?",
                            "family": "", "notice": {"head": "Delete #general?", "facts": [],
                                                     "surface": "discord"}})


def test_a_wait_that_gave_up_is_no_answer_and_not_the_users_no(monkeypatch):
    async def gave_up(aw, timeout=None):
        raise asyncio.TimeoutError

    monkeypatch.setattr(cards.asyncio, "wait_for", gave_up)

    async def go():
        ch = _Channel()
        dc = cards.DiscordCards(ch, asker=ASKER, owner=OWNER)
        got = await dc.ask(_card())
        return got, Approvals("d", ask=dc.ask)._value(_card(), got), ch

    got, value, ch = asyncio.run(go())
    assert got is None and value is None
    view = ch.sent[0]["view"]
    assert view.is_finished() and all(c.disabled for c in view.children)
    assert ch.msg.edits and ch.msg.edits[-1]["view"] is view


def test_a_card_the_backend_gave_up_on_first_cannot_be_pressed_afterwards():
    async def go():
        ch = _Channel()
        dc = cards.DiscordCards(ch, asker=ASKER, owner=OWNER)
        task = asyncio.create_task(dc.ask(_card("r2")))
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return ch

    ch = asyncio.run(go())
    view = ch.sent[0]["view"]
    assert view.is_finished() and all(c.disabled for c in view.children)
    assert ch.msg.edits and ch.msg.edits[-1]["view"] is view
    assert cards._OPEN == {}


def test_the_backend_window_the_discord_card_assumes_is_the_text_one():
    assert interaction.approval_timeout("text", el_agent=False) == interaction.TEXT_APPROVAL_TIMEOUT
    assert interaction.approval_timeout("voice", el_agent=False) < interaction.TEXT_APPROVAL_TIMEOUT
