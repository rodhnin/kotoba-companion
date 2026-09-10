"""The ElevenLabs key: whether it is real, and where it has to land to mean anything.

Both halves are asked in two places — `kotoba setup` in the terminal and the web's first-run screen —
and both are subtle enough that a second copy would be a second thing to get wrong, so the terminal
wizard and the web route call this one.

It sits beside `core/voice/` rather than inside it on purpose: `core.voice.__init__` imports stt/tts,
which need the `voice` extra, and an install without it must still be able to STORE a key it cannot yet
use. The one import of `core.voice.config` is local and guarded, exactly as `core.engine` guards its own.
"""
from __future__ import annotations

import logging

log = logging.getLogger("kotoba.voice_key")

VOICE_KEY_URL = "https://elevenlabs.io/app/settings/api-keys"


async def verify(key: str) -> tuple[bool, str]:
    """Is this an ElevenLabs key? Asked of `GET /v1/voices`, and that endpoint is not an accident.

    `/v1/user` is what every guide reaches for and is WRONG here: keys carry scopes, and a key that
    speaks perfectly answers it `401 missing the permission user_read`. A check that calls a good key
    bad is worse than none. `/v1/voices` is a metadata list — no synthesis, no money, back in 0.2 s.

    Only two answers throw a key away: ElevenLabs itself saying `invalid_api_key`, or a 429 against
    this key. Everything else keeps it, and that asymmetry used to leak — a 302 and a 404 both fell
    past every branch to `return False`, so a captive portal and a corporate proxy destroyed a good key
    on the way in. Returns (keep it, a sentence a person can act on)."""
    import httpx

    key = clean(key)
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key})
    except Exception:
        log.debug("first-run voice key check could not reach ElevenLabs", exc_info=True)
        return True, ("I couldn't reach ElevenLabs to check it, so I've kept it. If she can't speak "
                      "later, that's the first thing to suspect.")
    if r.status_code == 200:
        return True, ""
    if r.status_code == 401 and says(r, "invalid_api_key"):
        return False, ("ElevenLabs doesn't recognise that key. A stray space often comes along with a "
                       f"paste — otherwise make a fresh one at {VOICE_KEY_URL}")
    if r.status_code in (401, 403):
        return True, "that key is real — narrow permissions, but I'll take it."
    if r.status_code == 400 and says(r, "invalid_api_key_prefix"):
        return False, ("ElevenLabs says one of its keys starts with `sk_`, and that one does not — it "
                       f"looks like a key for something else. Theirs are made at {VOICE_KEY_URL}")
    if r.status_code == 429:
        return False, "ElevenLabs is rate-limiting that key right now. Give it a moment and try again."
    if r.status_code >= 500:
        return True, ("ElevenLabs is having trouble at their end, so I couldn't check it — I've kept it "
                      "anyway.")
    return True, ("Something answered instead of ElevenLabs, so I couldn't check that key — a sign-in "
                  "page on this network, or a proxy, does that. I've kept it. The whole answer is in "
                  f"the log: {log_path()}")


def clean(key: str) -> str:
    """A pasted key brings the whitespace around it, and httpx will not put that in a header at all:
    the request never leaves, the failure looks like the network, and the advice a person then gets
    sends them to check their connection instead of the end of the line they pasted."""
    return key.strip()


def says(response, code: str) -> bool:
    """ElevenLabs puts its own reason under `detail.status`. Read defensively: this runs on the path
    where something already went wrong, and a second failure here would hide the first."""
    try:
        detail = response.json().get("detail")
    except Exception:
        return False
    if isinstance(detail, dict):
        return code in (str(detail.get("status") or ""), str(detail.get("code") or ""))
    return False


async def save(db, key: str) -> None:
    """Stored where the Settings panel reads it, and live in this process at the same moment: the
    resolver consults an in-memory override and then the environment, so a row nobody loaded is a key
    that configured nothing (`core.engine._preload_voice_key` does the loading on every later start)."""
    from kotoba.core.engine import VOICE_KEY_NAME

    key = clean(key)
    await db.save_key(VOICE_KEY_NAME, key)
    try:
        from kotoba.core.voice import config as voice_config

        voice_config.set_api_key(key)
    except ImportError:
        pass


VOICE_KEY_ENV = "ELEVENLABS_API_KEY"


def from_environment() -> str:
    """The variable's NAME when it holds a usable key, else empty. A placeholder is not a key."""
    import os

    from kotoba.core.llm import looks_placeholder

    raw = os.getenv(VOICE_KEY_ENV, "") or ""
    return "" if looks_placeholder(raw) else (VOICE_KEY_ENV if raw.strip() else "")


async def where(db) -> str:
    """"app", "env" or "" — a different question from whether she has a voice at all, and the one both
    doors have to answer before they describe the key.

    Asked through the voice package alone this said no on an install without that extra, over a key the
    same install had just stored; and one boolean could not say WHERE, so the terminal told somebody
    who had typed their key to go looking for a variable that does not exist."""
    from kotoba.core.engine import VOICE_KEY_NAME

    try:
        saved = (await db.get_key(VOICE_KEY_NAME)) or ""
    except Exception:
        saved = ""
    if saved.strip():
        return "app"
    return "env" if from_environment() else ""


def configured() -> bool:
    """Whether she already has a voice, for a caller with no database to ask."""
    if from_environment():
        return True
    try:
        from kotoba.core.voice import config as voice_config
    except ImportError:
        return False
    return bool(voice_config.resolve_api_key())


def log_path() -> str:
    """Where the whole ElevenLabs answer went, for the one refusal this module cannot put simply."""
    from kotoba.core import logs

    try:
        return str(logs.path())
    except Exception:
        return "~/.kotoba/cli.log"
