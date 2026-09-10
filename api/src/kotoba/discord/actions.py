"""Changing a Discord server: what an op means, what is refused before anyone consents, and how a
half-finished batch reports itself.

Refusals are computed BEFORE the card. A plan that cannot run must be said in words, not discovered
at change forty with a 403 nobody can read — and a card must never promise something impossible.
"""
from __future__ import annotations

import asyncio
import re

CHANNEL_TOKEN = re.compile(r"<#(\d{15,25})>")

MAX_ACTIONS = 20
PACE = 0.35     # seconds between mutating calls; a burst is what trips the global limiter

DESTRUCTIVE = frozenset({"delete_channel", "delete_category", "delete_role", "kick", "ban"})

OPS = frozenset({
    "create_channel", "rename_channel", "move_channel", "set_topic", "set_slowmode",
    "delete_channel", "create_category", "rename_category", "delete_category",
    "create_role", "rename_role", "recolor_role", "set_role_flags", "delete_role",
    "give_role", "take_role",
    "kick", "ban", "timeout", "move_to_voice", "nickname", "post_message",
    "set_permissions", "make_private", "make_public", "set_forum", "forum_post",
})

# Nobody can meaningfully consent to this on a card: it is every permission, forever, to whoever holds
# the role — including the ones that would let them undo the consent.
NEVER = frozenset({"administrator"})


def _name(thing) -> str:
    return getattr(thing, "name", None) or str(thing)


def _tag(raw: str):
    """A forum tag, with its emoji split off the front when one is written there.

    Discord keeps the two apart; handed an emoji and a label as one string, the label reads as one odd
    word and the emoji never shows where a tag's emoji shows."""
    import discord

    text = (raw or "").strip()
    head, _, rest = text.partition(" ")
    if rest and head and not head[0].isalnum():
        return discord.ForumTag(name=rest.strip()[:20], emoji=discord.PartialEmoji(name=head))
    return discord.ForumTag(name=text[:20])


def valid_permissions() -> set[str]:
    import discord

    return set(discord.Permissions.VALID_FLAGS)


def unknown_permissions(names) -> list[str]:
    """Named back rather than ignored: a permission silently dropped is a channel that is not as
    private as the person was told it would be."""
    known = valid_permissions()
    return [n for n in (names or []) if str(n).lower() not in known]


def _principal(guild, wanted: str):
    """A role or a person — permissions are set on either, and the words for them look the same."""
    if not wanted or wanted.strip() in ("@everyone", "everyone"):
        return guild.default_role
    return find_role(guild, wanted) or find_member(guild, wanted)


def find_channel(guild, wanted: str):
    raw = (wanted or "").strip().lstrip("#")
    token = CHANNEL_TOKEN.search(raw)
    if token:
        raw = token.group(1)
    if raw.isdigit():
        return guild.get_channel(int(raw))
    low = raw.lower()
    return next((c for c in getattr(guild, "channels", []) if c.name.lower() == low), None)


def find_role(guild, wanted: str):
    raw = (wanted or "").strip().lstrip("@")
    if raw.isdigit():
        return guild.get_role(int(raw))
    low = raw.lower()
    return next((r for r in getattr(guild, "roles", []) if r.name.lower() == low), None)


def find_member(guild, wanted: str):
    import re

    raw = (wanted or "").strip()
    hit = re.search(r"<@!?(\d{15,25})>", raw)
    if hit:
        raw = hit.group(1)
    if raw.isdigit():
        return guild.get_member(int(raw))
    low = raw.lstrip("@").lower()
    return next((m for m in getattr(guild, "members", [])
                 if low in (m.name.lower(), (m.display_name or "").lower())), None)


