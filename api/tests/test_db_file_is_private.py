"""The database keeps itself to its owner, and creates its own directory.

sqlite makes a new file at 0644 minus umask, so on a shared host every turn she had ever been told was
readable by any local account — while the keystore and settings.yaml, which hold less, were already
0600. The keys inside are encrypted; the conversation is not, and the conversation is the private part.

The mkdir is the same function's other half: sqlite will not create a missing parent. A clone never hits
it because `api/` already exists, but a wheel install pointed at a fresh ~/.kotoba died in connect() with
`unable to open database file` — the cold start nobody could reproduce from the repo.
"""
from __future__ import annotations

import asyncio

import pytest
from conftest import posix_only, obeys_permissions

from kotoba.db.database import Database


def _mode(p) -> str:
    return oct(p.stat().st_mode)[-3:]


def _connected(path, after=None):
    """Connect, optionally look around while it is open, and always close — an abandoned aiosqlite
    connection raises out of its worker thread once the loop is gone and fills the suite with noise."""
    async def go():
        db = Database(str(path))
        await db.connect()
        try:
            return await after(db) if after else None
        finally:
            if db.conn is not None:
                await db.conn.close()

    return asyncio.run(go())


@posix_only("POSIX permission bits")
def test_a_new_database_is_readable_only_by_its_owner(tmp_path):
    db_path = tmp_path / "kotoba.db"
    _connected(db_path)
    assert _mode(db_path) == "600"


@posix_only("POSIX permission bits")
def test_an_existing_open_database_is_repaired(tmp_path):
    """Applied on every connect, not only creation — an install that already leaked gets closed."""
    db_path = tmp_path / "kotoba.db"
    db_path.touch()
    db_path.chmod(0o644)
    _connected(db_path)
    assert _mode(db_path) == "600"


@posix_only("POSIX permission bits")
def test_the_wal_sidecars_inherit_that(tmp_path):
    """WAL holds committed rows too, so a private main file with world-readable sidecars leaks anyway.
    sqlite copies the mode across — this pins that it keeps doing so."""
    db_path = tmp_path / "kotoba.db"

    async def write_and_look(db):
        await db.conn.execute("CREATE TABLE probe (a TEXT)")
        await db.conn.execute("INSERT INTO probe VALUES ('private')")
        await db.conn.commit()
        return {p.name: _mode(p) for p in db_path.parent.iterdir()}

    modes = _connected(db_path, write_and_look)
    assert modes and set(modes.values()) == {"600"}, modes


@posix_only("POSIX permission bits")
def test_a_first_run_creates_its_own_directory(tmp_path):
    db_path = tmp_path / "never" / "existed" / "kotoba.db"
    _connected(db_path)
    assert db_path.exists() and _mode(db_path) == "600"


def test_an_in_memory_database_still_connects(tmp_path):
    """`:memory:` is not a path — touching it would create a junk file next to the CWD."""
    _connected(":memory:")
    assert not (tmp_path / ":memory:").exists()


@posix_only("a directory made unwritable with chmod 0500")
@obeys_permissions("a directory made unwritable")
@pytest.mark.parametrize("unwritable", [True])
def test_an_unwritable_directory_does_not_block_the_boot(tmp_path, unwritable):
    """Best-effort: a foreign-owned or read-only path must reach sqlite's own error, not ours."""
    jail = tmp_path / "ro"
    jail.mkdir()
    jail.chmod(0o500)
    try:
        with pytest.raises(Exception) as exc:
            asyncio.run(Database(str(jail / "kotoba.db")).connect())
        assert "unable to open" in str(exc.value).lower() or "permission" in str(exc.value).lower()
    finally:
        jail.chmod(0o700)


def test_a_fresh_database_restricts_the_files_its_migrations_create(tmp_path, monkeypatch):
    """Asked once, before the migrations, there was nothing there to ask about: on a brand-new install
    the migrations are what create the sidecars, so the first process ran its whole life with them
    open to whatever the directory allowed. POSIX copies the mode, so the spy is the only witness."""
    from kotoba.core import perms

    seen: list[str] = []
    monkeypatch.setattr(perms, "restrict_file", lambda path: seen.append(str(path)))

    db = Database("sqlite:///" + str(tmp_path / "fresh.db"))

    async def go():
        await db.connect()
        await db.close()

    asyncio.run(go())
    assert any(x.endswith("-wal") for x in seen), seen
    assert any(x.endswith("-shm") for x in seen), seen
