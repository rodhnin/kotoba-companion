"""The HTTP gate, and the state it writes to disk — eight defects fixed and pinned here:
the cookie was a 12-hour bearer for the whole surface, sent cross-site; the SSE token leaked into
the uvicorn access log in clear text; the shared password had no lockout, and the first lockout
attempt rejected valid tokens on loopback; `/api` and `/v1` disagreed on the case of "Bearer"; a
garbage sandbox value silently disabled all execution while the panel said "local", and an
infinite/NaN timeout and a null model name were both accepted; a relative database URL resolved
against the current directory, opening a different empty database from elsewhere; performance
indexes were appended after existing databases had passed that migration step and so never
arrived; and restarting reverted a chosen voice back to the default."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import sqlite3

import pytest
from conftest import posix_only


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import kotoba.server as main

    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "pw-for-tests")
    main._FAILED_AUTH.clear()
    return TestClient(main.app), "pw-for-tests"


# A path no route claims: 401 means the gate stopped it, 404 means it got through.
PROBE = "/api/__gate_probe__"


# --- 1 + 4. what authenticates -----------------------------------------------------------------------

def test_the_files_cookie_is_not_the_password(client):
    """The viewer cookie is derived from the password, never the password itself, and it opens only
    the viewer.

    Its reach also narrowed: it used to be accepted on any path CONTAINING /api/files — DELETE
    /api/files/{path} included — and is now GET/HEAD under /api/files/raw only."""
    c, pw = client
    derived = hmac.new(pw.encode(), b"kotoba-files-viewer", hashlib.sha256).hexdigest()
    assert derived != pw
    assert c.get("/api/files/raw/__gate_probe__", cookies={"kf": derived}).status_code != 401
    assert c.get(PROBE, cookies={"kf": derived}).status_code == 401, "the viewer cookie must not open the API"
    assert c.get("/api/files/__gate_probe__", cookies={"kf": derived}).status_code == 401
    assert c.get("/api/files/raw/__gate_probe__", cookies={"kf": pw}).status_code == 401


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
def test_the_bearer_scheme_is_case_insensitive(client, scheme):
    c, pw = client
    assert c.get(PROBE, headers={"Authorization": f"{scheme} {pw}"}).status_code == 404


def test_health_is_public_and_docs_is_not(client):
    c, _pw = client
    assert c.get("/health").status_code == 200
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert c.get(path).status_code == 401, path


# --- 2. the access log --------------------------------------------------------------------------------

def test_the_access_log_does_not_carry_the_token():
    import logging

    import kotoba.server as main

    seen: list[str] = []

    class _Cap(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    lg = logging.getLogger("uvicorn.access")
    lg.addHandler(_Cap())
    lg.setLevel(logging.INFO)
    try:
        lg.info('%s - "%s %s HTTP/1.1" %d', "127.0.0.1", "GET", "/api/events/x?token=hunter2", 200)
    finally:
        lg.handlers = [h for h in lg.handlers if not isinstance(h, _Cap)]
    assert seen and "hunter2" not in seen[0], seen
    assert "token=<redacted>" in seen[0]
    assert "/api/events/x" in seen[0], "the path itself must survive — this is still an access log"


# --- 3. guessing --------------------------------------------------------------------------------------

def test_repeated_wrong_tokens_stop_being_answered(client):
    c, _pw = client
    codes = [c.get(PROBE, headers={"Authorization": "Bearer nope"}).status_code for _ in range(30)]
    assert codes[0] == 401
    assert 429 in codes, "an unlimited guessing rate is the whole attack"


def test_a_valid_token_is_never_locked_out(client):
    c, pw = client
    for _ in range(30):
        c.get(PROBE, headers={"Authorization": "Bearer nope"})
    assert c.get(PROBE, headers={"Authorization": f"Bearer {pw}"}).status_code == 404


# --- 5. settings validation ---------------------------------------------------------------------------

@pytest.mark.parametrize("key,value", [
    ("sandbox", "rocket"), ("model", None), ("model", {"a": 1}),
    ("work_timeout", float("inf")), ("work_timeout", float("nan")),
    ("work_max_iter", float("inf")), ("work_max_iter", "1e400"),
])
def test_a_bad_setting_is_rejected_not_coerced(tmp_path, monkeypatch, key, value):
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "s.yaml"))
    from kotoba.core.app_settings import set_runtime

    with pytest.raises(ValueError):
        set_runtime(key, value)


def test_a_hand_edited_setting_is_validated_on_read(tmp_path, monkeypatch):
    """The validators only guarded what the PANEL wrote. The CLI and a plain text editor write to
    this file too, so a hand-edited value has to be validated when it is READ: a nonsense sandbox
    falls back to the default, and a duration that is not a number falls back to the default."""
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "s.yaml"))
    monkeypatch.delenv("KOTOBA_SANDBOX", raising=False)
    from kotoba.core import app_settings

    (tmp_path / "s.yaml").write_text("runtime:\n  sandbox: rocket\n  work_timeout: 20 minutes\n")
    assert app_settings.runtime_value("sandbox", "KOTOBA_SANDBOX", "local") == "local"
    assert app_settings.runtime_value("work_timeout", "KOTOBA_WORK_TIMEOUT", 1500) == "1500"

    (tmp_path / "s.yaml").write_text("runtime:\n  sandbox: docker\n")
    assert app_settings.runtime_value("sandbox", "KOTOBA_SANDBOX", "local") == "docker"


def test_a_non_mapping_settings_file_is_not_a_total_outage(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "s.yaml"))
    monkeypatch.delenv("KOTOBA_SANDBOX", raising=False)
    from kotoba.core import app_settings

    (tmp_path / "s.yaml").write_text("broken\n")
    assert app_settings.runtime_value("sandbox", "KOTOBA_SANDBOX", "local") == "local"


# --- 6. where the database lives -----------------------------------------------------------------------

@posix_only("a rooted POSIX path (/tmp/x.db)")
def test_a_relative_database_url_does_not_follow_the_cwd(tmp_path, monkeypatch):
    """Pinning the answer to `api/` made this pass only where a database already sat there: on a fresh
    clone the same code answered the home and the guard failed instead of the bug."""
    from kotoba.db.database import _path_from_url
    from kotoba.paths import db_dir

    where = db_dir()
    monkeypatch.chdir(tmp_path)
    assert _path_from_url("sqlite:///./kotoba.db") == str(where / "kotoba.db")
    assert _path_from_url("sqlite:///kotoba.db") == str(where / "kotoba.db")
    assert _path_from_url("sqlite:////tmp/x.db") == "/tmp/x.db"
    assert _path_from_url("sqlite:///:memory:") == ":memory:"


# --- 7. the indexes reach an existing database ---------------------------------------------------------

def test_the_indexes_reach_an_already_versioned_database(tmp_path):
    from kotoba.db import migrations

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript(migrations._STEP_0)
    con.execute("PRAGMA user_version = 1")   # created before the indexes existed
    con.commit()
    con.close()

    async def go():
        import aiosqlite

        async with aiosqlite.connect(db) as conn:
            await migrations.run_migrations(conn)
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
            ) as cur:
                return {r[0] for r in await cur.fetchall()}

    assert asyncio.run(go()) == {"idx_turns_session_time", "idx_cronjobs_due",
                                 "idx_audit_log_time", "idx_person_facts_user"}


# --- 8. the voice the user chose ------------------------------------------------------------------------

def test_a_restart_keeps_the_users_voice(tmp_path):
    """A restart re-syncs the soul file, and must not undo the voice the user picked in the panel.

    The first sync adopts whatever the file says; after the user chooses a voice, a second sync —
    which is what a restart does — has to leave that choice alone. The personality text still
    follows the file, because only the values the user owns are protected from it."""
    from kotoba.db.database import Database
    from kotoba.soul.loader import sync_from_file

    soul = tmp_path / "default.md"
    soul.write_text("name: Kotoba\nlanguage: auto\nvoice_id: FROM_FILE\n---\n\n## Personality\nhi\n")

    async def go():
        db = Database("sqlite:///" + str(tmp_path / "voice.db"))
        await db.connect()
        await sync_from_file(db, str(soul))
        assert (await db.fetch_soul_config())["voice_id"] == "FROM_FILE", "first run adopts the file"
        await db.update_soul_config(voice_id="CHOSEN_IN_THE_PANEL")
        await sync_from_file(db, str(soul))
        kept = (await db.fetch_soul_config())["voice_id"]
        soul.write_text("name: Kotoba\nlanguage: auto\nvoice_id: FROM_FILE\n---\n\n## Personality\nchanged\n")
        await sync_from_file(db, str(soul))
        personality = (await db.fetch_soul_config())["personality"]
        await db.close()
        return kept, personality

    kept, personality = asyncio.run(go())
    assert kept == "CHOSEN_IN_THE_PANEL"
    assert personality == "changed"


# --- the cron claim -------------------------------------------------------------------------------------

def test_only_one_ticker_delivers_a_due_reminder(tmp_path):
    """Two tickers — the CLI starts one beside the web server's — each read the same due job, and
    only the first of them may claim it."""
    from kotoba.db.database import Database

    async def go():
        db = Database("sqlite:///" + str(tmp_path / "c.db"))
        await db.connect()
        await db.conn.execute(
            "INSERT INTO cronjobs (id, session_id, message, due_at, active) "
            "VALUES ('j1', 's', 'pills', '2020-01-01 00:00:00', 1)"
        )
        await db.conn.commit()
        first = await db.mark_cronjob_fired("j1")
        second = await db.mark_cronjob_fired("j1")
        await db.close()
        return first, second

    first, second = asyncio.run(go())
    assert first is True and second is False
