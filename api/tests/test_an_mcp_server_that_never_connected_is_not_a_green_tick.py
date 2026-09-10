"""Every way installing an MCP server can end without a server, and the mark each one earns.

Both tools grade themselves through two witnesses. `note_tool_refusal` means the tool read its own
arguments and declined: nothing happened, a grey mark, no audit row. `note_tool_failure` means the work
was attempted and did not land — red, and an `executed:failed` row, because an attempt leaves traces
even when it fails. An ending that calls neither is graded on returning a non-empty string, and every
one of these returns a helpful sentence.

So all of them drew a green tick: a connect still queued when the window ran out, a rejected token, and
a server torn down with nothing usable all wore the mark of one that came up ready.
"""
from __future__ import annotations

import asyncio
import types

import pytest

import kotoba.core.loop as loop


def _run(coro):
    return asyncio.run(coro)


class _Mcp:
    """An MCP layer that fails the one way each test is about."""

    def __init__(self, on_connect):
        self.server_tools = {}
        self._on_connect = on_connect
        self.disconnected = []

    async def connect(self, name, cfg):
        return self._on_connect(name)

    async def disconnect(self, name):
        self.disconnected.append(name)


class _Ctx:
    def __init__(self, mcp):
        self.mcp = mcp
        self.session_id = "mcp-marks"
        self.call_id = "c1"
        self.mode = "companion"
        self.db = None
        self._open_steps = {}


def _busy(name):
    from kotoba.core.mcp.client import MCPBusy

    raise MCPBusy(f"connect to {name!r} is still queued after 35s")


@pytest.fixture
def approved(monkeypatch):
    """Past the card: these endings all live downstream of an approval, and the card is not the subject."""
    from kotoba.core import interaction

    async def yes(*a, **k):
        return interaction.APPROVED

    monkeypatch.setattr(interaction, "ask_approval", yes)


# --- the queue, in both tools -------------------------------------------------------------------------

def test_a_server_still_queued_is_witnessed_as_a_failure(monkeypatch, approved):
    import kotoba.tools.action.mcp_install as mcp_install

    with loop.record_tool_failures() as failed:
        reply = _run(mcp_install.execute({"name": "notion"}, _Ctx(_Mcp(_busy))))

    assert failed, "installing nothing was graded as installing something"
    assert "queued" in failed[0]
    assert "still starting" in reply, "the honest sentence must survive the witness"


def test_the_same_ending_in_the_other_tool_is_witnessed_too(monkeypatch, approved):
    """`mcp_find` carries a second copy of this branch, and its two neighbours already witness."""
    import kotoba.tools.action.mcp_find as mcp_find

    src = __import__("pathlib").Path(mcp_find.__file__).read_text(encoding="utf-8")
    head, _, tail = src.partition("except MCPBusy:")
    assert tail, "mcp_find no longer handles a queued connect"
    block = tail.split("except Exception:")[0]
    assert "note_tool_failure" in block, "mcp_find grades a queued connect as a success again"


def test_a_queued_connect_is_not_dressed_as_a_refusal(monkeypatch, approved):
    """A refusal is somebody's decision and writes NO audit row. This was an attempt that went out and
    did not come back — filing it as a decision would lose the fact that she tried."""
    import kotoba.tools.action.mcp_install as mcp_install

    ctx = _Ctx(_Mcp(_busy))
    with loop.record_tool_failures():
        _run(mcp_install.execute({"name": "notion"}, ctx))

    assert not loop.tool_refused(ctx, "c1")


# --- the endings that DO earn a grey mark stay grey -----------------------------------------------------

def test_a_server_she_does_not_know_is_still_a_refusal(approved):
    """The contrast that keeps the two witnesses apart: here she read the arguments and declined, and
    nothing was attempted at all."""
    import kotoba.tools.action.mcp_install as mcp_install

    ctx = _Ctx(_Mcp(_busy))
    with loop.record_tool_failures() as failed:
        reply = _run(mcp_install.execute({"name": "a-server-that-does-not-exist"}, ctx))

    assert loop.tool_refused(ctx, "c1")
    assert not failed, "nothing was attempted, so nothing failed"
    assert "don't recognize" in reply


def test_an_already_connected_server_is_still_a_success(approved):
    import kotoba.tools.action.mcp_install as mcp_install

    mcp = _Mcp(_busy)
    mcp.server_tools = {"notion": ["notion__search"]}
    with loop.record_tool_failures() as failed:
        reply = _run(mcp_install.execute({"name": "notion"}, _Ctx(mcp)))

    assert not failed
    assert "connected" in reply


# --- the auth flow, which had no witness anywhere -------------------------------------------------------

def test_a_server_torn_down_for_having_no_tools_is_a_failure():
    from kotoba.core.mcp import auth_flow

    mcp = _Mcp(_busy)
    with loop.record_tool_failures() as failed:
        reply = _run(auth_flow.finish_connect(_Ctx(mcp), "notion", [], {}, stored_token=""))

    assert failed and "no usable tools" in failed[0]
    assert mcp.disconnected == ["notion"], "it really was torn down; that is what makes it an attempt"
    assert "removed it" in reply


def test_a_token_that_did_not_connect_is_a_failure(monkeypatch):
    from kotoba.core import interaction
    from kotoba.core.mcp import auth_flow, pending

    async def types_a_token(sid, prompt, kind, card=None):
        return "a-token-that-will-not-work"

    monkeypatch.setattr(interaction, "request_input", types_a_token)
    monkeypatch.setattr(pending, "record", lambda *a, **k: None)

    def refuses(name):
        raise RuntimeError("401 from the server")

    auth = types.SimpleNamespace(kind="token", reason="needs a token")
    with loop.record_tool_failures() as failed:
        reply = _run(auth_flow.handle_auth(_Ctx(_Mcp(refuses)), "notion", {}, "", auth))

    assert failed and "did not connect" in failed[0]
    assert "left it in Settings" in reply


def test_parking_a_server_in_settings_is_not_called_a_failure(monkeypatch):
    """The deliberate non-fix: an OAuth server she cannot sign into on her own is FILED, not failed.
    She did the whole of what she can do and says where it went. Marking that red would teach the model
    to retry an install that is waiting on a human at a browser."""
    from kotoba.core.mcp import auth_flow, pending

    monkeypatch.setattr(pending, "record", lambda *a, **k: None)
    auth = types.SimpleNamespace(kind="oauth", reason="needs a browser sign-in")
    with loop.record_tool_failures() as failed:
        reply = _run(auth_flow.handle_auth(_Ctx(_Mcp(_busy)), "notion", {}, "", auth))

    assert not failed
    assert "Needs connection" in reply
