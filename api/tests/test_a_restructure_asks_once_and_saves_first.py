"""Reshaping a whole server is the one action here that cannot be taken back, and the three properties
that carry it are invisible from the tool's output.

Sixty changes must ask ONCE. Approval fatigue is not a nuisance, it is the failure: a person clicking
the fortieth card is no longer reading them, which is exactly when the deletions go past.

The record of the server must be on disk BEFORE the first change, not after the last. If it is written
at the end, the run that needed it most — the one that died halfway — is the run that never wrote it.

And deletions run last, so any earlier failure aborts before a single message has gone.
"""
from __future__ import annotations

import asyncio
import json
import re

import pytest

from kotoba.core import interaction
from kotoba.discord import actions as act_mod
from kotoba.discord import guild as guild_mod
from kotoba.discord import plan as plan_mod
from kotoba.discord import state
from kotoba.tools import ToolContext
from kotoba.tools.action import discord_apply_plan as apply_tool
from kotoba.tools.action import discord_plan as plan_tool


class Actor:
    user_id = 7
    label = "someone"
    is_owner = True
    is_guild_admin = True


class Guild:
    id = 4242
    name = "test server"


class Client:
    def get_guild(self, gid):
        return Guild()


class FakeServer:
    def __init__(self, channels):
        self.channels = [{"id": str(100 + i), "name": n, "topic": None}
                         for i, n in enumerate(channels)]

    def snapshot(self, guild=None, **kw):
        return {"guild": {"id": "4242", "name": "test server"},
                "roles": [], "categories": [], "channels": [dict(c) for c in self.channels]}

    def _find(self, target):
        return next(c for c in self.channels if target in (c["id"], c["name"]))

    def do(self, action):
        op = action["op"]
        if op == "create_channel":
            self.channels.append({"id": str(1000 + len(self.channels)), "name": action["target"],
                                  "topic": action.get("topic")})
        elif op == "set_topic":
            self._find(action["target"])["topic"] = action["value"]
        elif op == "rename_channel":
            self._find(action["target"])["name"] = action["to"]
        elif op == "delete_channel":
            self.channels.remove(self._find(action["target"]))


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A server with thirty channels, and a plan that renames all of them and deletes six."""
    server = FakeServer([f"old-{i}" for i in range(30)] + [f"doomed-{i}" for i in range(6)])
    monkeypatch.setattr(guild_mod, "snapshot", server.snapshot)

    plan = plan_mod.new_plan("4242", "test server", "tidy up", {
        "channels": [{"key": f"old-{i}", "name": f"old-{i}", "topic": "now with a topic"}
                     for i in range(30)]})
    plan["remove"]["channels"] = True
    plan_mod.save(tmp_path, plan)

    calls = {"cards": 0, "applied": [], "snapshot_at": None, "fail_at": None, "server": server}

    async def one_card(ctx, label, **kw):
        calls["cards"] += 1
        calls["card_facts"] = kw.get("notice", {}).get("facts", [])
        return interaction.APPROVED

    async def fake_apply(guild, actions, **kw):
        if calls["fail_at"] is not None and len(calls["applied"]) >= calls["fail_at"]:
            return ([], [f"Discord refused {actions[0]['op']}"])
        for action in actions:
            server.do(action)
        calls["applied"].extend(a["op"] for a in actions)
        return ([f"did {a['op']}" for a in actions], [])

    real_save = plan_mod.save_snapshot

    def watched_save(workdir, snap):
        calls["snapshot_at"] = len(calls["applied"])
        return real_save(workdir, snap)

    monkeypatch.setattr(interaction, "ask_approval", one_card)
    monkeypatch.setattr(act_mod, "apply", fake_apply)
    monkeypatch.setattr(act_mod, "refusals", lambda guild, actions: [])
    monkeypatch.setattr(plan_mod, "save_snapshot", watched_save)
    ctx = ToolContext(db=None, session_id="s1", mode="companion", workdir=str(tmp_path))
    with state.turn(who=Actor(), bot=Client(), guild=4242, channel=1):
        yield plan, ctx, calls, tmp_path


def run(ctx, **args):
    return asyncio.run(apply_tool.execute(args, ctx))


def test_thirty_six_changes_ask_once(rig):
    plan, ctx, calls, _ = rig
    said = run(ctx, plan_id=plan["plan_id"], confirm_deletes=True)
    assert calls["cards"] == 1, "one plan, one question"
    assert len(calls["applied"]) == 36, said


def test_the_record_is_on_disk_before_the_first_change(rig):
    plan, ctx, calls, workdir = rig
    run(ctx, plan_id=plan["plan_id"], confirm_deletes=True)
    assert calls["snapshot_at"] == 0, "it was written after changes had already happened"
    kept = list((workdir / "discord" / "4242").glob("snapshot-*.json"))
    assert len(kept) == 1
    assert json.loads(kept[0].read_text(encoding="utf-8"))["guild"]["id"] == "4242"


def test_every_deletion_runs_after_every_other_change(rig):
    plan, ctx, calls, _ = rig
    run(ctx, plan_id=plan["plan_id"], confirm_deletes=True)
    ops = calls["applied"]
    first_delete = next(i for i, op in enumerate(ops) if op.startswith("delete_"))
    assert all(op.startswith("delete_") for op in ops[first_delete:])


def test_the_ordering_is_the_ordering_function_not_luck():
    """Fed in the worst possible order — every deletion first — the run must still reach them last.
    Read off a natural diff this holds by accident, which is not the same as holding."""
    hostile = [
        plan_mod.Change("c1", "delete_channel", "channel", "1", "doomed"),
        plan_mod.Change("c2", "delete_role", "role", "2", "old role"),
        plan_mod.Change("c3", "set_position", "channel", "3", "somewhere"),
        plan_mod.Change("c4", "create_channel", "channel", "4", "new"),
        plan_mod.Change("c5", "create_category", "category", "5", "group"),
        plan_mod.Change("c6", "create_role", "role", "6", "new role"),
    ]
    got = [c.op for c in plan_mod.order(hostile)]
    assert got == ["create_role", "create_category", "create_channel", "set_position",
                   "delete_channel", "delete_role"], got
    assert not any(op.startswith("delete_") for op in got[:4])


def test_a_category_is_made_before_the_channel_that_goes_in_it():
    """Reversed, every channel lands at the top level and the plan reports success."""
    got = [c.op for c in plan_mod.order([
        plan_mod.Change("c1", "create_channel", "channel", "1", "general"),
        plan_mod.Change("c2", "create_category", "category", "2", "text"),
    ])]
    assert got.index("create_category") < got.index("create_channel")


def test_the_card_names_the_deletions_rather_than_counting_them(rig):
    plan, ctx, calls, _ = rig
    run(ctx, plan_id=plan["plan_id"], confirm_deletes=True)
    facts = dict((f[0], f[1]) for f in calls["card_facts"])
    named = next(v for k, v in facts.items() if "DELETE" in k)
    for i in range(6):
        assert f"doomed-{i}" in named, "a count is the thing nobody can consent to"


def test_a_big_removal_needs_a_second_deliberate_yes(rig, monkeypatch):
    plan, ctx, calls, workdir = rig
    monkeypatch.setattr(apply_tool, "DELETE_CAP", 2)
    said = run(ctx, plan_id=plan["plan_id"])
    assert calls["cards"] == 0 and not calls["applied"]
    assert "confirm_deletes" in said


def test_a_run_that_stopped_does_not_redo_what_it_already_did(rig):
    plan, ctx, calls, _ = rig
    server = calls["server"]

    card = asyncio.run(plan_tool.execute({"plan_id": plan["plan_id"]}, ctx))
    number = re.search(r"(c\d+)\. delete channel “doomed-5”", card).group(1)
    asyncio.run(plan_tool.execute({"plan_id": plan["plan_id"], "drop_changes": [number]}, ctx))

    calls["fail_at"] = 9
    stopped = run(ctx, plan_id=plan["plan_id"], confirm_deletes=True)
    assert calls["applied"] == ["set_topic"] * 9 and "stopped" in stopped, stopped

    calls["applied"].clear()
    calls["fail_at"] = None
    said = run(ctx, plan_id=plan["plan_id"], confirm_deletes=True, resume=True)
    assert calls["applied"].count("set_topic") == 21, said
    assert calls["applied"].count("delete_channel") == 5, said
    assert all(c["topic"] == "now with a topic" for c in server.channels
               if c["name"].startswith("old-")), "a change that never ran was skipped as done"
    names = {c["name"] for c in server.channels}
    assert "doomed-5" in names, "the deletion they said no to came back"
    assert not any(f"doomed-{i}" in names for i in range(5))


def test_a_record_that_only_knew_positions_is_not_resumed(rig):
    plan, ctx, calls, workdir = rig
    journal = workdir / "discord" / "4242" / f"plan-{plan['plan_id']}.journal.jsonl"
    journal.write_text('{"id": "c1", "status": "done"}\n', encoding="utf-8")
    said = run(ctx, plan_id=plan["plan_id"], confirm_deletes=True, resume=True)
    assert calls["cards"] == 0 and not calls["applied"], said
    assert "fresh plan" in said, said
    assert len(calls["server"].channels) == 36


def test_a_plan_from_before_stable_ids_is_refused_by_both_tools(rig):
    plan, ctx, calls, workdir = rig
    plan["version"] = 1
    plan.pop("ids")
    plan["excluded"] = ["c4"]
    plan_mod.save(workdir, plan)
    revised = asyncio.run(plan_tool.execute({"plan_id": plan["plan_id"],
                                             "drop_changes": ["c5"]}, ctx))
    applied = run(ctx, plan_id=plan["plan_id"], confirm_deletes=True)
    assert "fresh plan" in revised and "fresh plan" in applied, (revised, applied)
    assert calls["cards"] == 0 and not calls["applied"]
    assert plan_mod.load(workdir, "4242", plan["plan_id"])["rev"] == 1
