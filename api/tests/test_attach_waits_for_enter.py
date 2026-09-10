"""`/attach` hands you a line to finish — never a turn nobody asked for.

`slash._attach` promises the line is queued rather than sent, so you can say what to look for. It
appended to `app.pending_send`, which `app._loop` drains AROUND the editor — every other line there
already carries an enter. `/attach` put an unsent line on that same list, buying a turn with no
moment to add anything. The fix seeds `Prompt.pending` instead, passed to prompt_toolkit as
`default`: it fills the buffer and waits, since nothing here passes `accept_default`.

`look at @sample.txt` sent her hunting for a literal file, since `@name` was never a handle — she
answered she could not find it."""
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest

from kotoba.cli import slash
from kotoba.cli.app import App
from kotoba.cli.input.prompt import Prompt
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console
from kotoba.core import attachments, session_captures

PNG = b"\x89PNG\r\n\x1a\n" + b"kotoba-pixels" * 40


@pytest.fixture
def app():
    caps = Caps(color="none", background="dark", unicode=True, interactive=False, width=96,
                g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    a = App(caps, screen, prompt=None)
    a.session = SimpleNamespace(session_id="s-attach")
    yield a
    attachments._pending.pop("s-attach", None)
    attachments._shared.pop("s-attach", None)
    session_captures.clear("s-attach")


def test_attach_leaves_the_line_in_the_box_and_not_on_the_channel_that_bypasses_it(app, tmp_path):
    shot = tmp_path / "trace.png"
    shot.write_bytes(PNG)

    slash._attach(app, str(shot))

    assert app.pending_send == [], (
        "`pending_send` is drained around the editor because its lines already carry an enter; a line "
        "nobody sent must never go on it")
    assert app.prompt.pending == "[Image #1]", "the marker belongs in the box, waiting for enter"
    assert app.shared_n == 1 and [g.kind for g in app.gifts] == ["sent"]


def test_the_sentence_for_a_text_file_names_a_path_that_resolves(app, tmp_path):
    """A file only copied into the workdir keeps a verb — nothing else tells her to open it — but the
    name it carries has to be one `read_file` can take. `save_text` stores a basename at the root of
    the library, which is her workdir, so the bare name is that path."""
    notes = tmp_path / "sample.txt"
    notes.write_text("hola")

    slash._attach(app, str(notes))

    assert app.pending_send == []
    assert "@" not in app.prompt.pending, "the `@name` was never a handle — she looked for it literally"
    assert "sample.txt" in app.prompt.pending and app.prompt.pending.startswith("read ")
    assert app.shared_n == 0, "a file she cannot see does not take an image's number"


def test_a_second_attach_adds_to_the_line_instead_of_replacing_what_is_in_it(app, tmp_path):
    one, two = tmp_path / "a.png", tmp_path / "b.png"
    one.write_bytes(PNG)
    two.write_bytes(PNG)

    slash._attach(app, str(one))
    slash._attach(app, str(two))

    assert app.prompt.pending == "[Image #1] [Image #2]" and app.pending_send == []


def test_a_file_that_could_not_be_carried_seeds_nothing(app, tmp_path):
    slash._attach(app, str(tmp_path / "not-here.png"))

    assert app.prompt.pending == "" and app.pending_send == [] and app.gifts == []


def test_pressing_enter_on_the_seeded_marker_alone_sends_the_no_caption_sentinel(app, tmp_path):
    """The seeded line goes out through `_flush_clips` like any other, so a marker with no words added
    to it still reaches the "shared with NO caption" branch — the one that stops her
    describing a picture in a language nobody was speaking. `/attach` carried the bytes itself, so there
    is nothing on `app.clips` to carry twice."""
    shot = tmp_path / "trace.png"
    shot.write_bytes(PNG)

    slash._attach(app, str(shot))
    line = app.prompt.pending

    assert app.clips == []
    assert app._flush_clips(line) == "__image_only__"
    assert app._flush_clips("what changed here? [Image #1]") == "what changed here? [Image #1]"


def test_the_box_is_seeded_with_the_line_and_still_waits_for_enter():
    """What `Prompt.pending` actually does: prompt_toolkit's `default` fills the buffer and leaves the
    caret in it. `accept_default` is the argument that would send it, and nothing passes that."""
    seen: dict = {}

    class _Session:
        async def prompt_async(self, **kw):
            seen.update(kw)
            return kw["default"]

    caps = Caps(color="none", background="dark", unicode=True, interactive=False, width=96,
                g=dict(GLYPHS_UNICODE))
    prompt = Prompt(caps)
    prompt.session = _Session()
    prompt.pending = "[Image #1]"

    assert asyncio.run(prompt.ask_async()) == "[Image #1]"
    assert seen["default"] == "[Image #1]" and "accept_default" not in seen
    assert prompt.pending == "", "the seed is spent once, like every other line the editor takes"
