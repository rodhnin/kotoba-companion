"""Migration versioning: PRAGMA user_version tracks which steps have already run.

Guards three invariants:
  1. An existing database with all tables already present is adopted without data loss
     (user_version advances, no tables dropped/recreated).
  2. A migration step runs exactly once, even if run_migrations is called twice.
  3. A fresh database reaches the latest version after one run.
"""
from __future__ import annotations

import asyncio

import aiosqlite
import pytest

from kotoba.db import migrations

_OPENED: list[aiosqlite.Connection] = []


@pytest.fixture(autouse=True)
def _close_whatever_was_opened():
    """Each test closes on its happy path only. A failing assertion skips that close, and aiosqlite's
    non-daemon worker then keeps the interpreter alive: the file reported four red tests and never
    returned, which on a runner is a cancelled job rather than a failure anybody reads."""
    yield
    for conn in _OPENED:
        try:
            asyncio.new_event_loop().run_until_complete(conn.close())
        except Exception:
            pass
    _OPENED.clear()


async def _open(path: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    _OPENED.append(conn)
    return conn


async def _version(conn) -> int:
    async with conn.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


def test_fresh_db_reaches_latest_version(tmp_path):
    """One run over an empty file leaves user_version at the last step in `_STEPS`."""
    db = str(tmp_path / "fresh.db")

    async def go():
        conn = await _open(db)
        await migrations.run_migrations(conn)
        v = await _version(conn)
        await conn.close()
        return v

    version = asyncio.run(go())
    assert version == len(migrations._STEPS), (
        f"fresh DB must be at version {len(migrations._STEPS)}, got {version}"
    )


def test_fresh_db_has_all_tables(tmp_path):
    """And that run is what creates the schema: every table the app opens must exist afterwards."""
    db = str(tmp_path / "fresh.db")

    async def go():
        conn = await _open(db)
        await migrations.run_migrations(conn)
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) as cur:
            tables = {r[0] for r in await cur.fetchall()}
        await conn.close()
        return tables

    tables = asyncio.run(go())
    for expected in ("turns", "sessions", "memory_facts", "soul_config",
                     "audit_log", "cronjobs", "saved_keys"):
        assert expected in tables, f"table {expected!r} missing after fresh migration"


def test_existing_db_adopted_without_data_loss(tmp_path):
    """An install predating the versioning — tables present, user_version still 0 — is ADOPTED rather
    than rebuilt.

    The schema is created by hand without versioning and seeded with a sentinel row; after
    run_migrations the row must still be there and user_version must have advanced."""
    db = str(tmp_path / "existing.db")

    async def go():
        conn = await _open(db)
        await conn.executescript(migrations._STEPS[0])
        await conn.execute(
            "INSERT INTO user_profile (key, value) VALUES ('sentinel', 'alive')"
        )
        await conn.commit()
        assert await _version(conn) == 0, "precondition: no user_version on old install"

        await migrations.run_migrations(conn)

        v = await _version(conn)
        async with conn.execute(
            "SELECT value FROM user_profile WHERE key = 'sentinel'"
        ) as cur:
            row = await cur.fetchone()
        await conn.close()
        return v, row["value"] if row else None

    version, sentinel = asyncio.run(go())
    assert sentinel == "alive", "existing data must survive migration adoption"
    assert version == len(migrations._STEPS), (
        "user_version must be advanced to the latest step after adoption"
    )


def test_migration_step_runs_exactly_once(tmp_path, monkeypatch):
    """A step applies once and only once, however often run_migrations is called.

    `_STEPS` is patched with a synthetic step that counts its own executions in a sentinel table, so
    a second run that re-applied it would show up as a second row."""
    db = str(tmp_path / "once.db")

    sentinel_step = """
    CREATE TABLE IF NOT EXISTS _migration_sentinel (run_count INTEGER NOT NULL DEFAULT 0);
    INSERT INTO _migration_sentinel (run_count) VALUES (1);
    """

    monkeypatch.setattr(migrations, "_STEPS", [migrations._STEPS[0], sentinel_step])

    async def go():
        conn = await _open(db)
        await migrations.run_migrations(conn)
        await migrations.run_migrations(conn)
        async with conn.execute("SELECT SUM(run_count) FROM _migration_sentinel") as cur:
            row = await cur.fetchone()
        v = await _version(conn)
        await conn.close()
        return row[0] if row else 0, v

    total_runs, version = asyncio.run(go())
    assert total_runs == 1, (
        f"sentinel step must run exactly once; ran {total_runs} times"
    )
    assert version == 2, "user_version must reflect both steps having been applied"


def test_versioned_db_restarts_cleanly(tmp_path):
    """An already-versioned database re-runs nothing on restart, and loses no rows to the attempt."""
    db = str(tmp_path / "restart.db")

    async def go():
        conn = await _open(db)
        await migrations.run_migrations(conn)
        v_before = await _version(conn)
        # Count turns rows as a proxy for 'no tables were wiped'.
        await conn.execute(
            "INSERT INTO sessions (id) VALUES ('s1')"
        )
        await conn.execute(
            "INSERT INTO turns (session_id, role, content) VALUES ('s1', 'user', 'hello')"
        )
        await conn.commit()

        await migrations.run_migrations(conn)
        v_after = await _version(conn)
        async with conn.execute("SELECT count(*) FROM turns") as cur:
            count = (await cur.fetchone())[0]
        await conn.close()
        return v_before, v_after, count

    v_before, v_after, count = asyncio.run(go())
    assert v_before == v_after, "user_version must not change on a clean restart"
    assert count == 1, "existing rows must survive a restart"


def test_indexes_created_by_migration(tmp_path):
    """The indexes are part of the schema the migration owes, not something the app adds later."""
    db = str(tmp_path / "indexed.db")

    async def go():
        conn = await _open(db)
        await migrations.run_migrations(conn)
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
        ) as cur:
            indexes = {r[0] for r in await cur.fetchall()}
        await conn.close()
        return indexes

    indexes = asyncio.run(go())
    for expected in ("idx_turns_session_time", "idx_cronjobs_due", "idx_audit_log_time"):
        assert expected in indexes, f"index {expected!r} missing after migration"
