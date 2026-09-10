"""request_credential — saves one of Kotoba's OWN reusable credentials to the DB (.env-style). These are
hers: stored directly, NOT behind the {{secret}} proxy. Confirms without echoing a placeholder."""
from __future__ import annotations

import asyncio

import kotoba.tools.action.request_credential as rc
from kotoba.tools import ToolContext


def test_marked_interactive_and_work_only():
    assert getattr(rc, "INTERACTIVE", False) is True
    from kotoba.tools.registry import COMPANION_TOOLSETS
    assert rc.TOOLSET not in COMPANION_TOOLSETS


def test_saves_to_db_and_confirms(monkeypatch):
    saved = {}

    class _DB:
        async def get_key(self, name):
            return saved.get(name)

        async def save_key(self, name, value):
            saved[name] = value

    async def fake_request_input(session_id, label, kind="text", timeout=180.0, **kw):
        assert kind == "key"
        return "sk-my-own-key"

    monkeypatch.setattr(rc.interaction, "request_input", fake_request_input)
    ctx = ToolContext(db=_DB(), session_id="s1", mode="work")
    out = asyncio.run(rc.execute({"name": "my_openai", "prompt": "paste your key"}, ctx))
    # stored NAMESPACED under cred: so get_credential can never reach system keys (llm:*/mcp:*)
    assert saved == {"cred:my_openai": "sk-my-own-key"}
    assert "my_openai" in out and "cred:" not in out and "{{secret" not in out  # confirms; user-facing name


def test_idempotent_when_already_saved(monkeypatch):
    calls = {"n": 0}

    class _DB:
        async def get_key(self, name):
            return "existing" if name == "cred:my_openai" else None
        async def save_key(self, name, value):
            pass

    async def fake_request_input(session_id, label, kind="text", timeout=180.0, **kw):
        calls["n"] += 1
        return "v"

    monkeypatch.setattr(rc.interaction, "request_input", fake_request_input)
    ctx = ToolContext(db=_DB(), session_id="s9", mode="work")
    out = asyncio.run(rc.execute({"name": "my_openai", "prompt": "paste"}, ctx))
    assert calls["n"] == 0 and "already" in out.lower()  # no new form; confirms it's there


def test_no_answer_is_graceful(monkeypatch):
    """The user was asked for a credential and gave none: that ends the tool call, gracefully.

    Re-recorded twice. The old fixture used `db=None` as a convenience, and `db=None` now refuses
    before opening the box at all. Then the ending itself changed: returning None left no witness, so
    a person who simply chose not to hand over a key was graded a tool failure, told "I couldn't save
    that — let's try that again", and the model was told the tool returned nothing. The ending is now
    named, which is what keeps it out of the failure column."""
    class _DB:
        async def get_key(self, name):
            return None

        async def save_key(self, name, value):
            pass

    async def fake_request_input(session_id, label, kind="text", timeout=180.0, **kw):
        return None

    monkeypatch.setattr(rc.interaction, "request_input", fake_request_input)
    ctx = ToolContext(db=_DB(), session_id="s2", mode="work")
    ctx.call_id = "call-1"
    out = asyncio.run(rc.execute({"name": "x", "prompt": "p"}, ctx))
    assert out and "Saved as" not in out, "it must not claim a save that never happened"
    assert rc.interaction.no_run_verdict(ctx, "call-1") == rc.interaction.UNANSWERED, (
        "without the witness the loop grades this a tool failure and she apologises for the user's own "
        "choice")


def test_without_a_store_it_refuses_before_opening_the_box(monkeypatch):
    """With db=None the old path opened the masked box, collected a secret, stored NOTHING and said
    "Saved as …" — an outright lie holding somebody's credential. Production always sets ctx.db, so
    this is a code-level guard: never collect a value that has nowhere to live."""
    calls = {"n": 0}

    async def fake_request_input(session_id, label, kind="text", timeout=180.0, **kw):
        calls["n"] += 1
        return "sk-secret"

    monkeypatch.setattr(rc.interaction, "request_input", fake_request_input)
    ctx = ToolContext(db=None, session_id="s2", mode="work")
    out = asyncio.run(rc.execute({"name": "x", "prompt": "p"}, ctx))
    assert calls["n"] == 0                       # the box never opened
    assert out and "Saved as" not in out
    assert "nothing was saved" in out.lower()
