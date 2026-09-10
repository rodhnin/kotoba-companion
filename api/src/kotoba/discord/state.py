"""What a Discord tool is allowed to know about the turn it is running inside.

ContextVars rather than tool arguments, because who is asking must not be something the model can
write. Anything it can put in a JSON payload is something a stranger can talk it into putting there.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

_ACTOR: ContextVar[Any] = ContextVar("kotoba_discord_actor", default=None)
_CLIENT: ContextVar[Any] = ContextVar("kotoba_discord_client", default=None)
# Two different objects with confusable names: `client` is the library's, `surface` is ours. A tool
# reaching for one of our methods on theirs gets an immediate AttributeError and reads, to
# the person, as "the connection failed".
_SURFACE: ContextVar[Any] = ContextVar("kotoba_discord_surface", default=None)
_GUILD: ContextVar[int | None] = ContextVar("kotoba_discord_guild", default=None)
_CHANNEL: ContextVar[int | None] = ContextVar("kotoba_discord_channel", default=None)

_LIVE = False


def set_runtime_live(live: bool) -> None:
    global _LIVE
    _LIVE = live


def runtime_live() -> bool:
    """Every Discord tool's `check()`. False everywhere but the bot process, which is what lets the
    family sit in the companion toolset without a restructure ever landing inside a voice turn."""
    return _LIVE


def actor() -> Any:
    return _ACTOR.get()


def client() -> Any:
    """The library's client: get_guild, get_channel, user."""
    return _CLIENT.get()


def surface() -> Any:
    """Ours: the voice rooms and the per-channel sessions."""
    return _SURFACE.get()


def guild_id() -> int | None:
    return _GUILD.get()


def channel_id() -> int | None:
    return _CHANNEL.get()


@contextmanager
def turn(*, who: Any, bot: Any, guild: int | None, channel: int | None,
         surface: Any = None) -> Iterator[None]:
    tokens = (_ACTOR.set(who), _CLIENT.set(bot), _GUILD.set(guild), _CHANNEL.set(channel),
              _SURFACE.set(surface))
    try:
        yield
    finally:
        for var, tok in zip((_ACTOR, _CLIENT, _GUILD, _CHANNEL, _SURFACE), tokens):
            var.reset(tok)
