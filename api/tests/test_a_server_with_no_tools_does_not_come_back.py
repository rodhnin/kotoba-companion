"""Boot must judge a saved MCP server by the rule the paths a person drives already use.

They refuse a server that connects and then offers nothing, and they disconnect it. Boot did not
look: it kept the session for the life of the process, and did it again on every restart. The config
entry is deliberately left alone — an empty tool list can be a server having a bad day, and deleting
somebody's config over one is the worse harm.
"""
from __future__ import annotations

import asyncio

import pytest

from kotoba.core.engine import _connect_saved
from kotoba.core.mcp.client import _AuthRequired

_SAVED = {"leftovers": {"url": "https://example.invalid/mcp"}}


class _Manager:
    def __init__(self, answer):
        self.answer = answer
        self.connected: list[str] = []
        self.disconnected: list[str] = []

    async def connect(self, name, cfg):
        self.connected.append(name)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    async def disconnect(self, name):
        self.disconnected.append(name)


@pytest.fixture
def recorded(monkeypatch):
    """pending.record writes a file under the home; a list says the same thing and touches nothing."""
    from kotoba.core.mcp import pending

    calls: list[str] = []
    monkeypatch.setattr(pending, "record", lambda name, *a, **kw: calls.append(name))
    return calls


def _boot(answer):
    manager = _Manager(answer)
    asyncio.run(_connect_saved(manager, _SAVED, _SAVED))
    return manager


def test_a_server_that_offers_nothing_is_dropped(recorded):
    manager = _boot([])
    assert manager.connected == ["leftovers"]
    assert manager.disconnected == ["leftovers"]
    assert recorded == [], "a server with no tools is not a server waiting on a credential"


def test_a_server_that_offers_tools_is_kept(recorded):
    manager = _boot(["leftovers__search"])
    assert manager.disconnected == []


def test_a_server_that_wants_a_credential_is_left_pending(recorded):
    manager = _boot(_AuthRequired("leftovers", "needs a token", "token", {}))
    assert recorded == ["leftovers"]
    assert manager.disconnected == [], "pending is not the same as connected-and-useless"


def test_a_server_that_will_not_connect_is_only_skipped(recorded):
    manager = _boot(RuntimeError("refused"))
    assert manager.disconnected == [], "nothing was connected, so there is nothing to disconnect"


def test_a_disconnect_that_fails_does_not_stop_the_rest(recorded):
    """Boot walks every saved server. A wedged one must not cost the others their tools."""
    class _Stubborn(_Manager):
        async def disconnect(self, name):
            raise RuntimeError("wedged")

    manager = _Stubborn([])
    asyncio.run(_connect_saved(manager, {**_SAVED, "second": {"url": "https://other.invalid/mcp"}},
                               {**_SAVED, "second": {"url": "https://other.invalid/mcp"}}))
    assert manager.connected == ["leftovers", "second"]


def test_the_config_entry_survives(recorded, monkeypatch):
    """The one thing boot may NOT do is edit the user's file over a bad day at the far end."""
    from kotoba.core.mcp import config

    monkeypatch.setattr(config, "remove_server",
                        lambda name: pytest.fail("boot deleted a saved server"))
    monkeypatch.setattr(config, "save_server",
                        lambda name, cfg: pytest.fail("boot rewrote a saved server"))
    _boot([])


def test_without_the_sdk_nothing_is_judged_at_all(recorded, monkeypatch):
    """Every connect answers an empty list when the SDK is absent, so each saved server would read as
    one that offered nothing and be "dropped" on the way past."""
    from kotoba.core.mcp import client

    monkeypatch.setattr(client, "_MCP_AVAILABLE", False)
    manager = _boot([])
    assert manager.connected == [] and manager.disconnected == []
