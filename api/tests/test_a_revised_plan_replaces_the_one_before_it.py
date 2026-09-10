"""Being able to say "that one, no" is what makes a plan reviewable rather than a yes/no.

Two things have to hold for that to be true. Revising must not leave the old plan on disk, or the
next "apply it" is ambiguous and the wrong one is a server reshaped the way somebody just refused.
And a change they dropped must stay dropped: the plan is re-diffed against a fresh reading of the
server every time, so a refusal edited out of the desired state would quietly reappear on the next
revision.
"""
from __future__ import annotations

from kotoba.discord import plan as plan_mod

SNAP = {"guild": {"id": "9", "name": "s"}, "roles": [], "categories": [],
        "channels": [{"id": "1", "name": "general"}, {"id": "2", "name": "random"}]}
WANTED = {"channels": [{"key": "general", "name": "general", "topic": "one"},
                       {"key": "random", "name": "random", "topic": "two"},
                       {"key": "new", "name": "new"}]}


def test_revising_leaves_exactly_one_plan_on_disk(tmp_path):
    plan = plan_mod.new_plan("9", "s", "tidy", WANTED)
    plan_mod.save(tmp_path, plan)
    plan["rev"] += 1
    plan["intent"] = "tidy, but differently"
    plan_mod.save(tmp_path, plan)
    files = list((tmp_path / "discord" / "9").glob("plan-*.json"))
    assert len(files) == 1, "two plans on disk means 'apply it' is ambiguous"
    back = plan_mod.load(tmp_path, "9", plan["plan_id"])
    assert back["rev"] == 2 and back["intent"] == "tidy, but differently"


def test_a_change_they_said_no_to_stays_gone_on_the_next_revision():
    plan = plan_mod.new_plan("9", "s", "tidy", WANTED)
    first = plan_mod.live_changes(plan, SNAP)
    refused = next(c for c in first if c.op == "create_channel")
    plan["excluded"].append(refused.id)
    again = plan_mod.live_changes(plan, SNAP)
    assert refused.id not in [c.id for c in again]
    assert len(again) == len(first) - 1
    # ...and still gone once the server has moved under it, which is when a re-diff is dangerous.
    moved = {**SNAP, "channels": SNAP["channels"] + [{"id": "3", "name": "extra"}]}
    assert refused.id not in [c.id for c in plan_mod.live_changes(plan, moved)]


MEMES = {"guild": {"id": "9", "name": "s"}, "roles": [], "categories": [],
         "channels": [{"id": "700", "name": "memes"}]}
THREE_NEW = {"channels": [{"key": "a", "name": "a"}, {"key": "b", "name": "b"},
                          {"key": "c", "name": "c"}]}


def _after_a_partial_apply():
    plan = plan_mod.new_plan("9", "s", "tidy", THREE_NEW)
    plan["remove"]["channels"] = True
    first = plan_mod.order(plan_mod.live_changes(plan, MEMES))
    assert [c.id for c in first] == ["c1", "c2", "c3", "c4"]
    plan["excluded"].append(next(c for c in first if c.destructive).id)
    moved = {**MEMES, "channels": MEMES["channels"] + [{"id": "701", "name": "a"}]}
    return first, plan_mod.order(plan_mod.live_changes(plan, moved))


def test_a_dropped_deletion_stays_dropped_once_the_server_moved_under_it():
    _, resumed = _after_a_partial_apply()
    assert not [c for c in resumed if c.destructive], [c.line() for c in resumed]


def test_a_change_keeps_its_number_once_the_server_moved_under_it():
    first, resumed = _after_a_partial_apply()
    before = {c.name: c.id for c in first}
    after = {c.name: c.id for c in resumed}
    assert after == {"b": before["b"], "c": before["c"]}, (before, after)


def test_what_a_run_did_is_named_by_what_it_is_not_where_it_sat():
    first, resumed = _after_a_partial_apply()
    journal = {first[0].ref}
    left = [c.name for c in resumed if c.ref not in journal]
    assert left == ["b", "c"], left


def test_a_plan_that_only_knew_positions_is_refused_not_guessed():
    old = plan_mod.new_plan("9", "s", "tidy", THREE_NEW)
    old["version"] = 1
    old.pop("ids")
    old["excluded"] = ["c4"]
    try:
        plan_mod.live_changes(old, MEMES)
    except plan_mod.StalePlan as why:
        assert "fresh plan" in str(why)
    else:
        raise AssertionError("a positional drop was mapped onto a re-diff by guesswork")


def test_a_number_read_off_the_card_resolves_to_one_change():
    plan = plan_mod.new_plan("9", "s", "tidy", THREE_NEW)
    shown = plan_mod.live_changes(plan, MEMES)
    for change in shown:
        assert plan_mod.resolve(plan, change.id) == change.ref
        assert plan_mod.resolve(plan, change.id[1:]) == change.ref
    assert plan_mod.resolve(plan, "c99") is None


def test_protecting_something_by_name_survives_the_revision():
    plan = plan_mod.new_plan("9", "s", "tidy", {"channels": []})
    plan["remove"]["channels"] = True
    assert [c.name for c in plan_mod.live_changes(plan, SNAP)] == ["general", "random"]
    plan["protect"].append("general")
    assert [c.name for c in plan_mod.live_changes(plan, SNAP)] == ["random"]
