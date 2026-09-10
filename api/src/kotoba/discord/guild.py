"""The server as a document: what is there, and what she is allowed to move.

Read once and reused by everything that changes anything, so a plan is always computed against a
shape somebody can also read out loud.
"""
from __future__ import annotations

CHANNEL_KINDS = {
    0: "text", 2: "voice", 4: "category", 5: "announcement",
    13: "stage", 15: "forum", 16: "media",
}


def _kind(channel) -> str:
    value = getattr(getattr(channel, "type", None), "value", None)
    return CHANNEL_KINDS.get(value, str(getattr(channel, "type", "unknown")))


def _overwrites(channel) -> list[dict]:
    out = []
    for target, perms in (getattr(channel, "overwrites", None) or {}).items():
        allow, deny = perms.pair()
        out.append({
            "for": getattr(target, "name", str(target)),
            "id": str(getattr(target, "id", "")),
            "allow": [n for n, v in allow if v],
            "deny": [n for n, v in deny if v],
        })
    return out


def my_ceiling(guild) -> int:
    me = getattr(guild, "me", None)
    top = getattr(me, "top_role", None)
    return getattr(top, "position", 0) or 0


def ordered_roles(guild) -> list:
    """Highest first, in Discord's own order — never by the position number.

    Positions tie routinely, and sorting the tie group by an integer leaves an arbitrary order. Shown
    a list headed "highest first" where a role she can manage sits above her own, she read the order,
    believed it, and refused work she was perfectly able to do. The library knows the real ordering.
    """
    roles = list(getattr(guild, "roles", []) or [])
    try:
        return sorted(roles, reverse=True)
    except TypeError:
        return sorted(roles, key=lambda r: -getattr(r, "position", 0))


def outranks_me(guild, role) -> bool:
    """Whether a role is out of her reach — asked of the library, not of two integers.

    Discord lets roles SHARE a position and breaks the tie by age, and a fresh server has most of
    them sitting at 1. Compared as numbers, every one of those read as above her and everything was
    refused. The library's own ordering knows the tiebreak; the numeric path is only for a double
    that has no ordering at all.
    """
    top = getattr(getattr(guild, "me", None), "top_role", None)
    if top is None or role is None:
        return False
    try:
        return bool(role >= top)
    except TypeError:
        return getattr(role, "position", 0) >= getattr(top, "position", 0)


def snapshot(guild, *, with_overwrites: bool = True) -> dict:
    ceiling = my_ceiling(guild)
    mine = getattr(getattr(guild, "me", None), "top_role", None)
    roles = []
    for role in ordered_roles(guild):
        roles.append({
            "mine": role is mine,
            "id": str(role.id),
            "name": role.name,
            "position": role.position,
            "colour": str(getattr(role, "colour", "")),
            "hoist": bool(getattr(role, "hoist", False)),
            "mentionable": bool(getattr(role, "mentionable", False)),
            "managed": bool(getattr(role, "managed", False)),
            "members": len(getattr(role, "members", []) or []),
            "above_me": outranks_me(guild, role),
        })

    categories, channels = [], []
    for channel in sorted(getattr(guild, "channels", []) or [],
                          key=lambda c: (getattr(c, "position", 0), c.id)):
        kind = _kind(channel)
        entry = {
            "id": str(channel.id),
            "name": channel.name,
            "type": kind,
            "position": getattr(channel, "position", 0),
        }
        if kind == "category":
            categories.append(entry)
            continue
        parent = getattr(channel, "category", None)
        entry["category"] = getattr(parent, "name", None)
        entry["topic"] = getattr(channel, "topic", None)
        entry["nsfw"] = bool(getattr(channel, "nsfw", False))
        entry["slowmode"] = getattr(channel, "slowmode_delay", 0)
        if with_overwrites:
            entry["overwrites"] = _overwrites(channel)
        channels.append(entry)

    return {
        "guild": {"id": str(guild.id), "name": guild.name,
                  "members": getattr(guild, "member_count", None),
                  "owner_id": str(getattr(guild, "owner_id", "") or "")},
        "my_top_role_position": ceiling,
        "roles": roles,
        "categories": categories,
        "channels": channels,
    }


def describe(snap: dict, *, complete: bool) -> str:
    """Prose she can read out. The completeness line is not decoration: without Administrator she
    cannot see every channel, and a summary that does not say so claims a completeness it lacks."""
    g = snap["guild"]
    lines = [f"**{g['name']}** — {len(snap['channels'])} channels, "
             f"{len(snap['categories'])} categories, {len(snap['roles'])} roles."]
    if not complete:
        lines.append("This is what I can see, which may not be all of it: I am not an "
                     "administrator here.")
    by_cat: dict[str | None, list[str]] = {}
    for ch in snap["channels"]:
        by_cat.setdefault(ch.get("category"), []).append(f"#{ch['name']} ({ch['type']})")
    for cat in snap["categories"]:
        kids = by_cat.pop(cat["name"], [])
        lines.append(f"\n{cat['name']}: " + (", ".join(kids) if kids else "(empty)"))
    loose = [x for kids in by_cat.values() for x in kids]
    if loose:
        lines.append("\nNo category: " + ", ".join(loose))
    lines.append("\nRoles, highest first:")
    for r in snap["roles"]:
        marks = []
        if r.get("mine"):
            marks.append("<- THIS IS YOU")
        if r["above_me"] and not r.get("mine"):
            marks.append("out of your reach")
        if r["managed"]:
            marks.append("managed by an integration")
        lines.append(f"  {r['name']}" + (f"  [{', '.join(marks)}]" if marks else ""))
    lines.append("Everything listed BELOW your own role is yours to hand out and edit. Positions "
                 "tie often, so trust these marks rather than the numbers.")
    return "\n".join(lines)
