"""The audit_log table and the database helpers that write and read it.

Every approval decision and every executed risky action lands here, so the row has to keep both who
approved it (a human, or the auto-safe rule that let it through unasked) and whether it was approved at
all."""
from __future__ import annotations

import asyncio

from kotoba.db.database import Database


def test_audit_log_insert_and_fetch(tmp_path):
    async def go():
        db = Database("sqlite:///" + str(tmp_path / "audit.db"))
        await db.connect()
        try:
            await db.insert_audit_log(
                action="rm -rf build", risk_kind="exec", approved=True,
                approver="user", session_id="s1", detail="decision",
            )
            await db.insert_audit_log(
                action="ls", risk_kind="exec", approved=False,
                approver="auto-safe", detail="executed",
            )
            return await db.fetch_audit_log(limit=10)
        finally:
            await db.close()

    rows = go_rows = asyncio.run(go())
    assert len(go_rows) == 2
    actions = {r["action"] for r in rows}
    assert actions == {"rm -rf build", "ls"}
    by_action = {r["action"]: r for r in rows}
    assert by_action["rm -rf build"]["approved"] == 1
    assert by_action["rm -rf build"]["approver"] == "user"
    assert by_action["ls"]["approver"] == "auto-safe"
