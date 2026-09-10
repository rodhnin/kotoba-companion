"""The status bar names the model and the provider that will actually serve the next turn.

`App.model` was assigned once, at boot, and never reassigned, so `/set provider xai` left the bottom
bar saying `OpenAI` for the rest of the session while Settings said `xai` three rows above it. The
header, bar and listing must not disagree — someone who switched provider for privacy or cost was told,
permanently and on screen, that their words still went to the old one.

The recompute is deliberately NOT on the repaint path: only the cheap MODEL line (two settings reads and
a cached client) is re-asked after a write, never per frame — the rest of the fact-gathering is too
expensive for that."""
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest

from kotoba.cli import slash
from kotoba.cli.app import App
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console
from kotoba.core import llm


@pytest.fixture
def app(monkeypatch):
    """Booted the way `App.run` boots: the MODEL fact in the header, and `caps.t` of it in the bar."""
    monkeypatch.setattr(llm, "get_client", lambda: object())   # a key, so the fact names the model
    caps = Caps(color="none", background="dark", unicode=True, interactive=False, width=96,
                g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    a = App(caps, screen, prompt=None)
    a.session = SimpleNamespace(session_id="s-bar")
    a.facts = ([("MODEL", _fact(), 2), ("KEYS", "/help", 1)], "local sandbox")
    a.model = caps.t(_fact())
    return a


def _fact() -> str:
    from kotoba.cli.facts import _model
    from kotoba.core import providers

    return _model(llm, providers)


def _header(app) -> str:
    return dict((k, v) for k, v, _ in app.facts[0])["MODEL"]


def test_the_bar_follows_the_model(app):
    before = app.model
    assert "gpt-4o-mini" not in before

    asyncio.run(slash._set(app, "model gpt-4o-mini"))

    assert "gpt-4o-mini" in app.model and app.model != before
    assert _header(app) == _fact(), "the header states the same fact the bar draws"


def test_the_bar_follows_the_provider(app, monkeypatch):
    """The serious half: the bar naming OpenAI while every token goes to xAI is a falsehood about where
    the operator's words are being sent."""
    monkeypatch.setattr(slash, "_confirm", _yes)
    assert "OpenAI" in app.model

    asyncio.run(slash._set(app, "provider xai"))

    assert "xAI" in app.model and "OpenAI" not in app.model
    assert _header(app) == _fact()


def test_slash_model_moves_the_bar_because_it_goes_through_set(app):
    asyncio.run(slash._model(app, "gpt-4o-mini"))

    assert "gpt-4o-mini" in app.model and _header(app) == _fact()


def test_a_setting_that_says_nothing_about_the_brain_leaves_the_bar_where_it_was(app):
    before, facts_before = app.model, app.facts

    asyncio.run(slash._set(app, "tts_engine fast"))

    assert app.model == before and app.facts[0] == facts_before[0]


async def _yes(app, head_line, why, *labels) -> bool:
    return True
