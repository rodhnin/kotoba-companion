"""One live execution sandbox per session, reused across turns.

Each work turn used to spin up its own and tear it down in the loop's finally, so "edit the HTML you
just made" failed and the model regenerated from scratch. acquire() reuses the live one, the per-turn
cleanup no longer kills it, and a fresh sandbox is rehydrated from file_store so edits survive.

The clock that recycles a sandbox belongs to the BACKEND. The TTL used to be read as
`getattr(sb, "_lifetime", 300)`, and `_lifetime` left with E2B, so 300 was the only value it ever
returned: every session's sandbox was killed and rebuilt every 285 seconds — under `docker`, a
`docker rm -f` on a live container mid-job. A backend declaring no lifetime never expires here."""
from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger("kotoba")

# session_id -> {"sb": Sandbox, "created": float, "last_used": float, "lifetime": float | None}
_live: dict[str, dict] = {}
_locks: dict[str, asyncio.Lock] = {}

# Sessions whose voice/SSE channel is OPEN: kept alive + never reaped; on close, torn down after a grace
# so a brief SSE reconnect doesn't kill the sandbox.
_active: set[str] = set()
_pending_close: dict[str, asyncio.Task] = {}

# Recreate a bit BEFORE the sandbox's own lifetime elapses, to avoid handing back a just-expired one.
_EXPIRY_MARGIN = 15.0
# Long enough to ride out an EventSource auto-reconnect; short enough to free it after a real hang-up.
_CLOSE_GRACE = 45.0


def _lock(session_id: str) -> asyncio.Lock:
    lk = _locks.get(session_id)
    if lk is None:
        lk = _locks[session_id] = asyncio.Lock()
    return lk


def _declared_lifetime(sb) -> float | None:
    """The TTL this backend expires on, or None when it has none (local, docker: nothing but us kills it)."""
    try:
        value = float(getattr(sb, "_lifetime", None))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _expired(entry: dict) -> bool:
    lifetime = entry.get("lifetime")
    if not lifetime:
        return False
    return (time.monotonic() - entry["created"]) >= max(0, lifetime - _EXPIRY_MARGIN)


async def _rehydrate(sb, session_id: str, workdir=None) -> None:
    """Write the session's known text files back into a fresh sandbox so cross-turn edits work even if the
    sandbox had to be recreated. Best-effort; images/binaries are skipped.

    Skipped when the workdir IS the file library: the durable files are already on disk, so writing the
    (possibly truncated) stash back over them would destroy data."""
    try:
        from pathlib import Path
        if workdir is not None:
            from kotoba.core import file_library
            if str(Path(workdir).resolve()) == str(file_library.library_dir().resolve()):
                return

        from kotoba.core import file_store

        for path, content in file_store.text_items(session_id):
            try:
                await sb.write(path, content.encode("utf-8", errors="replace"))
            except Exception:
                pass
    except Exception:
        pass


async def acquire(session_id: str | None, workdir):
    """Return a live sandbox for this session (reuse if alive, else create + rehydrate). None if no
    backend is available. No session_id (curl/tests) → None, so the caller falls back to a ctx-owned one."""
    if not session_id:
        return None
    from kotoba.core.sandbox import backend_name

    backend = backend_name()
    async with _lock(session_id):
        entry = _live.get(session_id)
        # Reuse only for the SAME workdir AND the SAME backend: the backend is read per turn, so flipping
        # Settings to docker/none rebuilt the ApprovalGate on the container assumption while this kept
        # handing back the live LocalSandbox — the user "enabled isolation" and got host commands instead.
        if (entry is not None and not _expired(entry)
                and entry.get("workdir") == str(workdir) and entry.get("backend") == backend):
            entry["last_used"] = time.monotonic()
            # Keepalive: push the TTL back out so an active session doesn't lose its sandbox mid-task.
            sb = entry["sb"]
            if entry.get("lifetime") and hasattr(sb, "set_timeout"):
                try:
                    await sb.set_timeout(entry["lifetime"])
                    entry["created"] = time.monotonic()
                except Exception:
                    pass
            return sb
        if entry is not None:
            _live.pop(session_id, None)
            try:
                await entry["sb"].kill()
            except Exception:
                pass

        from kotoba.core.sandbox import create_sandbox

        try:
            sb = create_sandbox(workdir)
            if sb is None:
                return None
            await sb.start()
        except Exception:
            return None

        lifetime = _declared_lifetime(sb)
        _live[session_id] = {"sb": sb, "workdir": str(workdir), "backend": backend,
                             "created": time.monotonic(),
                             "last_used": time.monotonic(), "lifetime": lifetime}
        await _rehydrate(sb, session_id, workdir)
        return sb


async def release(session_id: str | None) -> None:
    """Kill + forget a session's local sandbox (session end / channel closed)."""
    if not session_id:
        return
    entry = _live.pop(session_id, None)
    if entry is not None:
        try:
            await entry["sb"].kill()
        except Exception:
            pass


async def reap_idle(max_idle_seconds: float = 900.0) -> int:
    """Kill sandboxes unused for longer than max_idle (default 15 min). Returns how many were reaped.
    Sessions whose voice/SSE channel is still OPEN are never reaped (they're kept alive by keepalive)."""
    now = time.monotonic()
    stale = [sid for sid, e in _live.items()
             if sid not in _active and now - e["last_used"] > max_idle_seconds]
    for sid in stale:
        await release(sid)
    return len(stale)


def is_active(session_id: str) -> bool:
    return session_id in _active


async def keepalive_active() -> None:
    """Push the TTL of every active session's sandbox back out (for backends that declare one and expose
    set_timeout) so a long voice call never loses its sandbox mid-conversation. A true no-op for the
    local/docker backends, which don't expire on a TTL. Best-effort; called periodically."""
    for sid in list(_active):
        entry = _live.get(sid)
        if entry is None:
            continue
        sb = entry["sb"]
        if entry.get("lifetime") and hasattr(sb, "set_timeout"):
            try:
                await sb.set_timeout(entry["lifetime"])
                entry["created"] = time.monotonic()
                entry["last_used"] = time.monotonic()
            except Exception:
                pass


def note_connect(session_id: str | None) -> None:
    """The SSE/voice channel opened for this session: mark it active and cancel any pending teardown
    (handles an EventSource auto-reconnect — don't kill a sandbox the user is still using)."""
    if not session_id:
        return
    _active.add(session_id)
    task = _pending_close.pop(session_id, None)
    if task is not None:
        task.cancel()


def note_disconnect(session_id: str | None, grace: float = _CLOSE_GRACE) -> None:
    """The SSE/voice channel closed: schedule a deferred teardown. If the channel reopens within `grace`
    (reconnect), note_connect cancels it; otherwise the sandbox is killed."""
    if not session_id:
        return
    _active.discard(session_id)
    old = _pending_close.pop(session_id, None)
    if old is not None:
        old.cancel()

    async def _close_later():
        try:
            await asyncio.sleep(grace)
            await release(session_id)
            log.info("session %s sandbox released (channel closed)", session_id)
        except asyncio.CancelledError:
            pass
        finally:
            _pending_close.pop(session_id, None)

    try:
        _pending_close[session_id] = asyncio.create_task(_close_later())
    except RuntimeError:
        pass  # no running loop (shutdown) → nothing to schedule
