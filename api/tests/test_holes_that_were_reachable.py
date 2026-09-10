"""Three holes that were reachable, each proved reachable before it was closed.

1. The voice WebSocket had no Origin check — handshakes are exempt from CORS — so on the default
install any page the user visited could open a socket to the backend and drive the agentic loop.
2. `$HOME` defeated the read jail: the verdict is computed with shlex (no expansion) but enforced by
`/bin/sh -c` (expansion), so `cat $HOME/.ssh/id_rsa` resolved as an in-jail path and auto-ran.
3. Views were rebuilt with `executescript`, which commits between statements: two boots interleaving
DROP/CREATE could leave a window where the view existed for nobody."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# --- 1. the WebSocket origin check -----------------------------------------------------------------

@pytest.fixture
def open_app(monkeypatch, tmp_path):
    """The DEFAULT install: no password at all. The hostile-page case."""
    for v in ("KOTOBA_WEB_PASSWORD", "KOTOBA_GATE_PASSWORD", "KOTOBA_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ws.db'}")
    import kotoba.server as m

    with TestClient(m.app) as c:
        yield c


def test_a_hostile_page_cannot_open_the_voice_socket(open_app):
    with pytest.raises(Exception):
        with open_app.websocket_connect("/api/voice/drive-by",
                                        headers={"Origin": "https://evil.example"}):
            pass


def test_the_real_frontend_origin_still_connects(open_app):
    with open_app.websocket_connect("/api/voice/ok", headers={"Origin": "http://localhost:3000"}) as ws:
        assert ws is not None


def test_a_non_browser_client_still_connects(open_app):
    """Browsers always send Origin; a missing one means the CLI, a script or a test, all of which
    have to keep connecting."""
    with open_app.websocket_connect("/api/voice/cli") as ws:
        assert ws is not None


def test_an_extra_configured_origin_is_honoured(monkeypatch, tmp_path):
    monkeypatch.setenv("CORS_ORIGINS", "https://kotoba.example")
    for v in ("KOTOBA_WEB_PASSWORD", "KOTOBA_GATE_PASSWORD"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ws2.db'}")
    import importlib

    import kotoba.server as m

    importlib.reload(m)
    with TestClient(m.app) as c:
        with c.websocket_connect("/api/voice/x", headers={"Origin": "https://kotoba.example"}) as ws:
            assert ws is not None
        with pytest.raises(Exception):
            with c.websocket_connect("/api/voice/y", headers={"Origin": "https://evil.example"}):
                pass


# --- 2. $VAR must not ride the auto-safe read path -------------------------------------------------

ESCAPES = [
    "cat $HOME/.ssh/id_rsa",
    "cat ${HOME}/.ssh/id_rsa",
    'cat "$HOME"/.ssh/id_rsa',
    "cat $HOME/.kotoba/.keystore_key",
    "grep -r sk- $HOME",
    "find $HOME -name id_rsa",
    "cat ~/.ssh/id_rsa",          # the form that was already caught — keep it caught
]

READS = [
    "cat notes.txt", "ls -la", "wc -l notes.txt", "head -n 5 notes.txt", "tail -n 20 notes.txt",
    "grep -rn pattern .", "grep -ri pattern .", "rg -n pattern", "rg -il pattern",
    "sort -u notes.txt", 'find . -name "*.py"', "find . -type f -maxdepth 2",
]


@pytest.fixture
def gate():
    from kotoba.core.approval import ApprovalGate

    wd = Path(tempfile.mkdtemp())
    (wd / "notes.txt").write_text("x", encoding="utf-8")
    return ApprovalGate(host_exec=True, workspace_root=wd, default_decision=False)


@pytest.mark.parametrize("cmd", ESCAPES)
def test_an_env_expansion_never_auto_runs_on_the_host(gate, cmd):
    assert gate.auto_safe(cmd, "exec") is False, cmd


@pytest.mark.parametrize("cmd", READS)
def test_reading_still_costs_no_prompt(gate, cmd):
    assert gate.auto_safe(cmd, "exec") is True, f"{cmd} must not ask — it only reads"


def test_an_expansion_cannot_ride_a_saved_family(gate):
    """A saved 'always allow npm' must not cover `npm run $SOMETHING`."""
    from kotoba.core.approval import _has_shell_metachars

    assert _has_shell_metachars("npm run $EVIL") is True
    assert _has_shell_metachars("npm run build") is False


# --- 3. concurrent boots must not collide on the views ---------------------------------------------

def test_three_concurrent_boots_rebuild_the_views_without_error(tmp_path):
    import aiosqlite

    from kotoba.db.migrations import _rebuild_views, run_migrations

    db = tmp_path / "t.db"

    async def main():
        async with aiosqlite.connect(db) as c:
            await run_migrations(c)

        async def boot():
            async with aiosqlite.connect(db) as c:
                await _rebuild_views(c)

        results = await asyncio.gather(boot(), boot(), boot(), return_exceptions=True)
        errs = [r for r in results if isinstance(r, BaseException)]
        assert not errs, f"concurrent boot raised: {errs}"

        async with aiosqlite.connect(db) as c:
            async with c.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='view' AND name='audit_log_read'"
            ) as cur:
                assert (await cur.fetchone())[0] == 1

    asyncio.run(main())


def test_a_view_wording_change_still_takes_effect(tmp_path):
    """The reason the views live outside the versioned steps — do not regress it into IF NOT EXISTS."""
    import aiosqlite

    import kotoba.db.migrations as m

    db = tmp_path / "t.db"

    async def boot():
        async with aiosqlite.connect(db) as c:
            await m.run_migrations(c)

    asyncio.run(boot())
    original = m._VIEW_STATEMENTS
    try:
        m._VIEW_STATEMENTS = [s.replace("'user'       THEN 'user'", "'user'       THEN 'the-human'")
                              for s in original]
        asyncio.run(boot())

        async def read():
            async with aiosqlite.connect(db) as c:
                await c.execute("INSERT INTO audit_log (action, approved, approver, detail) "
                                "VALUES ('ls', 1, 'user', 'decision')")
                await c.commit()
                async with c.execute("SELECT authority FROM audit_log_read") as cur:
                    return (await cur.fetchone())[0]

        assert asyncio.run(read()) == "the-human"
    finally:
        m._VIEW_STATEMENTS = original
