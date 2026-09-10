"""A paste may not take the screen off her, and a paste too big to look at is shown as a reference.

Measured on the real binary through a pty: a 40-line paste at 96x30 put the frame's top border on row 0
and the bar on row 29 — every row of the landing scrolled away, with 27 of the 40 lines still scrolled
inside the box. At 30 rows the transcript stays whole at 12 pasted lines and starts losing at 16; at 24
rows it is whole at 8, losing at 12 — both the step where the box passes half the window.

So the ceiling is half: a chunk folds to a reference exactly when the box could not have held it, which
also keeps a fast typist's paragraph intact — the question is SIZE, never whether it was a real paste.
The reference is a DISPLAY only: the bytes leaving the prompt are the bytes that were pasted."""
from __future__ import annotations

import asyncio

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from conftest import needs_posix_terminal
from kotoba.cli.input import prompt as prompt_mod
from kotoba.cli.input.prompt import Prompt
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.theme import GLYPHS_UNICODE

pytestmark = needs_posix_terminal

PARAGRAPH = "\n".join(f"L{i:02d} " + "x" * 20 for i in range(1, 41))
SHORT = "first line\nsecond line"


def caps(height: int = 30, width: int = 96) -> Caps:
    return Caps(color="none", background="dark", unicode=True, interactive=True,
                width=width, height=height, g=dict(GLYPHS_UNICODE))


def with_a_box(work, height: int = 30, width: int = 96):
    """`work(prompt)` against a prompt with no keyboard behind it, for the questions that are about the
    layout alone. Inside a loop, because a buffer written to off one starts a validator that needs it."""
    async def drive():
        with create_pipe_input() as pipe:
            return work(Prompt(caps(height, width), complete_while_typing=False,
                               input=pipe, output=DummyOutput()))

    return asyncio.run(drive())


def rows_asked_for(text: str, height: int = 30) -> tuple[int, int]:
    """(rows the box asks for with `text` in it, rows it is allowed)."""
    def measure(p: Prompt) -> tuple[int, int]:
        p.session.default_buffer.text = text
        return p.body.preferred_height(90, height).preferred, p.ceiling()

    return with_a_box(measure, height)


def at_the_prompt(chunks, height: int = 30, beat: float = 0.25):
    """Each string in `chunks` written into the real key parser in one go, then enter. Returns
    (what the prompt handed back, what the box was showing before the enter)."""
    async def drive():
        with create_pipe_input() as pipe:
            prompt = Prompt(caps(height), complete_while_typing=False,
                            input=pipe, output=DummyOutput())
            asking = asyncio.create_task(prompt.ask_async())
            await asyncio.sleep(0.1)
            for chunk in chunks:
                pipe.send_text(chunk)
                await asyncio.sleep(beat)
            shown = prompt.session.default_buffer.text
            if not asking.done():
                pipe.send_text("\r")
            return await asyncio.wait_for(asking, 5), shown

    return asyncio.run(drive())


def bracketed(text: str) -> str:
    return "\x1b[200~" + text + "\x1b[201~"


def test_the_box_never_takes_more_than_half_the_window():
    """The measured boundary, at every window the sweep covered and the two around them."""
    for height in (24, 30, 40, 50, 80):
        room = with_a_box(lambda p: p.ceiling(), height) + prompt_mod.BOX_CHROME
        assert room <= height // 2, f"{height} rows"


def test_a_window_too_small_for_half_keeps_a_floor_of_rows_instead():
    """`render/listing.room` makes the same trade: under about twelve rows no budget holds the view, so
    the floor keeps a usable box rather than an arithmetically correct unusable one."""
    assert with_a_box(lambda p: p.ceiling(), 10) == prompt_mod.FLOOR
    assert with_a_box(lambda p: p.ceiling(), 6) == prompt_mod.FLOOR


def test_a_paste_taller_than_the_ceiling_stops_growing_at_it():
    """The defect, in the layout that produced it: 200 lines in the buffer used to ask for 200 rows and
    prompt_toolkit gave it every row the window had."""
    wanted, allowed = rows_asked_for("\n".join(f"line {i}" for i in range(200)))
    assert wanted <= allowed, f"the box asked for {wanted} rows of a 30-row window"


def test_a_short_line_still_gets_a_short_box():
    """A ceiling that also became a floor would put a half-window box under every one-line message."""
    assert rows_asked_for("hola")[0] == 1


