"""web_extract's readable-text parse (readability-lxml) is CPU-bound and SYNC; if it runs on the event
loop it blocks every other task — including the ElevenLabs voice turns — which shows up as the voice
WebSocket dropping (1006) while background research runs. The parse must be a plain sync helper that the
async path offloads with asyncio.to_thread, so the event loop stays free."""
from __future__ import annotations

import asyncio
import inspect

import kotoba.tools.builtin.web_extract as we


def test_readable_from_html_extracts_title_and_text():
    html = (
        "<html><head><title>Hardest Integral</title></head><body><article><p>"
        + ("This integral is famously difficult to evaluate by hand. " * 20)
        + "</p></article></body></html>"
    )
    out = we._readable_from_html(html)
    assert out and "integral" in out.lower()


def test_readable_from_html_is_sync_and_offloadable():
    # A plain (non-async) function → it can be handed to a worker thread so it never blocks the loop.
    assert not inspect.iscoroutinefunction(we._readable_from_html)
    html = "<html><body><article><p>" + ("hello world " * 40) + "</p></article></body></html>"
    out = asyncio.run(asyncio.to_thread(we._readable_from_html, html))
    assert out and "hello" in out.lower()
