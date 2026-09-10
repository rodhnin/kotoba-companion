"""A tool that is merely withheld is not refused: dispatch resolves by NAME.

`excluded_tools` decides what schemas a turn is offered, and nothing consults it again when a call
arrives. A model that saw a name earlier in the conversation can still emit it, so every restricted
Discord tool has to say no inside `execute`. This pins the two that had no guard at all, and the
history reader that would resolve a channel id belonging to somebody else's server.
"""
from __future__ import annotations

import asyncio

import pytest

from kotoba.discord import authority, state

OWNER = 4242


class _Perms:
    def __init__(self, administrator: bool) -> None:
        self.administrator = administrator


class _Guild:
    owner_id = 999
    id = 1

    def __init__(self, channels=()) -> None:
        self.text_channels = list(channels)
        self._members = {}

    def get_channel(self, cid):
        return next((c for c in self.text_channels if c.id == cid), None)

    def get_member(self, uid):
        return self._members.get(uid)


class _Member:
    def __init__(self, uid: int, admin: bool, name: str = "someone") -> None:
        self.id = uid
        self.name = name
        self.display_name = name
        self.guild_permissions = _Perms(admin)
        self.guild = _Guild()


class _Client:
    def __init__(self, guilds=(), extra=()) -> None:
        self._guilds = {g.id: g for g in guilds}
        self._loose = {c.id: c for c in extra}
        self.user = None

    def get_guild(self, gid):
        return self._guilds.get(gid)

    def get_channel(self, cid):
        for guild in self._guilds.values():
            hit = guild.get_channel(cid)
            if hit is not None:
                return hit
        return self._loose.get(cid)


def _actor(member, guild_id=1):
    return authority.actor_from_member(member, owner=OWNER, guild_id=guild_id)


def _run(tool, args, *, who, client, guild=1, channel=10):
    with state.turn(who=who, bot=client, guild=guild, channel=channel):
        return asyncio.run(tool.execute(args, ctx=None))


@pytest.mark.parametrize("admin", [False, True])
def test_only_her_person_gets_a_file_out_of_her_library(admin):
    from kotoba.tools.action import discord_send_file

    said = _run(discord_send_file, {"path": "notes.md"},
                who=_actor(_Member(7, admin=admin)), client=_Client())
    assert "can't" in said.lower()
    assert "her person" in said


def test_a_guest_cannot_read_the_servers_layout():
    from kotoba.tools.action import discord_guild_read

    said = _run(discord_guild_read, {}, who=_actor(_Member(7, admin=False)), client=_Client())
    assert "can't" in said.lower()
    assert "Administrator" in said


def test_nobody_at_all_is_treated_as_a_stranger():
    from kotoba.tools.action import discord_guild_read

    said = _run(discord_guild_read, {}, who=None, client=_Client())
    assert "who's asking" in said


class _Channel:
    def __init__(self, cid, name, *, visible=True) -> None:
        self.id = cid
        self.name = name
        self._visible = visible

    def permissions_for(self, member):
        return type("P", (), {"view_channel": self._visible,
                              "read_message_history": self._visible})()


def _history_client():
    mine = _Channel(10, "general")
    secret = _Channel(11, "staff", visible=False)
    here = _Guild([mine, secret])
    member = _Member(7, admin=False)
    here._members[7] = member
    elsewhere = _Guild([_Channel(20, "other-server")])
    elsewhere.id = 2
    return _Client([here, elsewhere]), member


def test_a_channel_id_from_another_server_is_not_reachable():
    from kotoba.tools.action import discord_read_history

    client, member = _history_client()
    said = _run(discord_read_history, {"channel": "20"}, who=_actor(member), client=client)
    assert "couldn't find" in said


def test_a_channel_the_asker_cannot_see_is_not_read_back_to_them():
    from kotoba.tools.action import discord_read_history

    client, member = _history_client()
    said = _run(discord_read_history, {"channel": "#staff"}, who=_actor(member), client=client)
    assert "couldn't find" in said
    # Named the same way a channel that is simply absent is named: telling them apart confirms the
    # private channel exists.
    assert "#staff" in said


