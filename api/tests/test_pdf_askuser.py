"""Gate — server-side PDF rendering + ask_user tool."""
from __future__ import annotations

import asyncio

import pytest

from kotoba.core import interaction
from kotoba.core.pdf import html_to_pdf, pdf_available
from kotoba.tools import ToolContext
from kotoba.tools.builtin import ask_user


@pytest.mark.skipif(not pdf_available(), reason="no browser that can write a PDF here (a snap cannot)")
def test_html_to_pdf_produces_pdf_bytes():
    html = "<!doctype html><html><body style='background:#fbf1e3'>"\
           "<div style='background:#fff;border:3px solid #211a2e'>hi</div></body></html>"
    pdf = asyncio.run(html_to_pdf(html))
    assert pdf is not None and pdf[:4] == b"%PDF"  # a real PDF


def test_ask_user_companion_opens_card_and_returns_immediately():
    # COMPANION (voice) contract: ask_user opens the input card and returns guidance at once (the typed
    # value comes back as the user's NEXT message, not through this call). It must never block the voice
    # turn (a long wait inside the ElevenLabs turn kills the WebSocket).
    async def go():
        from kotoba.core import events

        events.register("au1")
        ctx = ToolContext(db=None, session_id="au1", mode="companion")
        out = await ask_user.execute({"prompt": "Paste the repo link", "kind": "link"}, ctx)
        events.unregister("au1")
        return out

    out = asyncio.run(go())
    assert out and ("text box" in out.lower() or "type" in out.lower() or "screen" in out.lower())
    assert interaction.has_pending("au1") is False  # did not block on a Future


def test_ask_user_key_in_work_does_not_echo_secret():
    # In WORK mode, ask_user must NEVER collect/echo a secret — it redirects to the secure tools
    # (ask_secret / request_credential). It does not even open a Future for a key kind.
    async def go():
        from kotoba.core import events

        events.register("au2")
        ctx = ToolContext(db=None, session_id="au2", mode="work")
        out = await ask_user.execute({"prompt": "Your API key", "kind": "key"}, ctx)
        events.unregister("au2")
        return out

    out = asyncio.run(go())
    assert "ask_secret" in out and "secure" in out.lower()
    assert interaction.has_pending("au2") is False  # never opened a Future for a secret


def test_the_render_leaves_nothing_of_its_own_behind():
    """Everything it writes goes in a directory that leaves with it. The profile is the exception and
    it is deliberate: Brave never finishes a print into a fresh one."""
    from pathlib import Path

    from kotoba.core import pdf

    source = Path(pdf.__file__).read_text(encoding="utf-8")
    assert "TemporaryDirectory" in source, "the html and the pdf would outlive the render"
    assert "--user-data-dir=" not in source, "a fresh profile makes Brave hang instead of printing"
