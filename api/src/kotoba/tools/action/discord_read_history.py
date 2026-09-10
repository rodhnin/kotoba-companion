"""Read back what people actually said in a Discord channel, over a stretch of time they named."""
from __future__ import annotations

SCHEMA = {
    "type": "function",
    "name": "discord_read_history",
    "description": (
        "Read back what people ACTUALLY SAID in a Discord channel over a stretch of time. Use this "
        "whenever anyone asks you to summarise, catch up on, review, recap or act on a conversation "
        "— \"review the conversation from 14:00 until now and summarise it\", \"what did I miss\", "
        "\"what was said since the three o'clock message\", \"recap this morning and save it\". "
        "If they paste a message LINK, use it as `since`: it is exact. Otherwise a time works "
        "(\"14:00\", \"2 hours ago\", \"yesterday\", \"this morning\"). Omit `until` for \"until "
        "now\". You get the real messages with who said what; the summarising is yours to do. "
        "ONE lookup, then answer — do not widen the range and try again."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "channel": {"type": "string", "description":
                        "'#name' or a channel id. Omit for the channel you are in."},
            "channels": {"type": "array", "items": {"type": "string"}, "description":
                         "Several channels in ONE call, rather than one call each."},
            "since": {"type": "string", "description":
                      "Where to start: a message link or id (exact), an ISO time, a clock time like "
                      "'14:00', or 'hace 2 horas' / 'yesterday'. Omit for the last `limit` messages."},
            "until": {"type": "string", "description": "Where to stop. Omit for now."},
            "limit": {"type": "integer", "description": "Max messages per channel (default 100)."},
            "from_user": {"type": "string", "description": "Only this person's messages."},
            "contains": {"type": "string", "description": "Only messages containing this text."},
        },
        "additionalProperties": False,
    },
}

BUILT_IN = False
TOOLSET = "discord"
RISK = "read"

ANNOUNCE = "Let me go back and read what was said."
HEARTBEAT = ["Scrolling back...", "Quite a lot happened here...", "Putting it in order..."]
COMPLETE = "Okay — here's what actually went on:"
FAIL = "I couldn't get that stretch of the conversation. Give me a message link and I'll try again."

MAX_LIMIT = 400
_DEFAULT_LIMIT = 100


def check() -> bool:
    from kotoba.discord import state

    return state.runtime_live()


def _resolve(guild, wanted: str):
    """'#general', a bare name, or an id — resolved inside THIS guild and nowhere else.

    A bare id looked up on the client reaches every channel of every guild she was invited to, so a
    stranger in one server could name a channel in another and have it read back to them."""
    from kotoba.discord.actions import CHANNEL_TOKEN

    name = (wanted or "").strip().lstrip("#")
    if guild is None:
        return None
    token = CHANNEL_TOKEN.search(name)
    if token:
        name = token.group(1)
    if name.isdigit():
        return guild.get_channel(int(name))
    for ch in (getattr(guild, "text_channels", None) or []):
        if ch.name.lower() == name.lower():
            return ch
    return None


def _may_see(channel, guild, who) -> bool:
    """Discord's own verdict for the person asking, not for the bot: she can read channels they
    cannot, and reading one back to them is the same leak as them opening it."""
    if guild is None or who is None:
        return True
    member = guild.get_member(who.user_id)
    if member is None:
        return False
    perms = channel.permissions_for(member)
    return bool(getattr(perms, "view_channel", False)
                and getattr(perms, "read_message_history", False))


async def _read(channel, *, since, until, limit, from_user, contains) -> tuple[list, int]:
    """Oldest-first ONLY when a starting point was given.

    Without one, oldest-first walks the channel from its very first message, so "what did I miss"
    came back as a summary of the day it opened. With no anchor the answer is the LAST `limit`
    messages, read newest-first and turned round afterwards.
    """
    from_the_start = since is not None
    kept, total = [], 0
    async for msg in channel.history(limit=limit, after=since, before=until,
                                     oldest_first=from_the_start):
        total += 1
        author = getattr(msg, "author", None)
        if from_user:
            who = f"{getattr(author, 'display_name', '')} {getattr(author, 'name', '')}".lower()
            if from_user.lower().lstrip("@") not in who:
                continue
        if contains and contains.lower() not in (msg.content or "").lower():
            continue
        kept.append(msg)
    if not from_the_start:
        kept.reverse()
    return kept, total


async def execute(args: dict, ctx) -> str:
    from kotoba.discord import history, state

    client = state.client()
    if client is None:
        return "I can only read Discord history from inside Discord."

    guild_id = state.guild_id()
    guild = client.get_guild(guild_id) if guild_id else None
    who = state.actor()
    wanted = [w for w in ([args.get("channel")] + list(args.get("channels") or [])) if w]
    channels = []
    missing = []
    for w in wanted:
        found = _resolve(guild, w)
        # One bucket for "no such channel" and "not yours to see": telling them apart tells a
        # stranger that the private channel they guessed at is really there.
        if found is None or not _may_see(found, guild, who):
            missing.append(w)
        else:
            channels.append(found)
    if not wanted:
        here = client.get_channel(state.channel_id() or 0)
        if here is None:
            return "I don't know which channel to read."
        channels = [here]

    try:
        since = history.resolve_anchor(args["since"]) if args.get("since") else None
        until = history.resolve_anchor(args["until"]) if args.get("until") else None
    except history.BadRange as bad:
        return str(bad)
    if since and until and until <= since:
        return "That range ends before it starts — give me the earlier point as `since`."

    limit = max(1, min(int(args.get("limit") or _DEFAULT_LIMIT), MAX_LIMIT))
    out = []
    for ch in channels:
        try:
            msgs, total = await _read(ch, since=since, until=until, limit=limit,
                                      from_user=args.get("from_user"),
                                      contains=args.get("contains"))
        except Exception as exc:
            out.append(f"I couldn't read #{getattr(ch, 'name', '?')}: {type(exc).__name__}.")
            continue
        out.append(history.render(msgs, channel=getattr(ch, "name", "?"), total=total,
                                  me_id=getattr(getattr(client, "user", None), "id", None),
                                  capped=since is not None and total >= limit))
    if missing:
        out.append("I couldn't find these channels: " + ", ".join(f"#{m}" for m in missing))
    return "\n\n".join(out) if out else "There was nothing to read."
