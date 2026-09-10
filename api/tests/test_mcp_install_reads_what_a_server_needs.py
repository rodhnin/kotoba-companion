"""An approval is not spent on an install that cannot connect.

A registry declares what each known server needs, and the HTTP path already refuses an unmet need
with a setup note. The tool path never read either: install went straight to the approval card, so
the user let a third-party process run, said yes, and got a failure anyway — the approval spent on
a certainty, worse than a refusal (costs nothing) or a wasted approval (costs only the interruption):
this one also spends the trust of being asked about something that could never work. The gate fires
only on an unmet need WITH a setup sentence — servers whose requirement truly cannot be answered
from inside Kotoba; a server may declare a need with no sentence on purpose when its own auth flow
can still resolve it live, and generalising the gate to every need would delete that flow."""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.interaction as interaction
import kotoba.core.mcp.config as mcp_config
import kotoba.tools.action.mcp_install as mcp_install
from kotoba.core.loop import tool_refused
from kotoba.core.mcp.known import setup_note


class _FakeMCP:
    def __init__(self):
        self.server_tools: dict[str, list[str]] = {}
        self.connected: list[str] = []

    async def connect(self, name, cfg):
        self.connected.append(name)
        return [f"{name}__list_events"]


class _Ctx:
    def __init__(self, mcp):
        self.mcp = mcp
        self.session_id = "s-needs"
        self.call_id = "c-needs"


@pytest.fixture
def rig(monkeypatch):
    """Counts the cards asked for and the connects attempted, with no credential in the environment."""
    asked: list[str] = []

    async def _fake_approval(session_id, prompt, family="", card=None, **kw):
        asked.append(prompt)
        if card is not None:
            card["verdict"] = interaction.APPROVED
        return True, False

    monkeypatch.setattr(interaction, "request_approval", _fake_approval)
    monkeypatch.setattr(mcp_config, "save_server", lambda *a, **k: None)
    for var in ("GOOGLE_OAUTH_CREDENTIALS", "GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_MCP_TOKEN",
                "GITHUB_TOKEN"):
        monkeypatch.setenv(var, "")
    mcp = _FakeMCP()
    return asked, mcp, _Ctx(mcp)


def test_a_server_that_needs_setup_outside_kotoba_is_refused_before_the_card(rig):
    """The reproduction: nothing on screen, nothing launched, and the sentence says what is missing."""
    asked, mcp, ctx = rig
    out = asyncio.run(mcp_install.execute({"name": "google calendar"}, ctx))

    assert asked == [], "an approval card was raised for an install that could not connect"
    assert mcp.connected == [], "and it was spent: the doomed connect ran anyway"
    assert setup_note("google calendar") in out, \
        "the tool must say what known.py says — a second wording is the drift this audit removed"


def test_the_refusal_is_witnessed_so_the_row_does_not_paint_a_tick(rig):
    """A refusal is a non-empty string like any other, and `ok` is what the terminal ✓ and the `executed`
    audit row are built on. Nothing was installed, so nothing may be recorded as installed."""
    _, _, ctx = rig
    asyncio.run(mcp_install.execute({"name": "google calendar"}, ctx))
    assert tool_refused(ctx, "c-needs")


def test_the_same_server_installs_normally_once_the_variable_is_set(rig, monkeypatch):
    """The gate is the missing credential, not the name."""
    asked, mcp, ctx = rig
    monkeypatch.setenv("GOOGLE_OAUTH_CREDENTIALS", "/home/someone/gcp-oauth.keys.json")
    out = asyncio.run(mcp_install.execute({"name": "google calendar"}, ctx))

    assert len(asked) == 1 and mcp.connected == ["google-calendar"]
    assert "list_events" in out


def test_github_still_asks_because_its_token_can_be_answered_inside_the_call(rig):
    """It declares an `env:` need too, and its missing PAT is recovered past the card by the masked token
    box (`auth_flow.handle_auth`). Gating every `env:` need would silently delete that."""
    asked, mcp, ctx = rig
    asyncio.run(mcp_install.execute({"name": "github"}, ctx))

    assert len(asked) == 1, "the one server whose missing variable IS answerable in-call stopped asking"
    assert mcp.connected == ["github"]


def test_an_oauth_server_still_asks_because_the_install_really_does_something(rig):
    """notion/linear/slack connect, 401, and land under Settings → 'Needs connection'. That is the
    documented outcome of installing one, not a doomed connect."""
    asked, mcp, ctx = rig
    asyncio.run(mcp_install.execute({"name": "notion"}, ctx))

    assert len(asked) == 1 and mcp.connected == ["notion"]
