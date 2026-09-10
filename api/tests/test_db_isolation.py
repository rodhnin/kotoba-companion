"""The test suite must not be able to write to the real database.

Root cause: DATABASE_URL was captured as a module-level constant at import time.
A monkeypatch applied after import had no effect, so the tests used the real kotoba.db.

Fix: DATABASE_URL is now read inside lifespan() so monkeypatch always wins.
"""
from __future__ import annotations


from fastapi.testclient import TestClient


def test_no_module_level_database_url():
    """The module must not capture DATABASE_URL at import time.

    If main.DATABASE_URL exists, a test that monkeypatches the env var after
    importing `main` will still use the old (real) path — silently touching kotoba.db."""
    import kotoba.server as main  # already imported by the time this runs; just check the state
    assert not hasattr(main, "DATABASE_URL"), (
        "DATABASE_URL must not be a module-level constant in main.py — "
        "it is captured at import time and ignores monkeypatch applied later."
    )


def test_no_module_level_soul_path():
    """Same issue for SOUL_PATH — must be read inside lifespan, not at import."""
    import kotoba.server as main
    assert not hasattr(main, "SOUL_PATH"), (
        "SOUL_PATH must not be a module-level constant in main.py."
    )


def test_lifespan_uses_monkeypatched_database_url(tmp_path, monkeypatch):
    """When DATABASE_URL is monkeypatched, the lifespan must open THAT path, not the real one.

    This is the regression test for the original bug: if the module constant is back,
    the TestClient would open kotoba.db instead of the tmp path, and the real DB would
    accumulate test rows."""
    db_path = tmp_path / "isolated_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    import kotoba.server as m
    with TestClient(m.app):
        pass

    assert db_path.exists(), (
        "lifespan must have opened the monkeypatched DATABASE_URL, creating the file"
    )


def test_the_real_database_is_neither_touched_nor_created(tmp_path, monkeypatch):
    """Unchanged where one exists — and where none does, none must appear.

    Skipped for absence, this left a fresh clone with nothing watching at all, and absence is the
    stronger claim of the two: broken isolation does not modify a database there, it CREATES one."""
    import hashlib
    from pathlib import Path

    real_db = Path(__file__).parent.parent / "kotoba.db"

    def _digest() -> str | None:
        # WAL mode lands writes in -wal/-shm first, so hashing only the main file would miss them.
        if not real_db.exists():
            return None
        h = hashlib.md5(real_db.read_bytes())
        for suffix in ("-wal", "-shm"):
            side = real_db.with_name(real_db.name + suffix)
            if side.exists():
                h.update(side.read_bytes())
        return h.hexdigest()

    before = _digest()

    db_path = tmp_path / "test_real_db.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    import kotoba.server as m
    with TestClient(m.app) as client:
        client.get("/health")

    after = _digest()
    if before is None:
        assert after is None, "the suite created a database beside the package where there was none"
    else:
        assert before == after, (
            f"kotoba.db or its WAL was modified during the test — isolation is broken. "
            f"before={before} after={after}"
        )
