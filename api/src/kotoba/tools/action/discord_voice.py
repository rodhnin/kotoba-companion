"""Join or leave a Discord voice channel."""
from __future__ import annotations

SCHEMA = {
    "type": "function",
    "name": "discord_voice",
    "description": (
        "Join a Discord voice channel so you can hear people and talk back, or leave one. Use it "
        "whenever somebody asks you INTO voice or asks you OUT of it, in whatever language they "
        "happen to say it: being asked to leave is this tool exactly as much as being asked to "
        "join, and saying you will go while staying in the channel is the failure people notice. "
        "With no channel named you go to the one the person asking is already in. "
        "Once you are in, you only answer when somebody says your NAME first; the rest of the "
        "conversation is theirs. Say that when you arrive, so nobody waits for an answer that is "
        "not coming. Call it EVERY time somebody asks you in OR out, even if you joined or left "
        "earlier in this conversation: you may have been restarted since, and you cannot know "
        "whether you are in voice without calling. Never answer that you are already in, or "
        "already out, without calling it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["join", "leave"]},
            "channel": {"type": "string", "description":
                        "Voice channel name or id. Omit to join whichever one they are in."},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}

BUILT_IN = False
TOOLSET = "discord"
RISK = "write"

# Connecting negotiates a voice session and an MLS handshake; the default budget cuts that short.
TIMEOUT = 90

ANNOUNCE = "On my way."
HEARTBEAT = ["Connecting..."]
COMPLETE = "I'm in."
FAIL = "I couldn't get into voice."


def check() -> bool:
    from kotoba.discord import state

    return state.runtime_live()


async def execute(args: dict, ctx) -> str:
    from kotoba.discord import state

    client = state.client()
    surface = state.surface()
    who = state.actor()
    gid = state.guild_id()
    guild = client.get_guild(gid) if client and gid else None
    if guild is None:
        return "I can only join voice from inside a server."
    if surface is None:
        return "I can only join voice from inside the Discord bot."

    if str(args.get("action")) == "leave":
        left = await surface.leave_voice(guild.id)
        # Written as a sentence of hers, this came back out verbatim: she said "me salgo", called
        # this, and then said "ya me salí". The channel shows her leaving; the result is for her.
        return ("You are out of the voice channel. They watched you go, so do not announce it a "
                "second time — say nothing more unless you have something else to say."
                if left else "You were not in one, so nothing happened. Say so plainly.")

    wanted = str(args.get("channel") or "").strip().lstrip("#")
    target = None
    if wanted:
        target = next((c for c in guild.voice_channels
                       if c.name.lower() == wanted.lower() or str(c.id) == wanted), None)
        if target is None:
            return f"There's no voice channel called “{wanted}” here."
    else:
        member = guild.get_member(who.user_id) if who else None
        target = getattr(getattr(member, "voice", None), "channel", None)
        if target is None:
            return "You're not in a voice channel, so tell me which one to join."

    try:
        await surface.join_voice(target)
    except Exception as exc:
        from kotoba.discord.voice_recv import Unsupported

        if isinstance(exc, Unsupported):
            return f"I can't listen in there: {exc}"
        return f"I couldn't join {target.name} ({type(exc).__name__})."
    return (f"You are in {target.name} now, and they can see you arrive. Tell them ONCE, in your own "
            "words, that you answer there only when somebody says your name first — every time, from "
            "anybody — because in there nothing else reaches you. Do not also announce that you "
            "joined: they watched it happen.")
