"""“A channel only this group can see” is the request, and the ways to get it wrong are quiet ones.

Letting the group in without denying @everyone leaves the channel wide open while the person has
been told it is private. Denying @everyone without letting HER in leaves a channel she cannot read,
moderate or ever undo. And a permission name Discord does not have is silently ignored by the API —
so the channel comes out less private than the sentence that promised it.

Administrator is stripped here too, not only where roles are created: an overwrite is another door
into the same grant.
"""
from __future__ import annotations

import asyncio

from kotoba.discord import actions


class _Perms:
    administrator = True


class _Principal:
    def __init__(self, name):
        self.name = name
        self.id = abs(hash(name)) % 10**6
        self.position = 1
        self.managed = False
        self.members = []
        self.display_name = name


class _Channel:
    def __init__(self, name):
        self.name = name
        self.id = 20
        self.applied = []

    async def set_permissions(self, target, *, reason=None, overwrite="_", **flags):
        self.applied.append((getattr(target, "name", str(target)),
                             None if overwrite is None else flags))


class _Guild:
    id = 500
    owner_id = 1

    def __init__(self):
        self.default_role = _Principal("@everyone")
        self.me = _Principal("Kotoba")
        self.me.top_role = _Principal("Kotoba")
        self.me.top_role.position = 9
        self.me.guild_permissions = _Perms()
        self.roles = [self.default_role, _Principal("Founders"), self.me.top_role]
        self.channels = [_Channel("founders-room")]
        self.members = []

    def get_channel(self, cid):
        return self.channels[0]

    def get_role(self, rid):
        return None

    def get_member(self, uid):
        return None


def _run(guild, act):
    return asyncio.run(actions.apply(guild, [act], reason="test"))


def test_making_a_channel_private_denies_everyone_first():
    g = _Guild()
    _run(g, {"op": "make_private", "target": "founders-room", "roles": ["Founders"]})
    who, flags = g.channels[0].applied[0]
    assert who == "@everyone"
    assert flags["view_channel"] is False


def test_the_named_group_is_let_in():
    g = _Guild()
    _run(g, {"op": "make_private", "target": "founders-room", "roles": ["Founders"]})
    allowed = {w: f for w, f in g.channels[0].applied if f and f.get("view_channel")}
    assert "Founders" in allowed
    assert allowed["Founders"]["send_messages"] is True


def test_she_keeps_her_own_way_in():
    """Denying @everyone locks her out too, and then nobody can undo it through her."""
    g = _Guild()
    _run(g, {"op": "make_private", "target": "founders-room", "roles": ["Founders"]})
    assert any(w == "Kotoba" and f and f.get("view_channel") for w, f in g.channels[0].applied)


def test_a_private_channel_with_nobody_named_says_so():
    g = _Guild()
    done, _ = _run(g, {"op": "make_private", "target": "founders-room", "roles": []})
    assert "nobody has access yet" in done[0]


def test_making_it_public_clears_the_override_rather_than_setting_one():
    g = _Guild()
    _run(g, {"op": "make_public", "target": "founders-room"})
    assert g.channels[0].applied == [("@everyone", None)]


def test_administrator_never_rides_in_on_an_overwrite():
    g = _Guild()
    _run(g, {"op": "set_permissions", "target": "founders-room", "to": "Founders",
             "allow": ["view_channel", "administrator"]})
    _who, flags = g.channels[0].applied[0]
    assert flags == {"view_channel": True}


def test_a_permission_discord_does_not_have_is_named_back():
    """The API ignores an unknown flag, so the channel comes out less private than the promise."""
    said = actions.refusals(_Guild(), [{"op": "set_permissions", "target": "founders-room",
                                        "to": "Founders", "allow": ["ver_el_canal"]}])
    assert "ver_el_canal" in said[0]


def test_a_group_that_does_not_exist_is_refused_before_anything_happens():
    said = actions.refusals(_Guild(), [{"op": "make_private", "target": "founders-room",
                                        "roles": ["Wanderers"]}])
    assert "Wanderers" in said[0]
