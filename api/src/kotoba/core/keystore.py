"""Envelope encryption for secrets at rest (LLM provider API keys in the saved_keys table).

`saved_keys.value` is plaintext, fine for low-stakes pointers but NOT for a real provider key. This
encrypts with AES-256-GCM before persisting and decrypts only in memory at use time — never logged,
never returned to the model.

Master key (KEK) resolution: env KOTOBA_MASTER_KEY (any string) for prod, stable across
installs you control; else an auto-generated per-install file at ~/.kotoba/.keystore_key, mode 0600,
so encryption works out of the box for a self-hosted user with no config. The blob format is
`v1:<base64(nonce(12) + ciphertext)>` so the scheme can be rotated later."""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path

from kotoba.core import perms
from kotoba.paths import home_dir

_PREFIX = "v1:"
_KEK_LEN = 32
_warned_long = False
_MIN_BLOB = 12 + 16  # nonce + GCM tag: anything shorter cannot be one of ours

log = logging.getLogger("kotoba.keystore")


class KeystoreUnavailable(RuntimeError):
    """The KEK could not be resolved, so a secret cannot be encrypted. Callers must NOT fall back to
    storing plaintext — surface the error instead."""


def _key_file() -> Path:
    # An empty value is not a path. Taken as one it resolved to the current directory, and encrypt then
    # raised a raw OSError out of the one function whose whole promise is a named error.
    named = os.getenv("KOTOBA_KEYSTORE_KEY_FILE", "").strip()
    return Path(named or (home_dir() / ".keystore_key")).expanduser()


def _too_long(kf: Path, raw: bytes) -> bytes:
    """A key file with more than 32 bytes in it, kept working and finally said out loud.

    A Windows build wrote the key in text mode, so every 0x0A in it reached disk as 0x0D 0x0A. The
    first 32 bytes are still what everything on that install was encrypted with, so they are still what
    is returned — recovering the true key would break every secret saved since. Only the FIRST one ever
    saved is unreadable, and the operator was being told their master key had been lost."""
    global _warned_long
    if not _warned_long:
        _warned_long = True
        log.warning("keystore key file %s is %d bytes, expected %d — an old Windows build wrote it in "
                    "text mode. Everything still works except the first secret ever saved here, which "
                    "has to be entered again.", kf, len(raw), _KEK_LEN)
    return raw[:_KEK_LEN]


def _read_key_file(kf: Path, *, wait_for_writer: bool = False) -> bytes | None:
    """`wait_for_writer` is for the O_EXCL loser: the winner has CREATED the file but may not have
    written its 32 bytes yet, so a plain read got 0 bytes and turned into a 500 on the very first save
    of a fresh install. Short, bounded wait — the writer is one os.write away."""
    import time

    for attempt in range(20 if wait_for_writer else 1):
        raw = kf.read_bytes()
        if len(raw) == _KEK_LEN:
            return raw
        if len(raw) > _KEK_LEN:
            return _too_long(kf, raw)
        if attempt < 19 and wait_for_writer:
            time.sleep(0.01)
    # A TRUNCATED key file (interrupted write, a partial restore) used to be sliced to whatever it had
    # and handed to AESGCM, which raises — so saving a key answered 500 and every existing secret
    # became unreadable, with nothing saying why.
    log.error("keystore key file %s is %d bytes, expected %d — refusing to use it", kf, len(raw), _KEK_LEN)
    return None


