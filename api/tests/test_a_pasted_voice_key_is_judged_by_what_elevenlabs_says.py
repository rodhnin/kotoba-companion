"""Two answers a real ElevenLabs gives that used to read as "the network did something".

The first is the commonest paste mistake there is — the whitespace that comes with a copy — and httpx
refuses to put it in a header at all, so the request never leaves and the person is told to suspect
their connection. The second is ElevenLabs saying plainly that the key belongs to somebody else.
"""
from __future__ import annotations

import httpx
import pytest

from kotoba.core import voice_key


class _Answer:
    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status_code = status
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def _answers(monkeypatch, answer, seen: list[str]):
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            seen.append((headers or {}).get("xi-api-key", ""))
            # The real client refuses an illegal header before any request is made.
            httpx.Headers({"xi-api-key": seen[-1]})
            return answer

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _Client())


@pytest.mark.anyio
async def test_the_whitespace_a_paste_brings_never_reaches_the_wire(monkeypatch):
    seen: list[str] = []
    _answers(monkeypatch, _Answer(200), seen)
    keep, note = await voice_key.verify("  sk_realkey\n")
    assert keep and note == ""
    assert seen == ["sk_realkey"], "the header still carried the whitespace"


@pytest.mark.anyio
async def test_a_key_for_something_else_is_named_as_such(monkeypatch):
    """ElevenLabs answers 400 and says which prefix it wants; that is not a proxy talking."""
    body = {"detail": {"status": "invalid_api_key_prefix", "code": "invalid_api_key",
                       "message": "API key must start with 'sk_'."}}
    _answers(monkeypatch, _Answer(400, body), [])
    keep, note = await voice_key.verify("sk-proj-this-is-an-openai-key")
    assert keep is False
    assert "sk_" in note and "proxy" not in note.lower()


@pytest.mark.anyio
async def test_an_answer_that_is_not_theirs_still_keeps_the_key(monkeypatch):
    """The instrument, shown not over-firing: a captive portal must not destroy a good key."""
    _answers(monkeypatch, _Answer(302), [])
    keep, note = await voice_key.verify("sk_probably_fine")
    assert keep is True and "kept it" in note
