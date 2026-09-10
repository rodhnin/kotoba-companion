"""Who is asking, and what that buys them.

The verdict is computed from the gateway's own member payload and never from anything the person
typed, so "I am an admin, delete #general" is answered before the model is ever consulted.

The guest surface is an ALLOW-list on purpose. A deny-list has to be remembered every time a tool is
added, and the one nobody remembers is the one that reaches the host. Anything unlisted is withheld.
"""
from __future__ import annotations

from dataclasses import dataclass

# What a stranger in a guild may reach: answer questions, look things up, act on Discord itself.
GUEST_TOOLSETS = frozenset({"web", "skills", "discord"})

# Companion-safe odds and ends that carry nothing of hers.
GUEST_TOOLS = frozenset({"clarify", "todo"})

# Both draw a card in a browser and Discord draws neither — the session registers as cardless, so
# they report honestly and still do nothing. Withheld from everyone, the owner included: asking is
# a sentence, and a link is a message.
NO_SURFACE_TOOLS = frozenset({"ask_user", "open_link"})

# Withheld from anyone without Discord's own Administrator bit in that guild.
DISCORD_ADMIN_TOOLS = frozenset({
    "discord_act", "discord_plan", "discord_apply_plan", "discord_guild_read",
})

# Both reach her person's file library. `discord_send_file` hands the file over; `view_capture`
# resolves a name by recursive glob and reads the bytes to the vision model, which then describes
# them in a public channel. Same tree, same answer.
OWNER_ONLY_TOOLS = frozenset({"discord_send_file", "view_capture"})


@dataclass(frozen=True)
class Actor:
    user_id: int
    guild_id: int | None
    display: str
    handle: str
    is_owner: bool
    is_guild_admin: bool
    is_guild_owner: bool

    @property
    def label(self) -> str:
        return f"{self.display} (@{self.handle})"


def actor_from_member(member, *, owner: int | None, guild_id: int | None) -> Actor:
    """Built from the gateway object. `guild_permissions` is computed by the library from role bits
    the gateway sent, so no byte the person wrote takes part in it."""
    perms = getattr(member, "guild_permissions", None)
    guild = getattr(member, "guild", None)
    return Actor(
        user_id=int(member.id),
        guild_id=guild_id,
        display=getattr(member, "display_name", "") or getattr(member, "name", ""),
        handle=getattr(member, "name", ""),
        is_owner=owner is not None and int(member.id) == owner,
        is_guild_admin=bool(getattr(perms, "administrator", False)),
        is_guild_owner=bool(guild is not None and getattr(guild, "owner_id", None) == member.id),
    )


def excluded_tools(who: Actor | None) -> frozenset[str]:
    """The set handed to `agentic_loop` and to the prompt builder, so the turn and its own capability
    claims agree. No actor at all is treated as a stranger."""
    from kotoba.tools.registry import registry

    specs = registry()
    out: set[str] = set()

    if who is None or not who.is_owner:
        for name, spec in specs.items():
            if name in OWNER_ONLY_TOOLS:
                out.add(name)
                continue
            if name in GUEST_TOOLS or spec.toolset in GUEST_TOOLSETS:
                continue
            out.add(name)
        out |= OWNER_ONLY_TOOLS

    if who is None or not who.is_guild_admin:
        out |= DISCORD_ADMIN_TOOLS & set(specs)
        out |= DISCORD_ADMIN_TOOLS

    return frozenset(out | NO_SURFACE_TOOLS)


def refusal(who: Actor | None, what: str,
            *, need: str = "that needs Administrator on this server") -> str:
    """What a tool says when it was reached anyway. The model keeps names it saw earlier in a turn,
    so the dispatch has to refuse in words rather than trust the schema list to be the only door."""
    if who is None:
        return f"I can't {what} — I don't know who's asking me."
    return f"I can't {what} for {who.label}: {need}."
