"""A failure to ASK must not be recorded as the user's answer.

When the asker raises (SSE channel closed, emit_task failed) the action is correctly denied — but the
row said `approver='user', approved=0`, i.e. "they saw it and said no". Nobody saw anything. The audit
trail exists so the log never claims something happened that did not; 'error' is its own authority.

Views are rebuilt on every startup (they hold no data), so the label for a new authority actually
reaches an existing database instead of being frozen by the version gate.
"""
from __future__ import annotations

import asyncio

from kotoba.core.approval import ApprovalGate


def _run(asker):
    """Returns (allowed, decision_rows)."""
    rows: list[tuple] = []

    async def audit(action, risk, ok, who, detail):
        rows.append((action, risk, ok, who, detail))

    async def go():
        gate = ApprovalGate(ask=asker, audit=audit, default_decision=False)
        return await gate.confirm("rm -rf build", "exec", family="rm")

    allowed = asyncio.run(go())
    return allowed, [r for r in rows if r[4] == "decision"]


def test_an_asker_that_raises_is_denied_and_audited_as_error():
    async def broken_ask(action, risk_kind, family):
        raise RuntimeError("SSE channel closed")

    allowed, decisions = _run(broken_ask)
    assert allowed is False, "a failure to ask must deny"
    assert decisions, "the decision must still be recorded"
    assert decisions[-1][3] == "error", f"authority was {decisions[-1][3]!r}, not 'error'"


def test_a_real_denial_is_still_attributed_to_the_user():
    """The distinction has to cut both ways, or 'error' just hides real denials."""
    async def says_no(action, risk_kind, family):
        return False

    allowed, decisions = _run(says_no)
    assert allowed is False
    assert decisions[-1][3] == "user"


def test_a_real_approval_is_still_attributed_to_the_user():
    async def says_yes(action, risk_kind, family):
        return True

    allowed, decisions = _run(says_yes)
    assert allowed is True
    assert decisions[-1][3] == "user"


# --- the label has to reach an existing database ---------------------------------------------------

def test_the_read_view_explains_the_error_authority(tmp_path):
    """A label nobody can read is not a fix — the view must name what 'error' means."""
    import aiosqlite

    from kotoba.db.migrations import run_migrations

    async def go():
        async with aiosqlite.connect(tmp_path / "t.db") as conn:
            await run_migrations(conn)
            await conn.execute(
                "INSERT INTO audit_log (action, risk_kind, approved, approver, detail) "
                "VALUES ('rm -rf build', 'exec', 0, 'error', 'decision')"
            )
            await conn.commit()
            async with conn.execute("SELECT authority, regime FROM audit_log_read") as cur:
                return await cur.fetchone()

    authority, regime = asyncio.run(go())
    assert "never reached the user" in authority
    assert regime == "current"


def test_a_view_wording_fix_reaches_an_already_migrated_database(tmp_path):
    """The version gate skips applied steps, so a view inside one would be frozen forever."""
    import aiosqlite

    import kotoba.db.migrations as m

    db = tmp_path / "t.db"

    async def first_boot():
        async with aiosqlite.connect(db) as conn:
            await m.run_migrations(conn)
            async with conn.execute("PRAGMA user_version") as cur:
                return (await cur.fetchone())[0]

    async def second_boot():
        async with aiosqlite.connect(db) as conn:
            await m.run_migrations(conn)
            await conn.execute(
                "INSERT INTO audit_log (action, approved, approver, detail) "
                "VALUES ('ls', 1, 'user', 'decision')"
            )
            await conn.commit()
            async with conn.execute("SELECT authority FROM audit_log_read") as cur:
                return (await cur.fetchone())[0]

    assert asyncio.run(first_boot()) >= 1, "the step must be marked applied"

    original = m._VIEW_STATEMENTS
    try:
        m._VIEW_STATEMENTS = [s.replace("'user'       THEN 'user'", "'user'       THEN 'the-human'")
                              for s in original]
        assert asyncio.run(second_boot()) == "the-human"
    finally:
        m._VIEW_STATEMENTS = original
