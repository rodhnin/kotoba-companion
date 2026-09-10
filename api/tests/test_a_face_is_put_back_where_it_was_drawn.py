"""Her face goes back to the column it was DRAWN at, not to a constant somebody picked.

Three writers place her: the boot header, the live region while she speaks, and the menu that
restores every face when a panel closes over them — by ROW, re-emitting whatever art a frozen row
holds, with no idea which face that is. One constant was harmless while every face shared a margin;
once the reply face earned its own, the menu still returned everyone to the boot INDENT, and typing
`/` slid her sideways on a green suite that never checked the third writer.

The column now travels WITH the art: recorded where composed, banked beside the payload, and
handed back to whichever writer restores it."""
from __future__ import annotations

import io
import re

import pytest

from kotoba.cli.input.menu import Menu
from kotoba.cli.render import art
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import INDENT, INLINE_NUDGE, GUTTER, Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import build_console


@pytest.fixture
def screen(monkeypatch) -> Screen:
    """A real Screen over a sixel portrait, with chafa stubbed. The tier is ASSERTED: `Caps` is not
    interactive under pytest, so an unchecked portrait hands back numbers that are all zero."""
    def sixel(emotion, scale, cell, outlined=False):
        cols, rows = art.cells(emotion, scale, cell, outlined)
        return ("\x1bPq#0;2;0;0;0" + "?" * cols + "\x1b\\", cols, rows)

    monkeypatch.setattr(art, "sixel", sixel)
    monkeypatch.setenv("KOTOBA_FORCE_PORTRAIT", "sixel")
    caps = Caps(color="truecolor", background="dark", unicode=True, interactive=True,
                sixel=True, width=120, height=40, cell=(11, 25))
    box = Portrait(caps)
    assert box.mode == "sixel", "the ladder refused — every number below would be zero"
    console = build_console(caps, file=io.StringIO())
    console.width = caps.width
    return Screen(caps, console=console, portrait=box)


def test_the_two_faces_do_not_share_a_column(screen):
    """The premise. If they ever do again, everything below passes for the wrong reason."""
    box = screen.portrait
    assert box.inline_indent == INDENT + INLINE_NUDGE
    assert INLINE_NUDGE > 0, "the reply face is back at the boot margin — this file proves nothing"
    assert box.inline_gutter == box.icols + box.inline_indent + GUTTER, "the text did not move with her"


def test_the_column_is_banked_with_the_art_and_comes_back_with_it(screen):
    screen.kept(["header row", "and another"], "\x1bPq-header\x1b\\", INDENT)
    screen.kept(["reply row"], "\x1bPq-reply\x1b\\", screen.portrait.inline_indent)
    top = len(screen.tail)

    banked = {screen.frozen(r, top)[1]: screen.frozen(r, top)[2] for r in range(top)
              if screen.frozen(r, top)[1]}
    assert banked == {"\x1bPq-header\x1b\\": INDENT,
                      "\x1bPq-reply\x1b\\": screen.portrait.inline_indent}


def test_the_menu_puts_each_face_back_at_its_own_column(screen):
    """The real `_art`, driven. Two faces on screen at two columns; both must return to their own."""
    screen.kept(["header row", "second row"], "\x1bPq-header\x1b\\", INDENT)
    screen.kept(["reply row"], "\x1bPq-reply\x1b\\", screen.portrait.inline_indent)

    menu = Menu(screen, prompt=None)
    menu.top = len(screen.tail)
    emitted = menu._art(0, menu.top)
    assert emitted, "the menu restored no art at all — this test is looking at nothing"

    placed = {}
    for chunk in emitted:
        m = re.match(r"\x1b\[(\d+);(\d+)H(.*)", chunk, re.S)
        assert m, f"the menu emitted something that is not a placement: {chunk[:40]!r}"
        placed[m.group(3)] = int(m.group(2)) - 1        # the escape is 1-based
    assert placed == {"\x1bPq-header\x1b\\": INDENT,
                      "\x1bPq-reply\x1b\\": screen.portrait.inline_indent}, (
        f"a face came back at a column it was not drawn at: {placed} — that is her jumping when a "
        f"panel opens")


def test_each_composition_draws_at_the_column_it_banks(screen):
    """`last_col` is a promise about bytes already written, so it is checked against them."""
    box = screen.portrait
    for compose, want in ((lambda: box.header([f"row {i}" for i in range(box.rows)], "neutral"), INDENT),
                          (lambda: box.inline(["a reply", "on two lines"], "neutral")[0],
                           box.inline_indent)):
        text = compose()
        moves = {int(m) for m in re.findall(r"\r\x1b\[(\d+)C", text)}
        assert moves == {want}, f"drawn at {moves}, expected {want}"
        assert box.last_col == want, f"banked {box.last_col}, drew {want}"


def test_the_live_face_uses_the_reply_margin():
    """`region.py` paints the face she is speaking with, which is the reply face, so it reads the reply
    margin. With the region painting, the committed block emits no sixel at all — the live column is
    the one that stands."""
    source = (__import__("pathlib").Path(__file__).resolve().parents[1]
              / "src" / "kotoba" / "cli" / "render" / "region.py").read_text(encoding="utf-8")
    assert "screen.portrait.inline_indent" in source, "region.py stopped reading the reply margin"
    assert "import INDENT" not in source, "region.py went back to the boot margin"


def test_the_height_is_banked_with_the_art_and_it_is_that_faces_own(screen):
    """The same lesson one field over: a painter that reads the bank BY ROW cannot
    know which face it is holding, and `max(rows, irows)` gave the reply face the boot face's height.
    So `last_reach` is recorded where the face is composed, banked with the art, and handed back."""
    box = screen.portrait
    boot, inline = box.rows_for("neutral"), box.rows_for("neutral", art.INLINE_SCALE)
    assert boot > inline, "the two tiers are one size — this test cannot tell them apart"
    box.header([f"row {i}" for i in range(box.rows)], "neutral")
    assert box.last_reach == boot
    box.inline(["a reply"], "neutral")
    assert box.last_reach == inline
    screen.kept(["header row"], "\x1bPq-header\x1b\\", INDENT, boot)
    screen.kept(["reply row"], "\x1bPq-reply\x1b\\", box.inline_indent, inline)
    top = len(screen.tail)
    assert screen.frozen(0, top) == ("header row", "\x1bPq-header\x1b\\", INDENT, boot)
    assert screen.frozen(1, top) == ("reply row", "\x1bPq-reply\x1b\\", box.inline_indent, inline)
    assert screen.frozen(top, top) == ("", "", INDENT, 0), "the app's rows carry no face"
