"""The per-session sandbox lives across turns, so a file made in one turn is still there in the next
("edit the HTML you just made").

That lifetime is the whole point of the module, and the rule that protects it is narrow: a turn's
`ctx.cleanup()` must NOT kill the sandbox — only `release()`, the disconnect grace and the idle
reaper may. A context with no session_id is the exception: it owns a one-shot sandbox and tears it
down, because nothing will ever ask for it again.

The tests drive a fake backend, so no container or remote machine is started."""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.session_sandbox as ss
from kotoba.tools import ToolContext


class _FakeSB:
    def __init__(self):
        self.killed = False
        self.writes = []
        self._lifetime = 300
        self.timeouts = []

    async def start(self):
        pass

    async def write(self, path, data):
        self.writes.append((path, data))

    async def set_timeout(self, seconds):
        self.timeouts.append(seconds)

    async def kill(self):
        self.killed = True


@pytest.fixture
def fake_backend(monkeypatch):
    made = []

    def _create(workdir):
        sb = _FakeSB()
        made.append(sb)
        return sb

    monkeypatch.setattr("kotoba.core.sandbox.create_sandbox", _create, raising=False)
    # session_sandbox imports create_sandbox lazily from core.sandbox → patch there.
    import kotoba.core.sandbox as cs
    monkeypatch.setattr(cs, "create_sandbox", _create, raising=False)
    ss._live.clear()
    ss._active.clear()
    ss._pending_close.clear()
    yield made
    ss._live.clear()
    ss._active.clear()
    ss._pending_close.clear()


def test_acquire_reuses_same_sandbox_across_turns(fake_backend):
    """Two acquires for one session hand back the SAME live sandbox, and the backend is asked to
    create exactly one."""
    async def go():
        a = await ss.acquire("s1", "/tmp/x")
        b = await ss.acquire("s1", "/tmp/x")
        return a, b
    a, b = asyncio.run(go())
    assert a is b and a is not None
    assert len(fake_backend) == 1


def test_release_kills_and_forgets(fake_backend):
    async def go():
        sb = await ss.acquire("s2", "/tmp/x")
        await ss.release("s2")
        return sb
    sb = asyncio.run(go())
    assert sb.killed is True
    assert "s2" not in ss._live


def test_ctx_cleanup_keeps_session_sandbox_alive(fake_backend):
    """`ctx.cleanup()` is the end of a turn, not the end of the session: the sandbox stays live and
    registered, ready for the next turn to reuse."""
    async def go():
        ctx = ToolContext(db=None, session_id="s3", workdir="/tmp/x", mode="work")
        sb = await ctx.ensure_sandbox()
        await ctx.cleanup()
        return sb
    sb = asyncio.run(go())
    assert sb.killed is False
    assert "s3" in ss._live


def test_sessionless_ctx_sandbox_is_killed_on_cleanup(fake_backend):
    """With no session_id — a direct API call or a test — the context owns a one-shot sandbox and
    tears it down at cleanup: nothing can ask for it again, so keeping it alive only leaks."""
    async def go():
        ctx = ToolContext(db=None, session_id=None, workdir="/tmp/x", mode="work")
        sb = await ctx.ensure_sandbox()
        await ctx.cleanup()
        return sb
    sb = asyncio.run(go())
    assert sb is not None and sb.killed is True


def test_active_session_is_not_reaped(fake_backend):
    """An open channel outranks the idle clock. `note_connect` says the user is still on the call,
    so the reaper must leave the sandbox even when `last_used` is far past the threshold — a long
    silence in a live conversation is not an abandoned session."""
    async def go():
        await ss.acquire("act1", "/tmp/x")
        ss.note_connect("act1")
        ss._live["act1"]["last_used"] -= 100000   # far past any idle threshold
        reaped = await ss.reap_idle(900)
        return reaped
    reaped = asyncio.run(go())
    assert reaped == 0
    assert "act1" in ss._live


def test_inactive_session_is_reaped(fake_backend):
    async def go():
        await ss.acquire("idle1", "/tmp/x")
        ss._live["idle1"]["last_used"] -= 100000
        return await ss.reap_idle(900)
    reaped = asyncio.run(go())
    assert reaped == 1
    assert "idle1" not in ss._live


def test_disconnect_releases_after_grace(fake_backend):
    """A dropped channel does not free the sandbox at once — it starts a grace timer, and only when
    that elapses with no reconnect is the sandbox killed and forgotten. The grace here is
    milliseconds so the test can wait it out; production uses a much longer one."""
    async def go():
        sb = await ss.acquire("hang1", "/tmp/x")
        ss.note_connect("hang1")
        ss.note_disconnect("hang1", grace=0.02)
        await asyncio.sleep(0.06)
        return sb
    sb = asyncio.run(go())
    assert sb.killed is True
    assert "hang1" not in ss._live


def test_reconnect_within_grace_cancels_teardown(fake_backend):
    """The other half of the grace: a reconnect inside the window cancels the pending teardown. A
    network blip must not cost the user the files they were working on."""
    async def go():
        sb = await ss.acquire("recon1", "/tmp/x")
        ss.note_connect("recon1")
        ss.note_disconnect("recon1", grace=0.05)
        await asyncio.sleep(0.01)
        ss.note_connect("recon1")
        await asyncio.sleep(0.07)
        return sb
    sb = asyncio.run(go())
    assert sb.killed is False
    assert "recon1" in ss._live


def test_keepalive_extends_active_sandbox_ttl(fake_backend):
    """While a call is live the sandbox's own TTL is pushed back out, so a long conversation never
    loses its workspace to the backend's expiry."""
    async def go():
        sb = await ss.acquire("ka1", "/tmp/x")
        sb.timeouts.clear()   # acquire() may already have set one
        ss.note_connect("ka1")
        await ss.keepalive_active()
        return sb
    sb = asyncio.run(go())
    assert sb.timeouts == [300]


def test_rehydrate_writes_stashed_text_files(fake_backend, monkeypatch):
    """A fresh sandbox is rehydrated from the session's stashed TEXT files, so the work continues
    where it left off. Stashed images are not written: they are what she looked at, not source the
    sandbox can act on."""
    from kotoba.core import file_store
    file_store.clear("s4")
    file_store.stash("s4", "index.html", "<h1>hi</h1>")
    file_store.stash_image("s4", "shot.png", "data:image/png;base64,AAA")

    async def go():
        return await ss.acquire("s4", "/tmp/x")
    sb = asyncio.run(go())
    paths = [p for p, _ in sb.writes]
    assert "index.html" in paths and "shot.png" not in paths
