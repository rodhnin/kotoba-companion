"""A session's sandbox is recycled on a clock only when its backend actually runs one.

`acquire()` read the backend's TTL with a 300-second default, but no shipped backend ever defines that
attribute, so the fallback was the only value ever returned, and the recreate logic (which fires 15s
early) killed and rebuilt every session's sandbox every 285 seconds — for backends whose own code says
they do not expire. The push-back-the-TTL path and the keepalive check were both dead for the same
reason. Under `local` the recreate was invisible but not free: the old sandbox was merely forgotten and
the new one rehydrated the workdir from the session's file stash. Under `docker` the same path ran a
force-remove on a live container in the middle of a job.
"""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.session_sandbox as ss


class _NoTTL:
    """What both sandbox backends look like from here: no TTL, nothing to push back out."""

    def __init__(self):
        self.killed = False

    async def start(self):
        pass

    async def kill(self):
        self.killed = True


class _WithTTL(_NoTTL):
    """A backend that really does expire on a clock (what `_lifetime`/`set_timeout` were written for)."""

    _lifetime = 60

    def __init__(self):
        super().__init__()
        self.timeouts = []

    async def set_timeout(self, seconds):
        self.timeouts.append(seconds)


@pytest.fixture
def backend(monkeypatch):
    made = {"cls": _NoTTL, "all": []}

    def _create(workdir):
        sb = made["cls"]()
        made["all"].append(sb)
        return sb

    import kotoba.core.sandbox as cs
    monkeypatch.setattr(cs, "create_sandbox", _create, raising=False)
    monkeypatch.setattr(ss, "_rehydrate", lambda *a, **k: asyncio.sleep(0))
    ss._live.clear()
    ss._active.clear()
    yield made
    ss._live.clear()
    ss._active.clear()


def test_the_shipped_backends_declare_no_ttl(tmp_path):
    from kotoba.core.sandbox.docker import DockerSandbox
    from kotoba.core.sandbox.local import LocalSandbox

    for sb in (LocalSandbox(tmp_path), DockerSandbox(tmp_path)):
        assert not hasattr(sb, "_lifetime"), f"{type(sb).__name__} declares a TTL it does not run"
        assert not hasattr(sb, "set_timeout"), f"{type(sb).__name__} can push out a TTL it does not have"


def test_a_ttl_less_sandbox_is_never_recycled_on_a_clock(backend, tmp_path):
    async def go():
        first = await ss.acquire("s-nottl", tmp_path)
        # Ten minutes later — twice the TTL the code used to invent for a backend that has none.
        ss._live["s-nottl"]["created"] -= 600
        ss._live["s-nottl"]["last_used"] -= 600
        return first, await ss.acquire("s-nottl", tmp_path)

    first, again = asyncio.run(go())
    assert again is first, "a backend with no TTL was recycled on a TTL"
    assert first.killed is False, "a live sandbox (a docker container, mid-job) was torn down"
    assert len(backend["all"]) == 1


def test_a_backend_with_a_real_ttl_is_still_recreated_before_it_expires(backend, tmp_path):
    backend["cls"] = _WithTTL

    async def go():
        first = await ss.acquire("s-ttl", tmp_path)
        ss._live["s-ttl"]["created"] -= 120  # past its 60s lifetime
        return first, await ss.acquire("s-ttl", tmp_path)

    first, again = asyncio.run(go())
    assert again is not first, "an expiring backend must not hand back a dead sandbox"
    assert first.killed is True
    assert again.timeouts == [] or again.timeouts == [60]


def test_keepalive_never_pushes_out_a_ttl_the_backend_does_not_have(backend, tmp_path):
    class _Odd(_NoTTL):
        def __init__(self):
            super().__init__()
            self.timeouts = []

        async def set_timeout(self, seconds):
            self.timeouts.append(seconds)

    backend["cls"] = _Odd

    async def go():
        sb = await ss.acquire("s-odd", tmp_path)
        ss.note_connect("s-odd")
        await ss.keepalive_active()
        return sb

    sb = asyncio.run(go())
    assert sb.timeouts == [], "asked a TTL-less backend to renew a TTL (set_timeout(None))"
