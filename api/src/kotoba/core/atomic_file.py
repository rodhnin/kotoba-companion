"""Publish a small file so nothing — another thread or another process — can see or destroy a partial one.

Every shared store under ~/.kotoba used to write to a FIXED `.tmp` name and then `replace()`. Two
writers interleave: the visual-memory index lost 72% of its entries and stranded 177 images, and the
file-library index dropped 79% of its updates in silence, because its writer swallows exceptions.

THREADS COUNT, NOT JUST PROCESSES: tools run through `asyncio.to_thread`, so two turns plus a panel
click are three writers in ONE process. Re-entrancy must be tracked per THREAD — a process-global
version let a second thread conclude "already ours" and skip the lock, keeping 10 of 160 entries.
WINDOWS has no flock, and a bare `import fcntl` here stopped every entry point on that platform."""
from __future__ import annotations

import errno
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from kotoba.core import perms

try:
    import fcntl
except ModuleNotFoundError:
    fcntl = None
try:
    import msvcrt
except ModuleNotFoundError:
    msvcrt = None

log = logging.getLogger("kotoba")

_MAX_WAIT = 15.0   # a stuck holder must not freeze a request forever — fail loudly instead
_SLOW_WAIT = 1.0   # say something before a user notices the pause
_RANGE = 1         # bytes msvcrt locks; the sentinel is empty, so one byte at offset 0 is the file
_REPLACE_WAIT = 5.0    # a reader holding the destination on Windows; well under the lock's own deadline
_REPLACE_PAUSE = 0.02
# A module flag rather than os.name at the call, so the retry is provable from either platform.
_WINDOWS = os.name == "nt"

_local = threading.local()


def _depths() -> dict[str, int]:
    d = getattr(_local, "depths", None)
    if d is None:
        d = _local.depths = {}
    return d


@contextmanager
def exclusive(path: Path):
    """Hold an exclusive lock for `path` while a read-modify-write runs.

    The lock lives in a sibling `.lock` file, never the target: locking the target itself would be lost
    the moment `replace()` swaps the inode out from under it. flock belongs to the open file
    DESCRIPTION, so two threads opening it separately serialize against each other exactly like two
    processes — which is what we want. Re-entrancy is tracked per thread, because these guards nest (a
    fact write re-reads and retires older facts under the same lock)."""
    key = os.path.realpath(str(path))   # so `./x` and `/abs/x` cannot deadlock against each other
    depths = _depths()
    if depths.get(key):
        depths[key] += 1
        try:
            yield                       # already ours, on THIS thread — nesting, not contention
            return
        finally:
            depths[key] -= 1

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p.with_name(p.name + ".lock")), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _acquire(fd, key)
        depths[key] = 1                 # AFTER acquiring: a failed attempt must not look like ownership
        try:
            yield
        finally:
            depths.pop(key, None)
            try:
                _unlock(fd)
            except OSError:
                pass
    finally:
        os.close(fd)


def _lock(fd: int) -> None:
    """Take the exclusive lock, or raise BlockingIOError while another holder has it.

    Windows ships no flock; `msvcrt.locking` is the equivalent, and its three differences cost nothing
    GIVEN WHAT IS LOCKED. Mandatory rather than advisory — harmless, since the lock lives in a
    dedicated `.lock` sibling nothing opens for its contents. It locks a byte range at the CURRENT
    position, pinned by seeking to 0 first. And it has no shared mode, which loses nothing. What
    carries over: a Windows byte range belongs to the HANDLE, so two threads contend as two processes.

    Anything other than a locking violation degrades as flock's OSError does: no lock, previous
    behaviour, rather than fifteen seconds of retries against a filesystem that will never answer."""
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    if msvcrt is None:
        raise OSError("this platform offers no file locking")
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, _RANGE)
    except OSError as e:
        if e.errno in (errno.EACCES, errno.EDEADLOCK):
            raise BlockingIOError(e.errno, "another holder has the lock") from e
        raise


def _unlock(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    if msvcrt is None:
        return
    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_UNLCK, _RANGE)


def _acquire(fd: int, key: str) -> None:
    """Non-blocking retries with a deadline. A blocking flock has no timeout and no diagnostic, and two
    call sites take it straight from the event loop — one stuck holder would freeze the whole server."""
    deadline = time.monotonic() + _MAX_WAIT
    warned = False
    while True:
        try:
            _lock(fd)
            return
        except BlockingIOError:
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError(f"could not lock {key} after {_MAX_WAIT:g}s")
            if not warned and now > deadline - _MAX_WAIT + _SLOW_WAIT:
                log.warning("waiting for the lock on %s — another writer is holding it", key)
                warned = True
            time.sleep(0.01)
        except OSError:
            return  # no flock support (some network filesystems) → degrade to the previous behaviour


def publish(tmp: str, target: str) -> None:
    """Rename over the target, which on Windows is atomic but REFUSABLE. Public: three callers need it.

    MoveFileEx answers ACCESS_DENIED while anyone else holds the destination open, and Python's own
    `open()` asks for no delete-sharing, so an ordinary reader in another process is enough; the
    scanner that just looked at the temporary does it too. POSIX rename has no such window, so there
    the error is the caller's and is raised at once. Retried rather than raised because the holder is
    transient and the alternative is losing a write that reported success."""
    deadline = time.monotonic() + _REPLACE_WAIT
    while True:
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if not _WINDOWS or time.monotonic() >= deadline:
                raise
            time.sleep(_REPLACE_PAUSE)


def write_text(path: Path, text: str) -> None:
    """Write + fsync to a UNIQUE temporary, then rename over `path`.

    mkstemp, not a pid-suffixed name: two threads share a pid, so a per-process name reproduced the very
    FileNotFoundError this module exists to remove."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        perms.restrict_file(tmp)    # the chmod above writes no ACL on Windows; this does
        publish(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)              # never let cleanup mask the real failure
        except OSError:
            pass
        raise
