"""db.update_soul_config must reject a non-name companion name. The LLM name-extractor
occasionally emits junk — a bare number ('42'), a serialized blob, empty — and storing it corrupted her
identity (the recurring "she's called 42" bug). A non-name candidate is SKIPPED (the current name is kept),
while real names (Latin / accented / Japanese kana / CJK) are stored."""
from __future__ import annotations

import asyncio
import tempfile


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _db():
    from kotoba.db.database import Database

    db = Database("sqlite:///" + tempfile.mktemp(suffix=".db"))
    await db.connect()
    # update_soul_config UPDATEs the singleton row — seed it so the fresh test DB has one.
    await db.conn.execute("INSERT OR IGNORE INTO soul_config (id) VALUES ('default')")
    await db.conn.commit()
    return db


def test_junk_names_rejected_current_kept():
    async def go():
        db = await _db()
        await db.update_soul_config(name="Kotoba")  # establish a good name
        for junk in ("42", "99999", "['a']", "", "   ", "[]", "{x}", "x" * 60):
            await db.update_soul_config(name=junk)
            cur = await db.conn.execute("SELECT name FROM soul_config")
            assert (await cur.fetchone())[0] == "Kotoba", f"junk {junk!r} corrupted the name"

    _run(go())


def test_real_names_accepted():
    async def go():
        db = await _db()
        for n in ("Kotoba", "Yuki", "ユキ", "雪", "Renée"):
            await db.update_soul_config(name=n)
            cur = await db.conn.execute("SELECT name FROM soul_config")
            assert (await cur.fetchone())[0] == n, f"valid name {n!r} was not stored"

    _run(go())
