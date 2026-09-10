"""A third party's nickname was filed in the owner's own long-term memory.

Measured live. The owner mentioned what somebody else liked to be called, and the automatic extractor
— which keys on who SPOKE, not who was spoken about — wrote it into his USER.md as "a person
mentioned by the user prefers to be called Wren". Two defects in one line: the wrong store, and a
fact that names nobody and can therefore never be used again.

So the explicit tool refuses to file anything under a guess about who it is about, and it writes to a
store the owner's prompt never reads.
"""
from __future__ import annotations

import asyncio

import pytest

from kotoba.discord import state
from kotoba.tools import ToolContext
from kotoba.tools.action import discord_people, discord_remember_person

THEM = 9000000000000000321


class _Member:
    def __init__(self, uid: int, name: str) -> None:
        self.id = uid
        self.name = name
        self.display_name = name
        self.bot = False

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"


class _Guild:
    def __init__(self, members) -> None:
        self._members = list(members)
        self.members = self._members

    def get_member(self, uid):
        return next((m for m in self._members if m.id == uid), None)


class _Client:
    def __init__(self, guild) -> None:
        self._guild = guild

    def get_guild(self, gid):
        return self._guild

    def get_user(self, uid):
        return self._guild.get_member(uid)


class _DB:
    def __init__(self) -> None:
        self.people: dict[str, dict] = {}
        self.facts: list[dict] = []

    async def upsert_discord_person(self, user_id, handle, display, relation="known"):
        self.people[str(user_id)] = {"handle": handle, "display": display, "relation": relation}

    async def add_person_fact(self, user_id, fact, source="said", guild_id=None):
        if any(f["user_id"] == str(user_id) and f["fact"].lower() == fact.lower()
               for f in self.facts):
            return False
        self.facts.append({"user_id": str(user_id), "fact": fact, "source": source})
        return True

    async def person_facts(self, user_id, limit=12):
        return [f for f in self.facts if f["user_id"] == str(user_id)][:limit]

    async def list_discord_people(self, limit=50):
        return [{"display": v["display"], "handle": v["handle"], "relation": v["relation"],
                 "facts": sum(1 for f in self.facts if f["user_id"] == k)}
                for k, v in self.people.items()]


@pytest.fixture
def wired():
    guild = _Guild([_Member(THEM, "wrenlow")])
    db = _DB()
    ctx = ToolContext(db=db, session_id="s1", mode="companion")
    with state.turn(who=None, bot=_Client(guild), guild=1, channel=2):
        yield db, ctx


def _remember(ctx, **args):
    return asyncio.run(discord_remember_person.execute(args, ctx))


def test_a_fact_lands_in_that_persons_file(wired):
    db, ctx = wired
    said = _remember(ctx, user="wrenlow", fact="prefers to be called Wren")
    assert "wrenlow" in said
    assert db.facts == [{"user_id": str(THEM), "fact": "prefers to be called Wren",
                         "source": "said"}]


def test_a_fact_about_nobody_she_knows_is_refused_not_guessed(wired):
    """The live defect, in one assertion: it named nobody, so it was worth nothing."""
    db, ctx = wired
    said = _remember(ctx, user="somebody", fact="likes cats")
    assert "don't know who" in said
    assert db.facts == []


def test_hearsay_is_kept_as_hearsay(wired):
    db, ctx = wired
    _remember(ctx, user="wrenlow", fact="has a cat named Pepper", source="told")
    assert db.facts[0]["source"] == "told"
    read = asyncio.run(discord_people.execute({"users": ["wrenlow"]}, ctx))
    assert "second-hand" in read


def test_she_says_when_she_already_knew_it(wired):
    _db, ctx = wired
    _remember(ctx, user="wrenlow", fact="prefers to be called Wren")
    again = _remember(ctx, user="wrenlow", fact="PREFERS to be called wren")
    assert "Already known" in again
    # The half that matters: a duplicate she announces sounds like she forgot and learned it twice.
    assert "say nothing" in again


def test_an_empty_fact_is_not_written(wired):
    db, ctx = wired
    assert "nothing" in _remember(ctx, user="wrenlow", fact="   ").lower()
    assert db.facts == []


