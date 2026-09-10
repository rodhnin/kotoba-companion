"""A note ABOUT a picture is not the picture, and this tool's answer cannot tell them apart.

The answer is the last thing she reads before speaking, so asked to keep an image she replied that she
had — with nothing in visual memory. Every answer that sounds like the fact is now held carries the
warning; the ones that claim nothing stay bare; and once the picture really is stored the warning goes,
or it becomes the lie it was written to prevent.
"""
from __future__ import annotations

import asyncio
import base64

import pytest

import kotoba.core.attachments as attachments
import kotoba.tools.builtin.memory_write as mw

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAABAAAAAQAAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
_DATA_URL = "data:image/png;base64," + base64.b64encode(_PNG).decode()


class _Ctx:
    def __init__(self, session_id):
        self.session_id = session_id


@pytest.fixture(autouse=True)
def _clean_attachments(monkeypatch):
    attachments._pending.clear()
    attachments._shared.clear()
    import kotoba.core.user_memory as um
    monkeypatch.setattr(
        um, "write_fact",
        lambda fact, topic: {"written": True, "fact": fact, "retired": [], "conflicts": []},
    )
    yield
    attachments._pending.clear()
    attachments._shared.clear()


def test_success_line_says_text_only_while_an_image_is_shared():
    attachments.add("s-note", {"type": "input_image", "image_url": _DATA_URL}, name="pic.png")
    out = asyncio.run(mw.execute({"fact": "Likes green tea", "topic": "prefs"}, _Ctx("s-note")))
    assert "Saved under" in out
    assert "TEXT ONLY" in out and "no image was stored" in out
    assert 'remember_image(source="attachment")' in out


def test_no_note_without_a_shared_image():
    out = asyncio.run(mw.execute({"fact": "Likes green tea", "topic": "prefs"}, _Ctx("s-empty")))
    assert "Saved under" in out
    assert "TEXT ONLY" not in out


def _answer(monkeypatch, res: dict, session: str = "s-dup") -> str:
    import kotoba.core.user_memory as um
    monkeypatch.setattr(um, "write_fact", lambda fact, topic: res)
    attachments.add(session, {"type": "input_image", "image_url": _DATA_URL}, name="pic.png")
    return asyncio.run(mw.execute({"fact": "Likes green tea", "topic": "prefs"}, _Ctx(session)))


@pytest.mark.parametrize("reason", ["restated", "duplicate"])
def test_an_already_remembered_answer_carries_the_note_too(monkeypatch, reason):
    """The branch that wrote nothing is the one she is most likely to repeat about the image: to her,
    "already remembered" and "saved" read the same, and neither is true of a picture."""
    out = _answer(monkeypatch, {"written": False, "reason": reason, "duplicate_of": "Likes green tea",
                                "retired": [], "conflicts": []})
    assert "Already remembered" in out
    assert "TEXT ONLY" in out and 'remember_image(source="attachment")' in out


def test_a_refusal_with_a_stored_twin_carries_the_note(monkeypatch):
    """A compound fact is refused, but if one of its halves is already stored the answer switches to
    "already remembered as ..." — same claim, same risk."""
    import kotoba.core.user_memory as um
    monkeypatch.setattr(um, "is_compound", lambda fact: True)
    monkeypatch.setattr(um, "duplicate_of", lambda fact: "Likes green tea")
    attachments.add("s-ref", {"type": "input_image", "image_url": _DATA_URL}, name="pic.png")
    out = asyncio.run(mw.execute({"fact": "Likes green tea and lives in Madrid"}, _Ctx("s-ref")))
    assert "Already remembered" in out and "TEXT ONLY" in out


def test_an_answer_that_claims_nothing_stays_bare(monkeypatch):
    """The two that never sound like a save: a refusal with nothing stored, and an empty fact. A note
    about an image she was not asked to keep is noise she has to reason past."""
    import kotoba.core.user_memory as um
    monkeypatch.setattr(um, "is_compound", lambda fact: True)
    monkeypatch.setattr(um, "duplicate_of", lambda fact: "")
    attachments.add("s-bare", {"type": "input_image", "image_url": _DATA_URL}, name="pic.png")
    refused = asyncio.run(mw.execute({"fact": "Likes tea and lives in Madrid"}, _Ctx("s-bare")))
    assert "NOT SAVED" in refused and "TEXT ONLY" not in refused
    empty = asyncio.run(mw.execute({"fact": "  "}, _Ctx("s-bare")))
    assert "TEXT ONLY" not in empty


def test_the_warning_stops_once_the_picture_really_is_stored(monkeypatch):
    """It told her not to say the image was saved — while it WAS saved. The picture stays in the list,
    because she may want to keep it again under another name; only the warning goes."""
    attachments.add("s-kept", {"type": "input_image", "image_url": _DATA_URL}, name="pic.png")
    ctx = _Ctx("s-kept")
    assert "TEXT ONLY" in asyncio.run(mw.execute({"fact": "Likes green tea"}, ctx))

    attachments.mark_kept("s-kept", "pic.png")
    out = asyncio.run(mw.execute({"fact": "Likes green tea"}, ctx))
    assert "TEXT ONLY" not in out
    assert attachments.shared_images("s-kept"), "she can no longer point at the picture at all"


def test_a_second_unkept_picture_brings_the_warning_back(monkeypatch):
    """Keeping one is not keeping the next."""
    attachments.add("s-two", {"type": "input_image", "image_url": _DATA_URL}, name="one.png")
    attachments.mark_kept("s-two", "one.png")
    assert "TEXT ONLY" not in asyncio.run(mw.execute({"fact": "Likes tea"}, _Ctx("s-two")))
    attachments.add("s-two", {"type": "input_image", "image_url": _DATA_URL}, name="two.png")
    assert "TEXT ONLY" in asyncio.run(mw.execute({"fact": "Likes tea"}, _Ctx("s-two")))
