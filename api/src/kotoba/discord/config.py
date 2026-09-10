"""Where the Discord surface reads its settings. Pure stdlib, so the tool modules can import it.

The bot token is deliberately absent from `settings.yaml`: it belongs in the encrypted keystore, and
the env var is only the escape hatch for a first run that has no database yet.
"""
from __future__ import annotations

import os

TOKEN_KEY = "cred:discord_bot"
TOKEN_ENV = "DISCORD_BOT_TOKEN"

# A turn she answered this recently, from the same person, still counts as the same conversation.
ATTENTION_SECONDS = 90.0


def _ids(raw: str) -> frozenset[int]:
    out = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return frozenset(out)


def owner_id() -> int | None:
    """The one Discord account that is HER PERSON. Never inferred from anything anyone types."""
    raw = os.getenv("KOTOBA_DISCORD_OWNER_ID", "").strip()
    return int(raw) if raw.isdigit() else None


def allowed_guilds() -> frozenset[int]:
    """Empty means every guild she was invited to."""
    return _ids(os.getenv("KOTOBA_DISCORD_GUILDS", ""))


def home_channels() -> frozenset[int]:
    """Channels where she answers everything, not only when named."""
    return _ids(os.getenv("KOTOBA_DISCORD_HOME_CHANNELS", ""))


def token_from_env() -> str:
    return os.getenv(TOKEN_ENV, "").strip()
