"""ask_secret — type ONE thing in a masked secure box, ONE-TIME, NOT saved. For the user's sensitive
passwords (e.g. logging into the user's own account once). The value goes to the ephemeral store (never
DB, never the model); the model gets back only a {{secret:NAME}} placeholder to type."""
from __future__ import annotations

import asyncio

import kotoba.core.ephemeral_secrets as es
import kotoba.tools.action.ask_secret as asec
from kotoba.tools import ToolContext


def setup_function():
    es._store.clear()


def test_marked_interactive_and_work_only():
    assert getattr(asec, "INTERACTIVE", False) is True
    from kotoba.tools.registry import COMPANION_TOOLSETS
    assert asec.TOOLSET not in COMPANION_TOOLSETS


def test_stores_ephemeral_and_returns_placeholder_never_value(monkeypatch):
    async def fake_request_input(session_id, label, kind="text", timeout=180.0, **kw):
        assert kind == "secret"          # one-time masked field (NOT the saved-key field)
        return "one-time-pass"

    monkeypatch.setattr(asec.interaction, "request_input", fake_request_input)
    ctx = ToolContext(db=None, session_id="s1", mode="work")
    out = asyncio.run(asec.execute({"name": "fb", "prompt": "type your password"}, ctx))
    assert "{{secret:fb}}" in out                 # model gets the placeholder
    assert "one-time-pass" not in out             # never the value
    assert es.get("s1", "fb") == "one-time-pass"  # held ephemerally for the proxy to substitute


def test_not_saved_to_db(monkeypatch):
    saved = {}

    class _DB:
        async def save_key(self, name, value):
            saved[name] = value

    async def fake_request_input(session_id, label, kind="text", timeout=180.0, **kw):
        return "secret-val"

    monkeypatch.setattr(asec.interaction, "request_input", fake_request_input)
    ctx = ToolContext(db=_DB(), session_id="s2", mode="work")
    asyncio.run(asec.execute({"name": "x", "prompt": "p"}, ctx))
    assert saved == {}                            # one-time → NEVER persisted to the DB


def test_no_answer_is_graceful(monkeypatch):
    """Nothing was collected, and the ending says WHICH nothing: an unnamed one was graded a tool
    failure, so declining to type a secret came back as her mistake."""
    async def fake_request_input(session_id, label, kind="text", timeout=180.0, **kw):
        return None

    monkeypatch.setattr(asec.interaction, "request_input", fake_request_input)
    ctx = ToolContext(db=None, session_id="s3", mode="work")
    ctx.call_id = "call-2"
    out = asyncio.run(asec.execute({"name": "x", "prompt": "p"}, ctx))
    assert out and "{{secret:" not in out, "no placeholder for a secret nobody gave"
    assert asec.interaction.no_run_verdict(ctx, "call-2") == asec.interaction.UNANSWERED


def test_idempotent_does_not_reopen_form_if_already_have_it(monkeypatch):
    # Already collected this secret → return the placeholder WITHOUT opening another form (the cause of the
    # "form after form / password typed twice" storm when the model retried a failing login).
    calls = {"n": 0}

    async def fake_request_input(session_id, label, kind="text", timeout=180.0, **kw):
        calls["n"] += 1
        return "v"

    monkeypatch.setattr(asec.interaction, "request_input", fake_request_input)
    es.put("s4", "fb", "already-typed")
    ctx = ToolContext(db=None, session_id="s4", mode="work")
    out = asyncio.run(asec.execute({"name": "fb", "prompt": "p"}, ctx))
    assert calls["n"] == 0                         # no new form was opened
    assert "{{secret:fb}}" in out                  # reuses the placeholder
