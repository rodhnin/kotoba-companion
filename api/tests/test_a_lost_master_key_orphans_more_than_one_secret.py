"""A lost master key does not cost you one secret. It costs you all of them, and only one said so.

`doctor` already explained the case for the key she thinks with: "saved but cannot be decrypted". That
line is right and it is not enough — the same key encrypted the ElevenLabs key, the other provider's
key and every MCP credential, and those fail somewhere else entirely, as a voice that went quiet or a
server that stopped signing in. Somebody re-entered the one the report named and met the next by
surprise.

None of them can be recovered, so the only useful report is the whole list at once.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from kotoba.cli import doctor
from kotoba.core import keystore
from kotoba.db.database import Database

SECRETS = {
    "llm:openai:api_key": "sk-the-brain-key",
    "llm:xai:api_key": "xai-the-other-brain",
    "voice:elevenlabs:api_key": "el-the-voice",
    "mcp:notion:token": "ntn-a-server-credential",
}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A configured install: its own database, its own master key, four secrets stored through the
    real path. The key file lives beside the database so losing it is one unlink."""
    monkeypatch.delenv("KOTOBA_MASTER_KEY", raising=False)
    kek = tmp_path / "keystore_key"
    monkeypatch.setenv("KOTOBA_KEYSTORE_KEY_FILE", str(kek))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'kotoba.db'}")

    async def seed():
        db = Database(os.environ["DATABASE_URL"])
        await db.connect()
        try:
            for name, value in SECRETS.items():
                await db.save_key(name, value)
        finally:
            await db.close()

    _run(seed())
    assert kek.is_file(), "the fixture never exercised the keystore"
    return kek


def test_all_of_them_are_readable_while_the_key_is_there(install):
    check = _run(doctor._saved_secrets())
    assert check.status == "ok"
    assert "4 secrets" in check.detail


def test_losing_the_key_is_reported_as_losing_every_secret(install):
    """The point of the whole file: four rows, four names, one report."""
    install.unlink()

    check = _run(doctor._saved_secrets())
    assert check.status == "fail"
    for name in SECRETS:
        assert name in check.detail, f"{name} was orphaned in silence"
    assert "4 saved secrets" in check.detail


def test_a_replaced_key_reads_the_same_as_a_lost_one(install):
    """Restoring a backup of the wrong home, or letting a second install create its own key, leaves a
    key file that exists and opens nothing. To the person that is the identical accident."""
    install.write_bytes(b"\x00" * 32)

    check = _run(doctor._saved_secrets())
    assert check.status == "fail"
    assert all(name in check.detail for name in SECRETS)


def test_the_report_never_prints_the_secret_it_could_not_read(install):
    """A misclassified secret is still a secret, and this row exists to be pasted into an issue."""
    install.unlink()

    detail = _run(doctor._saved_secrets()).detail
    for value in SECRETS.values():
        assert value not in detail


def test_it_says_they_are_gone_rather_than_offering_to_fix_them(install):
    """AES-GCM without its key is not a recoverable state. A line that reads like a repair is worse
    than no line: it sends somebody looking for the undo that does not exist."""
    install.unlink()

    detail = _run(doctor._saved_secrets()).detail
    assert "entered again" in detail and "recovered" in detail


def test_an_install_with_no_secrets_is_not_a_problem(tmp_path, monkeypatch):
    monkeypatch.delenv("KOTOBA_MASTER_KEY", raising=False)
    monkeypatch.setenv("KOTOBA_KEYSTORE_KEY_FILE", str(tmp_path / "keystore_key"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'kotoba.db'}")

    check = _run(doctor._saved_secrets())
    assert check.status == "ok"
    assert "none stored" in check.detail


def test_a_row_that_was_never_encrypted_is_not_called_orphaned(install):
    """Rows predating the keystore sit in the clear and are used as they are. Counting one as
    unreadable would send somebody hunting for a master key that never encrypted it."""
    async def put_legacy():
        db = Database(os.environ["DATABASE_URL"])
        await db.connect()
        try:
            await db.conn.execute(
                "INSERT OR REPLACE INTO saved_keys (name, value) VALUES (:n, :v)",
                {"n": "llm:legacy:api_key", "v": "plaintext-from-before-the-keystore"},
            )
            await db.conn.commit()
        finally:
            await db.close()

    _run(put_legacy())
    assert keystore.is_encrypted("plaintext-from-before-the-keystore") is False

    check = _run(doctor._saved_secrets())
    assert check.status == "ok", check.detail
    assert "llm:legacy:api_key" not in check.detail


def test_the_report_survives_a_database_it_cannot_open(tmp_path, monkeypatch):
    """It is one row on a report whose job is explaining a broken install — it must not be the thing
    that raises. The database check above already names the real fault."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'nope' / 'kotoba.db'}")

    check = _run(doctor._saved_secrets())
    assert check.status in ("skip", "ok", "fail")