def test_a_paste_too_big_for_the_box_is_shown_as_a_reference():
    line, shown = at_the_prompt([bracketed(PARAGRAPH)])
    assert shown == "[Pasted text #1]", f"the box was showing {shown!r}"
    assert line == PARAGRAPH


def test_the_reference_is_a_display_and_the_paste_is_what_gets_sent():
    """The one that decides whether any of this may ship. Byte for byte, including the line the person
    typed beside the reference."""
    line, shown = at_the_prompt([bracketed(PARAGRAPH), " look at this"])
    assert shown == "[Pasted text #1] look at this", f"the box was showing {shown!r}"
    assert "[Pasted text" not in line
    assert line == PARAGRAPH + " look at this"


def test_a_paste_the_box_could_have_held_is_left_alone():
    line, shown = at_the_prompt([bracketed(SHORT)])
    assert shown == SHORT and line == SHORT


def test_a_fast_typists_paragraph_is_never_replaced_by_a_reference():
    """The rule keys off SIZE, not off "was it a real paste". Two lines typed in one burst are two
    lines."""
    line, shown = at_the_prompt(["hola\rque tal"])
    assert "Pasted text" not in shown
    assert line == "hola\nque tal"


def test_an_unbracketed_paragraph_collapses_and_still_round_trips():
    """`tmux send-keys` and `xdotool type` send no paste markers, so the first line is typed into the
    box and the rest arrives behind the enter (`_burst`). The reference stands for that rest, and what
    leaves has to be what was typed — no separator invented, none lost."""
    typed = PARAGRAPH.replace("\n", "\r")
    line, shown = at_the_prompt([typed])
    assert "[Pasted text #1]" in shown and shown.count("\n") == 0
    assert line == PARAGRAPH


def test_pasting_the_same_thing_again_expands_it_where_the_reference_stood():
    """The hint says "paste again to expand", and this is the whole of what makes that true. A hint
    naming a gesture that does nothing is the failure this project keeps finding."""
    line, shown = at_the_prompt([bracketed(PARAGRAPH), bracketed(PARAGRAPH)])
    assert shown == PARAGRAPH, "the second paste did not expand the reference"
    assert line == PARAGRAPH


def test_the_note_names_the_gesture_the_test_above_proves():
    """The words on the bar and the behaviour are one edit apart, so they are pinned together."""
    seen: list = []

    def paste_into(p: Prompt) -> None:
        p.note = seen.append
        p.insert_chunk(p.session.default_buffer, PARAGRAPH)

    with_a_box(paste_into)
    assert seen and "paste again" in seen[0][0] and "paste again" in seen[0][1]


def test_a_cleared_line_takes_its_references_with_it():
    """ctrl-c empties the box, and the text the reference stood for goes with it. Left behind, a
    marker typed out by hand — or one recalled from anywhere — would have carried a paste from a
    minute ago out on a line nobody put it in."""
    line, shown = at_the_prompt([bracketed(PARAGRAPH), "\x03", "[Pasted text #1]"])
    assert line == "[Pasted text #1]"


BIG = "\n".join(f"L{i:03d} " + "y" * 60 for i in range(1, 121))


def test_a_paste_bigger_than_one_read_still_round_trips_bracketed():
    """A terminal is read 1024 bytes at a time (`Vt100Input`, and `keys.CHUNK` on the other surface),
    and this is seven of those. prompt_toolkit's parser holds a bracketed paste open until its closing
    marker, so it is still one arrival — the point of the test is that nothing here assumed so."""
    line, shown = at_the_prompt([bracketed(BIG)], beat=0.5)
    assert shown == "[Pasted text #1]"
    assert line == BIG


def test_a_paste_bigger_than_one_read_still_round_trips_unbracketed():
    """The one that cannot be assumed. With no paste markers there is nothing to hold the arrival
    open, so a paste this size is several reads and each one is its own enter, its own `_burst` and
    its own reference — with the head of the next read typed in plainly between them. What comes back
    has to be the paragraph, byte for byte: no line ending dropped at a seam, and no separator
    invented at one (every burst opens on its own line break, which is why none is added)."""
    line, shown = at_the_prompt([BIG.replace("\n", "\r")], beat=0.8)
    assert "[Pasted text #1]" in shown
    assert line == BIG
