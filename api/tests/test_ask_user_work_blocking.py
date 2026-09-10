"""ask_user must behave differently per mode, because the two input mechanisms are not interchangeable:

  • companion (voice turn): NON-blocking — open the card and return at once (a long wait inside the
    ElevenLabs turn kills the WebSocket). The typed value arrives as the user's NEXT message.
  • work (background loop): there IS no "next message" — the loop runs to completion on its goal. So
    ask_user must BLOCK on request_input (resolved by POST /input) and hand the typed value back to the
    model, exactly like ask_secret. Otherwise the loop barrels past the empty value and the login breaks.
"""
from __future__ import annotations


import asyncio
import types

import kotoba.tools.builtin.ask_user as au
from kotoba.core import interaction





def _ctx(mode):
    return types.SimpleNamespace(session_id="sess", mode=mode)


def test_work_mode_blocks_and_returns_typed_value(monkeypatch):
    calls = {}

    async def fake_request_input(sid, prompt, kind, **kw):
        calls["request_input"] = (sid, prompt, kind)
        return "jordan@example.com"

    async def fake_open(*a, **k):
        calls["open_input_card"] = True

    monkeypatch.setattr(interaction, "request_input", fake_request_input)
    monkeypatch.setattr(interaction, "open_input_card", fake_open)

    out = asyncio.run(au.execute({"prompt": "Your Facebook email?"}, _ctx("work")))

    assert "request_input" in calls            # it BLOCKED for the answer
    assert "open_input_card" not in calls       # not the non-blocking voice path
    assert "jordan@example.com" in out          # the typed value is handed to the model to type


def test_work_mode_timeout_reports_the_timeout_and_invents_nothing(monkeypatch):
    """No answer is not a value and not a refusal either. It used to come back as a bare None, graded a
    tool failure and spoken as one canned line over all four endings; what the model needs is which."""
    async def fake_request_input(sid, prompt, kind, **kw):
        return None  # user didn't type in time

    monkeypatch.setattr(interaction, "request_input", fake_request_input)
    out = asyncio.run(au.execute({"prompt": "email?"}, _ctx("work")))
    assert "expired" in out and "timeout, not their choice" in out
    assert "email" not in out.replace("email?", "")  # no value invented for the model


def test_companion_mode_stays_non_blocking(monkeypatch):
    calls = {}

    async def fake_request_input(*a, **k):
        calls["request_input"] = True
        return "should-not-be-used"

    async def fake_open(sid, prompt, kind, **kwargs):
        calls["open_input_card"] = (sid, prompt, kind)
        return True  # the real one returns whether the card was DRAWN

    monkeypatch.setattr(interaction, "request_input", fake_request_input)
    monkeypatch.setattr(interaction, "open_input_card", fake_open)

    out = asyncio.run(au.execute({"prompt": "paste the link"}, _ctx("companion")))

    assert "open_input_card" in calls           # non-blocking voice path
    assert "request_input" not in calls          # never blocks the voice turn
    assert "next message" in out.lower()         # tells the model the value arrives as the next turn


def test_interactive_flag_set():
    # In work mode ask_user blocks up to ~180s; the loop must NOT compute-cancel it at 30s.
    assert getattr(au, "INTERACTIVE", False) is True
