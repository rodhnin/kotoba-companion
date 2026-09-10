"""A seam the exact-grant work left open: the approved-commands table keyed only on the pattern.

A family grant and an exact grant are two different promises, and over a bare single-token command
both are stored under the same string. With the pattern alone as the key, the second write did not
add a row, it overwrote the first one's scope — a narrow grant silently became broad, or a broad one
silently became narrow, with nothing warned.

Unreachable from today's UI, since a bare token under a family grant auto-approves and draws no
card. Scope is now part of the key, so both coexist and each is revocable on its own; revoking by
name alone still takes both, since taking away more permission than asked is the safe direction."""
from __future__ import annotations

import asyncio

import pytest

from kotoba.db.database import Database


def _db(tmp_path, name="grants.db"):
    return Database("sqlite:///" + str(tmp_path / name))


def _rows(db):
    return {(r["pattern"], r["scope"]) for r in asyncio.run(db.list_approved_commands())}


@pytest.fixture
def db(tmp_path):
    d = _db(tmp_path)
    asyncio.run(d.connect())
    try:
        yield d
    finally:
        asyncio.run(d.close())


def test_a_family_grant_and_an_exact_grant_on_one_token_both_survive(db):
    asyncio.run(db.save_approved_command("ls", "command"))
    asyncio.run(db.save_approved_command("ls", "exact"))

    assert _rows(db) == {("ls", "command"), ("ls", "exact")}


def test_saving_the_exact_line_does_not_narrow_a_family_the_user_already_granted(db):
    """The dangerous direction is the other one, but this one is a broken promise: they said "always allow
    ls" and the next card silently took it back."""
    asyncio.run(db.save_approved_command("ls", "command"))
    asyncio.run(db.save_approved_command("ls", "exact"))

    families = {r["pattern"] for r in asyncio.run(db.list_approved_commands())
                if r["scope"] != "exact"}
    assert families == {"ls"}, "the family grant is still there"


def test_saving_a_family_does_not_widen_an_exact_grant_into_one(db):
    """The direction that hands out permission nobody granted: an exact `ls` becoming every `ls …`."""
    asyncio.run(db.save_approved_command("ls", "exact"))
    asyncio.run(db.save_approved_command("ls", "command"))
    asyncio.run(db.save_approved_command("df -h", "exact"))

    exact = {r["pattern"] for r in asyncio.run(db.list_approved_commands()) if r["scope"] == "exact"}
    assert exact == {"ls", "df -h"}


def test_the_gate_reads_the_two_as_the_two_grants_they_are(db):
    """What the loop builds its gate from — one bare token, two widths, no collapse."""
    from kotoba.core.approval import ApprovalGate

    asyncio.run(db.save_approved_command("ls", "command"))
    asyncio.run(db.save_approved_command("ls", "exact"))
    rows = asyncio.run(db.list_approved_commands())
    gate = ApprovalGate(
        saved_commands={r["pattern"] for r in rows if r["scope"] != "exact"},
        saved_exact={r["pattern"] for r in rows if r["scope"] == "exact"},
        host_exec=False,
    )

    assert gate.is_saved("ls") is True
    assert gate.is_saved_exact("ls") is True


def test_both_widths_survive_a_round_trip_through_the_gate_that_saves_them(db, tmp_path):
    """Not just the table: the gate's own two persistence doors, which is how a granted card lands."""
    gate = _gate_over(db, workspace_root=tmp_path)
    asyncio.run(gate.persist_always("ls"))
    asyncio.run(gate.persist_exact("ls"))

    assert _rows(db) == {("ls", "command"), ("ls", "exact")}
    reopened = _gate_over(db, workspace_root=tmp_path)
    assert reopened.is_saved("ls") and reopened.is_saved_exact("ls")


def test_re_saving_the_same_grant_stays_one_row(db):
    asyncio.run(db.save_approved_command("npm", "command"))
    asyncio.run(db.save_approved_command("npm", "command"))

    assert _rows(db) == {("npm", "command")}


def test_revoking_by_name_takes_every_width_of_that_name(db):
    """The Settings button and `/approvals rm` both name a pattern and nothing else. Leaving the other
    row behind would mean pressing revoke and still being auto-approved."""
    asyncio.run(db.save_approved_command("ls", "command"))
    asyncio.run(db.save_approved_command("ls", "exact"))
    asyncio.run(db.save_approved_command("npm", "command"))

    asyncio.run(db.delete_approved_command("ls"))

    assert _rows(db) == {("npm", "command")}


def test_one_width_can_be_revoked_on_its_own(db):
    asyncio.run(db.save_approved_command("ls", "command"))
    asyncio.run(db.save_approved_command("ls", "exact"))

    asyncio.run(db.delete_approved_command("ls", "exact"))

    assert _rows(db) == {("ls", "command")}


