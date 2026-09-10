"""Turning the log on must not write down what the user said.

`work_runner START` carried `request=%r` — `ctx.user_text`, the raw utterance — and the day INFO
records started reaching a handler that line began writing "entra en mi banco, la clave es …" into
`/tmp/kotoba_serve.log` (mode 644), or the journal, or `docker logs`. The line has a job: after the
work-handoff fix, live QA needed to see the datum arriving, because the one-sentence goal above it is
where a URL goes to die. That job is answerable from the SHAPE of the message, so INFO reports the
shape and only DEBUG — an explicit KOTOBA_LOG_LEVEL — quotes the words.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

import kotoba.core.work_runner as wr
import kotoba.core.work_state as ws

SECRET = ("hazme el trabajo: entra en mi banco https://bank.example, usuario andrea y la clave es "
          "Correct-Horse-9!")


class _DB:
    async def insert_turn(self, *a, **k): pass


def setup_function():
    ws._state.clear(); ws._tasks.clear()


def _run_and_capture(monkeypatch, level: int) -> str:
    records: list[str] = []

    class _Cap(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    async def fake_emit(session_id, kind, **data):
        pass

    async def done(*a, **k):
        return "finished"

    monkeypatch.setattr(wr, "emit_task", fake_emit)
    monkeypatch.setattr("kotoba.core.loop.agentic_loop", done)
    log = logging.getLogger("kotoba")
    handler, old_level = _Cap(), log.level
    log.addHandler(handler)
    log.setLevel(level)
    try:
        ws.start("s1", "log into the user's bank")
        asyncio.run(wr._run("s1", "log into the user's bank", _DB(), {}, mcp=None, request=SECRET))
    finally:
        log.removeHandler(handler)
        log.setLevel(old_level)
    return "\n".join(records)


def test_the_default_log_does_not_quote_the_user(monkeypatch):
    out = _run_and_capture(monkeypatch, logging.INFO)
    assert "work_runner START" in out
    assert "Correct-Horse-9!" not in out
    assert "entra en mi banco" not in out
    assert "bank.example" not in out


def test_the_line_still_says_the_message_arrived(monkeypatch):
    """What it was added for: the goal is a paraphrase, so 'did the raw message get here, and does it
    carry the link the paraphrase dropped' has to stay answerable."""
    out = _run_and_capture(monkeypatch, logging.INFO)
    assert f"request_chars={len(SECRET)}" in out
    assert "request_urls=1" in out


def test_debug_is_the_opt_in_that_still_quotes(monkeypatch):
    out = _run_and_capture(monkeypatch, logging.DEBUG)
    assert "entra en mi banco" in out, "the diagnostic must remain reachable on purpose"


@pytest.mark.parametrize("request_text,expected", [
    ("", "request_chars=0 request_urls=0"),
    ("read https://a.example and http://b.example", "request_chars=43 request_urls=2"),
])
def test_the_shape_is_a_shape(request_text, expected):
    log = logging.getLogger("kotoba")
    old = log.level
    log.setLevel(logging.INFO)
    try:
        assert wr._request_note(request_text) == expected
    finally:
        log.setLevel(old)
