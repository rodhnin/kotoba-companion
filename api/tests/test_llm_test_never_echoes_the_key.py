"""`POST /api/settings/llm-test` says "Never echoes the key" — this makes that true instead of hoped.

The endpoint returns the provider's own exception text to the Settings panel, and a provider (or an
OpenAI-compatible `base_url` the user pointed us at) that quotes the request back puts the active key on
screen and into whatever screenshot the panel ends up in. Its sibling `/api/settings/llm-key` runs the
same round trip through `core.first_run.verify_key`, which redacts; only the claim travelled here.

The second pin is the ORDER. Redacting after the 160-character cut cannot work: the cut can land inside
the key, leaving a usable prefix that no longer matches the string being replaced.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import kotoba.server as main

_KEY = "sk-liveKEY-do-not-print-0123456789abcdef"


class _Responses:
    def __init__(self, message: str) -> None:
        self._message = message

    async def create(self, **_kw):
        raise RuntimeError(self._message)


class _Client:
    def __init__(self, message: str) -> None:
        self.api_key = _KEY
        self.responses = _Responses(message)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _run(client, monkeypatch, message: str) -> str:
    from kotoba.core import llm

    monkeypatch.setattr(llm, "get_client", lambda: _Client(message))
    monkeypatch.setattr(llm, "model_name", lambda role="companion": "some-model")
    r = client.post("/api/settings/llm-test", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    return body["detail"]


def test_a_provider_that_echoes_the_key_does_not_get_it_onto_the_screen(client, monkeypatch):
    detail = _run(client, monkeypatch, f"401 Incorrect API key provided: {_KEY}. Check your account.")
    assert _KEY not in detail
    assert "RuntimeError" in detail          # the useful half still arrives


def test_the_key_is_removed_before_the_length_cut_not_after(client, monkeypatch):
    # The key must STRADDLE the 160-character budget, or the cut removes it whatever the order is and
    # this proves nothing: "RuntimeError: " is 14, so 136 filler characters put its head at 155.
    detail = _run(client, monkeypatch, "x" * 136 + f" key={_KEY} trailing")
    assert _KEY not in detail
    for n in range(5, len(_KEY) + 1):
        assert _KEY[:n] not in detail
