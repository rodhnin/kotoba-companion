"""Apply a plan she already showed them: snapshot first, one card, then paced and journalled."""
from __future__ import annotations

SCHEMA = {
    "type": "function",
    "name": "discord_apply_plan",
    "description": (
        "Apply a plan you already made with discord_plan and showed them. Only call it once they "
        "have actually said to go ahead. It writes a snapshot of the server first, shows ONE "
        "approval card naming every deletion, then works through the changes in order. If more than "
        "a handful of things would be deleted it refuses until you pass `confirm_deletes`, so a big "
        "removal is always a second, deliberate answer. If it stops partway it tells you exactly "
        "where; call it again with `resume` to carry on. There is no undo — the snapshot is a record "
        "of what was there, and recreating a channel does NOT bring its messages back. Say that."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "confirm_deletes": {"type": "boolean", "description":
                                "Required when the plan deletes more than a handful of things."},
            "resume": {"type": "boolean", "description":
                       "Carry on a run that stopped, skipping what already happened."},
        },
        "required": ["plan_id"],
        "additionalProperties": False,
    },
}

BUILT_IN = False
TOOLSET = "discord"
RISK = "write"

# A carded write tool buys no approval headroom for free, and this one runs for a while after the
# card is answered.
TIMEOUT = 600

ANNOUNCE = "Alright — putting the plan into place."
HEARTBEAT = ["Working through the changes...", "Channels done, carrying on...", "Nearly there..."]
COMPLETE = "Done. Here's what changed:"
FAIL = "I stopped partway — let me tell you exactly where."

EXPRESSIONS = {"focus": "determined", "fail": "embarrassed"}

DELETE_CAP = 12


def check() -> bool:
    from kotoba.discord import state

    return state.runtime_live()


def _as_actions(change) -> list[dict]:
    """One planned change, in the shape the executor already speaks — as MANY actions as it names.

    A single update carries every field the plan asked for. Returning one action meant "rename it and
    make it hoisted" applied whichever field was tested first and reported the other one as done."""
    after = change.after or {}
    name = change.name
    ident = change.key if change.key.isdigit() else change.name
    if change.op == "create_role":
        act = {"op": "create_role", "target": name, "value": after.get("colour")}
        act.update({k: after[k] for k in ("hoist", "mentionable") if k in after})
        return [act]
    if change.op == "create_category":
        return [{"op": "create_category", "target": name}]
    if change.op == "create_channel":
        made = {"op": "create_channel", "target": name,
                "to": after.get("category") or "", "value": after.get("type") or "text"}
        if after.get("topic"):
            made["topic"] = after["topic"]
        return [made]
    if change.op == "update_channel":
        out = []
        if "category" in after:
            out.append({"op": "move_channel", "target": ident, "to": after["category"] or ""})
        if "topic" in after:
            out.append({"op": "set_topic", "target": ident, "value": after["topic"]})
        if "name" in after:
            out.append({"op": "rename_channel", "target": ident, "to": after["name"]})
        return out or [{"op": "rename_channel", "target": ident, "to": name}]
    if change.op == "update_category":
        return [{"op": "rename_category", "target": ident, "to": after.get("name", name)}]
    if change.op == "update_role":
        out = []
        if "colour" in after:
            out.append({"op": "recolor_role", "target": ident, "value": after["colour"]})
        flags = {k: after[k] for k in ("hoist", "mentionable") if k in after}
        if flags:
            out.append({"op": "set_role_flags", "target": ident, **flags})
        if "name" in after:
            out.append({"op": "rename_role", "target": ident, "to": after["name"]})
        return out or [{"op": "rename_role", "target": ident, "to": name}]
    return [{"op": change.op, "target": ident}]