def test_an_existing_database_keeps_its_grants_across_the_migration(tmp_path):
    """The step has to reach a live install without dropping what is already saved there."""
    import aiosqlite

    from kotoba.db import migrations

    path = tmp_path / "old.db"

    async def old_shape():
        async with aiosqlite.connect(path) as conn:
            await conn.executescript(migrations._STEP_0)
            await conn.execute("PRAGMA user_version = 1")
            await conn.execute(
                "INSERT INTO approved_commands (pattern, scope) VALUES ('git', 'command')"
            )
            await conn.execute(
                "INSERT INTO approved_commands (pattern, scope) VALUES ('sleep 15 && echo ok', 'exact')"
            )
            await conn.commit()

    asyncio.run(old_shape())
    db = _db(tmp_path, "old.db")
    asyncio.run(db.connect())
    try:
        assert _rows(db) == {("git", "command"), ("sleep 15 && echo ok", "exact")}
        asyncio.run(db.save_approved_command("git", "exact"))
        assert ("git", "command") in _rows(db) and ("git", "exact") in _rows(db)
    finally:
        asyncio.run(db.close())


# --- the negatives: making room for both widths must widen NEITHER ----------------------------------

def _gate_over(db, **kw):
    """The gate exactly as core.loop builds it, over whatever this database holds."""
    from kotoba.core.approval import ApprovalGate

    rows = asyncio.run(db.list_approved_commands())
    return ApprovalGate(
        saved_commands={r["pattern"] for r in rows if r["scope"] != "exact"},
        saved_exact={r["pattern"] for r in rows if r["scope"] == "exact"},
        on_persist=db.save_approved_command,
        on_persist_exact=lambda cmd: db.save_approved_command(cmd, "exact"),
        host_exec=True, **kw,
    )


def test_a_dangerous_command_is_still_unsavable_at_either_width(db, tmp_path):
    """Both grants are checked before the store, and `detect_dangerous` runs before either grant is
    consulted — so even a row somebody wrote by hand buys nothing."""
    from kotoba.core.approval import persistable, persistable_exact

    danger = "rm -rf /tmp/build"
    gate = _gate_over(db, workspace_root=tmp_path)
    assert persistable(danger) is False and persistable_exact(danger) is False

    asyncio.run(gate.persist_always(danger))
    asyncio.run(gate.persist_exact(danger))
    assert gate.is_saved("rm") is False and gate.is_saved_exact(danger) is False

    asyncio.run(db.save_approved_command(danger, "exact"))
    asyncio.run(db.save_approved_command("rm", "command"))
    assert _gate_over(db, workspace_root=tmp_path).would_auto_allow(danger, "exec") is False


def test_a_compound_and_an_interpreter_family_are_still_unpersistable(db, tmp_path):
    from kotoba.core.approval import persistable

    gate = _gate_over(db, workspace_root=tmp_path)
    for cmd in ("npm run build && ./deploy.sh", "sh script.sh", "python evil.py", "env python evil.py"):
        assert persistable(cmd) is False, cmd
        asyncio.run(gate.persist_always(cmd))

    assert _rows(db) == set(), "no family key may reach the store"


def test_an_exact_grant_still_never_widens_into_a_prefix_or_a_family(db, tmp_path):
    asyncio.run(db.save_approved_command("npm run build", "exact"))
    gate = _gate_over(db, workspace_root=tmp_path)

    assert gate.would_auto_allow("npm run build", "exec") is True
    for near in ("npm publish", "npm run build --prod", "npm run", "NPM RUN BUILD",
                 "npm  run build", "npm run build; id"):
        assert gate.would_auto_allow(near, "exec") is False, near
    assert gate.is_saved("npm") is False


def test_force_ask_still_beats_both_grants_at_once(db, tmp_path):
    asked: list[str] = []

    async def ask(action, risk, fam):
        asked.append(action)
        return False

    asyncio.run(db.save_approved_command("ls", "command"))
    asyncio.run(db.save_approved_command("ls", "exact"))
    gate = _gate_over(db, workspace_root=tmp_path, ask=ask)

    assert gate.would_auto_allow("ls", "exec", force_ask=True) is False
    assert asyncio.run(gate.confirm("ls", "exec", force_ask=True)) is False
    assert asked == ["ls"]


def test_the_migration_step_is_safe_to_run_twice(tmp_path):
    """A crash between the step and its version bump re-runs it — the module's whole rule."""
    import aiosqlite

    from kotoba.db import migrations

    path = tmp_path / "twice.db"

    async def go():
        async with aiosqlite.connect(path) as conn:
            await migrations.run_migrations(conn)
            await conn.execute("INSERT INTO approved_commands (pattern, scope) VALUES ('git', 'exact')")
            await conn.commit()
            await conn.executescript(migrations._STEPS[-1])
            async with conn.execute("SELECT pattern, scope FROM approved_commands") as cur:
                return await cur.fetchall()

    assert [tuple(r) for r in asyncio.run(go())] == [("git", "exact")]