def refusals(guild, actions: list[dict]) -> list[str]:
    """Everything that cannot run, said before anybody is asked to approve anything.

    Checked in order, against the server PLUS whatever earlier actions in the same batch will have
    created by then. Judged against the server alone, "create the role, then give it to her" was
    refused as a whole because the role did not exist yet — and the natural way to ask for anything
    interesting is exactly that shape.
    """
    from kotoba.discord import guild as guild_mod

    out: list[str] = []
    owner_id = getattr(guild, "owner_id", None)
    outranks = guild_mod.outranks_me
    coming_roles: set[str] = set()
    coming_channels: set[str] = set()

    def _known_role(wanted: str) -> bool:
        return (find_role(guild, wanted) is not None
                or (wanted or "").strip().lower().lstrip("@") in coming_roles)

    def _known_channel(wanted: str) -> bool:
        return (find_channel(guild, wanted) is not None
                or (wanted or "").strip().lower().lstrip("#") in coming_channels)

    for i, act in enumerate(actions, 1):
        op = str(act.get("op") or "")
        target = str(act.get("target") or "")
        if op not in OPS:
            out.append(f"{i}. I don't know how to “{op}”.")
            continue
        if op == "create_role":
            coming_roles.add(target.strip().lower().lstrip("@"))
        elif op in ("create_channel", "create_category"):
            coming_channels.add(target.strip().lower().lstrip("#"))
        if op in ("rename_role", "recolor_role", "set_role_flags", "delete_role", "give_role",
                  "take_role"):
            wanted = str(act.get("to") or target) if op in ("give_role", "take_role") else target
            role = find_role(guild, wanted)
            if role is None:
                if not _known_role(wanted):
                    out.append(f"{i}. There is no role called “{wanted}”.")
            elif outranks(guild, role):
                out.append(f"{i}. **{role.name}** sits above me in the role list, so I cannot "
                           "touch it. Move my role above it and I can.")
            elif getattr(role, "managed", False):
                out.append(f"{i}. **{role.name}** belongs to an integration; nobody can edit it.")
        # Discord gates these two differently, and conflating them refuses work that would succeed:
        # kicking, banning, timing out or renaming somebody needs her above THAT PERSON, while
        # handing out a role needs her above THE ROLE and does not care whose it is. Checked the
        # first way, giving a role to any administrator was refused for a hierarchy that never
        # applied.
        if op in ("kick", "ban", "timeout", "nickname", "move_to_voice"):
            who = act.get("user") or target
            member = find_member(guild, str(who or ""))
            if member is None:
                out.append(f"{i}. I can't find “{who}” here.")
            elif getattr(member, "id", None) == owner_id:
                out.append(f"{i}. That is the server owner; nothing I do reaches them.")
            elif outranks(guild, getattr(member, "top_role", None)):
                out.append(f"{i}. **{_name(member)}** has a role above mine, so I cannot act on "
                           "them.")
        if op in ("give_role", "take_role"):
            who = act.get("user") or target
            if find_member(guild, str(who or "")) is None:
                out.append(f"{i}. I can't find “{who}” here.")
        if op in ("rename_channel", "move_channel", "set_topic", "set_slowmode", "delete_channel",
                  "post_message", "rename_category", "delete_category", "set_forum",
                  "forum_post"):
            if not _known_channel(target):
                out.append(f"{i}. There is no channel or category called “{target}”.")
        if op == "post_message":
            ch = find_channel(guild, target)
            if ch is not None and not hasattr(ch, "send"):
                out.append(f"{i}. #{_name(ch)} takes posts, not messages — use forum_post with "
                           "a title in `to` and the text in `value`.")
        if op == "forum_post" and not str(act.get("to") or "").strip():
            out.append(f"{i}. A forum post needs a title: put it in `to`.")
        perms = [str(p).lower() for p in
                 (list(act.get("permissions") or []) + list(act.get("allow") or []))]
        if NEVER.intersection(perms):
            out.append(f"{i}. I will not hand out **administrator**. Ask for the specific "
                       "permissions instead.")
        if op in ("set_permissions", "make_private", "make_public"):
            if not _known_channel(target):
                out.append(f"{i}. There is no channel called “{target}”.")
            for wanted in ([act.get("to")] if op != "make_private"
                           else list(act.get("roles") or [act.get("to")])):
                if not wanted:
                    continue
                if _principal(guild, str(wanted)) is None and not _known_role(str(wanted)):
                    out.append(f"{i}. I can't find “{wanted}” to give access to.")
            bad = unknown_permissions(list(act.get("allow") or []) + list(act.get("deny") or []))
            if bad:
                out.append(f"{i}. Discord has no permission called {', '.join(bad)}.")
    return out


# Ops that happen TO somebody. A card saying "give role @Moderator" with no name is asking for consent
# to half a sentence: the whole question is who receives it.
_ABOUT_A_PERSON = frozenset({"give_role", "take_role", "kick", "ban", "timeout", "nickname",
                             "move_to_voice"})


def summarise(actions: list[dict]) -> dict:
    """The card's own facts. Deletions are NAMED, never counted — a number is the thing nobody can
    consent to."""
    creates, changes, deletes = [], [], []
    for act in actions:
        op = str(act.get("op") or "")
        verb = op.replace("_", " ")
        label = str(act.get("to") or act.get("target") or act.get("value") or "?")
        if op in _ABOUT_A_PERSON:
            who = act.get("user") or act.get("target") or "?"
            thing = act.get("to") or act.get("value") or ""
            line = f"{verb} {thing} → {who}".replace("  ", " ") if thing else f"{verb} {who}"
            (deletes if op in DESTRUCTIVE else changes).append(line)
        elif op.startswith("create_"):
            creates.append(f"{op.removeprefix('create_')} {label}")
        elif op in DESTRUCTIVE:
            deletes.append(f"{verb} {act.get('target') or '?'}")
        else:
            changes.append(f"{verb} {act.get('target') or label}")
    return {"creates": creates, "changes": changes, "deletes": deletes}