def test_every_field_a_planned_change_names_is_actually_applied():
    from kotoba.discord.plan import Change
    from kotoba.tools.action.discord_apply_plan import _as_actions

    change = Change("c1", "update_role", "role", "7", "Mods",
                    after={"hoist": True, "name": "Moderators", "colour": "#ff0000"})
    ops = {a["op"] for a in _as_actions(change)}
    assert ops == {"recolor_role", "set_role_flags", "rename_role"}

    made = _as_actions(Change("c2", "create_role", "role", "x", "Helper",
                              after={"colour": "#00ff00", "mentionable": True}))
    assert made[0]["mentionable"] is True


def test_post_message_with_no_channel_means_the_one_she_is_in():
    """Every other Discord tool defaults to here. This one refused with "there is no channel called
    ''", so asked to greet somebody in the room she was standing in she could not."""
    from kotoba.tools.action import discord_act

    class _Ch:
        id = 10
        name = "general"

    class _G:
        id = 1
        owner_id = 999
        text_channels = [_Ch()]

        def get_member(self, uid):
            return None

    class _C:
        def __init__(self):
            self.guild = _G()

        def get_guild(self, gid):
            return self.guild

        def get_channel(self, cid):
            return _Ch() if cid == 10 else None

    seen = {}

    import kotoba.discord.actions as act_mod

    real = act_mod.refusals
    act_mod.refusals = lambda guild, actions: (seen.setdefault("actions",
                                               [dict(a) for a in actions]), ["stop here"])[1]
    try:
        who = _actor(_Member(7, admin=True))
        _run(discord_act, {"actions": [{"op": "post_message", "value": "hola"}]},
             who=who, client=_C(), guild=1, channel=10)
    finally:
        act_mod.refusals = real
    assert seen["actions"][0]["target"] == "general"


def test_the_work_done_trigger_is_not_a_message_and_cannot_start_more_work():
    """`__work_done__` is a token a surface fires so a finished job gets reported. Persisted, it
    becomes a user line she reads back for ever; offered `start_work`, announcing a job means
    starting another, which is the loop that once stopped only at a rate limit. The terminal guards
    both and this surface did not."""
    import inspect

    from kotoba.core.context import is_trigger_sentinel
    from kotoba.core.work_state import WORK_DONE
    from kotoba.discord.bridge import ChannelSession

    assert is_trigger_sentinel(WORK_DONE)

    src = inspect.getsource(ChannelSession.ask)
    assert "is_trigger_sentinel(text)" in src, "the surface does not recognise the trigger at all"
    assert '{"start_work", "delegate"}' in src, "the announce turn can start another job"
    assert "persisted=not trigger" in src, "the trigger is written to the transcript"
    assert src.count("if not trigger") >= 2, "the trigger still reaches the DB or the memory pass"


def test_an_announcement_card_never_reaches_this_surface_at_all():
    """Calling `DiscordCards.ask` by hand proves nothing about the live path: `Approvals.present`
    hands over only the cards a Future is waiting on, so a link offer is dropped before Discord sees
    it. Withholding the tool is the only thing that keeps her from announcing one."""
    from kotoba.cli.approvals import Approvals, Card

    card = Card.from_frame({"mode": "open_link", "url": "https://example.com/a",
                            "request_id": "r1", "wait": False})
    assert not card.blocking

    async def presented() -> bool:
        return Approvals("s", ask=lambda c: None).present(card)

    assert not asyncio.run(presented())
    assert "open_link" in authority.NO_SURFACE_TOOLS


def test_both_card_tools_are_withheld_from_her_person_too():
    owner = _actor(_Member(OWNER, admin=True))
    assert owner.is_owner
    for name in ("ask_user", "open_link"):
        assert name in authority.NO_SURFACE_TOOLS, name
        assert name in authority.excluded_tools(owner), name
