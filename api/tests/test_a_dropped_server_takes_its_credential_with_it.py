"""A token must not outlive the server it was stored for — and only the one this call stored.

Both install paths ask the user for a key, store it, and only then find out whether the server offers
anything. When it offers nothing she says "I removed it", and the server was gone while the secret
stayed, filed under a name that no longer appears anywhere on disk.

Taking back a key that was ALREADY there is the opposite mistake: a name can be reused by a server
the registry resolved to the same word, and the credential would belong to whatever saved it.
"""
from __future__ import annotations

import asyncio

import pytest

from kotoba.core.mcp import auth_flow


class _Db:
    def __init__(self):
        self.deleted: list[str] = []

    async def delete_key(self, name):
        self.deleted.append(name)


class _Mcp:
    def __init__(self):
        self.disconnected: list[str] = []

    async def disconnect(self, name):
        self.disconnected.append(name)


class _Ctx:
    def __init__(self, db=None):
        self.session_id = "s-1"
        self.db = db
        self.mcp = _Mcp()


@pytest.fixture(autouse=True)
def _no_disk(monkeypatch):
    from kotoba.core.mcp import config, pending

    monkeypatch.setattr(config, "save_server", lambda name, cfg: None)
    monkeypatch.setattr(pending, "clear", lambda name: None)


def _finish(ctx, tools, *, stored_token=True):
    return asyncio.run(auth_flow.finish_connect(ctx, "leftovers", tools, {"url": "https://x.invalid"},
                                                stored_token=stored_token))


def test_a_server_with_no_tools_leaves_no_key_behind():
    ctx = _Ctx(_Db())
    reply = _finish(ctx, [])
    assert "didn't offer any usable tools" in reply
    assert ctx.mcp.disconnected == ["leftovers"]
    assert ctx.db.deleted == ["mcp:leftovers", "mcp_oauth:leftovers"]


def test_a_key_this_call_did_not_store_is_left_alone():
    """The install found the server already had one. Dropping it would rob whatever put it there."""
    ctx = _Ctx(_Db())
    reply = _finish(ctx, [], stored_token=False)
    assert "didn't offer any usable tools" in reply
    assert ctx.mcp.disconnected == ["leftovers"]
    assert ctx.db.deleted == []


def test_a_server_that_works_keeps_its_key():
    ctx = _Ctx(_Db())
    reply = _finish(ctx, ["leftovers__search"])
    assert "Connected" in reply
    assert ctx.db.deleted == []


def test_no_database_is_not_a_crash():
    """The tool paths run with whatever ctx they are given, and one without a db still has to answer."""
    reply = _finish(_Ctx(None), [])
    assert "didn't offer any usable tools" in reply


def test_a_delete_that_fails_still_answers():
    class _Broken(_Db):
        async def delete_key(self, name):
            raise RuntimeError("keystore locked")

    reply = _finish(_Ctx(_Broken()), [])
    assert "didn't offer any usable tools" in reply