async def execute(args: dict, ctx) -> str:
    import json

    from kotoba.core import interaction, text_security
    from kotoba.discord import actions as act_mod
    from kotoba.discord import authority, guild as guild_mod, plan as plan_mod, state

    who = state.actor()
    if who is None or not who.is_guild_admin:
        return authority.refusal(who, "apply changes to this server")

    client = state.client()
    gid = state.guild_id()
    guild = client.get_guild(gid) if client and gid else None
    if guild is None:
        return "I can only reshape a server from inside one."

    workdir = ctx.workdir
    if workdir is None:
        from kotoba.core import workspace

        workdir = workspace.resolve_workdir(getattr(ctx, "session_id", None))

    plan = plan_mod.load(workdir, str(guild.id), str(args.get("plan_id") or ""))
    if plan is None:
        return f"I don't have a plan called “{args.get('plan_id')}” for this server."

    snap = guild_mod.snapshot(guild, with_overwrites=False)
    try:
        changes = plan_mod.order(plan_mod.live_changes(plan, snap))
    except plan_mod.StalePlan as why:
        return str(why)
    plan_mod.save(workdir, plan)
    if not changes:
        return "Nothing to do — the server already looks like that plan."

    done: set[str] = set()
    journal = plan_mod.plan_dir(workdir, str(guild.id)) / f"plan-{plan['plan_id']}.journal.jsonl"
    if args.get("resume") and journal.is_file():
        for line in journal.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if not entry.get("ref"):
                return (f"I can't safely resume plan {plan['plan_id']}: the record of that run "
                        "doesn't say which changes it made in a way I can still match, so I won't "
                        "guess. Make a fresh plan from the server as it is now — it will only list "
                        "what is left.")
            done.add(entry["ref"])
        changes = [c for c in changes if c.ref not in done]
        if not changes:
            return "That run already finished everything in the plan."

    deletions = [c for c in changes if c.destructive]
    if len(deletions) > DELETE_CAP and not args.get("confirm_deletes"):
        return (f"That plan deletes {len(deletions)} things, which is more than I will do on one "
                "answer. Read them back to them and call me again with confirm_deletes once they "
                "have said yes to that specifically.")

    plan_actions = [_as_actions(c) for c in changes]
    stopped = act_mod.refusals(guild, [a for group in plan_actions for a in group
                                       if a.get("op") in act_mod.OPS])
    if stopped:
        return ("I can't run this plan as it stands, so I've done none of it:\n"
                + "\n".join(stopped) + "\nTell me how to handle those and I'll revise the plan.")

    kept = plan_mod.save_snapshot(workdir, guild_mod.snapshot(guild, with_overwrites=True))

    facts = [["What for", text_security.scrub(plan.get("intent") or "") or "not said"],
             ["Changes", f"{len(changes) - len(deletions)} to make"]]
    if deletions:
        named = " · ".join(c.name for c in deletions[:DELETE_CAP])
        extra = "" if len(deletions) <= DELETE_CAP else f" … and {len(deletions) - DELETE_CAP} more"
        facts.append(["DELETES — messages go with them", text_security.scrub(named + extra)])
    facts.append(["Saved first", f"a record of the server as it is now, at {kept}"])
    facts.append(["No undo", "recreating a channel does not bring its messages back"])
    if plan.get("protect"):
        facts.append(["Never touched", text_security.scrub(" · ".join(plan["protect"]))])

    verdict = await interaction.ask_approval(
        ctx, f"Apply plan {plan['plan_id']} to “{guild.name}”: {len(changes)} change(s)"
             + (f", DELETING {len(deletions)}" if deletions else "") + "?",
        family="",
        notice={"head": f"Reshape “{guild.name}”?", "facts": facts, "surface": "discord"})
    if verdict != interaction.APPROVED:
        return interaction.refusal_note(verdict, f"reshaping “{guild.name}”")

    applied, problems = [], []
    with journal.open("a", encoding="utf-8") as log:
        for change, group in zip(changes, plan_actions):
            got, failed = await act_mod.apply(guild, group, reason=plan.get("intent") or "")
            if failed:
                problems = failed
                break
            applied.append(f"{change.id}: {'; '.join(got) if got else change.line()}")
            log.write(json.dumps({"id": change.id, "ref": change.ref, "status": "done"}) + "\n")
            log.flush()

    said = [f"Applied {len(applied)} of {len(changes)} change(s) to “{guild.name}”."]
    if applied:
        said.append("\n".join(f"- {a}" for a in applied[:40]))
    if problems:
        said.append(f"Then I stopped: {problems[0]}")
        said.append(f"Nothing after that ran{', and no deletion happened' if deletions else ''} — "
                    "deletions come last. The plan is still saved as "
                    f"`{plan['plan_id']}`; fix that and tell me to continue.")
    else:
        said.append(f"The server as it was is saved at {kept}.")
    return "\n".join(said)
