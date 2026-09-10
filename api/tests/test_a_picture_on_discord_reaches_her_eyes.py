"""Someone posted an image and she said she could not see any image attached.

She has vision on every other surface; on Discord she was simply never handed the bytes. The answer
she gave was honest, which is the only reason this was a missing capability and not a lie — but the
picture was right there above her reply.

The refusal path is the other half: a file reported as received and then answered around is the shape
of this that does read as lying, so anything that did not fit has to reach her words.
"""
from __future__ import annotations

import asyncio

from kotoba.core import attachments
from kotoba.discord import media


class _Att:
    def __init__(self, name: str, ctype: str, raw: bytes, size: int | None = None) -> None:
        self.filename = name
        self.content_type = ctype
        self._raw = raw
        self.size = len(raw) if size is None else size

    async def read(self) -> bytes:
        return self._raw


class _Msg:
    def __init__(self, atts) -> None:
        self.attachments = list(atts)


def _stash(atts, sid: str):
    return asyncio.run(media.stash(_Msg(atts), sid))


def test_an_image_reaches_the_next_turn():
    sid = "discord:test-image"
    assert _stash([_Att("meme.png", "image/png", b"\x89PNG-pretend")], sid) == []
    parts = attachments.take(sid)
    assert len(parts) == 1
    assert parts[0]["type"] == "input_image"
    assert parts[0]["image_url"].startswith("data:image/png;base64,")


def test_a_pdf_reaches_the_next_turn_as_a_file():
    sid = "discord:test-pdf"
    assert _stash([_Att("doc.pdf", "application/pdf", b"%PDF-1.4")], sid) == []
    parts = attachments.take(sid)
    assert parts[0]["type"] == "input_file"
    assert parts[0]["filename"] == "doc.pdf"


def test_a_pdf_named_by_its_extension_alone_still_counts():
    sid = "discord:test-pdf2"
    assert _stash([_Att("doc.pdf", "", b"%PDF")], sid) == []
    assert attachments.take(sid)[0]["type"] == "input_file"


def test_something_she_cannot_look_at_is_quietly_skipped():
    """A .zip is not a refusal to report — she was never going to look at it."""
    sid = "discord:test-zip"
    assert _stash([_Att("build.zip", "application/zip", b"PK\x03\x04")], sid) == []
    assert attachments.take(sid) == []


def test_a_file_too_big_is_named_back_not_swallowed():
    sid = "discord:test-big"
    huge = _Att("huge.png", "image/png", b"x", size=media.MAX_BYTES + 1)
    refused = _stash([huge], sid)
    assert refused == ["huge.png (too big)"]
    assert attachments.take(sid) == []


def test_the_fifth_file_in_one_message_is_named_back():
    """The store caps one message, and a cap that is not reported is a file she answers around."""
    sid = "discord:test-cap"
    many = [_Att(f"p{i}.png", "image/png", b"x") for i in range(attachments.MAX_PER_SESSION + 1)]
    refused = _stash(many, sid)
    assert refused == [f"p{attachments.MAX_PER_SESSION}.png"]
    assert len(attachments.take(sid)) == attachments.MAX_PER_SESSION
