"""One rule — "a multi-line burst is a paste" — and the two places that read it must not disagree.

`_typeahead` strips complete escape sequences before it asks, so an arrow key arriving in the same
read as an Enter is a separate keypress and the Enter still ends the line. `_burst` asked instead
whether ANYTHING was queued behind the Enter and drained only text, so a queued arrow key — or a
second Enter from a held-down key — turned that Enter into a literal newline and the message never
left. Over ssh two presses 100 ms apart routinely arrive in one read, so this is the ordinary way to
type a line and then press an arrow key. Both surfaces are driven here on purpose: the rule lives in
two places, and exercising only one of them can pass with half the repair reverted.
"""
from __future__ import annotations

import asyncio
import io

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from conftest import needs_posix_terminal
from kotoba.cli.app import App
from kotoba.cli.input.prompt import Prompt
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

pytestmark = needs_posix_terminal

HELD_ENTER = "hello\r\r\r"
THEN_UP = "hello\r\x1b[A"
UP_MID_BURST = "line one\r\x1b[Aline two\r"
WITH_A_BACKSPACE = "a\rb\x7fc\r"


def caps() -> Caps:
    return Caps(color="none", background="dark", unicode=True, interactive=True,
                width=80, g=dict(GLYPHS_UNICODE))


def at_the_prompt(chunk: str, beat: float = 0.25) -> tuple[bool, str, str | None]:
    """`chunk` written into the real key parser in ONE go. Returns (did the line go, what is in the
    box, what the prompt finally handed back)."""
    async def drive():
        with create_pipe_input() as pipe:
            prompt = Prompt(caps(), complete_while_typing=False, input=pipe, output=DummyOutput())
            asking = asyncio.create_task(prompt.ask_async())
            await asyncio.sleep(0.1)
            pipe.send_text(chunk)
            await asyncio.sleep(beat)
            sent, text = asking.done(), prompt.session.default_buffer.text
            if not sent:
                pipe.send_text("\r")
            return sent, text, await asyncio.wait_for(asking, 5)

    return asyncio.run(drive())


def mid_turn(chunk: str) -> tuple[tuple, str]:
    """The same arrival, typed while she is working. Returns (what was queued to send, what is left
    half-typed in the line that goes back to the box)."""
    screen = Screen(caps(), console=build_console(caps(), file=io.StringIO()),
                    portrait=Portrait(caps(), wanted=False))
    app = App(caps(), screen, prompt=None)
    app._typeahead(chunk)
    return app.queued, app.typing


def test_a_line_and_an_enter_still_goes_on_both_surfaces():
    sent, _, line = at_the_prompt("hola\r")
    assert sent and line == "hola"
    assert mid_turn("hola\r") == (("hola",), "")


def test_a_held_down_enter_sends_the_line_once_on_both_surfaces():
    """Nothing typed behind the second and third Enter, so they are not paste content — they are a key
    that repeated. Mid-turn the line went; at the prompt it became `hello\\n\\n\\n` and sat there."""
    assert mid_turn(HELD_ENTER)[0] == ("hello",)
    sent, box, line = at_the_prompt(HELD_ENTER)
    assert sent, f"the line never left — the box still holds {box!r}"
    assert line == "hello"


def test_an_arrow_behind_the_enter_is_a_keypress_and_not_paste_content():
    """Type a message, press Enter, press ↑ to see the last one: over ssh those two arrive in one
    read. The message has to go — and then ↑ is answered by a prompt that is empty again."""
    assert mid_turn(THEN_UP)[0] == ("hello",)
    sent, box, line = at_the_prompt(THEN_UP)
    assert sent, f"the line never left — the box still holds {box!r}"
    assert line == "hello"


def test_the_line_that_was_finished_is_never_mangled_by_what_came_after_it():
    """The worst of it: ↑ moved the cursor into the line that had just been finished and the text
    behind it was typed INTO it, so one mangled message went out in place of two clean ones."""
    assert mid_turn(UP_MID_BURST)[0] == ("line one\nline two",)
    sent, box, line = at_the_prompt(UP_MID_BURST)
    assert line is not None and "line twoline one" not in line
    assert line.startswith("line one") or box.startswith("line one")


def test_a_backspace_behind_the_enter_edits_the_burst_and_both_agree_on_the_result():
    """Text behind the Enter IS paste content, so this one is a burst on both surfaces — and the
    backspace edits it to the same two lines either way."""
    assert mid_turn(WITH_A_BACKSPACE)[0] == ("a\nc",)
    _, _, line = at_the_prompt(WITH_A_BACKSPACE)
    assert line == "a\nc"


def test_a_real_pasted_paragraph_is_still_one_thing_on_both_surfaces():
    """The behaviour this rule exists for, and the one that must not be lost to the repair: a program
    typing a paragraph at her is ONE message, never one turn per line."""
    para = "/help\nsecond pasted line"
    sent, box, line = at_the_prompt(para)
    assert not sent and box == para and line == para
    assert mid_turn(para.replace("\n", "\r") + "\r")[0] == ("/help\nsecond pasted line",)
