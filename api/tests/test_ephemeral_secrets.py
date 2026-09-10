"""One-time secrets: typed into the masked secure box, held in memory for the current task only, NEVER
saved to DB and NEVER returned to the model. Substituted into a tool call by the MCP layer, then cleared
when the work item ends."""
from __future__ import annotations

import kotoba.core.ephemeral_secrets as es


def setup_function():
    es._store.clear()


def test_put_get_roundtrip():
    es.put("s1", "fb_pw", "hunter2")
    assert es.get("s1", "fb_pw") == "hunter2"


def test_get_unknown_is_none():
    assert es.get("s1", "nope") is None
    assert es.get(None, "x") is None


def test_clear_removes_session_secrets():
    es.put("s1", "a", "1")
    es.put("s1", "b", "2")
    es.clear("s1")
    assert es.get("s1", "a") is None and es.get("s1", "b") is None


def test_scoped_per_session():
    es.put("s1", "k", "one")
    es.put("s2", "k", "two")
    assert es.get("s1", "k") == "one" and es.get("s2", "k") == "two"
