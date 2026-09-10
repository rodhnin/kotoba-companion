"""Acceptance tests for the audit_log_read view and the required-detail contract.

The audit log was written by three generations of code, indistinguishable in the raw rows. The view
labels each row: legacy (detail IS NULL; `approved` does not mean consent was sought), pre-vocab
(detail is set, but approver was recorded as 'work-loop', the wrong vocabulary), or current (correct
vocabulary and semantics).

The other two contracts pinned here: `detail` is required, so no writer can leave a NULL behind
silently; and a row the loop wrote on its own must never read as though somebody approved it."""
from __future__ import annotations

import asyncio

import pytest

from kotoba.db.database import Database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


async def _open(tmp_path, name: str) -> Database:
    db = Database("sqlite:///" + str(tmp_path / name))
    await db.connect()
    return db


# ---------------------------------------------------------------------------
# View: three distinct regimes
# ---------------------------------------------------------------------------

def test_view_exists_and_classifies_three_regimes(tmp_path):
    """run_migrations must create audit_log_read, and it must place each boundary row in a regime of
    its own — three rows in, three different regime strings out.

    The labels have to be self-explanatory to someone reading the table with no context, which is why
    the legacy label names the unreliability of `approved`, and why a pre-vocab row keeps "pre-vocab"
    in its authority column.

    The authority column is the whole point: a row the loop ran must not read as anyone having
    decided anything, so it must not echo the raw approver name back either."""

    async def go():
        db = await _open(tmp_path, "v.db")
        try:
            # (a) Legacy row: detail IS NULL — bypass insert_audit_log to avoid the required-detail guard.
            await db.conn.execute(
                "INSERT INTO audit_log (action, approved, approver) "
                "VALUES ('legacy_action', 1, 'work-loop')"
            )
            # (b) Pre-vocab row: detail IS NOT NULL, approver still 'work-loop'.
            await db.insert_audit_log(
                action="prevocab_action", risk_kind="write",
                approved=False, approver="work-loop", detail="executed",
            )
            # (c) Current row: detail IS NOT NULL, new approver vocabulary.
            await db.insert_audit_log(
                action="current_action", risk_kind="exec",
                approved=False, approver="agent-loop", detail="executed",
            )
            await db.conn.commit()

            async with db.conn.execute(
                "SELECT action, regime, authority FROM audit_log_read ORDER BY created_at"
            ) as cur:
                rows = {r["action"]: dict(r) for r in await cur.fetchall()}

            return rows
        finally:
            await db.close()

    rows = _run(go())

    assert set(rows) == {"legacy_action", "prevocab_action", "current_action"}

    regimes = [rows[k]["regime"] for k in ("legacy_action", "prevocab_action", "current_action")]
    assert len(set(regimes)) == 3, f"expected 3 distinct regimes, got: {regimes}"

    legacy_regime = rows["legacy_action"]["regime"]
    assert "legacy" in legacy_regime.lower()
    assert "approved" in legacy_regime.lower() or "unreliable" in legacy_regime.lower(), (
        f"legacy regime label must explain that approved is unreliable: {legacy_regime!r}"
    )

    assert rows["prevocab_action"]["regime"] == "pre-vocab"
    assert rows["current_action"]["regime"] == "current"
    assert "pre-vocab" in rows["prevocab_action"]["authority"]
    assert rows["current_action"]["authority"] == "none — the loop ran it, nobody was asked"
    assert "agent-loop" not in rows["current_action"]["authority"]


# ---------------------------------------------------------------------------
# Required detail: no writer can produce NULL silently
# ---------------------------------------------------------------------------

def test_insert_audit_log_requires_detail(tmp_path):
    """Omitting detail must raise TypeError, not silently insert a NULL row."""

    async def go():
        db = await _open(tmp_path, "t.db")
        try:
            with pytest.raises(TypeError):
                await db.insert_audit_log(
                    action="shell ls", risk_kind="exec",
                    approved=False, approver="agent-loop",
                    # `detail` deliberately absent
                )
            rows = await db.fetch_audit_log()
            assert rows == [], f"no row should exist, got: {rows}"
        finally:
            await db.close()

    _run(go())


# ---------------------------------------------------------------------------
# Vocabulary pin: agent-loop rows must not claim consent
# ---------------------------------------------------------------------------

def test_agent_loop_rows_use_correct_approver_and_no_consent(tmp_path):
    """Rows the agentic loop writes must carry approver='agent-loop' and approved=0 (no consent sought)."""

    async def go():
        db = await _open(tmp_path, "pin.db")
        try:
            await db.insert_audit_log(
                action="write_file path=readme.txt", risk_kind="write",
                approved=False, approver="agent-loop", detail="executed",
            )
            await db.insert_audit_log(
                action="shell cmd", risk_kind="exec",
                approved=False, approver="agent-loop", detail="executed:failed",
            )
            rows = await db.fetch_audit_log()
        finally:
            await db.close()
        return rows

    rows = _run(go())
    assert len(rows) == 2
    for r in rows:
        assert r["approver"] == "agent-loop", f"expected agent-loop, got {r['approver']!r}"
        assert r["approver"] != "work-loop", "old vocabulary must not appear in new rows"
        assert r["approved"] == 0, (
            f"agent-loop rows must not assert consent (approved should be 0): {r}"
        )
        assert r["detail"] in ("executed", "executed:failed")
