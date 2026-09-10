"""Async SQLite access layer (aiosqlite). One connection per PROCESS, not per app.

Every entry point builds it through core.engine.start() and keeps it on Engine.db; the server also
publishes that same object as app.state.db. One shared connection is enough inside a process because
SQLite serializes writes anyway — revisit with a pool only if write contention shows up under load.

What is NOT internal is the second process. The CLI runs beside the server on the same file, which is
why connect() sets busy_timeout before anything that can take a lock, why _enable_wal retries, and why
the cron claim below is a compare-and-swap rather than a read-then-write.
"""
from __future__ import annotations

import json
import logging
import threading
import re
import weakref
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from kotoba.core import perms
from kotoba.db import migrations, queries
from kotoba.paths import db_dir


#: The scheme, with the driver SQLAlchemy may qualify it with, and the third slash that makes what
#: follows RELATIVE — a fourth one belongs to the path and stays.
_SQLITE_URL = re.compile(r"^sqlite(?:\+[a-z0-9_]+)?:///?", re.I)


def _path_from_url(database_url: str) -> str:
    """Turn DATABASE_URL (sqlite:///./kotoba.db) into a filesystem path for aiosqlite.

    A RELATIVE path resolves against her HOME, never the current directory. Launched from anywhere else
    the shipped `./kotoba.db` opened — and created — a different, empty one: no memory, no saved keys,
    no history, and nothing on screen to say why. The CLI starts in the user's own project directory,
    so it has to land on the same file the server uses.

    A driver-qualified URL is the same URL. Stripping only the bare `sqlite:///` left
    `sqlite+aiosqlite:///…` to be read as a relative path, so the scheme became a directory name and
    the same empty database arrived through the spelling nobody stripped."""
    m = _SQLITE_URL.match(database_url)
    raw = database_url[m.end():] if m else database_url
    if m and not raw:
        return ":memory:"
    if raw == ":memory:" or raw.startswith("file:"):
        return raw
    p = Path(raw).expanduser()
    return str(p if p.is_absolute() else (db_dir() / p).resolve())


_OPEN: "weakref.WeakSet[Database]" = weakref.WeakSet()


def _close_open_databases() -> None:
    """Release every still-open database while the interpreter is shutting down.

    aiosqlite runs each statement on a worker thread that is NOT a daemon, and CPython joins non-daemon
    threads BEFORE atexit. So a Database that outlives its loop with no close() parks that worker forever
    and the process can never exit: no traceback, no timeout, and to a parent reading through a pipe no
    output at all, since the pipe never reaches EOF. Nothing in the app meets this; a script does.

    Connection.stop() rather than close(): there is no event loop left to await on. It queues the real
    sqlite close onto the worker and lets it break out of its loop, so the thread ends and WAL is
    checkpointed. The worker deliberately stays non-daemon — daemonising would blind the leak test."""
    for db in list(_OPEN):
        conn, db.conn = db.conn, None
        if conn is None:
            continue
        try:
            conn.stop()
        except Exception:
            logging.getLogger("kotoba").debug("could not stop a database worker at exit", exc_info=True)


# _register_atexit is the only hook that runs before the join; atexit itself is already too late.
_register_before_thread_join = getattr(threading, "_register_atexit", None)
if _register_before_thread_join is not None:
    try:
        _register_before_thread_join(_close_open_databases)
    except RuntimeError:
        pass


