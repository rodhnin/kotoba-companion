"""“Create the role and give it to her” was refused whole, because the role did not exist yet.

Measured live. Validation ran every action against the server as it stands, so any batch that built
something and then used it failed its own check — and building something and then using it is the
shape of every interesting request. She recovered gracefully, split it in two and reported honestly
that half of it had not run, which is exactly right and still one round trip nobody should need.

So the check walks the batch in order and counts what earlier actions will have created by then.
"""
from __future__ import annotations

from kotoba.discord import actions


class _Perms:
    administrator = True


class _Thing:
    def __init__(self, name, position=1, managed=False):
        self.name = name
        self.id = abs(hash(name)) % 10**6
        self.position = position
        self.managed = managed
        self.members = []
        self.display_name = name


class _Guild:
    id = 500
    owner_id = 1

    def __init__(self):
        self.default_role = _Thing("@everyone")
        top = _Thing("Kotoba", position=9)
        self.me = _Thing("Kotoba")
        self.me.top_role = top
        self.me.guild_permissions = _Perms()
        self.roles = [self.default_role, top]
        self.channels = [_Thing("general")]
        self.members = [_Thing("wrenlow")]

    def get_channel(self, cid):
        return None

    def get_role(self, rid):
        return None

    def get_member(self, uid):
        return None


def _no(batch):
    return actions.refusals(_Guild(), batch)


def test_a_role_created_earlier_in_the_batch_can_be_used():
    assert _no([
        {"op": "create_role", "target": "Founders", "value": "#3b82f6"},
        {"op": "give_role", "user": "wrenlow", "to": "Founders"},
    ]) == []


def test_a_channel_created_earlier_in_the_batch_can_be_made_private():
    assert _no([
        {"op": "create_role", "target": "Founders"},
        {"op": "create_channel", "target": "founders-room", "value": "text"},
        {"op": "make_private", "target": "founders-room", "roles": ["Founders"]},
    ]) == []


def test_order_still_matters_inside_the_batch():
    """Using it BEFORE creating it is a real mistake and still gets named."""
    said = _no([
        {"op": "make_private", "target": "founders-room", "roles": ["Founders"]},
        {"op": "create_channel", "target": "founders-room", "value": "text"},
    ])
    assert said and "founders-room" in said[0]


def test_something_that_exists_nowhere_at_all_is_still_refused():
    said = _no([{"op": "give_role", "user": "wrenlow", "to": "Wanderers"}])
    assert "Wanderers" in said[0]


def test_a_forum_is_a_channel_kind_she_can_ask_for():
    from kotoba.tools.action import discord_act

    props = discord_act.SCHEMA["parameters"]["properties"]["actions"]["items"]["properties"]
    assert "tags" in props
    assert "forum" in props["value"]["description"]
