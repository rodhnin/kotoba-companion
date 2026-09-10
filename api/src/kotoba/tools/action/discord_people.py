"""Read back what she has written down about the people here."""
from __future__ import annotations

from kotoba.discord import people

SCHEMA = {
    "type": "function",
    "name": "discord_people",
    "description": (
        "Find someone in this server and read back what you know about them. Use it whenever a "
        "name comes up — \"what's her favourite?\", \"who is that?\", \"mention them\" "
        "— and to get the @handle you need before you can mention anybody. It searches the REAL "
        "member list, so it finds people you have never written a note about, and a name spelled "
        "the way somebody said it out loud usually still lands. When it comes back with several "
        "close matches, ask which one rather than picking. Omit `users` to see who is here. ONE "
        "lookup, then answer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "users": {"type": "array", "items": {"type": "string"}, "description":
                      "Mentions, ids or names. Omit to list everyone you know here."},
        },
        "additionalProperties": False,
    },
}

BUILT_IN = False
TOOLSET = "discord"
RISK = "read"

ANNOUNCE = "Let me look them up."
HEARTBEAT = ["Checking..."]
COMPLETE = "Here's what I have:"
FAIL = "I couldn't look that up right now."

_FACT_LIMIT = 12
_CENSUS_LIMIT = 40
_ROSTER_LIMIT = 60


def check() -> bool:
    from kotoba.discord import state

    return state.runtime_live()


def _line(fact: dict) -> str:
    mark = {"told": " (second-hand)", "observed": " (you worked this out)"}.get(fact["source"], "")
    return f"- {fact['fact']}{mark}"


def _reads_her_persons_notes(who) -> bool:
    """His notes leave by this door and no other. The writer already refuses to file one, so today
    the row is empty and this guards nothing — which is exactly the wrong reason to leave it open:
    the guarantee lives in one tool's discipline, and the next writer will not inherit it."""
    from kotoba.discord import config, state

    owner = config.owner_id()
    if owner is None or getattr(who, "id", None) != owner:
        return False
    return not getattr(state.actor(), "is_owner", False)


async def execute(args: dict, ctx) -> str:
    from kotoba.discord import state

    client = state.client()
    if client is None:
        return "I can only look up Discord people from inside Discord."

    gid = state.guild_id()
    wanted = [w for w in (args.get("users") or []) if str(w).strip()]
    if not wanted:
        return await _census(ctx, client, gid)

    out = []
    for one in wanted:
        who = people.resolve_user(client, gid, str(one),
                                  speaker_id=getattr(state.actor(), "user_id", None))
        if who is None:
            out.append(_no_match(client, gid, str(one)))
            continue
        head = f"{_who(who)} — {getattr(who, 'mention', '')}".rstrip(" —")
        if _reads_her_persons_notes(who):
            # Naming him is fine — anyone in the channel can see him. What he told her is not.
            out.append(head + ": that's my person. What he tells me stays between us.")
            continue
        facts = await ctx.db.person_facts(str(who.id), limit=_FACT_LIMIT)
        out.append(head + (":\n" + "\n".join(_line(f) for f in facts) if facts
                           else ": nothing written down yet."))
    return "\n\n".join(out)


def _who(member) -> str:
    display = getattr(member, "display_name", None) or getattr(member, "name", "")
    handle = getattr(member, "name", "")
    return f"{display} (@{handle})" if handle and handle != display else str(display or handle)


def _no_match(client, gid, one: str) -> str:
    """Offer the near misses instead of stopping. Mentioning the wrong person in public is the thing
    worth refusing; being unable to look somebody up is not."""
    close = people.nearest(client, gid, one)
    if not close:
        return f"Nobody here goes by “{one}”, and nothing close either."
    if len(close) == 1:
        return (f"No exact “{one}”, but there is {_who(close[0])} — "
                f"{getattr(close[0], 'mention', '')}. Say if that is who you meant.")
    names = ", ".join(_who(m) for m in close)
    return f"No exact “{one}”. Closest here: {names}. Which one?"


async def _census(ctx, client, gid) -> str:
    known = {r["handle"]: r for r in await ctx.db.list_discord_people(limit=_CENSUS_LIMIT)}
    here = people.roster(client, gid, limit=_ROSTER_LIMIT)
    if not here:
        # A DM has no roster, and the stored table has no guild in its query, so this used to answer
        # "who do you know?" with everybody from every server and every DM. There is nobody to list
        # in a room of two.
        return ("I only have a list of people when I'm in a server. Ask me here about one person by "
                "name and I'll tell you what I know about them.")
    lines = []
    for member in here:
        row = known.get(getattr(member, "name", ""))
        hidden = _reads_her_persons_notes(member)
        note = f" — {row['facts']} thing(s) noted" if row and not hidden else ""
        if row and row["relation"] == "owner":
            note += " — my person"
        lines.append(f"- {_who(member)}{note}")
    return f"{len(here)} people in this server:\n" + "\n".join(lines)
