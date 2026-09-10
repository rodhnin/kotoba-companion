"""MCP config secret hygiene (standard mcp.json convention): a server's headers/env hold ${VAR} references;
the real secret lives in the environment (api/.env), never inline in mcp.yaml. Expansion happens only when
building connection params (_expand_env) — the on-disk config stays a raw ${VAR}."""
from __future__ import annotations

from kotoba.core.mcp.client import _expand_env


def test_expands_env_refs_in_headers(monkeypatch):
    monkeypatch.setenv("GITHUB_MCP_TOKEN", "ghp_secret123")
    cfg = {"url": "https://api/mcp", "headers": {"Authorization": "Bearer ${GITHUB_MCP_TOKEN}"}}
    out = _expand_env(cfg)
    assert out["headers"]["Authorization"] == "Bearer ghp_secret123"
    assert cfg["headers"]["Authorization"] == "Bearer ${GITHUB_MCP_TOKEN}"  # original NOT mutated


def test_unset_var_left_literal(monkeypatch):
    monkeypatch.delenv("NOPE_TOKEN", raising=False)
    out = _expand_env({"env": {"X": "${NOPE_TOKEN}"}})
    assert out["env"]["X"] == "${NOPE_TOKEN}"  # visible, not a silent empty


def test_recurses_and_passes_through_non_strings(monkeypatch):
    monkeypatch.setenv("A", "1")
    out = _expand_env({"args": ["--k", "${A}"], "n": 5, "b": True})
    assert out["args"] == ["--k", "1"] and out["n"] == 5 and out["b"] is True


def test_the_remote_preflight_gets_the_expanded_headers_too(monkeypatch):
    """Expansion must happen before the preflight, or the documented convention cannot work at all.

    The raw config was passed to the SSRF guard and to the preflight, and only the connection-params
    step expanded, so a remote server got the literal unexpanded token, returned a bare 401, and was
    misclassified as needing a token — recorded pending and shown as "needs connection" on every boot,
    while the token was sitting in the environment the whole time. The person is sent to fix the one
    thing that was never wrong.

    The config the exception carries stays raw: it is what gets recorded, and a recorded secret is the
    defect config.strip_secrets exists to prevent."""
    import asyncio

    import kotoba.core.mcp.client as mc

    monkeypatch.setenv("MY_MCP_TOKEN", "real-token-123")
    monkeypatch.setenv("KOTOBA_ALLOW_LOCAL_MCP", "1")   # skip DNS in the sandbox, not the point here
    seen: dict = {}

    async def _fake_preflight(url, headers):
        seen["url"], seen["headers"] = url, headers
        return None            # clean: let the connect proceed

    monkeypatch.setattr(mc, "_remote_preflight", _fake_preflight)

    raw = {"url": "https://example.test/mcp", "headers": {"Authorization": "Bearer ${MY_MCP_TOKEN}"}}
    mgr = mc.MCPManager()

    class _Boom(Exception):
        pass

    class _Group:
        async def connect_to_server(self, params):
            raise _Boom()

    mgr.group = _Group()
    try:
        asyncio.run(mgr._do_connect("acme", raw))
    except _Boom:
        pass

    assert seen["headers"] == {"Authorization": "Bearer real-token-123"}
    assert raw["headers"]["Authorization"] == "Bearer ${MY_MCP_TOKEN}", "the stored cfg is not mutated"
