"""`recall_image` may only order an analysis of images it actually attached.

The instruction was built from hits before anything knew whether fetching an image had worked. An
entry whose bytes were gone — or a hit past the image cap — still came back ordering her to analyze a
profile photo by description, with no image attached and no frame on screen: handed her own old note
describing the face, the obvious completion was to describe it back as if looking. So hits are now
split by what was witnessed: attached images get the order, the rest are named as notes with no
picture behind them, and when nothing came back the text says so and tells her to say it too. The
screen already agreed — `recalled_image` is emitted per attached image, never per hit.
"""
from __future__ import annotations

import asyncio
import base64

import pytest

import kotoba.core.visual_memory as vm
from kotoba.core import events
from kotoba.tools import ToolContext
from kotoba.tools.builtin import recall_image

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
_SID = "recall-witness"


def _ctx():
    return ToolContext(db=None, session_id=_SID, mode="companion")


def _call(query: str):
    return asyncio.run(recall_image.execute({"query": query}, _ctx()))


def _text(res) -> str:
    return getattr(res, "text", res) or ""


def _images(res) -> list:
    return list(getattr(res, "images", None) or [])


@pytest.fixture(autouse=True)
def _channel():
    q = events.register(_SID)
    yield q
    events.unregister(_SID, q)


def _frames(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# --- what she is looking at ------------------------------------------------------------------------

def test_an_attached_image_is_ordered_analysed(_channel):
    vm.add("Mimi", "the user's cat, orange", "person", _PNG, "png", "s")
    res = _call("Mimi")
    assert len(_images(res)) == 1
    assert "1 image(s) attached below; analyze them" in _text(res)
    assert [f.get("kind") for f in _frames(_channel)] == ["recalled_image"]


# --- what she is not -------------------------------------------------------------------------------

def test_missing_bytes_are_never_ordered_analysed(_channel):
    entry = vm.add("Fulano Perez", "his profile photo: dark hair, glasses", "person", _PNG, "png", "s")
    (vm.images_dir() / entry["file"]).unlink()

    res = _call("Fulano Perez")
    text = _text(res)
    assert _images(res) == []
    assert _frames(_channel) == [], "nothing reached the screen either"
    assert "analyze" not in text.lower()
    assert "you are NOT looking at anything" in text
    assert "your own old words, not a picture" in text
    assert "can't bring it up right now" in text, "she needs the true sentence, not just the absence of a lie"


def test_the_old_note_is_still_handed_over_so_she_can_answer_from_it(_channel):
    entry = vm.add("Fulano Perez", "his profile photo: dark hair, glasses", "person", _PNG, "png", "s")
    (vm.images_dir() / entry["file"]).unlink()
    assert "dark hair, glasses" in _text(_call("Fulano Perez"))


def test_a_mixed_result_separates_what_is_attached_from_what_is_not(_channel):
    gone = vm.add("Fulano Perez", "his profile photo: dark hair, glasses", "person", _PNG, "png", "s")
    vm.add("Fulano Perez", "another shot, at the office", "person", _PNG, "png", "s")
    (vm.images_dir() / gone["file"]).unlink()

    res = _call("Fulano Perez")
    text = _text(res)
    assert len(_images(res)) == 1
    head, tail = text.split("ALSO saved under this query but NOT attached", 1)
    assert "another shot, at the office" in head          # the one she can see
    assert "dark hair, glasses" in tail                    # the one she cannot
    assert "never describe these as if you were looking at them" in text


def test_hits_beyond_the_image_cap_are_listed_as_not_attached(_channel):
    for i in range(recall_image._MAX_IMAGES + 2):
        vm.add("Fulano Perez", f"shot number {i}", "person", _PNG, "png", "s")
    res = _call("Fulano Perez")
    assert len(_images(res)) == recall_image._MAX_IMAGES
    assert "ALSO saved under this query but NOT attached" in _text(res)
    assert len(_frames(_channel)) == recall_image._MAX_IMAGES


def test_no_hits_still_falls_through_to_the_graceful_fail_line(_channel):
    assert _call("someone she never met") is None
