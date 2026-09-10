"""The keystore is the only thing between a provider API key and a plaintext row on disk. Five
defects fixed and pinned here: a prefix check meant to detect encryption instead made a save SKIP
it, so a key that happened to start with the version prefix was written in plaintext and read back
as none — leaked and lost at once; every read resolved the master key with create-if-missing, so
decrypting with a lost key file quietly minted a new one, breaking every existing secret silently;
two processes booting together each generated a key and the second overwrote the first, orphaning
everything it had encrypted; a truncated key file was sliced to whatever bytes it had and handed to
the cipher, which raises — saving a key then answered 500 with no reason; and a row that no longer
decrypts still rendered as "saved" in the panel."""
from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from conftest import posix_only

from kotoba.core import keystore


@pytest.fixture
def kek(tmp_path, monkeypatch):
    monkeypatch.delenv("KOTOBA_MASTER_KEY", raising=False)
    kf = tmp_path / ".keystore_key"
    monkeypatch.setenv("KOTOBA_KEYSTORE_KEY_FILE", str(kf))
    return kf


# --- 1. the prefix is not proof ---------------------------------------------------------------------

def test_a_secret_that_starts_like_a_blob_is_still_encrypted(kek):
    secret = "v1:sk-proj-REAL-SECRET-abcdef"
    assert keystore.is_ours(secret) is False
    blob = keystore.encrypt(secret)
    assert blob != secret
    assert keystore.decrypt(blob) == secret


def test_is_ours_accepts_only_what_we_can_actually_decrypt(kek):
    blob = keystore.encrypt("sk-real")
    assert keystore.is_ours(blob) is True
    # right shape, wrong bytes → the GCM tag rejects it
    tampered = keystore._PREFIX + base64.b64encode(b"\x00" * 40).decode()
    assert keystore.looks_encrypted(tampered) is True
    assert keystore.is_ours(tampered) is False


def test_save_key_never_stores_a_secret_in_plaintext(kek):
    async def go():
        from kotoba.db.database import Database

        db = Database("sqlite:///" + tempfile.mktemp(suffix=".db"))
        await db.connect()
        for secret in ("v1:sk-looks-like-a-blob", "sk-plain", "xai-abc"):
            await db.save_key("llm:openai:api_key", secret)
            async with db.conn.execute("SELECT value FROM saved_keys WHERE name='llm:openai:api_key'") as cur:
                stored = (await cur.fetchone())["value"]
            assert secret not in stored, "the raw secret reached the database"
            assert await db.get_key("llm:openai:api_key") == secret
        await db.close()

    asyncio.run(go())


# --- 2. reading never mints a key -------------------------------------------------------------------

def test_decrypt_does_not_create_a_master_key(kek):
    blob = keystore.encrypt("sk-original")
    kek.unlink()
    assert keystore.decrypt(blob) is None
    assert not kek.exists(), "a read path created a new KEK, making the loss permanent and silent"


def test_encrypt_still_creates_one_on_first_use(kek):
    assert not kek.exists()
    keystore.encrypt("x")
    assert kek.exists()
    assert kek.stat().st_size == keystore._KEK_LEN


# --- 3. two processes, one KEK ----------------------------------------------------------------------

