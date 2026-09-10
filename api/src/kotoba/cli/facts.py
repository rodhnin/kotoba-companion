"""What the header states, asked of the running process rather than read off a config file.

A header that claims a model this process is not on is worse than no header, so every number comes from
the object that will serve the turn: the tool registry, the runtime settings, the open database.
Counting her turns is a query, so this is async, and it reads `turns` — `sessions.turn_count` is dead.

Rank is what survives a narrow terminal, lowest first: KEYS is 1, the only advertisement of `@file`
and alt-enter outside `/help`; MEM is 6, decorative. The `local` clause names the machine and never a
sandbox, because it runs on the host with the gate as the only thing standing there. The WORK row
gives up a path's MIDDLE first: the grid clips the tail's file count.
"""
from __future__ import annotations


async def facts(db) -> tuple[list[tuple[str, str, int]], str]:
    from kotoba.cli.render import paths
    from kotoba.core import (app_settings, file_library, llm, providers, sandbox, skill_docs,
                             user_memory)
    from kotoba.tools.registry import registry

    workdir = file_library.library_dir()
    files = sum(1 for p in workdir.rglob("*") if p.is_file()) if workdir.is_dir() else 0
    runtime = app_settings.runtime_all()
    stats = [
        ("MODEL", _model(llm, providers), 2),
        ("WORK", f"{paths.shown(workdir)} · {_count(files, 'file')}", 3),
        ("VOICE", _voice(runtime), 5),
        ("TOOLS", f"{len(registry())} ready · {_count(len(skill_docs.list_skills()), 'skill')}", 4),
        ("MEM", f"{_count(len(user_memory.list_topics()), 'topic')} "
                f"· {_thousands(await db.count_turns())} turns", 6),
        ("KEYS", "/help · @file · alt-enter", 1),
    ]
    return stats, ("on this machine" if sandbox.backend_name() == "local" else "")


def _model(llm, providers) -> str:
    """The same rule the VOICE row follows: a model name says HOW she would think, never WHETHER she
    can. A first run that was declined leaves no key at all, and the header then named a model nothing
    on this machine can reach, printed under the line that had just said she has none. `get_client` is
    the question the loop itself asks and is already built by boot, so this costs nothing.

    A brain it cannot reach is not only a missing key: `provider` and `model` are two settings and only
    one moves at a time, so `/set provider xai` leaves the old model behind and this row stated a pair
    that 404s on every turn as if it were ready — `serves_model` answers that with no network. What it
    never does is CALL the provider: blocking the terminal on a round trip nobody asked for is worse
    than naming a model the provider will refuse, and whether a key works is what doctor is for."""
    spec = providers.get_spec()
    if llm.get_client() is None:
        return f"{spec.label} · no key yet"
    model = llm.model_name()
    if not providers.serves_model(model):
        return f"{spec.label} · wrong model"
    return f"{model} · {spec.label}"


def _voice(runtime: dict) -> str:
    """Both voice_mode values need an ElevenLabs key, so without one neither is true. The mode and the
    engine describe HOW she would speak; they cannot say WHETHER she can, and the row claiming
    `local · expressive` on a machine with no key contradicted the setup screen that had just told the
    person she has no voice. `resolve_api_key` is the same question `doctor` and the pipeline ask.

    The import is guarded because the voice package eagerly pulls in stt/tts, which need `websockets`,
    an extra `kotoba[cli]` does not bring. Bare, this one row took the whole terminal down with a
    ModuleNotFoundError at boot, on every machine that followed the documented install."""
    try:
        from kotoba.core.voice import config as voice_config
    except ImportError:
        return "text only · no voice extra"
    if not voice_config.resolve_api_key():
        return "text only · no key"
    if runtime["voice_mode"] == "agent":
        return "agent · ElevenLabs tunnel"
    return f"{runtime['voice_mode']} · {runtime['tts_engine']}"


def _count(n: int, word: str) -> str:
    """One place for every plural, so nothing ever renders `1 skills`."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _thousands(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)
