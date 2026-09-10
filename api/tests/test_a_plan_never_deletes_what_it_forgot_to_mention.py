"""Reshaping a whole server is the one thing here that destroys something irreplaceable.

Discord has no undelete: a channel takes its messages with it, so the diff carries two properties that
matter more than anything else it does.

Silence means KEEP. `remove` defaults to all-false, so a "reorganise by topic" that forgets a channel
leaves it exactly where it is: a deletion enters a plan only through an explicit flag, never through
the omission somebody actually makes.

Destructive LAST, so a failure anywhere earlier aborts before one has happened.
"""
from __future__ import annotations

from kotoba.discord import plan as plan_mod

SNAP = {
    "guild": {"id": "500", "name": "srv"},
    "roles": [{"id": "1", "name": "@everyone", "managed": False},
              {"id": "2", "name": "Mods", "managed": False},
              {"id": "3", "name": "Nitro", "managed": True}],
    "categories": [{"id": "10", "name": "Lounge"}],
    "channels": [{"id": "20", "name": "general", "category": "Lounge", "topic": None},
                 {"id": "21", "name": "notices", "category": "Lounge", "topic": None},
                 {"id": "22", "name": "spam", "category": "Lounge", "topic": None}],
}


def _diff(desired, **kw):
    return plan_mod.diff(SNAP, desired, **kw)


def test_leaving_something_out_never_deletes_it():
    """The mistake somebody actually makes: describing the new shape and forgetting a channel."""
    changes = _diff({"channels": [{"name": "general"}]})
    assert not [c for c in changes if c.destructive]


def test_a_deletion_needs_an_explicit_flag():
    quiet = _diff({"channels": [{"name": "general"}]})
    loud = _diff({"channels": [{"name": "general"}]}, remove={"channels": True})
    assert [c.name for c in quiet if c.destructive] == []
    assert sorted(c.name for c in loud if c.destructive) == ["notices", "spam"]


def test_a_protected_name_survives_the_removal_flag():
    changes = _diff({"channels": [{"name": "general"}]},
                    remove={"channels": True}, protect={"#notices"})
    assert [c.name for c in changes if c.destructive] == ["spam"]


def test_everyone_and_a_managed_role_are_never_deleted():
    changes = _diff({"roles": []}, remove={"roles": True})
    gone = {c.name for c in changes if c.destructive}
    assert "@everyone" not in gone
    assert "Nitro" not in gone
    assert "Mods" in gone


def test_an_existing_channel_is_updated_not_recreated():
    changes = _diff({"channels": [{"name": "general", "topic": "hola"}]})
    assert [c.op for c in changes] == ["update_channel"]
    assert changes[0].after == {"topic": "hola"}


def test_a_new_channel_is_created():
    changes = _diff({"channels": [{"key": "nuevo", "name": "diseño", "type": "text"}]})
    assert [c.op for c in changes] == ["create_channel"]


def test_nothing_to_change_produces_nothing():
    assert _diff({"channels": [{"name": "general"}, {"name": "notices"}, {"name": "spam"}]}) == []


def test_deletions_come_after_everything_else():
    changes = plan_mod.order(_diff(
        {"channels": [{"key": "n", "name": "nuevo"}]}, remove={"channels": True}))
    first_delete = next(i for i, c in enumerate(changes) if c.destructive)
    assert all(not c.destructive for c in changes[:first_delete])
    assert all(c.destructive for c in changes[first_delete:])


def test_a_dropped_change_stays_dropped_across_a_fresh_reading():
    """Recorded on the plan, not edited out of the desired shape — or a re-diff against a server
    that moved would quietly bring back the very change somebody said no to."""
    plan = plan_mod.new_plan("500", "srv", "por temas",
                             {"channels": [{"key": "n", "name": "nuevo"}]})
    first = plan_mod.live_changes(plan, SNAP)
    assert len(first) == 1
    plan["excluded"].append(first[0].id)
    assert plan_mod.live_changes(plan, SNAP) == []


def test_no_two_changes_share_an_identity():
    twice = _diff({"roles": [{"key": "x", "name": "x"}],
                   "channels": [{"key": "x", "name": "x"}, {"key": "x", "name": "x"},
                                {"key": "20", "name": "general", "topic": "a"},
                                {"key": "20", "name": "general", "topic": "b"}]},
                  remove={"channels": True})
    refs = [c.ref for c in twice]
    assert len(refs) == len(set(refs)), refs
    assert len(twice) == 7, [c.line() for c in twice]
    by_op = {}
    for c in twice:
        by_op.setdefault((c.op, c.key), []).append(c.ref)
    assert by_op[("create_channel", "x")] != by_op[("create_role", "x")]
    assert len(by_op[("update_channel", "20")]) == 2


def test_the_rendering_numbers_every_change_so_they_can_be_quoted():
    plan = plan_mod.new_plan("500", "srv", "por temas",
                             {"channels": [{"key": "a", "name": "uno"}, {"key": "b", "name": "dos"}]})
    body = plan_mod.render(plan, plan_mod.live_changes(plan, SNAP))
    assert "c1." in body and "c2." in body
    assert "Say which numbers to drop" in body


def test_deletions_are_shown_under_their_own_warning():
    plan = plan_mod.new_plan("500", "srv", "limpieza", {"channels": [{"name": "general"}]})
    plan["remove"]["channels"] = True
    body = plan_mod.render(plan, plan_mod.live_changes(plan, SNAP))
    assert "these remove messages with them" in body
