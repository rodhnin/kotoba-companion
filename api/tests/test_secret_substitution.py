"""Secret placeholder substitution. {{secret:NAME}} is the ONE-TIME path only: the model types the
placeholder, the MCP proxy swaps it for the EPHEMERAL value (masked box, never saved, never in the model
context) just before the browser call. Kotoba's OWN saved (.env/DB) credentials do NOT use this proxy —
she accesses those directly (get_credential)."""
from __future__ import annotations

import asyncio
import types

import kotoba.core.ephemeral_secrets as es
import kotoba.core.mcp.client as mc


class _Result:
    def __init__(self, content, is_error=False):
        self.content = content
        self.isError = is_error


class _Group:
    def __init__(self):
        self.seen_args = None

    async def call_tool(self, name, args):
        self.seen_args = args
        return _Result([types.SimpleNamespace(text="ok", type="text", data=None)])


def _proxy(group, ns_name):
    return mc._MCPProxy(types.SimpleNamespace(group=group), ns_name)


def setup_function():
    es._store.clear()


def test_one_time_placeholder_replaced_at_the_browser():
    es.put("sess", "facebook", "hunter2-real")
    group = _Group()
    ctx = types.SimpleNamespace(session_id="sess")
    p = _proxy(group, "browser__browser_type")
    asyncio.run(p.execute({"ref": "e5", "text": "{{secret:facebook}}"}, ctx))
    assert group.seen_args["text"] == "hunter2-real"     # real one-time secret reached the browser


def test_non_placeholder_args_unchanged():
    group = _Group()
    ctx = types.SimpleNamespace(session_id="sess")
    p = _proxy(group, "browser__browser_type")
    asyncio.run(p.execute({"ref": "e5", "text": "hello world"}, ctx))
    assert group.seen_args["text"] == "hello world"


def test_unknown_secret_is_refused_not_typed():
    # Safety net: an unloaded {{secret:...}} must NOT be typed literally into the page — the proxy refuses
    # and returns a hint to call ask_secret first (it never reaches the tool).
    group = _Group()
    ctx = types.SimpleNamespace(session_id="sess")
    p = _proxy(group, "browser__browser_type")
    out = asyncio.run(p.execute({"text": "{{secret:missing}}"}, ctx))
    assert group.seen_args is None and "ask_secret" in out  # refused, not executed with the literal


def test_resolve_helper_uses_ephemeral_only():
    es.put("sess", "fb", "s3cr3t")
    out = mc._resolve_secrets_gated("browser__browser_type", {"text": "{{secret:fb}}", "ref": "e1"}, "sess")
    assert out["text"] == "s3cr3t" and out["ref"] == "e1"


def test_resolve_helper_no_session_leaves_placeholder():
    out = mc._resolve_secrets_gated("browser__browser_type", {"text": "{{secret:fb}}"}, None)
    assert out["text"] == "{{secret:fb}}"  # ephemeral is per-session; no session → no resolve
