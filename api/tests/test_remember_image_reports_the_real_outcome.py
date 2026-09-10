"""`remember_image` may only report a save it witnessed.

`visual_memory.add()` returns None for five different reasons — a duplicate, an empty `about`, an
oversized image, an undecodable data URL, a failed write — and only the first means the image was kept.
The branch instead searched for ANY entry under the entity name, true in the commonest case (a second
photo of someone already kept), so a too-big image or a failed write were both announced as saved. Two
witnesses replace the search: `duplicate_of` says which None it was, and `has_image` confirms the bytes
are on disk, since "kept" is a claim about the image, not an index row. Everything else now reports as
a save that did NOT happen, filed as a failure rather than a refusal, since the attempt here may have
left a half-written file and so the row must survive."""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

import kotoba.core.visual_memory as vm
from kotoba.core import attachments
from kotoba.core.loop import _nothing_ran, record_tool_failures, tool_refused
from kotoba.tools import ToolContext
from kotoba.tools.builtin import remember_image

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
_SESSION = "vm-outcome"


def _data_url(raw: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def _ctx():
    return ToolContext(db=None, session_id=_SESSION, mode="companion", call_id="c1")


def _share(name: str, data_url: str):
    attachments._shared[_SESSION] = [{"name": name, "data_url": data_url, "ts": 0}]


def _keep(about: str, note: str, kind: str = "person"):
    return vm.add(about, note, kind, _PNG, "png", "seed")


def _run(about: str, note: str, ctx=None):
    return asyncio.run(remember_image.execute(
        {"source": "attachment", "about": about, "note": note, "kind": "person"}, ctx or _ctx()
    ))


@pytest.fixture(autouse=True)
def _clean():
    attachments._shared.pop(_SESSION, None)
    yield
    attachments._shared.pop(_SESSION, None)


# --- the ONE case that really means "already kept" -------------------------------------------------

def test_an_exact_duplicate_is_the_only_thing_reported_as_already_kept():
    _keep("Fulano Perez", "his profile photo: dark hair, glasses")
    _share("same.png", _data_url(_PNG))
    out = _run("Fulano Perez", "his profile photo: dark hair, glasses")
    assert "ALREADY keep" in out and "recall_image" in out


def test_a_second_photo_of_someone_she_keeps_is_not_a_duplicate():
    """The commonest case, and the one the entity-name search got wrong every time."""
    _keep("Fulano Perez", "his profile photo: dark hair, glasses")
    _share("beach.png", _data_url(_PNG))
    out = _run("Fulano Perez", "a second photo of him, at the beach")
    assert "KEPT THE IMAGE" in out
    assert any(e["note"] == "a second photo of him, at the beach" for e in vm.all_entries())


# --- the four that do not ---------------------------------------------------------------------------

def test_an_image_over_the_size_cap_is_not_reported_as_kept():
    _keep("Fulano Perez", "his profile photo: dark hair, glasses")
    _share("huge.png", _data_url(b"\x89PNG\r\n\x1a\n" + b"\x00" * (vm._MAX_IMAGE_BYTES + 10)))
    out = _run("Fulano Perez", "a second photo of him, at the beach")
    assert "NOTHING WAS SAVED" in out
    assert "IS kept" not in out and "ALREADY keep" not in out
    assert not any(e["note"] == "a second photo of him, at the beach" for e in vm.all_entries())


def test_a_failed_write_is_not_reported_as_kept(monkeypatch):
    _keep("Mimi", "the user's cat")
    _share("mimi2.png", _data_url(_PNG))

    def boom(self, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", boom)
    out = _run("Mimi", "Mimi asleep on the couch")
    assert "NOTHING WAS SAVED" in out and "ALREADY keep" not in out


def test_an_undecodable_data_url_is_not_reported_as_kept():
    _keep("Fulano Perez", "his profile photo: dark hair, glasses")
    _share("broken.png", "data:image/png;base64,!!!!")
    out = _run("Fulano Perez", "another shot, indoors")
    assert "NOTHING WAS SAVED" in out


def test_an_index_row_whose_bytes_are_gone_is_not_a_kept_image():
    """"Kept" is a claim about the image. A duplicate whose file has vanished cannot back it."""
    entry = _keep("Ghost", "a photo that will lose its bytes")
    (vm.images_dir() / entry["file"]).unlink()
    _share("ghost2.png", _data_url(b"\x89PNG\r\n\x1a\n" + b"\x00" * (vm._MAX_IMAGE_BYTES + 10)))
    out = _run("Ghost", "a photo that will lose its bytes")
    assert "NOTHING WAS SAVED" in out
    assert vm.duplicate_of("Ghost", "a photo that will lose its bytes", "person") is not None
    assert vm.has_image(vm.duplicate_of("Ghost", "a photo that will lose its bytes", "person")) is False


# --- a save that did not happen is not a success ----------------------------------------------------

def test_a_failed_save_leaves_a_ran_and_failed_witness():
    """Without a witness the non-empty explanation grades ok=True: a green row, an `executed` audit line
    and the spoken "Saved to my visual memory~" over an image that was never stored. With the WRONG
    witness the row disappears instead, and an attempt that may have written bytes leaves no trail."""
    ctx = _ctx()
    _share("huge.png", _data_url(b"\x89PNG\r\n\x1a\n" + b"\x00" * (vm._MAX_IMAGE_BYTES + 10)))
    with record_tool_failures() as failed:
        _run("Nobody", "too big to keep", ctx)
    assert failed, "a save that was attempted and did not land left no failure witness"
    assert tool_refused(ctx, "c1") is False, "a real attempt must not be recorded as 'nothing ran'"
    assert _nothing_ran(ctx, "c1") is False


def test_a_real_save_leaves_no_witness_of_either_kind():
    ctx = _ctx()
    _share("ok.png", _data_url(_PNG))
    with record_tool_failures() as failed:
        out = _run("Fulano Perez", "his profile photo", ctx)
    assert "KEPT THE IMAGE" in out
    assert tool_refused(ctx, "c1") is False and failed == []


# --- the witness itself -----------------------------------------------------------------------------

def test_duplicate_of_matches_the_key_add_dedups_on():
    kept = vm.add("Fulano Pérez", "stern profile photo", "person", _PNG)
    assert vm.duplicate_of("fulano perez", "stern profile photo", "person")["id"] == kept["id"]
    assert vm.duplicate_of("Fulano Pérez", "a different note", "person") is None
    assert vm.duplicate_of("Fulano Pérez", "stern profile photo", "product") is None
    assert vm.duplicate_of("", "stern profile photo", "person") is None
    # and add() still refuses exactly what duplicate_of names
    assert vm.add("Fulano Pérez", "stern profile photo", "person", _PNG) is None
    assert vm.add("Fulano Pérez", "a different note", "person", _PNG) is not None


def test_a_ghost_row_does_not_block_re_saving_the_image_forever():
    """The reporting half asks `has_image`; the WRITING half did not, so the row that cannot back the
    claim could still veto the save that would fix it.

    `add()` returned None on any key match, ghost or not, so a VALID image under that exact
    about+note+kind was refused for good — and the honest `_not_saved` line ("It may be too big … ask
    them to send it again if they want to retry") sends the user around a loop that fails identically
    every time. There is no way out: `visual_memory.delete()` has no production caller, so nothing
    reaps the row. A duplicate is now a duplicate only while its bytes are there; the ghost is replaced
    rather than left beside its successor, or the next save would meet the ghost first all over again."""
    entry = _keep("Casper", "the photo whose bytes vanish")
    (vm.images_dir() / entry["file"]).unlink()

    _share("again.png", _data_url(_PNG))
    out = _run("Casper", "the photo whose bytes vanish")

    assert "NOTHING WAS SAVED" not in out
    dup = vm.duplicate_of("Casper", "the photo whose bytes vanish", "person")
    assert vm.has_image(dup) is True
    assert len([e for e in vm.all_entries() if e["about"] == "Casper"]) == 1, "the ghost row is gone"
