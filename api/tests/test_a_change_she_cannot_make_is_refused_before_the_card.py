"""A card must never promise something impossible.

A bot cannot touch a role at or above its own, cannot act on the server owner, and cannot edit an
integration's managed role. Discovered at change forty, each of those is a 403 nobody can read and a
half-applied server; said before the card, each is one sentence naming what is in the way.

Administrator is refused outright and not by hierarchy: it is every permission, forever, to whoever
holds the role — including the ones that undo the consent that granted it.
"""
from __future__ import annotations

from kotoba.discord import actions

CEILING = 5


class _Perms:
    def __init__(self, administrator=False):
        self.administrator = administrator


class _Role:
    def __init__(self, rid, name, position, managed=False):
        self.id = rid
        self.name = name
        self.position = position
        self.managed = managed
        self.members = []


class _Member:
    def __init__(self, uid, name, top):
        self.id = uid
        self.name = name
        self.display_name = name
        self.top_role = top


class _Channel:
    def __init__(self, cid, name):
        self.id = cid
        self.name = name


class _Guild:
    id = 500
    owner_id = 1

    def __init__(self):
        mine = _Role(10, "Kotoba", CEILING)
        self.roles = [_Role(11, "Boss", 9), _Role(12, "Nitro", 3, managed=True), mine,
                      _Role(13, "Temp", 2)]
        self.me = type("M", (), {"top_role": mine, "guild_permissions": _Perms(True)})()
        self.channels = [_Channel(20, "general")]
        self.members = [_Member(1, "founder", _Role(11, "Boss", 9)),
                        _Member(2, "regular", _Role(13, "Temp", 2)),
                        _Member(3, "senior", _Role(11, "Boss", 9))]

    def get_channel(self, cid):
        return next((c for c in self.channels if c.id == cid), None)

    def get_role(self, rid):
        return next((r for r in self.roles if r.id == rid), None)

    def get_member(self, uid):
        return next((m for m in self.members if m.id == uid), None)


def _no(actions_list):
    return actions.refusals(_Guild(), actions_list)


def test_a_role_above_hers_is_named_as_the_thing_in_the_way():
    said = _no([{"op": "rename_role", "target": "Boss", "to": "Bossy"}])
    assert len(said) == 1
    assert "Boss" in said[0] and "above me" in said[0]


def test_a_managed_role_is_refused_with_its_real_reason():
    said = _no([{"op": "delete_role", "target": "Nitro"}])
    assert "integration" in said[0]


def test_a_role_below_hers_is_fine():
    assert _no([{"op": "rename_role", "target": "Temp", "to": "Guests"}]) == []


def test_the_server_owner_is_out_of_reach():
    said = _no([{"op": "kick", "op_user": None, "target": "founder", "user": "founder"}])
    assert "server owner" in said[0]


def test_somebody_wearing_a_role_above_hers_is_out_of_reach():
    said = _no([{"op": "timeout", "target": "senior", "user": "senior", "value": "10"}])
    assert "above mine" in said[0]


def test_administrator_is_never_handed_out():
    said = _no([{"op": "create_role", "target": "Mods", "permissions": ["administrator"]}])
    assert "administrator" in said[0].lower()


def test_a_channel_that_is_not_there_is_named_not_guessed():
    said = _no([{"op": "rename_channel", "target": "notices", "to": "avisos"}])
    assert "notices" in said[0]


def test_an_op_she_does_not_know_is_said_out_loud():
    assert "nuke_everything" in _no([{"op": "nuke_everything", "target": "x"}])[0]


def test_deletions_are_named_one_by_one_never_counted():
    """A count is exactly the thing a person cannot consent to."""
    parts = actions.summarise([
        {"op": "delete_channel", "target": "viejo-general"},
        {"op": "delete_channel", "target": "spam"},
        {"op": "create_channel", "target": "nuevo"},
    ])
    assert parts["deletes"] == ["delete channel viejo-general", "delete channel spam"]
    assert parts["creates"] == ["channel nuevo"]


def test_a_carded_write_tool_outlives_its_own_card():
    """Approval headroom is free only for RISK='exec'. At the default budget the loop would cancel
    this one long before its card expires, leaving the card orphaned on screen."""
    from kotoba.core.interaction import TEXT_APPROVAL_TIMEOUT
    from kotoba.tools.action import discord_act

    assert discord_act.TIMEOUT > TEXT_APPROVAL_TIMEOUT


def test_the_batch_is_capped_so_one_call_is_one_card():
    assert actions.MAX_ACTIONS >= 10
    assert "actions" in discord_act_schema()["properties"]


def discord_act_schema():
    from kotoba.tools.action import discord_act

    return discord_act.SCHEMA["parameters"]


def test_an_action_about_a_person_names_the_person_on_the_card():
    """“give role @Founders” with no name is asking for consent to half a sentence: the whole
    question is who receives it. Measured on a live card that read exactly that."""
    parts = actions.summarise([{"op": "give_role", "user": "Rowan Vale", "to": "Founders"}])
    assert parts["changes"] == ["give role Founders → Rowan Vale"]


def test_removing_somebody_is_counted_as_destructive():
    parts = actions.summarise([{"op": "kick", "user": "spammer"},
                               {"op": "timeout", "user": "loud", "value": "10"}])
    assert parts["deletes"] == ["kick spammer"]
    assert parts["changes"] == ["timeout 10 → loud"]
