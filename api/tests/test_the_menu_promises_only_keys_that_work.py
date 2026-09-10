"""Two lines a launch menu used to say over a list that could not do what they described.

The placeholder told a fresh install to "type to narrow the list down" while the only row under it said
there is nothing here yet — the screen every empty section opens on. And the bar promised "enter reads
it back" on that row and on "no match", where the selection is empty and enter does nothing at all.

Both now come off one fact, asked at render time rather than banked earlier: a flag banked before
render answers for the keystroke before the one on the glass, and at the prompt nothing repaints to
correct it.
"""
from __future__ import annotations

import io

import pytest
from conftest import needs_posix_terminal
from prompt_toolkit.filters import to_filter
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from kotoba.cli.app import App, IDLE_TWINS
from kotoba.cli.input.menu import Picker
from kotoba.cli.input.prompt import Prompt
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

pytestmark = needs_posix_terminal

SETTINGS_DATA = {
    "security": {"approvals": [], "trust": "ask"}, "browser_cdp": "",
    "personality": {}, "mcp_servers": [], "pending_mcp": [], "skills": [],
    "memory": {"topics": []}, "reminders": [], "toolsets": [], "keys": [], "plugins": [],
}
A_SESSION = {"id": "s1", "started_at": "2026-01-02 03:04:05", "turns": 3,
             "opened": "about the garden"}


@pytest.fixture
def app(monkeypatch):
    """One App with a real line editor behind it, because both readers under test are drawn from the
    live box and one of them reaches for it itself."""
    with create_pipe_input() as pipe:
        caps = Caps(color="none", background="dark", unicode=True, interactive=True,
                    width=96, height=24, g=dict(GLYPHS_UNICODE))
        monkeypatch.setattr(caps, "sync_size", lambda: None)
        console = build_console(caps, file=io.StringIO())
        console.width = caps.width
        screen = Screen(caps, console=console, portrait=Portrait(caps, wanted=False))
        prompt = Prompt(caps, complete_while_typing=False, input=pipe, output=DummyOutput())
        prompt.session.default_buffer.validate_while_typing = to_filter(False)
        yield App(caps, screen, prompt)


def opened(app, kind: str, level: str = "", arg: str = "", **source) -> Picker:
    """The menu as it is once `launch` has put it on the glass: loaded, and one level deep if asked."""
    picker = Picker(app, kind)
    for name, value in source.items():
        setattr(picker, name, value)
    if level:
        picker.stack.append((level, arg))
    app.prompt.picker = picker
    return picker


def box(app):
    return app.prompt.session.default_buffer


def typed(app, text: str) -> None:
    box(app).text = text
    box(app).cursor_position = len(text)


def test_the_box_never_asks_for_a_filter_over_a_list_with_nothing_to_filter(app):
    """The hint under the empty box, against the row it is printed beneath. A fresh install has no
    conversations and no servers, and telling somebody to narrow that is advice they cannot take."""
    empty = opened(app, "sessions")
    assert empty.rows("")[0][1] == "no conversations"
    assert app.prompt._placeholder() == "nothing to narrow yet"

    bare = opened(app, "settings", level="keys", arg="MCP", data=SETTINGS_DATA)
    assert bare.rows("")[0][1] == "no servers yet"
    assert app.prompt._placeholder() == "nothing to narrow yet"


def test_the_box_still_asks_for_a_filter_wherever_there_is_a_list_to_narrow(app):
    """The other three states, so the fix is a branch and not a rewrite."""
    opened(app, "sessions", sessions=[A_SESSION])
    assert app.prompt._placeholder() == "type to narrow the list down"

    opened(app, "settings", data=SETTINGS_DATA)
    assert app.prompt._placeholder() == "type to narrow the list down"

    opened(app, "settings", level="keys", arg="BRAIN", data=SETTINGS_DATA)
    assert app.prompt._placeholder() == "type to narrow the list down"

    opened(app, "settings", level="edit", arg="model", data=SETTINGS_DATA)
    assert app.prompt._placeholder() == "type the new value"


def test_the_bar_promises_enter_only_where_enter_does_something(app):
    """Every row `picked` cannot name, checked against what `enter` actually returns on it. A key the
    bar names and the row does not answer to is worse than a bar with one fewer key on it."""
    empty = opened(app, "sessions")
    empty.sync(box(app))
    assert empty.picked() == "" and empty.enter(box(app)) == ()
    assert empty.twins() == ("esc leaves it", "esc leaves it", "esc")

    missed = opened(app, "sessions", sessions=[A_SESSION])
    typed(app, "zzz")
    missed.sync(box(app))
    assert missed.rows("zzz")[0][1] == "no match"
    assert missed.picked() == "" and missed.enter(box(app)) == ()
    assert missed.twins() == ("esc leaves it", "esc leaves it", "esc")

    inside = opened(app, "settings", level="keys", arg="REMINDERS", data=SETTINGS_DATA)
    typed(app, "")
    inside.sync(box(app))
    assert inside.picked() == "" and inside.enter(box(app)) == ()
    assert inside.twins() == ("esc goes back", "esc goes back", "esc")