def _master_key(*, create: bool) -> bytes | None:
    """32-byte key for AES-256-GCM, from KOTOBA_MASTER_KEY or a per-install key file.

    `create=False` on every READ path: generating a KEK while trying to DECRYPT is how a lost key file
    turned into a silent, permanent loss — the fresh key made new saves work, so the panel kept listing
    the old secret as saved while nothing could ever read it again."""
    env = os.getenv("KOTOBA_MASTER_KEY", "").strip()
    if env:
        # Accept base64 or any string; hash to a stable 32 bytes so any input length works.
        try:
            raw = base64.b64decode(env, validate=True)
            if len(raw) >= _KEK_LEN:
                return raw[:_KEK_LEN]
        except Exception:
            pass
        return hashlib.sha256(env.encode()).digest()

    kf = _key_file()
    if kf.exists():
        return _read_key_file(kf)
    if not create:
        return None

    # O_EXCL, not write_bytes: two processes booting together each generated a KEK and the second
    # overwrote the first, orphaning everything the first had encrypted. The loser adopts the winner's.
    # 0600 is set AT creation, so no other local user ever sees the key; on Windows that mode is not a
    # permission, and the ACL written just below closes a window that holds no key yet.
    #
    # O_BINARY is not decoration: os.open is TEXT mode on Windows, which rewrote every 0x0A in the key
    # as 0x0D 0x0A on about one install in eight. The creating call returns the key it holds in memory
    # and every later one reads the file, so exactly the FIRST secret saved is unreadable — the
    # provider key typed at setup, which then had to be typed again with nothing saying why.
    key = os.urandom(_KEK_LEN)
    kf.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(kf), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError:
        return _read_key_file(kf, wait_for_writer=True)  # another process won the race — adopt its key
    except OSError as e:
        raise KeystoreUnavailable(f"cannot create {kf}: {e}") from e
    written = False
    try:
        perms.restrict_file(kf)     # before the key exists, so the window holds nothing
        os.write(fd, key)
        written = True
    finally:
        os.close(fd)
        if not written:
            # An empty key file is never rewritten — _master_key only creates when one is absent — so
            # leaving it behind kills the keystore permanently on a machine that can raise here.
            kf.unlink(missing_ok=True)
    return key


def encrypt(plaintext: str) -> str:
    """Encrypt a secret → opaque blob string safe to store in saved_keys.value."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:   # optional dep: say so, never store the secret in the clear instead
        raise KeystoreUnavailable("cryptography is not installed — cannot store a secret safely") from e

    key = _master_key(create=True)
    if key is None:
        raise KeystoreUnavailable(f"no usable master key ({_key_file()})")
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, (plaintext or "").encode(), None)
    return _PREFIX + base64.b64encode(nonce + ct).decode()


def decrypt(blob: str) -> str | None:
    """Decrypt a blob produced by encrypt(). Returns None on any failure (wrong key, tampering, or a
    legacy plaintext value that was never encrypted — caller can treat None as 'unusable')."""
    if not looks_encrypted(blob):
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = _master_key(create=False)
        if key is None:
            return None
        raw = base64.b64decode(blob[len(_PREFIX):])
        nonce, ct = raw[:12], raw[12:]
        return AESGCM(key).decrypt(nonce, ct, None).decode()
    except Exception:
        return None


def looks_encrypted(blob: str | None) -> bool:
    """STRUCTURAL check — does this have the shape of one of our blobs? Only for telling an
    undecryptable secret (wrong/lost KEK: report it) apart from a legacy plaintext row (use it)."""
    if not blob or not blob.startswith(_PREFIX):
        return False
    try:
        return len(base64.b64decode(blob[len(_PREFIX):], validate=True)) >= _MIN_BLOB
    except Exception:
        return False


def is_ours(blob: str | None) -> bool:
    """AUTHENTICATED check — is this a blob we can actually decrypt? The prefix alone was used to skip
    encryption on save, so a real API key that happened to start with `v1:` was written to the database
    in plaintext AND read back as None: leaked and lost at once. GCM's tag settles it."""
    return bool(blob) and decrypt(blob) is not None


def is_encrypted(blob: str | None) -> bool:
    return looks_encrypted(blob)


def fingerprint(secret: str) -> str:
    """Short, non-reversible id for a key value — for cache keys / logs. NEVER the key itself."""
    return hashlib.sha256((secret or "").encode()).hexdigest()[:12]