def test_a_second_process_adopts_the_existing_key(kek, tmp_path):
    child = tmp_path / "child.py"
    child.write_text(
        "import os, sys\n"
        "os.environ['KOTOBA_KEYSTORE_KEY_FILE'] = sys.argv[1]\n"
        "os.environ.pop('KOTOBA_MASTER_KEY', None)\n"
        "from kotoba.core import keystore\n"
        "print(keystore.encrypt('sk-child'))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    env.pop("KOTOBA_MASTER_KEY", None)
    mine = keystore.encrypt("sk-parent")
    r = subprocess.run([sys.executable, str(child), str(kek)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr[-400:]
    assert keystore.decrypt(r.stdout.strip()) == "sk-child"
    assert keystore.decrypt(mine) == "sk-parent", "the second process overwrote the KEK"


@posix_only("POSIX permission bits")
def test_creation_is_not_world_readable(kek):
    import stat

    os.umask(0o022)
    keystore.encrypt("x")
    assert stat.S_IMODE(kek.stat().st_mode) == 0o600


# --- 4. a truncated key file says so ----------------------------------------------------------------

def test_a_short_key_file_raises_a_named_error(kek):
    kek.write_bytes(b"short")
    with pytest.raises(keystore.KeystoreUnavailable):
        keystore.encrypt("x")
    assert keystore.decrypt("v1:" + base64.b64encode(b"\x00" * 40).decode()) is None


# --- 5. the panel tells the truth -------------------------------------------------------------------

def test_an_unreadable_key_is_not_reported_as_saved(kek):
    async def go():
        from kotoba.core.settings import _llm_settings
        from kotoba.db.database import Database

        db = Database("sqlite:///" + tempfile.mktemp(suffix=".db"))
        await db.connect()
        await db.save_key("llm:openai:api_key", "sk-real")
        names = [k["name"] for k in await db.list_key_names()]
        got = await _llm_settings({}, names, db)
        assert next(p for p in got["providers"] if p["id"] == "openai")["has_key"] is True

        kek.unlink()  # the master key is gone — the row is undecryptable
        got = await _llm_settings({}, names, db)
        assert next(p for p in got["providers"] if p["id"] == "openai")["has_key"] is False
        await db.close()

    asyncio.run(go())


def test_the_master_key_is_written_in_binary_on_every_platform(kek, monkeypatch):
    """os.open defaults to TEXT mode on Windows, and the key is 32 random bytes.

    Every 0x0A in it is written as 0x0D 0x0A while the read side is binary, and the reader slices to
    32 — so the file is the right length, the write succeeds, the read succeeds, and the key that
    comes back is a different one. Roughly one Windows install in eight, silent, reported only as
    "the master key changed or lost" once a secret had already been saved under it."""
    seen: list[int] = []
    real = os.open

    def spy(path, flags, *a, **kw):
        seen.append(flags)
        return real(path, flags, *a, **kw)

    monkeypatch.setattr(os, "O_BINARY", 0x8000, raising=False)
    monkeypatch.setattr(os, "open", spy)
    keystore.encrypt("anything")
    monkeypatch.undo()

    assert seen, "the key file was never created through os.open"
    assert any(f & 0x8000 for f in seen), "the master key is written in text mode"


def test_the_key_file_is_exactly_thirty_two_bytes_on_disk(kek):
    """The end-to-end assertion, which the flag spy cannot make: text mode adds a byte per 0x0A, so
    the SIZE is the thing that proves nothing translated on the way out. Written with a key that
    carries one, because a random key carries one only about an eighth of the time."""
    real = os.urandom
    keystore.os.urandom = lambda n: b"\x01" * 10 + b"\n" + b"\x02" * (n - 11)
    try:
        blob = keystore.encrypt("a secret worth keeping")
    finally:
        keystore.os.urandom = real
    # The BYTES, not the count. Asked only for the size, this could not fail on a platform that never
    # translates, so the half that runs here proved nothing; the exact content catches a translated
    # newline, a trailing one, and a key that arrived mangled by any other route.
    assert kek.read_bytes() == b"\x01" * 10 + b"\n" + b"\x02" * 21, "the key on disk is not the key"
    assert kek.stat().st_size == 32, "a byte was added on the way to disk"
    assert keystore.decrypt(blob) == "a secret worth keeping"


def test_a_key_file_left_over_long_by_the_old_bug_still_opens_what_it_encrypted(kek, caplog):
    """Recovering the true key would be worse than the bug: everything saved after the first secret
    was encrypted with the mangled one, so the mangled one is what has to keep being returned. What
    changed is that it says so instead of letting doctor accuse the operator of losing a file."""
    kek.write_bytes(b"\x01" * 10 + b"\r\n" + b"\x02" * 21)      # 33 bytes, as text mode left it
    keystore._warned_long = False
    with caplog.at_level("WARNING", logger="kotoba"):
        blob = keystore.encrypt("saved after the mangling")
    assert keystore.decrypt(blob) == "saved after the mangling"
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "33 bytes" in said and "text mode" in said and "first secret" in said


def test_a_failed_acl_never_leaves_an_empty_key_file_behind(kek, monkeypatch):
    """_master_key only ever creates when the file is absent, so a zero-byte one is permanent: the
    keystore is dead until a human deletes it. restrict_file sits between the open and the write."""
    monkeypatch.setattr(keystore.perms, "restrict_file",
                        lambda p: (_ for _ in ()).throw(RuntimeError("icacls said no")))
    with pytest.raises(RuntimeError):
        keystore.encrypt("x")
    assert not kek.exists(), "an empty key file survived and would never be rewritten"

    monkeypatch.undo()
    assert keystore.decrypt(keystore.encrypt("x")) == "x", "the next boot must recover on its own"


def test_an_empty_key_file_variable_is_not_a_path(tmp_path, monkeypatch):
    """Read as one it resolved to the current directory, and encrypt raised a raw OSError out of the
    one function every caller is written against for a named error."""
    from kotoba.core import keystore

    monkeypatch.delenv("KOTOBA_MASTER_KEY", raising=False)
    monkeypatch.setenv("KOTOBA_HOME", str(tmp_path))
    monkeypatch.setenv("KOTOBA_KEYSTORE_KEY_FILE", "")

    assert keystore._key_file() == tmp_path / ".keystore_key"
    assert keystore.decrypt(keystore.encrypt("hello")) == "hello"
