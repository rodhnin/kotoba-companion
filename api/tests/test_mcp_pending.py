"""Pending-MCP store: servers that need auth, recorded so Settings can show them across restarts and boot
does NOT auto-connect them. Persisted to a yaml file (no secrets ever stored here)."""
from __future__ import annotations

import importlib


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_PENDING_MCP", str(tmp_path / "pending_mcp.yaml"))
    import kotoba.core.mcp.pending as pending
    importlib.reload(pending)
    return pending


def test_record_list_get_clear_roundtrip(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    assert p.list_pending() == []

    p.record("notion", {"url": "https://notion/mcp"}, "needs a token", "token", "Read Notion pages.")
    p.record("railway", {"url": "https://mcp.railway.com/"}, "needs sign-in", "oauth", "Railway deploys.")

    names = {x["name"] for x in p.list_pending()}
    assert names == {"notion", "railway"}
    notion = p.get("notion")
    assert notion["kind"] == "token" and notion["cfg"] == {"url": "https://notion/mcp"}
    assert notion["reason"] == "needs a token" and notion["description"] == "Read Notion pages."

    # list_pending exposes name/reason/kind/description but is safe to show in the UI
    row = next(x for x in p.list_pending() if x["name"] == "railway")
    assert set(row) >= {"name", "reason", "kind", "description"}

    p.clear("notion")
    assert {x["name"] for x in p.list_pending()} == {"railway"}


def test_persists_across_reload(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    p.record("slack", {"url": "https://slack/mcp"}, "needs a token", "token", "Slack.")
    # New process / reload reads the same file.
    import kotoba.core.mcp.pending as pending2
    importlib.reload(pending2)
    assert any(x["name"] == "slack" for x in pending2.list_pending())


def test_record_never_persists_secret_values(tmp_path, monkeypatch):
    # The store holds the cfg WITHOUT secrets; the caller must strip tokens before recording. We assert the
    # file content has no obvious token if the caller passes a clean cfg.
    p = _fresh(tmp_path, monkeypatch)
    p.record("x", {"url": "https://x/mcp"}, "needs a token", "token", "X")
    text = (tmp_path / "pending_mcp.yaml").read_text(encoding="utf-8")
    assert "Bearer" not in text and "token-value" not in text
