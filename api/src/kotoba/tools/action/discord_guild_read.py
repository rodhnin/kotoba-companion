"""Look at the whole Discord server: channels, categories, roles, and what she may not touch."""
from __future__ import annotations

SCHEMA = {
    "type": "function",
    "name": "discord_guild_read",
    "description": (
        "See the whole shape of the Discord server you are in: every channel and category, every "
        "role in order, and which roles sit above you and are therefore out of your reach. Read "
        "this BEFORE you change anything or propose changing anything — a plan written from memory "
        "moves channels that are not there. Set `full` for the raw structure with permission "
        "overwrites when you actually need them; the default summary is what you read out loud."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "full": {"type": "boolean", "description":
                     "Raw structure including permission overwrites, rather than the summary."},
        },
        "additionalProperties": False,
    },
}

BUILT_IN = False
TOOLSET = "discord"
RISK = "read"

ANNOUNCE = "Let me have a look at the server."
HEARTBEAT = ["Reading the channel list...", "Going through the roles..."]
COMPLETE = "Here's how it's laid out:"
FAIL = "I couldn't read the server structure."


def check() -> bool:
    from kotoba.discord import state

    return state.runtime_live()


async def execute(args: dict, ctx) -> str:
    import json

    from kotoba.discord import authority, guild as guild_mod
    from kotoba.discord import state

    # The layout names who can see what, so it is not a public fact about the server.
    who = state.actor()
    if who is None or not who.is_guild_admin:
        return authority.refusal(who, "read this server's layout")

    client = state.client()
    gid = state.guild_id()
    guild = client.get_guild(gid) if client and gid else None
    if guild is None:
        return "I can only read a server from inside one."

    me = getattr(guild, "me", None)
    complete = bool(getattr(getattr(me, "guild_permissions", None), "administrator", False))
    snap = guild_mod.snapshot(guild, with_overwrites=bool(args.get("full")))
    if args.get("full"):
        head = "" if complete else ("NOTE: not an administrator here, so this may not be every "
                                    "channel.\n")
        return head + json.dumps(snap, ensure_ascii=False, indent=1)[:14_000]
    return guild_mod.describe(snap, complete=complete)