def test_the_bar_keeps_every_promise_a_real_row_does_answer_to(app):
    """The rows that can be opened say so, at every level, including the one whose single row is a
    value rather than a stand-in."""
    had = opened(app, "sessions", sessions=[A_SESSION])
    typed(app, "garden")
    assert had.twins()[0] == "arrows move · enter reads it back · esc leaves it"

    typed(app, "")
    assert opened(app, "settings", data=SETTINGS_DATA).twins()[0].endswith(
        "enter opens it · esc leaves it")
    assert opened(app, "settings", level="keys", arg="BRAIN",
                  data=SETTINGS_DATA).twins()[0].endswith("enter changes it · esc goes back")
    assert opened(app, "settings", level="edit", arg="model",
                  data=SETTINGS_DATA).twins() == ("type it · enter saves it · esc goes back",
                                                  "enter saves it · esc goes back", "enter · esc")


def test_the_bar_answers_for_the_keystroke_on_the_glass_not_the_one_before_it(app):
    """Why the fact is asked and never banked. `Panel.sync` runs AFTER a render, so a flag it left
    behind describes the box as it was one keystroke ago — and out at the prompt nothing repaints on
    its own to catch up, so the bar would sit there promising `enter` over a `no match` row."""
    picker = opened(app, "sessions", sessions=[A_SESSION])
    picker.sync(box(app))
    typed(app, "zzz")
    assert "enter" not in picker.twins()[0]
    typed(app, "garden")
    assert "enter reads it back" in picker.twins()[0]


def bar(app) -> str:
    """The bar as prompt_toolkit will paint it, fragments joined — the thing a person actually reads."""
    return "".join(text for _, text in app._toolbar())


def hint_in_the_box(app) -> str:
    """The placeholder as prompt_toolkit will ask for it: the session's own callable, not the method."""
    return "".join(text for _, text in app.prompt.session.placeholder())


def test_the_keys_the_menu_answers_for_are_the_keys_on_the_bar(app):
    """`twins()` is a phrase until something draws it, and everything above stops at the phrase. The bar
    out at the prompt is prompt_toolkit's, drawn by `App._toolbar`, and a panel's keys reach it only
    because that function asks the PICKER for them instead of the out-of-turn state it would otherwise
    use. Ask only the picker and the file passes with the bar promising `enter` over `no match`, which
    is the whole defect."""
    empty = opened(app, "sessions")
    empty.sync(box(app))
    assert "enter" not in bar(app), bar(app)
    assert "esc leaves it" in bar(app)

    had = opened(app, "sessions", sessions=[A_SESSION])
    typed(app, "garden")
    had.sync(box(app))
    assert "enter reads it back" in bar(app), bar(app)

    typed(app, "zzz")
    assert "enter" not in bar(app), "the only row under it says no match"


def test_the_bar_says_which_list_it_is_and_never_falls_back_to_the_idle_one(app):
    """The other half of the same wiring: with a menu open the right slot is the panel's breadcrumb, so a
    bar that had quietly gone back to the out-of-turn state would still be missing `enter` on the rows
    above and pass for the right reason nowhere."""
    opened(app, "settings", level="keys", arg="BRAIN", data=SETTINGS_DATA).sync(box(app))
    drawn = bar(app)
    assert "settings · BRAIN" in drawn, drawn
    assert "esc goes back" in drawn, drawn
    assert not any(twin and twin in drawn for twin in IDLE_TWINS), drawn


def test_the_hint_the_box_shows_is_the_one_the_panel_was_asked_for(app):
    """`_placeholder` is a method until the editor reads it, and it is read through the callable the
    session was BUILT with. Every assertion above calls the method by hand, so a box that had stopped
    asking would keep all of them green while a fresh install is told to narrow a list with nothing
    in it."""
    opened(app, "sessions")
    assert hint_in_the_box(app) == "nothing to narrow yet"

    opened(app, "sessions", sessions=[A_SESSION])
    assert hint_in_the_box(app) == "type to narrow the list down"

    opened(app, "settings", level="edit", arg="model", data=SETTINGS_DATA)
    assert hint_in_the_box(app) == "type the new value"
