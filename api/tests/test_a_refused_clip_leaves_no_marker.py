"""A file she was never handed must not be referred to in the line she is handed.

ctrl-v writes `[Image #n]` into the box at paste time; the bytes travel later, when `_flush_clips`
walks the queued clips. The session cap is 4, so a fifth paste is refused out loud — but the line
went out as typed anyway, four images beside a `[Image #5]` pointing at nothing.

Dropped rather than renumbered: the marker counts the SESSION, not an index into this message (a
second message's first clip reused `clip-1.png` and overwrote the first file), so renumbering would
invent a correspondence nothing downstream ever had. A message that is nothing but a refused marker
comes back empty — the refusal is already on screen, and an empty line is not a turn."""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from kotoba.cli import slash
from kotoba.cli.app import App
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console
from kotoba.core import attachments, session_captures

PNG = b"\x89PNG\r\n\x1a\n" + b"kotoba-pixels" * 40
SID = "s-clip"


@pytest.fixture
def app(tmp_path):
    caps = Caps(color="none", background="dark", unicode=True, interactive=False, width=76,
                g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    a = App(caps, screen, prompt=None)
    a.session = SimpleNamespace(session_id=SID)
    a.buf = buf
    yield a
    attachments._pending.pop(SID, None)
    attachments._shared.pop(SID, None)
    session_captures.clear(SID)


def _pasted(app, tmp_path, count: int) -> str:
    """`count` clips queued exactly as ctrl-v queues them, and the line they left in the box."""
    marks = []
    for i in range(1, count + 1):
        name = f"clip-{i}.png"
        path = tmp_path / name
        path.write_bytes(PNG + bytes([i]))
        mark = slash.marker(i, name)
        app.clips.append((name, str(path), len(PNG) + 1, mark))
        marks.append(mark)
    app.shared_n = count
    return " ".join(marks)


def test_the_refused_clip_takes_its_marker_out_of_the_line(app, tmp_path):
    """The reproduction: five pastes with a caption, four parts, and a fifth reference to nothing."""
    line = "compare these " + _pasted(app, tmp_path, 5)
    out = app._flush_clips(line)

    assert len(attachments.take(SID)) == attachments.MAX_PER_SESSION
    assert "[Image #5]" not in out, "she was handed a reference to a file that never arrived"
    assert all(f"[Image #{i}]" in out for i in range(1, 5)), "the four that DID arrive lost their marker"
    assert out.startswith("compare these "), "the caption is the person's own words — leave them"
    assert "didn't fit" in app.buf.getvalue(), "and the person still has to be told"


def test_nothing_is_dropped_while_they_all_fit(app, tmp_path):
    line = "look " + _pasted(app, tmp_path, 4)
    out = app._flush_clips(line)

    assert out == line
    assert len(attachments.take(SID)) == 4


def test_a_caption_less_share_still_reaches_the_image_only_branch(app, tmp_path):
    """Markers and nothing else is the sentinel with a branch of its own — dropping the fifth
    marker must not knock the other four out of it."""
    out = app._flush_clips(_pasted(app, tmp_path, 5))

    assert out == "__image_only__"
    assert len(attachments.take(SID)) == attachments.MAX_PER_SESSION


def test_a_message_whose_every_marker_was_refused_has_nothing_left_to_send(app, tmp_path):
    """The cap is not the only way `_carry` says no — a clip whose file has gone takes the same branch,
    and with an empty pile behind it there is neither an image nor a word left. The refusal is already on
    screen, and an empty string is what `_loop` declines to turn into a turn."""
    line = _pasted(app, tmp_path, 1)
    (tmp_path / "clip-1.png").unlink()
    out = app._flush_clips(line)

    assert not attachments.has(SID)
    assert out == "", f"a share with nothing behind it went out as {out!r}"
    assert "can't read" in app.buf.getvalue()


def test_the_loop_does_not_open_a_turn_on_an_empty_line():
    """The guard that goes with it, read where it lives — nothing else re-checks after the flush."""
    import inspect

    src = inspect.getsource(App._loop)
    at = src.index("_flush_clips")
    assert "if " in src[at:at + 120] and "_turn(" in src[at:at + 120], \
        "the flush result reaches _turn unchecked; a message emptied by a refusal becomes an empty turn"
