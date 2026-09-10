"""`kotoba discord` — the bot, in its own process, over the same engine as everything else.

`tickers=False` is load-bearing rather than tidy: cron delivers into whatever event queues its own
process holds, so a second ticker would claim due jobs and announce a private reminder into whichever
channel happened to be listening. `refresh_oauth=False` for the neighbouring reason — two processes
rotating one token retire each other's credential.
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys

from kotoba.core import engine as core_engine
from kotoba.discord import config

log = logging.getLogger("kotoba.discord")

def _missing() -> str:
    """The command that installs the extra is not the same command for both kinds of install, and the
    one that is wrong does nothing visible: a person who installed the package gets told to point pip
    at a folder they never downloaded."""
    from kotoba import paths

    if paths._CLONE is None:
        return 'the Discord bot needs its extra:\n    pip install "kotoba-companion[discord]"'
    return 'the Discord bot needs its extra:\n    pip install -e "api/[discord]"'

_NO_TOKEN = (
    "I have no Discord bot token.\n"
    "  Save one:  kotoba discord --save-token\n"
    f"  Or export: {config.TOKEN_ENV}=…"
)


def _installed() -> bool:
    import importlib.util

    return all(importlib.util.find_spec(m) for m in ("discord", "nacl", "davey"))


async def _token(db) -> str:
    """The keystore first: the env var is the escape hatch for a first run with no database yet."""
    try:
        saved = await db.get_key(config.TOKEN_KEY)
    except Exception:
        saved = None
    return (saved or config.token_from_env() or "").strip()


async def save_token(raw: str) -> int:
    engine = await core_engine.start(tickers=False, refresh_oauth=False)
    try:
        await engine.db.save_key(config.TOKEN_KEY, raw.strip())
    finally:
        await core_engine.stop(engine)
    print("saved, encrypted at rest.")
    return 0


async def serve(guilds: frozenset[int]) -> int:
    from kotoba.discord.client import KotobaClient

    engine = await core_engine.start(tickers=False, refresh_oauth=False)
    bot = None
    try:
        token = await _token(engine.db)
        if not token:
            print(_NO_TOKEN, file=sys.stderr)
            return 1
        bot = KotobaClient(engine, guilds=guilds)
        await bot.client.start(token)
        return 0
    except Exception as exc:
        return _explain(exc)
    finally:
        if bot is not None:
            await bot.close()
        await core_engine.stop(engine)


# Three dot-separated base64url parts: the shape of a bot token, and of a JWT, which is equally
# something that must never be printed.
_SECRET = re.compile(r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{20,}")


def _redact(text: str) -> str:
    return _SECRET.sub("<token redacted>", text)


def _explain(exc: Exception) -> int:
    """A configuration mistake reaches the operator as a sentence, not as a traceback.

    Scrubbed on the way out: a transport error can quote the whole request it failed on, headers
    included, and this sentence is the kind somebody pastes into a chat to ask what it means."""
    import discord

    if isinstance(exc, discord.LoginFailure):
        print("Discord refused that token. Reset it in the Developer Portal and save the new one.",
              file=sys.stderr)
        return 1
    if isinstance(exc, discord.PrivilegedIntentsRequired):
        print("Discord refused the privileged intents.\n"
              "  Developer Portal -> Bot -> Privileged Gateway Intents\n"
              "  turn ON: Message Content, Server Members", file=sys.stderr)
        return 1
    import traceback

    log.error("discord bot stopped\n%s",
              _redact("".join(traceback.format_exception(exc))))
    print(f"The bot stopped: {_redact(str(exc))}", file=sys.stderr)
    return 1


def run(args) -> int:
    from kotoba.core import logs

    logs.also_to_console()
    if not _installed():
        print(_missing(), file=sys.stderr)
        return 1
    if getattr(args, "save_token", False):
        from kotoba.cli import secret

        # No terminal and nothing piped in is an ordinary way to run this — over ssh without a tty,
        # from a script, from an editor's shell. It used to end in a traceback, which reads as a
        # broken command rather than as a missing answer.
        try:
            raw = secret.read("Discord bot token: ")
        except EOFError:
            raw = config.token_from_env()
            if not raw:
                print("Nothing to read. Pipe the token in, or set DISCORD_BOT_TOKEN.",
                      file=sys.stderr)
                return 1
        if not raw or not raw.strip():
            print("nothing given.", file=sys.stderr)
            return 1
        return asyncio.run(save_token(raw))
    guilds = frozenset(getattr(args, "guild", None) or ()) or config.allowed_guilds()
    try:
        return asyncio.run(serve(guilds))
    except KeyboardInterrupt:
        return 130