async def apply(guild, actions: list[dict], *, reason: str = "") -> tuple[list[str], list[str]]:
    """Sequential and paced. Returns (done, problems); a hard failure stops the run rather than
    carrying on into a half-applied permissions model."""
    import discord

    done: list[str] = []
    problems: list[str] = []
    for act in actions:
        try:
            done.append(await _one(guild, act, reason))
        except discord.Forbidden:
            problems.append(f"Discord refused “{act.get('op')}” on “{act.get('target')}” — I do "
                            "not have the permission for it. I stopped there.")
            break
        except Exception as exc:
            problems.append(f"“{act.get('op')}” on “{act.get('target')}” failed "
                            f"({type(exc).__name__}). I stopped there.")
            break
        await asyncio.sleep(PACE)
    return done, problems


async def _one(guild, act: dict, reason: str) -> str:
    import discord

    op = str(act.get("op"))
    target = str(act.get("target") or "")
    to = str(act.get("to") or "")
    value = act.get("value")
    why = str(act.get("reason") or reason) or None

    # Creating something that is already there by that name is what a second pass over the same
    # request looks like, and a duplicate category is worse than a no-op. Declarative means the end
    # state is what was asked for, not that a call happened.
    if op in ("create_channel", "create_category") and find_channel(guild, target) is not None:
        return f"{target} was already there, so I left it alone"
    if op == "create_role" and find_role(guild, target) is not None:
        return f"the {target} role was already there, so I left it alone"

    if op == "create_channel":
        parent = find_channel(guild, to) if to else None
        kind = str(value or "text").lower()
        common = {"category": parent, "reason": why}
        topic = act.get("topic")
        if kind == "voice":
            made = await guild.create_voice_channel(target, **common)
        elif kind == "stage":
            made = await guild.create_stage_channel(target, **common)
        elif kind in ("forum", "media"):
            tags = [_tag(str(t)) for t in (act.get("tags") or [])][:20]
            extra = {"media": True} if kind == "media" else {}
            emoji = act.get("emoji")
            if emoji:
                extra["default_reaction_emoji"] = discord.PartialEmoji(name=str(emoji))
            made = await guild.create_forum(target, topic=str(topic or ""),
                                            available_tags=tags or discord.utils.MISSING,
                                            **extra, **common)
            said = f"created forum #{made.name}"
            if tags:
                said += " with tags: " + ", ".join(t.name for t in tags)
            first = act.get("first_post")
            if first:
                await made.create_thread(name=str(first)[:100],
                                         content=str(act.get("first_body") or first)[:1900])
                said += ", and opened the first post"
            return said
        elif kind in ("announcement", "news"):
            made = await guild.create_text_channel(target, news=True,
                                                   topic=str(topic or "") or None, **common)
        else:
            made = await guild.create_text_channel(target, topic=str(topic or "") or None, **common)
        return f"created {kind} #{made.name}"
    if op == "create_category":
        made = await guild.create_category(target, reason=why)
        return f"created category {made.name}"
    if op in ("rename_channel", "rename_category"):
        ch = find_channel(guild, target)
        was = ch.name
        await ch.edit(name=to, reason=why)
        return f"renamed {was} to {to}"
    if op == "move_channel":
        ch = find_channel(guild, target)
        await ch.edit(category=find_channel(guild, to) if to else None, reason=why)
        return f"moved #{ch.name} to {to or 'no category'}"
    if op == "set_topic":
        ch = find_channel(guild, target)
        await ch.edit(topic=str(value or ""), reason=why)
        return f"set the topic of #{ch.name}"
    if op == "set_slowmode":
        ch = find_channel(guild, target)
        await ch.edit(slowmode_delay=int(value or 0), reason=why)
        return f"set slowmode on #{ch.name} to {int(value or 0)}s"
    if op in ("delete_channel", "delete_category"):
        ch = find_channel(guild, target)
        name = ch.name
        await ch.delete(reason=why)
        return f"deleted {name}"
    if op == "create_role":
        colour = discord.Colour.from_str(str(value)) if value else discord.Colour.default()
        flags = {k: bool(act[k]) for k in ("hoist", "mentionable") if act.get(k) is not None}
        made = await guild.create_role(name=target, colour=colour, reason=why, **flags)
        return f"created role {made.name}"
    if op == "set_role_flags":
        role = find_role(guild, target)
        flags = {k: bool(act[k]) for k in ("hoist", "mentionable") if act.get(k) is not None}
        if not flags:
            return f"nothing to change on the {role.name} role"
        await role.edit(reason=why, **flags)
        said = ", ".join(f"{k} {'on' if v else 'off'}" for k, v in flags.items())
        return f"set {said} on the {role.name} role"
    if op == "rename_role":
        role = find_role(guild, target)
        was = role.name
        await role.edit(name=to, reason=why)
        return f"renamed role {was} to {to}"
    if op == "recolor_role":
        role = find_role(guild, target)
        await role.edit(colour=discord.Colour.from_str(str(value)), reason=why)
        return f"recoloured {role.name}"
    if op == "delete_role":
        role = find_role(guild, target)
        name = role.name
        await role.delete(reason=why)
        return f"deleted role {name}"
    if op in ("give_role", "take_role"):
        member = find_member(guild, str(act.get("user") or target))
        role = find_role(guild, to or target)
        if op == "give_role":
            await member.add_roles(role, reason=why)
            return f"gave {_name(member)} the {role.name} role"
        await member.remove_roles(role, reason=why)
        return f"took the {role.name} role from {_name(member)}"
    if op == "nickname":
        member = find_member(guild, str(act.get("user") or target))
        await member.edit(nick=to or None, reason=why)
        return f"renamed {_name(member)} to {to or 'their own name'}"
    if op == "move_to_voice":
        member = find_member(guild, str(act.get("user") or target))
        await member.move_to(find_channel(guild, to), reason=why)
        return f"moved {_name(member)} to {to}"
    if op == "timeout":
        from datetime import timedelta

        member = find_member(guild, str(act.get("user") or target))
        await member.timeout(timedelta(minutes=int(value or 10)), reason=why)
        return f"timed {_name(member)} out for {int(value or 10)} minutes"
    if op == "kick":
        member = find_member(guild, str(act.get("user") or target))
        name = _name(member)
        await guild.kick(member, reason=why)
        return f"kicked {name}"
    if op == "ban":
        member = find_member(guild, str(act.get("user") or target))
        name = _name(member)
        await guild.ban(member, reason=why, delete_message_days=0)
        return f"banned {name}"
    if op == "set_permissions":
        ch = find_channel(guild, target)
        who = _principal(guild, to)
        flags = {str(p).lower(): True for p in (act.get("allow") or [])}
        flags.update({str(p).lower(): False for p in (act.get("deny") or [])})
        await ch.set_permissions(who, reason=why,
                                 **{k: v for k, v in flags.items() if k not in NEVER})
        return f"set permissions for {_name(who)} on #{ch.name}"
    if op == "make_private":
        ch = find_channel(guild, target)
        wanted = [w for w in (act.get("roles") or ([to] if to else [])) if w]
        await ch.set_permissions(guild.default_role, view_channel=False, reason=why)
        given = []
        me = getattr(guild, "me", None)
        if me is not None:
            await ch.set_permissions(me, view_channel=True, reason=why)
        for one in wanted:
            principal = _principal(guild, str(one))
            if principal is None:
                continue
            await ch.set_permissions(principal, view_channel=True, send_messages=True,
                                     connect=True, speak=True, reason=why)
            given.append(_name(principal))
        return (f"made #{ch.name} private" +
                (f", visible to {', '.join(given)}" if given else " — nobody has access yet"))
    if op == "make_public":
        ch = find_channel(guild, target)
        await ch.set_permissions(guild.default_role, overwrite=None, reason=why)
        return f"made #{ch.name} visible to everyone again"
    if op == "set_forum":
        ch = find_channel(guild, target)
        changes = {}
        if act.get("emoji"):
            changes["default_reaction_emoji"] = discord.PartialEmoji(name=str(act["emoji"]))
        if act.get("tags"):
            changes["available_tags"] = [_tag(str(t)) for t in act["tags"]][:20]
        if act.get("topic") is not None:
            changes["topic"] = str(act["topic"])
        if not changes:
            return f"nothing to change on #{ch.name}"
        await ch.edit(reason=why, **changes)
        return f"configured forum #{ch.name}: " + ", ".join(sorted(changes))
    if op == "forum_post":
        ch = find_channel(guild, target)
        wanted = {str(t).strip().lower() for t in (act.get("tags") or [])}
        picked = [t for t in (getattr(ch, "available_tags", None) or [])
                  if t.name.lower() in wanted]
        made = await ch.create_thread(name=str(to or target)[:100],
                                      content=str(value or "")[:1900],
                                      applied_tags=picked or discord.utils.MISSING)
        name = getattr(getattr(made, "thread", made), "name", to)
        return f"opened “{name}” in #{ch.name}"
    if op == "post_message":
        ch = find_channel(guild, target)
        await ch.send(str(value or ""))
        return f"posted in #{ch.name}"
    raise ValueError(f"unknown op {op}")