class Database:
    def __init__(self, database_url: str) -> None:
        self.path = _path_from_url(database_url)
        self.conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """A raise after aiosqlite.connect (WAL conversion, a failed migration) must close the
        connection on the way out: its worker thread is not a daemon, and an open one keeps the whole
        process alive after the traceback.

        busy_timeout is set FIRST: the WAL pragma is the only statement here that can fail — converting
        a fresh rollback-journal database to WAL takes an exclusive lock and returns SQLITE_BUSY rather
        than waiting, so two processes booting the same fresh install (CLI beside server) crashed one.

        A caller that never closes is caught by _close_open_databases above, not here: forgetting is
        what a script does, and the cost of it used to be a process that could not exit."""
        self._prepare_file()
        self.conn = await aiosqlite.connect(self.path)
        _OPEN.add(self)
        try:
            self.conn.row_factory = aiosqlite.Row
            await self.conn.execute("PRAGMA busy_timeout=5000")
            await self._enable_wal()
            self._restrict_sidecars()
            await self.conn.execute("PRAGMA foreign_keys = ON")
            await migrations.run_migrations(self.conn)
            self._restrict_sidecars()
        except BaseException:
            await self.close()
            raise

    def _prepare_file(self) -> None:
        """Make the directory, then keep the database to this user.

        sqlite creates a new file at 0644 minus umask, so every turn she has ever been told sat readable by
        any local account while the keystore and settings.yaml, which hold less, were 0600. Applied on EVERY
        connect so an existing install is repaired too. The mkdir is the other half: sqlite will not create
        a missing parent, so a wheel install pointed at ~/.kotoba died with `unable to open database file`.

        Two things this does that "repair" undersells: it runs on EVERY connect, so a 0644 someone set
        deliberately goes back to 0600 each boot, and it does not refuse a symlink, so a path through one
        chmods the target. Both only ever move toward the more restrictive mode. Best-effort throughout."""
        p = Path(self.path)
        if p.name == ":memory:" or str(self.path).startswith("file:") or p.is_dir():
            return
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        try:
            p.touch(mode=0o600, exist_ok=True)
            p.chmod(0o600)
        except OSError:
            pass
        perms.restrict_file(p)      # the mode above is one read-only bit on Windows, and no ACL

    def _restrict_sidecars(self) -> None:
        """WAL puts recent turns in `-wal` and `-shm`, beside the database and outside its mode.

        On POSIX sqlite copies the database file's mode onto both, so the 0600 above already covers
        them. Windows has no mode to copy: the two are created with whatever the DIRECTORY hands down,
        which under a clone on `C:\\` is everybody. Asked twice because on a FRESH install the migrations
        are what create the files: before them there was nothing there to restrict, and the first
        process of a new install ran its whole life with them open."""
        p = Path(self.path)
        if p.name == ":memory:" or str(self.path).startswith("file:"):
            return
        for sidecar in (f"{p}-wal", f"{p}-shm", f"{p}-journal"):
            if Path(sidecar).exists():
                perms.restrict_file(sidecar)

    async def _enable_wal(self) -> None:
        """Retry the one-time journal conversion; it is contended only on a fresh file."""
        import asyncio
        import sqlite3

        for attempt in range(10):
            try:
                await self.conn.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError:
                if attempt == 9:
                    raise
                await asyncio.sleep(0.05 * (attempt + 1))

    async def close(self) -> None:
        _OPEN.discard(self)
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    async def fetch_soul_config(self) -> dict[str, Any]:
        async with self.conn.execute(queries.SOUL_CONFIG_GET) as cur:
            row = await cur.fetchone()
        return dict(row) if row else {}

    async def seed_soul_config(self, soul: dict[str, Any]) -> None:
        await self.conn.execute(queries.SOUL_CONFIG_SEED, soul)
        await self.conn.commit()

    async def sync_soul_config_fields(self, soul: dict[str, Any]) -> None:
        """Refresh developer-authored personality fields from the file; leave what the user owns
        (name, language, voice_id, avatar_model) alone. The last two are only FILLED when the row has
        no answer, so the file still seeds a fresh install without overruling a later choice."""
        keys = ("personality", "address_style", "emotional_rules", "tool_patterns", "quirks")
        await self.conn.execute(queries.SOUL_CONFIG_SYNC, {k: soul.get(k) for k in keys})
        if soul.get("voice_id"):
            await self.conn.execute(queries.SOUL_CONFIG_FILL_VOICE, {"voice_id": soul["voice_id"]})
        if soul.get("avatar_model"):
            await self.conn.execute(queries.SOUL_CONFIG_FILL_AVATAR, {"avatar_model": soul["avatar_model"]})
        await self.conn.commit()

    async def update_soul_config(self, **fields: str) -> None:
        for col, value in fields.items():
            stmt = queries.SOUL_CONFIG_UPDATE.get(col)
            if stmt is None:
                raise ValueError(f"refusing to update unexpected soul_config column: {col}")
            if isinstance(value, (list, tuple)):
                value = next((str(v) for v in value if v not in (None, "")), "")
            value = "" if value is None else str(value).strip()
            # language must be a short plain token: junk ("['a']") → 'auto', or the Settings re-save loop resurrects it.
            if col == "language" and (not value or value[0] in "[{(" or len(value) > 20):
                value = "auto"
            # The LLM name-extractor sometimes emits junk ('42', '[...]') — the recurring "she's called 42"
            # bug. Non-name-like candidates SKIP the write (no safe default name); letters incl. kana/CJK.
            if col == "name":
                import re as _re
                if not value or len(value) > 40 or value[0] in "[{(" or not _re.search(
                    r"[A-Za-zÀ-ÿ぀-ヿ一-鿿]", value
                ):
                    import logging
                    logging.getLogger("kotoba").warning("soul_config.name REJECTED non-name %r — keeping current", value)
                    continue
            if col in ("name", "language"):
                import logging
                import traceback
                caller = "".join(traceback.format_stack(limit=4)[:-1]).strip().splitlines()[-2:]
                logging.getLogger("kotoba").info("soul_config.%s := %r  ← %s", col, value, " | ".join(s.strip() for s in caller))
            await self.conn.execute(stmt, {"value": value})
        await self.conn.commit()
    async def fetch_user_profile_as_markdown(self) -> str:
        async with self.conn.execute(queries.USER_PROFILE_ALL) as cur:
            rows = await cur.fetchall()
        if not rows:
            return "(nothing known about the user yet)"
        return "\n".join(f"- {r['key']}: {r['value']}" for r in rows)

    async def fetch_user_profile(self) -> dict[str, str]:
        """The same rows as the markdown reader, unprosed — a caller that wants one field should not
        have to parse a bulleted list she was meant to read."""
        async with self.conn.execute(queries.USER_PROFILE_ALL) as cur:
            return {r["key"]: r["value"] for r in await cur.fetchall()}

    async def upsert_user_profile(self, key: str, value: str) -> None:
        await self.conn.execute(queries.USER_PROFILE_UPSERT, {"key": key, "value": value})
        await self.conn.commit()
    async def fetch_active_memory_facts(self) -> list[str]:
        async with self.conn.execute(queries.MEMORY_FACTS_ACTIVE) as cur:
            rows = await cur.fetchall()
        return [r["fact"] for r in rows]

    async def ensure_session(self, session_id: str) -> None:
        await self.conn.execute(queries.SESSION_ENSURE, {"id": session_id})
        await self.conn.commit()

    async def fetch_recent_turns(self, session_id: Optional[str], limit: int = 20) -> list[dict]:
        if not session_id:
            return []
        async with self.conn.execute(
            queries.RECENT_TURNS, {"session_id": session_id, "limit": limit}
        ) as cur:
            rows = await cur.fetchall()
        # RECENT_TURNS returns newest-first (to LIMIT to the latest); reverse to chronological for the model.
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    async def insert_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        emotion: Optional[str] = None,
        tools_used: Optional[list[str]] = None,
    ) -> None:
        await self.conn.execute(
            queries.TURN_INSERT,
            {
                "session_id": session_id,
                "role": role,
                "content": content,
                "emotion": emotion,
                "tools_used": json.dumps(tools_used) if tools_used else None,
            },
        )
        await self.conn.commit()

    async def list_sessions(self, limit: int = 20) -> list[dict]:
        """The conversations that happened, newest first: {id, started_at, turns, opened}."""
        async with self.conn.execute(queries.SESSIONS_LIST, {"limit": limit}) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def repeated_turn_report(self) -> list[dict]:
        """READ-ONLY audit with NO PRODUCTION CALLER: run it from a test or a REPL when auditing.
        Assistant turns that were written down holding a block she said twice —
        the ones stored before core.stream.collapse_repeats ran on the way in. Returns
        [{id, session_id, created_at, chars, collapsed_chars, removed}] and changes NOTHING.

        Deliberately not a sweeper. A verbatim halving is lossless, but this is the user's own
        conversation history and which of their records gets rewritten is their call, not a de-dup
        verdict — the same line attribute_conflict_report() draws for the memory store."""
        from kotoba.core.stream import collapse_repeats

        async with self.conn.execute(queries.ASSISTANT_TURNS) as cur:
            rows = await cur.fetchall()
        out = []
        for r in rows:
            text = r["content"] or ""
            collapsed = collapse_repeats(text)
            if collapsed != text:
                out.append({
                    "id": r["id"], "session_id": r["session_id"], "created_at": r["created_at"],
                    "chars": len(text), "collapsed_chars": len(collapsed),
                    "removed": len(text) - len(collapsed),
                })
        return out

    async def count_turns(self) -> int:
        async with self.conn.execute(queries.TURNS_COUNT) as cur:
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def search_turns(self, query: str, limit: int = 5) -> list[dict]:
        async with self.conn.execute(
            queries.TURNS_FTS_SEARCH, {"query": query, "limit": limit}
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    async def insert_audit_log(
        self,
        action: str,
        *,
        risk_kind: Optional[str] = None,
        approved: bool = False,
        approver: Optional[str] = None,
        session_id: Optional[str] = None,
        detail: str,
    ) -> None:
        await self.conn.execute(
            queries.AUDIT_LOG_INSERT,
            {
                "session_id": session_id,
                "action": action,
                "risk_kind": risk_kind,
                "approved": 1 if approved else 0,
                "approver": approver,
                "detail": detail,
            },
        )
        await self.conn.commit()

    async def fetch_audit_log(self, limit: int = 50) -> list[dict]:
        """The recent audit rows. NO PRODUCTION CALLER, and that is not neglect: the audit trail is read
        by a HUMAN, through the `audit_log_read` view. Neither it nor the view is dead."""
        async with self.conn.execute(queries.AUDIT_LOG_RECENT, {"limit": limit}) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    async def insert_cronjob(
        self, message: str, due_at: str, session_id: Optional[str] = None,
        recurring: Optional[str] = None,
    ) -> None:
        await self.conn.execute(
            queries.CRON_INSERT,
            {"session_id": session_id, "message": message, "due_at": due_at, "recurring": recurring},
        )
        await self.conn.commit()

    async def list_cronjobs(self) -> list[dict]:
        async with self.conn.execute(queries.CRON_LIST) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def due_cronjobs(self) -> list[dict]:
        async with self.conn.execute(queries.CRON_DUE) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # Both return True only if THIS caller won the row — two tickers (CLI beside server) each read the
    # same due job and each delivered it, so the reminder was voiced twice.
    async def mark_cronjob_fired(self, job_id: str) -> bool:
        cur = await self.conn.execute(queries.CRON_MARK_FIRED, {"id": job_id})
        await self.conn.commit()
        return cur.rowcount == 1

    async def reschedule_cronjob(self, job_id: str, due_at: str, was_due: str | None = None) -> bool:
        cur = await self.conn.execute(
            queries.CRON_RESCHEDULE, {"id": job_id, "due_at": due_at, "was_due": was_due}
        )
        await self.conn.commit()
        return cur.rowcount == 1

    async def deactivate_cronjob(self, job_id: str) -> None:
        await self.conn.execute(queries.CRON_DEACTIVATE, {"id": job_id})
        await self.conn.commit()

    async def purge_finished_cronjobs(self, older_than_days: int = 30) -> int:
        """Delete cancelled/one-shot-fired jobs older than the cutoff so soft-deleted rows don't grow
        unbounded. Recurring jobs (fired_at cleared on reschedule) are kept. Returns rows removed."""
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).strftime("%Y-%m-%d %H:%M:%S")
        cur = await self.conn.execute(queries.CRON_PURGE, {"cutoff": cutoff})
        await self.conn.commit()
        return cur.rowcount or 0
    async def save_approved_command(self, pattern: str, scope: str = "command") -> None:
        await self.conn.execute(queries.APPROVED_CMD_UPSERT, {"pattern": pattern, "scope": scope})
        await self.conn.commit()

    async def list_approved_commands(self) -> list[dict]:
        async with self.conn.execute(queries.APPROVED_CMD_LIST) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def delete_approved_command(self, pattern: str, scope: str | None = None) -> None:
        """Revoke a saved grant. With no `scope`, EVERY width saved under that name goes — the Settings
        button and `/approvals rm` both name a pattern and nothing else, and leaving the other row behind
        would mean pressing revoke and still being auto-approved. Revoking more than was asked is the
        safe direction here; revoking less is not."""
        if scope is None:
            await self.conn.execute(queries.APPROVED_CMD_DELETE, {"pattern": pattern})
        else:
            await self.conn.execute(queries.APPROVED_CMD_DELETE_SCOPED,
                                    {"pattern": pattern, "scope": scope})
        await self.conn.commit()
    async def upsert_discord_person(self, user_id: str, handle: str, display: str,
                                    relation: str = "known") -> None:
        await self.conn.execute(queries.PERSON_UPSERT, {
            "user_id": str(user_id), "handle": handle, "display": display, "relation": relation})
        await self.conn.commit()

    async def list_discord_people(self, limit: int = 50) -> list[dict]:
        async with self.conn.execute(queries.PERSON_LIST, {"limit": limit}) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def add_person_fact(self, user_id: str, fact: str, source: str = "said",
                              guild_id: Optional[str] = None) -> bool:
        """False means she already knew it. The verdict is what stops her writing the same thing
        five times and then reading five contradictory versions back."""
        async with self.conn.execute(
            queries.PERSON_FACT_EXISTS, {"user_id": str(user_id), "fact": fact}
        ) as cur:
            if await cur.fetchone():
                return False
        await self.conn.execute(queries.PERSON_FACT_INSERT, {
            "user_id": str(user_id), "fact": fact, "source": source,
            "guild_id": None if guild_id is None else str(guild_id)})
        await self.conn.commit()
        return True

    async def person_facts(self, user_id: str, limit: int = 12) -> list[dict]:
        async with self.conn.execute(
            queries.PERSON_FACTS, {"user_id": str(user_id), "limit": limit}
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def save_key(self, name: str, value: str) -> None:
        """A value we can DECRYPT stores as-is (no double-encrypt) — the prefix alone is not proof;
        legacy plaintext rows pass through on read and re-encrypt on save."""
        from kotoba.core import keystore
        stored = value if keystore.is_ours(value) else keystore.encrypt(value or "")
        await self.conn.execute(queries.SAVED_KEY_UPSERT, {"name": name, "value": stored})
        await self.conn.commit()

    async def get_key(self, name: str) -> Optional[str]:
        async with self.conn.execute(queries.SAVED_KEY_GET, {"name": name}) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        from kotoba.core import keystore
        raw = row["value"]
        plain = keystore.decrypt(raw)
        if plain is not None:
            return plain
        if keystore.is_encrypted(raw):
            # Ours by shape but not by key (KEK changed or lost): say so — silent None reads as "never saved".
            logging.getLogger("kotoba").error(
                "saved key %r cannot be decrypted (master key changed or lost) — re-enter it", name
            )
            return None
        return raw

    async def list_key_names(self) -> list[dict]:
        async with self.conn.execute(queries.SAVED_KEY_NAMES) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def delete_key(self, name: str) -> None:
        """DIAGNOSTIC TRAP, not leftover debug code: MCP credential keys have vanished from prod with
        no known cause, so every such delete is stack-traced — logging the key NAME only, never the
        value. KOTOBA_KEY_DELETE_TRAP=0 disables."""
        if name.startswith("mcp:") or name.startswith("mcp_oauth:"):
            import os
            if os.getenv("KOTOBA_KEY_DELETE_TRAP", "1").strip().lower() not in ("0", "false", "no"):
                import logging
                import traceback
                logging.getLogger("kotoba").warning(
                    "KEY_DELETE_TRAP: deleting saved_key %r — caller stack:\n%s",
                    name, "".join(traceback.format_stack()[:-1]),
                )
        await self.conn.execute(queries.SAVED_KEY_DELETE, {"name": name})
        await self.conn.commit()
