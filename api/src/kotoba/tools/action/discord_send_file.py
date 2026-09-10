"""Put a file from her library into the Discord channel, as a real attachment."""
from __future__ import annotations

SCHEMA = {
    "type": "function",
    "name": "discord_send_file",
    "description": (
        "Attach a file from your workspace to the Discord channel you are in, so people can download "
        "it. Use it when somebody asks you to send, share, attach or upload something you made or "
        "have — \"send me that file\", \"attach it to the chat\". The path is relative to your "
        "workspace, exactly as `file_read` takes it. Say what you attached; do not paste the "
        "contents as well unless they asked for both."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description":
                     "Workspace-relative path, e.g. 'notes.txt' or 'reports/summary.md'."},
            "note": {"type": "string", "description":
                     "What you say when you hand it over — THIS is your message about the file, "
                     "sent with it. Say it here and do not say it again afterwards."},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
}

BUILT_IN = False
TOOLSET = "discord"
RISK = "write"

ANNOUNCE = "Let me attach that."
HEARTBEAT = ["Uploading..."]
COMPLETE = "Sent — it's attached above."
FAIL = "I couldn't attach that one. Tell me the name again and I'll look for it."

# Discord refuses more than this on an unboosted server, and its own error is not readable.
MAX_BYTES = 8_000_000


def check() -> bool:
    from kotoba.discord import state

    return state.runtime_live()


async def execute(args: dict, ctx) -> str:
    from pathlib import Path

    from kotoba.core.path_security import PathSecurityError, validate_within_dir
    from kotoba.discord import authority, state

    # Not being offered a tool is not the same as being refused it: dispatch resolves by name and
    # never reads the exclusion, so this is the door.
    who = state.actor()
    if who is None or not who.is_owner:
        return authority.refusal(who, "send you one of her files", need="only her person can ask for that")

    client = state.client()
    channel = client.get_channel(state.channel_id() or 0) if client else None
    if channel is None:
        return "I can only attach files from inside a Discord channel."

    raw = str(args.get("path") or "").strip()
    if not raw:
        return "Tell me which file to attach."

    root = ctx.workdir
    if root is None:
        from kotoba.core import workspace

        root = workspace.resolve_workdir(getattr(ctx, "session_id", None))
    try:
        target = validate_within_dir(raw, root)
    except PathSecurityError:
        return f"“{raw}” is outside my workspace, so it is not mine to hand out."

    path = Path(target)
    if not path.is_file():
        return f"I don't have a file called “{raw}”."
    size = path.stat().st_size
    if size > MAX_BYTES:
        return (f"“{path.name}” is {size // 1_000_000} MB, over what Discord accepts here. "
                "I can send a smaller part of it instead.")

    import discord

    try:
        await channel.send(content=str(args.get("note") or "") or None,
                           file=discord.File(str(path), filename=path.name))
    except Exception as exc:
        return f"Discord refused the upload ({type(exc).__name__}). The file is still here."
    # She used to say her line here AND again in the turn, so the person read the same sentence twice
    # two seconds apart. The note is already on screen; this result is for her, not for them.
    said = " Your note went with it." if str(args.get("note") or "").strip() else ""
    return (f"“{path.name}” ({size} bytes) is in the channel now.{said} They can see it already, so "
            "do not announce it again — if you have nothing to add, say nothing.")
