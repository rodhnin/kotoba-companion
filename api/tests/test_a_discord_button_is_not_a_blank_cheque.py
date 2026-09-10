"""Who may press an approval button, and what a button may never grant.

The authority filter withholds the terminal from everyone but the owner, but it cannot stop an admin
from ANSWERING a card the owner's own turn opened — and that card can be a shell line running on the
owner's machine. A card carrying a command family is therefore the owner's alone.

The other half is the secret card: `ask_secret` and `request_credential` open an input box, and a key
typed into a Discord channel is a key every member of that channel now holds.
"""
from __future__ import annotations

import asyncio

from kotoba.cli.approvals import Card
from kotoba.discord import cards

OWNER = 4242
ASKER = 77
ADMIN = 88
STRANGER = 99


class _Perms:
    def __init__(self, administrator: bool) -> None:
        self.administrator = administrator


class _User:
    def __init__(self, uid: int, admin: bool) -> None:
        self.id = uid
        self.display_name = f"user{uid}"
        self.guild_permissions = _Perms(admin)


class _Response:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, content, **kw) -> None:
        self.messages.append(content)

    async def edit_message(self, **kw) -> None:
        self.messages.append(kw.get("content", ""))


class _Itx:
    def __init__(self, user) -> None:
        self.user = user
        self.response = _Response()


class _Channel:
    id = 1

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, content=None, **kw):
        self.sent.append({"content": content, **kw})
        return object()


def _shell_card() -> Card:
    return Card.from_frame({"request_id": "r1", "mode": "approval",
                            "label": "rm -rf ~/notes", "family": "rm", "can_always": True})


def _tool_card() -> Card:
    """One this surface raised itself, and says so. An empty family is not the mark: an unparseable
    shell line has one too, and that is the card that most needs an owner."""
    return Card.from_frame({"request_id": "r2", "mode": "approval",
                            "label": "Delete #general?", "family": "",
                            "notice": {"head": "Delete #general?", "facts": [],
                                       "surface": "discord"}})


def test_a_card_that_runs_on_the_host_is_the_owners_alone():
    assert cards.owner_only(_shell_card())
    assert not cards.owner_only(_tool_card())


def _check(card, user) -> bool:
    async def run():
        view = cards._view_class()(card, asker=ASKER, owner=OWNER, timeout=5.0)
        return await view.interaction_check(_Itx(user))
    return asyncio.run(run())


def test_an_admin_cannot_approve_a_command_on_someone_elses_machine():
    assert _check(_shell_card(), _User(ADMIN, admin=True)) is False


def test_the_owner_can_approve_a_command_on_their_own_machine():
    assert _check(_shell_card(), _User(OWNER, admin=False)) is True


def test_an_admin_can_answer_an_ordinary_discord_card():
    assert _check(_tool_card(), _User(ADMIN, admin=True)) is True


def test_a_stranger_can_answer_nothing():
    assert _check(_tool_card(), _User(STRANGER, admin=False)) is False
    assert _check(_shell_card(), _User(STRANGER, admin=False)) is False


def test_the_person_who_asked_can_always_answer_their_own_card():
    assert _check(_tool_card(), _User(ASKER, admin=False)) is True


def test_a_secret_is_never_taken_in_a_channel():
    card = Card.from_frame({"request_id": "r3", "mode": "input", "wait": True,
                            "input_kind": "key", "label": "Your OpenAI key"})
    channel = _Channel()
    got = asyncio.run(cards.DiscordCards(channel, asker=ASKER, owner=OWNER).ask(card))
    assert got is None
    assert channel.sent, "she has to say why, not go quiet"
    assert "won't take a key" in channel.sent[0]["content"]


def test_a_timed_out_card_reads_as_a_refusal_not_an_approval():
    async def run():
        view = cards._view_class()(_tool_card(), asker=ASKER, owner=OWNER, timeout=5.0)
        await view.on_timeout()
        return view.answer.result()
    assert asyncio.run(run()) == cards.DENIED


def test_the_named_facts_survive_into_the_card():
    """A count is what a person cannot consent to; the names are the point."""
    card = Card.from_frame({"request_id": "r4", "mode": "approval", "label": "Apply plan",
                            "notice": {"head": "Restructure?",
                                       "facts": [["DELETES", "#viejo, #spam"]]}})
    assert card.notice is not None
    assert cards._facts(card) == [("DELETES", "#viejo, #spam")]
