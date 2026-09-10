"""A card is claimed only when somebody could see it.

`open_input_card` returns whether anyone is listening; the frame is dropped when nobody is, and the
caller must not then tell the model it opened a box that was never drawn. The window is real: any SSE
drop, and every caller that is not the web.

The `key` variant costs most: she would tell the user to type an API key into a field that does not
exist, then wait for a message nobody can send. `make_report` is the split case: the report still
renders, saves and gets noted in memory regardless — only the "it opened" half is conditional."""
from __future__ import annotations

import asyncio
import types

import pytest

from kotoba.core import events, interaction
from kotoba.tools.action import make_report
from kotoba.tools.builtin import ask_user, open_link

_SID = "cards-witness"


def _ctx():
    return types.SimpleNamespace(session_id=_SID, mode="companion", channel="voice", run_id="", db=None,
                                 approval=None)


@pytest.fixture
def listening():
    q = events.register(_SID)
    yield q
    events.unregister(_SID, q)


@pytest.fixture(autouse=True)
def _no_listener_by_default():
    events.unregister(_SID)
    yield
    events.unregister(_SID)


def _run(coro):
    return asyncio.run(coro)


# --- ask_user ---------------------------------------------------------------------------------------

def test_the_key_field_is_not_claimed_when_no_screen_can_draw_it():
    out = _run(ask_user.execute({"prompt": "Your OpenAI key", "kind": "key", "name": "k"}, _ctx()))
    assert "no screen" in out.lower()
    assert "NO box was opened" in out
    assert "I opened a secure field" not in out


def test_a_key_typed_into_the_open_box_never_answers_an_approval_card(listening):
    async def go():
        card: dict = {}
        approval = asyncio.create_task(interaction.request_approval(
            _SID, "pip install requests", timeout=2, channel="text", card=card))
        await asyncio.sleep(0)
        assert card.get("request_id")
        await interaction.open_input_card(_SID, "Your OpenAI key", "key", name="k")
        frames = []
        while not listening.empty():
            frames.append(listening.get_nowait())
        key = next(f for f in frames if f.get("kind") == "need_input" and f.get("input_kind") == "key")
        assert key.get("request_id") and key["request_id"] != card["request_id"]
        body = {"kind": "key", "name": "k", "value": "sk-live", "request_id": key["request_id"]}
        assert interaction.resolve(_SID, body, key["request_id"]) is False
        assert card.get("verdict") is None
        interaction.resolve(_SID, {"approved": True}, card["request_id"])
        assert await approval == (True, False)
        assert card["verdict"] == interaction.APPROVED

    _run(go())


def test_the_text_box_is_not_claimed_when_no_screen_can_draw_it():
    out = _run(ask_user.execute({"prompt": "Paste the link here"}, _ctx()))
    assert "NOTHING was opened" in out
    assert "I opened a text box" not in out
    assert "next message" not in out.lower(), "nothing will arrive as a next message — nobody can type"


def test_the_claim_returns_the_moment_a_listener_exists(listening):
    out = _run(ask_user.execute({"prompt": "Paste the link here"}, _ctx()))
    assert "I opened a text box on screen" in out
    key = _run(ask_user.execute({"prompt": "Your OpenAI key", "kind": "key", "name": "k"}, _ctx()))
    assert "I opened a secure field on screen" in key


def test_open_input_card_still_reports_whether_anyone_heard(listening):
    assert _run(interaction.open_input_card(_SID, "label", "text")) is True
    events.unregister(_SID)
    assert _run(interaction.open_input_card(_SID, "label", "text")) is False
    assert _run(interaction.open_input_card(None, "label", "text")) is False


# --- open_link --------------------------------------------------------------------------------------

def test_the_link_card_is_not_claimed_when_no_screen_can_draw_it():
    out = _run(open_link.execute({"url": "https://example.com", "why": "the principal source"}, _ctx()))
    assert "no screen" in out.lower() and "NOTHING was shown" in out
    # The drawn branch's own words, which are the ones that would be the lie here.
    assert "in front of them now" not in out


def test_the_link_card_claim_returns_with_a_listener(listening):
    """Drawn, the answer says the link REACHED them — and still refuses to name which of the two
    shapes it took, because the tool cannot see whether a card or a bare address was rendered."""
    out = _run(open_link.execute({"url": "https://example.com", "why": "the principal source"}, _ctx()))
    assert "in front of them now" in out
    assert "no screen" not in out.lower()
    assert "do NOT describe a card" in out


def test_open_link_card_reports_whether_anyone_heard(listening):
    assert _run(interaction.open_link_card(_SID, "https://example.com", "why")) is True
    events.unregister(_SID)
    assert _run(interaction.open_link_card(_SID, "https://example.com", "why")) is False
    assert _run(interaction.open_link_card(None, "https://example.com", "why")) is False


# --- make_report ------------------------------------------------------------------------------------

def test_the_report_is_still_made_but_not_said_to_be_open():
    out = _run(make_report.execute({"title": "Probe report", "summary": "What this covers."}, _ctx()))
    assert "Report ready: Probe report." in out, "the artifact really was produced — say that"
    assert "did NOT open on screen" in out
    assert "in front of them now" not in out


def test_the_report_says_it_opened_when_a_viewer_is_connected(listening):
    out = _run(make_report.execute({"title": "Probe report two", "summary": "What this covers."}, _ctx()))
    assert "in front of them now" in out


def test_a_report_that_neither_opened_nor_saved_points_at_nothing(monkeypatch):
    from kotoba.core import file_library

    monkeypatch.setattr(file_library, "save_text", lambda rel, content: None)
    out = _run(make_report.execute({"title": "Probe report lost", "summary": "Body."}, _ctx()))
    assert "did NOT open on screen" in out
    assert "could not be saved" in out
    assert "where it's saved" not in out and "Saved it to the user's Files" not in out


def test_a_listener_that_paints_no_viewer_is_not_a_screen():
    """The subtler half of the same defect: Discord consumes the queue and renders no report viewer,
    so a claim keyed on "did the frame reach anyone" announces a report that opened nowhere."""
    q = events.register(_SID, draws_cards=False)
    try:
        out = _run(make_report.execute({"title": "Probe report three", "summary": "Body."}, _ctx()))
    finally:
        events.unregister(_SID, q)
    assert "did NOT open on screen" in out
    assert "in front of them now" not in out


def test_the_double_call_guard_does_not_claim_it_is_open(listening):
    first = _run(make_report.execute({"title": "Same title", "summary": "Body."}, _ctx()))
    again = _run(make_report.execute({"title": "Same title", "summary": "Body."}, _ctx()))
    assert "in front of them now" in first
    # The second call opens nothing, so it must not repeat the first one's claim.
    assert "already put together" in again and "in front of them now" not in again
