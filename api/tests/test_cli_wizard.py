"""First run.

A key is validated with a real round-trip BEFORE it is stored: a well-formed key with no credit is
indistinguishable from a good one until she fails on the first real question, and by then the person
cannot tell a wrong key from a broken install.

The wizard must also know when NOT to run. An environment variable is a perfectly good configuration,
and asking someone who already has one to type a key again is a bug, not a courtesy.
"""
from __future__ import annotations

import asyncio
import tempfile


from kotoba.cli import wizard
from kotoba.db.database import Database


def _run(coro):
    return asyncio.run(coro)


def _db() -> Database:
    return Database("sqlite:///" + (tempfile.mkdtemp() + "/kotoba.db"))


def test_a_fresh_install_needs_the_wizard():
    async def go():
        db = _db()
        await db.connect()
        needed = await wizard.needed(db)
        await db.close()
        return needed

    assert _run(go()) is True


def test_a_saved_key_means_it_never_asks_again():
    async def go():
        db = _db()
        await db.connect()
        await db.save_key("llm:openai:api_key", "sk-already-configured")
        needed = await wizard.needed(db)
        await db.close()
        return needed

    assert _run(go()) is False


def test_an_environment_key_is_a_valid_configuration(monkeypatch):
    """Asking someone who set OPENAI_API_KEY to type it again would be a bug."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")

    async def go():
        db = _db()
        await db.connect()
        needed = await wizard.needed(db)
        await db.close()
        return needed

    assert _run(go()) is False


def test_a_key_that_does_not_work_is_never_stored(monkeypatch):
    async def refuse(provider_id, key):
        return False, "AuthenticationError: incorrect api key"

    monkeypatch.setattr(wizard, "_verify", refuse)

    async def go():
        db = _db()
        await db.connect()
        ok = await wizard.run(db, ask=lambda _p: "1", ask_secret=lambda _p: "sk-wrong")
        stored = await db.get_key("llm:openai:api_key")
        await db.close()
        return ok, stored

    ok, stored = _run(go())
    assert ok is False
    assert stored is None, "a key that failed its round-trip must not be saved"


def test_a_working_key_is_stored_encrypted_and_the_provider_is_remembered(monkeypatch, tmp_path):
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))

    async def accept(provider_id, key):
        return True, ""

    async def accept_voice(key):
        return True, ""

    monkeypatch.setattr(wizard, "_verify", accept)
    # The wizard does not stop at the brain: `ask_secret` answers the voice prompt too, and that check
    # is a live request to the voice service. Both round-trips are stubbed or neither is.
    monkeypatch.setattr(wizard.voice_key, "verify", accept_voice)

    async def go():
        from kotoba.core import app_settings

        db = _db()
        await db.connect()
        ok = await wizard.run(db, ask=lambda _p: "2", ask_secret=lambda _p: "xai-good-key")
        async with db.conn.execute("SELECT value FROM saved_keys WHERE name='llm:xai:api_key'") as cur:
            row = await cur.fetchone()
        readable = await db.get_key("llm:xai:api_key")
        await db.close()
        return ok, row["value"], readable, app_settings.runtime_value("provider", "KOTOBA_LLM_PROVIDER", "openai")

    ok, stored, readable, provider = _run(go())
    assert ok is True
    assert "xai-good-key" not in stored, "the raw key reached the database"
    assert readable == "xai-good-key"
    assert provider == "xai", "the choice must survive the next launch"


def test_backing_out_at_the_provider_changes_nothing():
    async def go():
        db = _db()
        await db.connect()
        ok = await wizard.run(db, ask=lambda _p: "42", ask_secret=lambda _p: "never asked")
        still_needed = await wizard.needed(db)
        await db.close()
        return ok, still_needed

    ok, still_needed = _run(go())
    assert ok is False
    assert still_needed is True


def test_an_empty_key_is_not_a_key():
    async def go():
        db = _db()
        await db.connect()
        ok = await wizard.run(db, ask=lambda _p: "1", ask_secret=lambda _p: "   ")
        await db.close()
        return ok

    assert _run(go()) is False
