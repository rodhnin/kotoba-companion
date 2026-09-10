"""She refused to give a role to somebody, citing a hierarchy that does not apply to giving roles.

Discord gates the two differently. Kicking, banning, timing out or renaming somebody needs her above
THAT PERSON. Handing out a role needs her above THE ROLE, whoever holds it. Conflated, every
administrator became unassignable — and where everybody is an admin, that is everybody. Worse than a
bug: the refusal named a real-sounding reason for something that would have worked, so it read as a
limit of Discord rather than a mistake of ours.

Also here: creating something already there by name is a no-op, not a duplicate.
"""
from __future__ import annotations

import asyncio

from kotoba.discord import actions

CEILING = 5


class _Perms:
    administrator = True


class _Role:
    def __init__(self, name, position, managed=False):
        self.name = name
        self.id = abs(hash(name)) % 10**6
        self.position = position
        self.managed = managed
        self.members = []


class _Member:
    def __init__(self, name, top):
        self.name = name
        self.display_name = name
        self.id = abs(hash(name)) % 10**6
        self.top_role = top


class _Guild:
    id = 500
    owner_id = 99

    def __init__(self):
        mine = _Role("Kotoba", CEILING)
        self.default_role = _Role("@everyone", 0)
        self.roles = [self.default_role, _Role("Founders", 2), _Role("Admin", 8), mine]
        self.me = type("M", (), {"top_role": mine, "guild_permissions": _Perms()})()
        self.channels = [_Role("general", 0)]
        # An administrator: their top role sits above hers, as most members of a busy server do.
        self.members = [_Member("wrenlow", _Role("Admin", 8))]

    def get_channel(self, cid):
        return None

    def get_role(self, rid):
        return None

    def get_member(self, uid):
        return None


def _no(batch):
    return actions.refusals(_Guild(), batch)


def test_giving_a_role_to_an_administrator_is_allowed():
    """The live refusal, gone: her ceiling is above the ROLE, which is the gate that applies."""
    assert _no([{"op": "give_role", "user": "wrenlow", "to": "Founders"}]) == []


def test_taking_a_role_from_an_administrator_is_allowed():
    assert _no([{"op": "take_role", "user": "wrenlow", "to": "Founders"}]) == []


def test_a_role_above_hers_still_cannot_be_handed_out():
    said = _no([{"op": "give_role", "user": "wrenlow", "to": "Admin"}])
    assert "above me" in said[0]


def test_kicking_that_same_person_is_still_refused():
    """The gate that DOES apply to people is untouched."""
    said = _no([{"op": "kick", "user": "wrenlow"}])
    assert "above mine" in said[0]


def test_a_person_who_is_not_here_is_still_named():
    assert "fantasma" in _no([{"op": "give_role", "user": "fantasma", "to": "Founders"}])[0]


def _run(guild, act):
    return asyncio.run(actions.apply(guild, [act], reason="t"))


def test_creating_a_channel_that_is_already_there_leaves_it_alone():
    done, problems = _run(_Guild(), {"op": "create_channel", "target": "general"})
    assert problems == []
    assert "already there" in done[0]


def test_creating_a_role_that_is_already_there_leaves_it_alone():
    done, _ = _run(_Guild(), {"op": "create_role", "target": "Founders"})
    assert "already there" in done[0]


def test_a_forum_tag_splits_its_emoji_from_its_label():
    """Given “❓ Duda” whole, the label reads as one odd word and the emoji never shows."""
    tag = actions._tag("❓ Duda")
    assert tag.name == "Duda"
    assert tag.emoji is not None


def test_a_plain_tag_keeps_its_whole_name():
    assert actions._tag("Recurso").name == "Recurso"


def test_roles_sharing_a_position_are_ordered_by_the_library_not_by_the_number():
    """Measured live: a fresh server had nine roles all at position 1, and `1 >= 1` read as "above
    me" for every one of them. Discord breaks that tie by age; only the library knows how."""
    import discord

    from kotoba.discord import guild as guild_mod

    class _Tied:
        def __init__(self, older):
            self.position = 1
            self._older = older

        def __ge__(self, other):
            return not self._older

    g = _Guild()
    g.me = type("M", (), {"top_role": _Tied(older=True)})()
    assert guild_mod.outranks_me(g, _Tied(older=True)) is False


def test_an_existing_forum_can_still_be_finished():
    """Creating never overwrites what is there, so a forum that already exists needs its own way to
    get a default reaction and a first post — without them Discord's own checklist stays unfinished."""
    assert "set_forum" in actions.OPS
    assert "forum_post" in actions.OPS
