"""Propose a shape for the whole server, and revise it out loud until it is right. Changes nothing."""
from __future__ import annotations

SCHEMA = {
    "type": "function",
    "name": "discord_plan",
    "description": (
        "Work out how to reshape a whole Discord server, WITHOUT touching it. Read the server first, "
        "then describe the shape you want and this returns a numbered list of what would change. "
        "Show that list and let them change their mind: call it again with `plan_id` and "
        "`drop_changes` (\"drop 17 and 22\"), `protect` (\"leave #notices alone\"), "
        "`drop_kinds` (\"keep the roles as they are\") or `allow_delete`. Only when they say to go "
        "ahead do you call discord_apply_plan.\n"
        "NOTHING IS EVER DELETED unless `allow_delete` says so: leaving something out of `desired` "
        "keeps it exactly as it is. Say that plainly when you show the plan."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description":
                        "Revise this plan instead of starting one. Omit to create."},
            "intent": {"type": "string", "description":
                       "One line in their own words: what this reshape is for."},
            "desired": {
                "type": "object",
                "description": "The shape you want. Anything you leave out stays as it is.",
                "properties": {
                    "roles": {"type": "array", "items": {"type": "object", "properties": {
                        "key": {"type": "string", "description":
                                "The role's id if it exists, or a name you invent for a new one."},
                        "name": {"type": "string"},
                        "colour": {"type": "string"},
                        "hoist": {"type": "boolean"},
                        "mentionable": {"type": "boolean"},
                    }, "required": ["name"], "additionalProperties": False}},
                    "categories": {"type": "array", "items": {"type": "object", "properties": {
                        "key": {"type": "string"}, "name": {"type": "string"},
                    }, "required": ["name"], "additionalProperties": False}},
                    "channels": {"type": "array", "items": {"type": "object", "properties": {
                        "key": {"type": "string"}, "name": {"type": "string"},
                        "type": {"type": "string", "enum": ["text", "voice"]},
                        "category": {"type": "string"},
                        "topic": {"type": "string"},
                    }, "required": ["name"], "additionalProperties": False}},
                },
                "additionalProperties": False,
            },
            "protect": {"type": "array", "items": {"type": "string"}, "description":
                        "Names or ids that must never be touched, whatever else the plan says."},
            "drop_changes": {"type": "array", "items": {"type": "string"}, "description":
                             "Numbered ids from the list you showed them, e.g. ['c17','c22']."},
            "drop_kinds": {"type": "array", "items": {"type": "string",
                           "enum": ["role", "category", "channel"]},
                           "description": "Leave a whole kind alone."},
            "allow_delete": {"type": "object", "description":
                             "The ONLY way a deletion enters a plan, e.g. {\"channels\": true}.",
                             "properties": {"channels": {"type": "boolean"},
                                            "categories": {"type": "boolean"},
                                            "roles": {"type": "boolean"}},
                             "additionalProperties": False},
        },
        "additionalProperties": False,
    },
}

BUILT_IN = False
TOOLSET = "discord"
RISK = "read"

ANNOUNCE = "Let me work out what that would take."
HEARTBEAT = ["Reading the server...", "Working out the difference..."]
COMPLETE = "Here's what it would change:"
FAIL = "I couldn't put that plan together."


def check() -> bool:
    from kotoba.discord import state

    return state.runtime_live()


async def execute(args: dict, ctx) -> str:
    from kotoba.discord import authority, guild as guild_mod, plan as plan_mod, state

    who = state.actor()
    if who is None or not who.is_guild_admin:
        return authority.refusal(who, "plan changes to this server")

    client = state.client()
    gid = state.guild_id()
    guild = client.get_guild(gid) if client and gid else None
    if guild is None:
        return "I can only plan a server from inside one."

    workdir = ctx.workdir
    if workdir is None:
        from kotoba.core import workspace

        workdir = workspace.resolve_workdir(getattr(ctx, "session_id", None))

    plan = None
    if args.get("plan_id"):
        plan = plan_mod.load(workdir, str(guild.id), str(args["plan_id"]))
        if plan is None:
            return f"I don't have a plan called “{args['plan_id']}” for this server."
        try:
            plan_mod.excluded_refs(plan)
        except plan_mod.StalePlan as why:
            return str(why)
        plan["rev"] += 1
    if plan is None:
        if not args.get("desired"):
            return "Tell me the shape you want and I'll work out what it would take."
        plan = plan_mod.new_plan(str(guild.id), guild.name, str(args.get("intent") or ""),
                                 dict(args["desired"]))

    if args.get("intent"):
        plan["intent"] = str(args["intent"])
    if args.get("desired"):
        plan["desired"] = dict(args["desired"])
    for name in (args.get("protect") or []):
        if name not in plan["protect"]:
            plan["protect"].append(str(name))
    unknown = []
    for cid in (args.get("drop_changes") or []):
        ref = plan_mod.resolve(plan, str(cid))
        if ref is None:
            unknown.append(str(cid))
        elif ref not in plan["excluded"]:
            plan["excluded"].append(ref)
    if unknown:
        return (f"I don't have a change called {', '.join(unknown)} on plan {plan['plan_id']}. "
                "Show them the list again and use the numbers on it.")
    for kind in (args.get("drop_kinds") or []):
        plural = {"role": "roles", "category": "categories", "channel": "channels"}[kind]
        plan["desired"].pop(plural, None)
        plan["remove"][plural] = False
    for kind, allowed in (args.get("allow_delete") or {}).items():
        if kind in plan["remove"]:
            plan["remove"][kind] = bool(allowed)

    snap = guild_mod.snapshot(guild, with_overwrites=False)
    changes = plan_mod.live_changes(plan, snap)
    plan_mod.save(workdir, plan)
    body = plan_mod.render(plan, changes)
    if not any(c.destructive for c in changes):
        body += "\nNothing is deleted by this plan."
    return body
