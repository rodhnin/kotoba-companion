"""CRITICAL: secret resolution + the safety-net must work for NESTED args, not just top-level strings.

browser_fill_form passes fields as a LIST of DICTS: {"fields": [{"name":..,"ref":..,"value":"{{secret:X}}"}]}.
The old flat implementation only looked at top-level string values, so a {{secret:..}} buried in fields[i]
.value was (a) never substituted with the real password AND (b) never caught by the safety-net → the literal
placeholder '{{secret:facebook}}' got typed into Facebook's password box. Both must recurse into lists/dicts.
"""
from __future__ import annotations

import asyncio
import types

import kotoba.core.ephemeral_secrets as es
import kotoba.core.mcp.client as mc


def setup_function():
    es._store.clear()


def test_resolve_recurses_into_fill_form_fields():
    es.put("sess", "facebook", "hunter2-real")
    args = {"fields": [
        {"name": "Email", "ref": "e3", "value": "jordan@example.com"},
        {"name": "Password", "ref": "e5", "value": "{{secret:facebook}}"},
    ]}
    # The gated resolver is what production calls; the ungated one it replaced is gone.
    out = mc._resolve_secrets_gated("browser__browser_fill_form", args, "sess")
    assert out["fields"][1]["value"] == "hunter2-real"   # the real secret reached the nested field
    assert out["fields"][0]["value"] == "jordan@example.com"  # other fields untouched


def test_has_unresolved_secret_detects_nested():
    # Unloaded placeholder buried in a list-of-dicts MUST be detected (else it leaks into the page).
    args = {"fields": [{"name": "Password", "ref": "e5", "value": "{{secret:facebook}}"}]}
    assert mc._has_unresolved_secret(args) is True
    # A fully-resolved / plain nested structure is fine.
    assert mc._has_unresolved_secret({"fields": [{"value": "plain text"}]}) is False


def test_safety_net_refuses_nested_unloaded_secret():
    # End-to-end via the proxy: an unloaded nested secret is refused, never sent to browser_fill_form.
    class _Group:
        def __init__(self):
            self.seen = None

        async def call_tool(self, name, args):
            self.seen = args
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(text="ok", type="text", data=None)], isError=False
            )

    group = _Group()
    ctx = types.SimpleNamespace(session_id="sess")
    p = mc._MCPProxy(types.SimpleNamespace(group=group), "browser__browser_fill_form")
    out = asyncio.run(p.execute({"fields": [{"ref": "e5", "value": "{{secret:facebook}}"}]}, ctx))
    assert group.seen is None                 # never executed with the literal placeholder
    assert "ask_secret" in out                # told to collect it securely first


def test_resolve_recurses_and_substitutes_via_proxy():
    es.put("sess", "facebook", "realpw")

    class _Group:
        def __init__(self):
            self.seen = None

        async def call_tool(self, name, args):
            self.seen = args
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(text="ok", type="text", data=None)], isError=False
            )

    group = _Group()
    ctx = types.SimpleNamespace(session_id="sess")
    p = mc._MCPProxy(types.SimpleNamespace(group=group), "browser__browser_fill_form")
    asyncio.run(p.execute({"fields": [{"ref": "e5", "value": "{{secret:facebook}}"}]}, ctx))
    assert group.seen["fields"][0]["value"] == "realpw"  # real value reached the browser, in the nested field
