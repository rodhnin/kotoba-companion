"""A session id is minted here and echoed back, so a shape we never mint is somebody else's idea.

It reaches the operator's log at twenty-five places, and a newline inside one writes a second line
there that they will read as their own record of what she did. Scrubbing twenty-five writers is a list
to keep up with; refusing the shape at the door is one place. Three things mint one — the browser's
hyphenated UUID, `uuid4().hex`, and `discord:` plus a digest — and every one of the three must still
be accepted, which is the half of this that a stricter pattern would quietly break."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kotoba.models.schemas import ChatRequest

MINTED = [
    "3f2a1b4c-5d6e-7f80-9a1b-2c3d4e5f6071",   # the browser: crypto.randomUUID()
    "ec2d6a7182614b7eaebdadbcf38f24cb",       # the server and the terminal: uuid4().hex
    "discord:10ec38ab88ce574e",               # a Discord room
]

REFUSED = [
    "a\nINFO  kotoba: nothing was approved",  # the forged line this exists to stop
    "../../etc/passwd",
    "with a space",
    "x" * 200,
    "",
    "-leading-dash",
]

# A newline cannot travel in a request line at all, so the URL is refused before it is sent and the
# path endpoints were never the way in. The JSON body is, which is why the check lives on both doors.
UNSPEAKABLE_IN_A_URL = {"a\nINFO  kotoba: nothing was approved"}
# Slashes and emptiness stop matching the route, so the handler is never reached either — a different
# refusal from the pattern's, and worth pinning as such rather than counting it as the pattern working.
NEVER_ROUTED = {"../../etc/passwd", ""}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("KOTOBA_WEB_PASSWORD", raising=False)
    monkeypatch.delenv("KOTOBA_GATE_PASSWORD", raising=False)
    from kotoba import server

    with TestClient(server.app) as c:
        yield c


@pytest.mark.parametrize("sid", MINTED)
def test_every_shape_we_mint_is_still_accepted(client, sid):
    assert client.get(f"/api/session/{sid}/tasks").status_code != 422


@pytest.mark.parametrize("sid", REFUSED)
def test_a_shape_we_never_mint_never_reaches_the_handler(client, sid):
    if sid in UNSPEAKABLE_IN_A_URL:
        with pytest.raises(Exception):
            client.get(f"/api/session/{sid}/tasks")
        return
    got = client.get(f"/api/session/{sid}/tasks").status_code
    assert got == (404 if sid in NEVER_ROUTED else 422), sid


@pytest.mark.parametrize("sid", MINTED)
def test_the_elevenlabs_body_keeps_a_real_one(sid):
    assert ChatRequest(messages=[], session_id=sid).resolve_session_id() == sid


@pytest.mark.parametrize("sid", REFUSED)
def test_the_elevenlabs_body_drops_a_bad_one_instead_of_refusing_the_call(sid):
    """A live call must not die over a field that only routes events: the id is dropped, not the turn,
    and the caller is handed a fresh one."""
    assert ChatRequest(messages=[], session_id=sid).resolve_session_id() is None


@pytest.mark.parametrize("container", ["elevenlabs_extra_body", "extra_body", "customLlmExtraBody"])
def test_the_nested_places_elevenlabs_hides_it_are_checked_too(container):
    good = ChatRequest.model_validate({"messages": [], container: {"session_id": MINTED[0]}})
    bad = ChatRequest.model_validate({"messages": [], container: {"session_id": REFUSED[0]}})
    assert good.resolve_session_id() == MINTED[0]
    assert bad.resolve_session_id() is None
