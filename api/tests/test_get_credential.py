"""get_credential — Kotoba USES her OWN saved credentials without the raw value ever reaching the model.
With a name → loads the value into the ephemeral store and returns the {{secret:NAME}} placeholder (the
MCP proxy substitutes the real value at the browser); without a name → lists saved names only."""
from __future__ import annotations

import asyncio

import kotoba.tools.action.get_credential as gc
from kotoba.core import ephemeral_secrets
from kotoba.tools import ToolContext


class _DB:
    def __init__(self, keys):
        self._keys = keys

    async def get_key(self, name):
        return self._keys.get(name)

    async def list_key_names(self):
        return [{"name": n} for n in self._keys]


def test_work_only():
    from kotoba.tools.registry import COMPANION_TOOLSETS
    assert gc.TOOLSET not in COMPANION_TOOLSETS


def test_get_returns_placeholder_not_value():
    # user credentials are stored namespaced under cred:
    ctx = ToolContext(db=_DB({"cred:my_openai": "sk-123"}), session_id="s1", mode="work")
    out = asyncio.run(gc.execute({"name": "my_openai"}, ctx))
    assert "sk-123" not in out                    # the raw value NEVER reaches the model
    assert "{{secret:my_openai}}" in out          # she gets the placeholder to type instead
    assert ephemeral_secrets.get("s1", "my_openai") == "sk-123"  # value is held for the browser proxy
    ephemeral_secrets.clear("s1")


def test_list_when_no_name():
    ctx = ToolContext(db=_DB({"cred:my_openai": "sk-1", "cred:github": "ghp_2"}), session_id="s1", mode="work")
    out = asyncio.run(gc.execute({}, ctx))
    assert "my_openai" in out and "github" in out    # names shown WITHOUT the cred: prefix
    assert "cred:" not in out                        # the internal prefix is not leaked to the model
    assert "sk-1" not in out and "ghp_2" not in out  # listing shows names, not values


def test_missing_name_reports_not_found():
    ctx = ToolContext(db=_DB({"cred:a": "1"}), session_id="s1", mode="work")
    out = asyncio.run(gc.execute({"name": "nope"}, ctx))
    assert "nope" in out and "1" not in out


def test_cannot_list_or_load_system_keys():
    """SECURITY: get_credential must NEVER expose provider API keys (llm:*) or MCP tokens (mcp:*/mcp_oauth:*)
    — those are exfiltratable via the browser-substituted placeholder. Only cred:* user credentials."""
    db = _DB({
        "cred:my_openai": "sk-user",
        "llm:xai:api_key": "xai-SYSTEM-SECRET",
        "mcp:github": "ghp_SYSTEM",
        "mcp_oauth:notion": "oauth-SYSTEM",
    })
    # listing shows ONLY the user credential, none of the system keys
    out = asyncio.run(gc.execute({}, ToolContext(db=db, session_id="s2", mode="work")))
    assert "my_openai" in out
    assert "xai" not in out and "github" not in out and "notion" not in out and "llm:" not in out

    # trying to load a system key by name resolves to cred:<name>, which doesn't exist → not found, no leak
    for target in ("llm:xai:api_key", "mcp:github", "mcp_oauth:notion"):
        out = asyncio.run(gc.execute({"name": target}, ToolContext(db=db, session_id="s2", mode="work")))
        assert "SYSTEM" not in out and "xai-" not in out and "ghp_" not in out
        assert ephemeral_secrets.get("s2", target) is None  # never loaded into the ephemeral store
    ephemeral_secrets.clear("s2")