def test_reading_back_someone_she_knows_nothing_about_says_so(wired):
    _db, ctx = wired
    said = asyncio.run(discord_people.execute({"users": ["wrenlow"]}, ctx))
    assert "nothing written down" in said


def test_the_person_store_is_not_the_owners_memory():
    """The two stores must not be the same module, or the separation is a comment, not a fact."""
    import inspect

    src = inspect.getsource(discord_remember_person)
    assert "user_memory" not in src
    assert "extract_and_save_memory" not in src
    assert "add_person_fact" in src


# --- and the same store read back the other way ----------------------------------------------------

OWNER = 9000000000000000777
THIRD = 9000000000000000555


@pytest.fixture
def owner_known(monkeypatch):
    """Her person is in the room, and the store already holds a note about him."""
    from kotoba.discord import config

    guild = _Guild([_Member(THEM, "wrenlow"), _Member(OWNER, "hisname"), _Member(THIRD, "adaq")])
    db = _DB()
    asyncio.run(db.upsert_discord_person(OWNER, "hisname", "hisname", relation="owner"))
    asyncio.run(db.add_person_fact(OWNER, "flies to Lisbon on the fourth"))
    monkeypatch.setattr(config, "owner_id", lambda: OWNER)
    ctx = ToolContext(db=db, session_id="s2", mode="companion")
    yield guild, db, ctx


def _actor(uid, owner):
    from kotoba.discord.authority import Actor

    return Actor(user_id=uid, guild_id=1, display="x", handle="x",
                 is_owner=uid == owner, is_guild_admin=False, is_guild_owner=False)


def _read(guild, ctx, asker, users=None):
    with state.turn(who=_actor(asker, OWNER), bot=_Client(guild), guild=1, channel=2):
        return asyncio.run(discord_people.execute({"users": users} if users else {}, ctx))


def test_a_guest_cannot_read_back_what_her_person_told_her(owner_known):
    """The writer already refuses to file a note about him, so this row is empty today — which is the
    wrong reason to leave the door open: that guarantee lives in one tool's discipline, and the next
    writer will not inherit it."""
    guild, _db, ctx = owner_known
    said = _read(guild, ctx, THEM, ["hisname"])
    assert "Lisbon" not in said
    assert "stays between us" in said
    assert "hisname" in said, "naming him is fine — everyone in the channel can see him"


def test_her_person_reads_his_own_back(owner_known):
    guild, _db, ctx = owner_known
    assert "Lisbon" in _read(guild, ctx, OWNER, ["hisname"])


def test_the_roster_does_not_count_his_notes_for_a_guest(owner_known):
    """A count is a smaller leak than the text and leaks the same fact: that there is something."""
    guild, _db, ctx = owner_known
    assert "thing(s) noted" not in _read(guild, ctx, THEM)
    assert "thing(s) noted" in _read(guild, ctx, OWNER)


def test_a_guest_still_reads_back_another_guest(owner_known):
    """The lock is his alone. People telling her things in a public room is the whole feature, and
    narrowing it further would be inventing a rule nobody asked for."""
    guild, _db, ctx = owner_known
    asyncio.run(ctx.db.add_person_fact(THIRD, "supports a terrible team"))
    assert "terrible team" in _read(guild, ctx, THEM, ["adaq"])


def test_a_guests_words_never_reach_her_persons_proactive_memory(monkeypatch):
    import types

    from kotoba.discord import bridge

    seen: list[str] = []

    async def _extract(text, db):
        seen.append(text)

    monkeypatch.setattr(bridge, "extract_and_save_memory", _extract)
    fake = types.SimpleNamespace(engine=types.SimpleNamespace(db=None), _extractions=set())

    async def _speak():
        bridge.ChannelSession._remember(fake, "call me Wren from now on", _actor(THEM, OWNER))
        bridge.ChannelSession._remember(fake, "flies to Lisbon on the fourth", _actor(OWNER, OWNER))
        await asyncio.gather(*fake._extractions)

    asyncio.run(_speak())
    assert seen == ["flies to Lisbon on the fourth"]

