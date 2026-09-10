"""Keep a file to the account that created it, on the platform where `chmod` cannot.

On Windows `os.chmod(path, 0o600)` toggles the read-only bit and writes no ACL, so the database, the
keystore's master key and the atomic writer's temporaries had no protection outside `%USERPROFILE%`,
whose inherited ACL is what makes this easy to miss. `icacls` ships with Windows and needs no
dependency; `pywin32` was rejected because it arrives only with the OPTIONAL `mcp` extra, which would
leave the barest install unprotected. Failure is logged, never raised. POSIX is untouched.
"""
from __future__ import annotations

import csv
import logging
import os
import subprocess
import tempfile

log = logging.getLogger("kotoba")

_TIMEOUT = 10.0
# Full control for one account and nobody else: `/inheritance:r` drops the ACEs the parent handed
# down instead of copying them, and `/grant:r` replaces rather than adds to what is already there.
_ACE = "(F)"

# A module flag rather than `os.name` at the call, so BOTH branches are reachable from a test on
# either platform. Reading os.name here left the off-Windows half provable only off Windows, and it
# was the half that failed when the suite finally ran there.
_WINDOWS = os.name == "nt"

_HOLDER: str | None = None


def restrict_file(path) -> None:
    """Make this file readable and writable by the current account alone. A no-op off Windows."""
    if not _WINDOWS:
        return
    _apply(str(path))


def _posix_modes() -> tuple[bool, str]:
    """Read the files the claim is about, rather than answering from the platform.

    Returning a constant here made the report say 0600 over a key sitting at 0644 — the two files can
    predate the code that chmods them, and a probe would only have measured today's umask."""
    from kotoba.core.keystore import _key_file
    from kotoba.paths import DB_NAME, db_dir

    loose = []
    for p in (_key_file(), db_dir() / DB_NAME):
        try:
            mode = p.stat().st_mode & 0o777
        except OSError:
            continue
        if mode & 0o077:
            loose.append(f"{p.name} is {mode:04o}")
    return (False, ", ".join(loose)) if loose else (True, "POSIX modes")


def self_check(where) -> tuple[bool, str]:
    """Grant to a throwaway file next to the real ones and report whether it took.

    A failed grant is logged and never raised, so an install that kept its inherited ACL looked exactly
    like one that got locked down. Nothing could tell the two apart until this asked."""
    if not _WINDOWS:
        return _posix_modes()
    who = _principal()
    if not who:
        return False, "this account has no name to grant to"
    fd, probe = tempfile.mkstemp(prefix=".perms-", dir=str(where))
    os.close(fd)
    try:
        return (True, who) if _apply(probe) else (False, f"icacls refused {who}")
    finally:
        os.unlink(probe)


def _apply(path: str) -> bool:
    who = _principal()
    if not who:
        log.warning("could not restrict %s: this account has no name to grant to", path)
        return False
    argv = ["icacls", path, "/inheritance:r", "/grant:r", f"{who}:{_ACE}"]
    try:
        # icacls answers in the console codepage while text=True decodes with the locale one, and the
        # UnicodeDecodeError that follows is neither an OSError nor a SubprocessError — it escaped a
        # function whose whole promise is that it never raises, from between the two halves of a
        # keystore write.
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=_TIMEOUT,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        log.warning("could not restrict %s to %s: %s", path, who, e)
        return False
    if done.returncode != 0:
        said = (done.stderr or done.stdout or "").strip().splitlines()
        log.warning("could not restrict %s to %s: %s", path, who, said[0] if said else "icacls failed")
        return False
    return True


def _principal() -> str:
    """The account to grant to, as a SID, resolved once per process.

    A name has to be looked up, and the environment named the wrong place to look it up in: on a
    standalone machine that has a workgroup, USERDOMAIN is the WORKGROUP and not the computer, so
    `WORKGROUP\\user` mapped to no account and every grant failed. A SID needs no lookup and is never
    translated, which also settles the localized names of the built-in groups."""
    global _HOLDER
    if not _HOLDER:
        # Falsy, not None: caching "" made one failed lookup permanent, and every write for the rest
        # of the session granted nothing while logging that it had no name to grant to.
        _HOLDER = _my_sid() or os.environ.get("USERNAME", "").strip()
    return _HOLDER


def _my_sid() -> str:
    argv = ["whoami", "/user", "/fo", "csv", "/nh"]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=_TIMEOUT,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return ""
    rows = (done.stdout or "").strip().splitlines() if done.returncode == 0 else []
    fields = next(csv.reader(rows[-1:]), []) if rows else []
    sid = fields[-1].strip() if fields else ""
    return f"*{sid}" if sid.upper().startswith("S-1-") else ""
