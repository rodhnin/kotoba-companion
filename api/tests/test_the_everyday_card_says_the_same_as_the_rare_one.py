"""The card that opens during an ordinary turn is the one somebody presses `y` on, and it said LESS
about the command than the card that opens out at the prompt.

`_inline` built its `Approval` with `danger=""` hard-coded and left `blast` at its default, so `why`
fell back to the generic sandbox sentence and the blast-radius rows drew nothing — the concrete danger
was on the deferred card only. Neither field is on the wire, so both paths must measure them, and the
measuring now lives in one place. Driven at both entry points with the read stubbed, since what is
under test is the card each path BUILDS: the last assertion renders both and compares the drawn rows —
a field set but never reaching the screen is not this repair."""
from __future__ import annotations

import asyncio
import io

from conftest import needs_posix_terminal
from kotoba.cli.app import App
from kotoba.cli.approvals import Card
from kotoba.cli.render import cards
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

pytestmark = needs_posix_terminal


def wired(*, interactive: bool = False) -> App:
    caps = Caps(color="none", background="dark", unicode=True, interactive=interactive,
                width=76, g=dict(GLYPHS_UNICODE))
    screen = Screen(caps, console=build_console(caps, file=io.StringIO()),
                    portrait=Portrait(caps, wanted=False))
    return App(caps, screen, prompt=None)


def a_tree(root) -> str:
    (root / "logs").mkdir()
    for i in range(3):
        (root / "logs" / f"{i}.log").write_bytes(b"x" * 4096)
    return str(root)


def drawn_inline(command: str, family: str) -> cards.Approval:
    app = wired()
    captured: dict = {}

    def reader(drawn, held=None):
        captured["drawn"] = drawn
        return "n"

    app._read_approval = reader
    asyncio.run(app._inline(Card(request_id="r1", mode="approval", label=command, family=family)))
    return captured["drawn"]


def drawn_held(command: str, family: str) -> cards.Approval:
    app = wired(interactive=True)
    captured: dict = {}

    def reader(drawn, held=None):
        captured["drawn"] = drawn
        return "n"

    app._read_approval = reader

    async def go():
        await app._answer_held(app._hold(
            Card(request_id="h1", mode="approval", label=command, family=family)))

    asyncio.run(go())
    return captured["drawn"]


def test_the_in_turn_card_names_the_danger_the_deferred_one_names(tmp_path):
    command = f"rm -rf {a_tree(tmp_path)}"
    inline, held = drawn_inline(command, "rm"), drawn_held(command, "rm")
    assert inline.danger == held.danger == "recursive-delete"
    assert "recursive delete" in inline.why and inline.why == held.why


def test_the_in_turn_card_counts_what_the_command_would_reach(tmp_path):
    """Real sizes and real counts, measured the same way on both paths — the rows are the whole answer
    to `? what it touches`, and the everyday card drew none of them."""
    command = f"rm -rf {a_tree(tmp_path)}"
    inline, held = drawn_inline(command, "rm"), drawn_held(command, "rm")
    assert inline.blast and inline.blast == held.blast
    assert "3 files" in inline.blast[0] and str(tmp_path) in inline.blast[0]


def test_a_command_that_is_neither_dangerous_nor_touching_anything_still_agrees():
    """The control: with nothing to say, the two cards say the same nothing, and the generic sandbox
    sentence is still the one that shows."""
    inline, held = drawn_inline("npm install left-pad", "npm"), drawn_held("npm install left-pad", "npm")
    assert inline.danger == held.danger == "" and inline.blast == held.blast == ()
    assert inline.why == held.why == "the sandbox is local, so anything that isn't a plain read needs you"


def test_the_two_why_panels_draw_the_same_rows(tmp_path):
    """The one that is not about fields: `?` pressed on either card puts the same words on the screen."""
    command = f"rm -rf {a_tree(tmp_path)}"
    inline, held = drawn_inline(command, "rm"), drawn_held(command, "rm")
    inline.show_why = held.show_why = True
    caps = wired().caps

    def panel(card):
        return [row.plain.strip() for row in cards.approval_rows(caps, card, 76)]

    held.held = False
    assert panel(inline) == panel(held)
    assert any("recursive delete" in row for row in panel(inline))
    assert any("3 files" in row for row in panel(inline))
