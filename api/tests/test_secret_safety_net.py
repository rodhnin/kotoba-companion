"""Safety net: if the model tries to type a {{secret:NAME}} placeholder that was never loaded via
ask_secret, the proxy must NOT type the literal placeholder into the page (that's what leaked
'{{secret:facebook}}' into Facebook's password field). Instead it refuses and tells the model to call
ask_secret(NAME) first."""
from __future__ import annotations

import asyncio
import types

import kotoba.core.ephemeral_secrets as es
import kotoba.core.mcp.client as mc


class _Group:
    def __init__(self):
        self.called = False

    async def call_tool(self, name, args):
        self.called = True
        return types.SimpleNamespace(content=[types.SimpleNamespace(text="ok", type="text", data=None)], isError=False)


def _proxy(group, ns_name):
    return mc._MCPProxy(types.SimpleNamespace(group=group), ns_name)


def setup_function():
    es._store.clear()


def test_unloaded_secret_is_refused_not_typed_literally():
    group = _Group()
    ctx = types.SimpleNamespace(session_id="s1")
    p = _proxy(group, "browser__browser_type")
    out = asyncio.run(p.execute({"ref": "e5", "text": "{{secret:facebook}}"}, ctx))
    assert group.called is False                       # the tool was NOT executed with the literal
    assert out is not None and "ask_secret" in out      # model is told to collect it securely first
    assert "facebook" in out


def test_loaded_secret_passes_through_and_executes():
    es.put("s1", "facebook", "realpass")
    group = _Group()
    ctx = types.SimpleNamespace(session_id="s1")
    p = _proxy(group, "browser__browser_type")
    asyncio.run(p.execute({"ref": "e5", "text": "{{secret:facebook}}"}, ctx))
    assert group.called is True                        # resolved → executed normally


def test_has_unresolved_secret_detector():
    assert mc._has_unresolved_secret({"text": "{{secret:x}}"}) is True
    assert mc._has_unresolved_secret({"text": "hello"}) is False
    assert mc._has_unresolved_secret({"text": "realpass"}) is False
