"""She answered about herself in the third person when people used her name.

Two things had to be true at once. She has to KNOW that the name people are saying is her, or she
reports on herself like a bystander. And the name can never be a constant in the source: whoever runs
this renames her, and a per-server nickname is ordinary Discord — a hardcoded name would be correct
for exactly one installation, its author's.

So it is read off the gateway every turn: nickname here, account name, and the integration role,
because people reach for whichever of those Discord autocompleted for them.
"""
from __future__ import annotations

from kotoba.discord.bridge import ChannelSession

GUILD = 500


class _Role:
    def __init__(self, rid: int, name: str) -> None:
        self.id = rid
        self.name = name


class _Me:
    def __init__(self, display: str, name: str, roles=()) -> None:
        self.display_name = display
        self.name = name
        self.roles = list(roles)


class _Guild:
    id = GUILD

    def __init__(self, me) -> None:
        self.me = me


class _Bot:
    def __init__(self, guild, user) -> None:
        self._guild = guild
        self.user = user

    def get_guild(self, gid):
        return self._guild if gid == GUILD else None


def _session(*, me=None, user=None, guild_id=GUILD) -> ChannelSession:
    s = ChannelSession.__new__(ChannelSession)
    s.guild_id = guild_id
    s.bot = _Bot(_Guild(me) if me else None, user)
    return s


def test_the_name_comes_from_the_gateway_not_from_the_source():
    """Renamed by whoever runs it, she still knows who she is."""
    s = _session(me=_Me("Mizuki", "mizuki-bot"), user=_Me("Mizuki", "mizuki-bot"))
    assert s.my_names()[0] == "Mizuki"
    assert "Mizuki" in s._name_note()


def test_a_per_server_nickname_wins_over_the_account_name():
    s = _session(me=_Me("Sora", "kotoba-bot"), user=_Me("Kotoba", "kotoba-bot"))
    assert s.my_names()[0] == "Sora"


def test_the_integration_role_counts_as_her_name_too():
    """People reach for whichever one Discord autocompleted, and half of them get the role."""
    me = _Me("Kotoba", "kotoba", roles=[_Role(999, "Kotoba")])
    assert "Kotoba" in _session(me=me, user=me).my_names()


def test_the_everyone_role_is_not_one_of_her_names():
    me = _Me("Kotoba", "kotoba", roles=[_Role(GUILD, "@everyone")])
    assert "@everyone" not in _session(me=me, user=me).my_names()


def test_she_is_told_to_answer_in_the_first_person():
    note = _session(me=_Me("Kotoba", "kotoba"), user=_Me("Kotoba", "kotoba"))._name_note()
    assert "is you" in note
    assert "answer as I" in note


def test_no_name_known_says_nothing_rather_than_guessing():
    assert _session(me=None, user=None, guild_id=None)._name_note() == ""


def test_no_name_is_hardcoded_in_the_module():
    """The whole point: this file must work for an installation that never heard of Kotoba."""
    import inspect

    from kotoba.discord import bridge

    src = inspect.getsource(bridge.ChannelSession.my_names)
    assert "Kotoba" not in src
