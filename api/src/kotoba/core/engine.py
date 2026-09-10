"""Process startup and teardown, shared by every entry point.

All of this used to live inside FastAPI's `lifespan`, so a second entry point either copied it or
silently ran without it. The two that hurt most when skipped: the LLM key preload — a key saved from
the Settings panel lives encrypted in the database, never in the environment, so `get_client()` returns
None and she tells a configured user she has no key — and `apply_on_startup`, which re-applies the tool
families the user switched off, so a fresh process offers exactly the tools the app hides."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

from kotoba.core.voice_patterns import build_soul_patterns
from kotoba.db.database import Database
from kotoba.soul.loader import sync_from_file

log = logging.getLogger("kotoba.engine")

_REAP_SANDBOXES_SECS = 120.0
_SANDBOX_IDLE = 900.0       # release a session's sandbox after this long untouched
_SCRATCH_AGE = 24.0         # hours before a scratch file is collectable


@dataclass
class Engine:
    db: Database
    soul_patterns: dict
    mcp: object | None
    tasks: list[asyncio.Task] = field(default_factory=list)


async def start(*, tickers: bool, refresh_oauth: bool | None = None) -> Engine:
    """Bring the process up and return as soon as it can serve a turn.

    Saved MCP servers connect in the background: N unreachable ones would otherwise hold this for
    minutes. `tickers` starts the cron loop; `refresh_oauth` defaults to it and is the half a second
    process must NOT take, because two of them rotating one token retires the other's credential.

    Any failure past `connect()` tears down whatever was already up, via stop(). aiosqlite's worker
    thread is not a daemon, so a raise that leaves the connection open — a missing soul file was the
    live case — does not exit the process: it prints its traceback and then hangs forever."""
    db = Database(os.getenv("DATABASE_URL", "sqlite:///./kotoba.db"))
    await db.connect()
    engine = Engine(db=db, soul_patterns={}, mcp=None)
    try:
        soul = await sync_from_file(db, os.getenv("SOUL_PATH", "./soul/default.md"))
        engine.soul_patterns = build_soul_patterns(dict(soul))

        await _preload_llm_keys(db)
        await _preload_voice_key(db)
        await _migrate_memory(db)
        engine.mcp = await _start_mcp(db, engine.tasks)
        _apply_saved_settings()

        engine.tasks.append(asyncio.create_task(_sandbox_reaper()))
        if tickers:
            engine.tasks.extend(await _start_tickers(
                db, engine.mcp, refresh_oauth=tickers if refresh_oauth is None else refresh_oauth))
        return engine
    except BaseException:
        await stop(engine)
        raise


async def stop(engine: Engine) -> None:
    """Mirror of start(), in reverse. Idempotent: the CLI calls it from a signal handler and again from
    its own finally, and a half-torn-down engine must not raise on the second pass."""
    for task in engine.tasks:
        task.cancel()
    for task in engine.tasks:
        try:
            await task
        except BaseException:
            pass
    engine.tasks.clear()
    if engine.mcp is not None:
        try:
            await engine.mcp.aclose()   # owner-task exit; has its own cap
        except Exception:
            log.warning("MCP did not close cleanly", exc_info=True)
        engine.mcp = None
    await engine.db.close()


VOICE_KEY_NAME = "voice:elevenlabs:api_key"   # namespaced away from `cred:`, which tools can read


async def _preload_llm_keys(db: Database) -> None:
    from kotoba.core import llm, providers

    try:
        for provider_id in providers.PROVIDERS:
            key = await db.get_key(f"llm:{provider_id}:api_key")
            if key:
                llm.set_provider_key(provider_id, key)
    except Exception:
        log.warning("LLM key preload failed — she will report having no key", exc_info=True)


async def _preload_voice_key(db: Database) -> None:
    """The ElevenLabs key first run saved, decrypted into the module every voice call reads.

    Without this the key is written and never read: `voice.config.resolve_api_key` consults an
    in-memory override and then the environment, and nothing else, so a key saved in the app would
    have configured nothing. Guarded on the import because `core.voice.__init__` pulls in stt/tts,
    which need the `voice` extra — an install without it must not fail to start over a key it has no
    way to use."""
    try:
        from kotoba.core.voice import config as voice_config
    except ImportError:
        return
    try:
        key = await db.get_key(VOICE_KEY_NAME)
        if key:
            voice_config.set_api_key(key)
    except Exception:
        log.warning("ElevenLabs key preload failed — she will report having no voice", exc_info=True)


async def _migrate_memory(db: Database) -> None:
    from kotoba.core import user_memory

    try:
        existing = await db.fetch_active_memory_facts()
        await asyncio.to_thread(user_memory.migrate_from_db_facts, existing)
    except Exception:
        log.debug("memory migration skipped", exc_info=True)


async def hydrated_servers(db: Database) -> tuple[dict[str, dict], dict[str, dict]]:
    """The saved MCP servers twice: as stored, and with their tokens injected ready to connect. Kept
    apart because a failed connect is recorded against the stored copy, which holds no credential."""
    from kotoba.core.mcp.config import hydrate_auth, load_servers

    saved = load_servers()
    tokens = {c["auth_key"]: await db.get_key(c["auth_key"])
              for c in saved.values() if isinstance(c, dict) and c.get("auth_key")}
    return saved, {name: hydrate_auth(cfg, tokens.get) for name, cfg in saved.items()}


async def _start_mcp(db: Database, tasks: list[asyncio.Task]):
    from kotoba.core.mcp.client import MCPManager

    manager = MCPManager()
    try:
        await manager.start()   # owner task only — fast, no network
    except Exception:
        log.warning("MCP manager failed to start — MCP tools will be unavailable", exc_info=True)
        return manager

    saved, hydrated = await hydrated_servers(db)
    tasks.append(asyncio.create_task(_connect_saved(manager, saved, hydrated)))
    return manager


async def _connect_saved(manager, saved: dict, hydrated: dict) -> None:
    from kotoba.core.mcp import pending
    from kotoba.core.mcp.client import _MCP_AVAILABLE, _AuthRequired
    from kotoba.core.mcp.known import build_cfg, resolve_known

    # Without the SDK every connect answers with an empty list, and each saved server would read as one
    # that offered no tools.
    if not _MCP_AVAILABLE:
        return

    for name, cfg in hydrated.items():
        original = saved.get(name) or {}
        # Re-resolve a known server from its recipe: a frozen browser CDP address in the saved config
        # would otherwise never pick up a change to the env.
        if not original.get("auth_key") and not original.get("headers") and resolve_known(name):
            fresh = build_cfg(name)
            if fresh is not None:
                cfg = fresh[1]
        try:
            tools = await manager.connect(name, cfg)
        except _AuthRequired as need:
            clean = {k: v for k, v in original.items()
                     if k not in ("auth_key", "auth_env", "headers", "env")}
            try:
                pending.record(name, clean, need.reason, need.kind, "")
            except Exception:
                log.warning("could not record %r as pending", name, exc_info=True)
        except Exception:
            log.warning("MCP %r failed to connect on boot — skipping", name)
        else:
            # The paths a PERSON drives refuse a server that offers nothing; boot kept it, holding a
            # session for the life of the process. The entry stays on disk on purpose: an empty tool
            # list can be a server having a bad day, and deleting a config over one is worse.
            if not tools:
                log.warning("MCP %r connected but offered no tools — dropping the connection", name)
                try:
                    await manager.disconnect(name)
                except Exception:
                    log.debug("MCP %r would not disconnect after an empty connect", name, exc_info=True)


def _apply_saved_settings() -> None:
    from kotoba.core.app_settings import apply_on_startup

    try:
        apply_on_startup()
    except Exception:
        log.warning("could not apply saved toolset toggles", exc_info=True)


async def _start_tickers(db: Database, mcp, *, refresh_oauth: bool) -> list[asyncio.Task]:
    """Cron may run in more than one process — `cron._tick` refuses to claim a job it cannot deliver, so
    the ticker follows the user rather than racing for them. The OAuth refresh may not: two processes
    rotating the same token means one of them holds a credential the provider has already retired."""
    from kotoba.core.cron import cron_loop
    from kotoba.core.mcp import oauth

    try:
        purged = await db.purge_finished_cronjobs(30)
        if purged:
            log.info("cron: purged %d finished job(s)", purged)
    except Exception:
        log.warning("cron purge failed", exc_info=True)
    tasks = [asyncio.create_task(cron_loop(db))]
    if refresh_oauth:
        tasks.append(asyncio.create_task(oauth.refresh_loop(db, mcp)))
    return tasks


async def _sandbox_reaper() -> None:
    from kotoba.core import session_sandbox
    from kotoba.core.workspace import clean_scratch

    while True:
        await asyncio.sleep(_REAP_SANDBOXES_SECS)
        try:
            await session_sandbox.keepalive_active()
            await session_sandbox.reap_idle(_SANDBOX_IDLE)
            await asyncio.to_thread(clean_scratch, _SCRATCH_AGE)
        except Exception:
            log.debug("sandbox sweep failed", exc_info=True)
