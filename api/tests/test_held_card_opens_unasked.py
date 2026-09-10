"""A held card puts itself on the glass, unasked. Reported live: a card is something she is asking
for, so it should appear on its own — if it does not, there is no way for the person to notice it.

This overturns the shipped premise that a hold waits for Enter. The door is the prompt's own exit:
the beat ends an idle prompt the way a menu pick does, and the existing deferred-card pass draws it
with the region, the rail and every guard it already had. The gate must not get easier to answer by
accident, so the door only opens over an empty box, never over a panel or an armed ctrl-c, and an
unasked card refuses every key for a short arming grace — a keystroke in flight across the handover
flashes instead of answering."""
from __future__ import annotations

import asyncio
import io
import time
from types import SimpleNamespace

from conftest import needs_posix_terminal
from kotoba.cli import state
from kotoba.cli.app import CARD_GRACE_S, App
from kotoba.cli.approvals import Card
from kotoba.cli.input import keys
from kotoba.cli.render import cards
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

pytestmark = needs_posix_terminal


def wired() -> App:
    caps = Caps(color="none", background="dark", unicode=True, interactive=True,
                width=96, g=dict(GLYPHS_UNICODE))
    screen = Screen(caps, console=build_console(caps, file=io.StringIO()),
                    portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1",
                                  events=SimpleNamespace(steps={}, settle=lambda: None))
    return app


def at_idle_prompt(app: App, typed: str = "") -> SimpleNamespace:
    """A live prompt, faked at the seam `_card_deliverable` reads: a running app and its buffer."""
    fake = SimpleNamespace(app=SimpleNamespace(_is_running=True, exits=[], tasks=[],
                                               invalidate=lambda: None),
                           default_buffer=SimpleNamespace(text=typed))
    fake.app.exit = lambda **kw: fake.app.exits.append(kw)
    fake.app.create_background_task = lambda coro: fake.app.tasks.append(coro) or coro.close()
    app.prompt.session = fake
    app.prompt.picker = None
    app.prompt.leaving = False
    return fake


def a_card(**over) -> Card:
    frame = {"request_id": "r1", "mode": "approval", "label": "rm -rf build",
             "family": "rm", "can_always": False}
    return Card.from_frame({**frame, **over})


def held(app: App) -> state.Held:
    async def go():
        return app._hold(a_card())

    return asyncio.run(go())


def test_a_held_card_at_an_idle_empty_prompt_is_deliverable_and_wakes_the_beat_at_once():
    app = wired()
    at_idle_prompt(app)
    held(app)
    assert app._card_deliverable() is True
    assert app._wake() == 0.02


def test_a_half_typed_line_blocks_the_door_and_the_beat_polls_for_it_to_clear():
    """The one state in which a rail key could eat a keystroke is a message in progress, so the card
    keeps today's bell-and-bar behaviour until the box is the card's own."""
    app = wired()
    fake = at_idle_prompt(app, typed="ahora te cuento")
    held(app)
    assert app._card_deliverable() is False
    assert app._wake() == 0.5, "the beat polls so the card opens the moment the line is gone"
    fake.default_buffer.text = ""
    assert app._card_deliverable() is True


def test_a_panel_an_armed_ctrl_c_a_turn_or_a_dead_app_each_keep_the_door_shut():
    app = wired()
    fake = at_idle_prompt(app)
    held(app)
    app.prompt.picker = object()
    assert app._card_deliverable() is False
    app.prompt.picker = None
    app.prompt.leaving = True
    assert app._card_deliverable() is False
    app.prompt.leaving = False
    app.turn_start = time.monotonic()
    assert app._card_deliverable() is False
    app.turn_start = 0.0
    fake.app._is_running = False
    assert app._card_deliverable() is False


def test_the_door_is_the_prompts_own_exit_and_it_marks_the_opening_as_unasked():
    """`exit(result="")` is the same door a menu pick leaves by, so `_loop` runs its existing
    `_deferred` pass — one renderer, one key reader, no second card surface."""
    app = wired()
    fake = at_idle_prompt(app)
    held(app)
    app._yield_prompt()
    assert fake.app.exits == [{"result": ""}]
    assert app._auto_opened is True


def test_an_unasked_card_refuses_every_key_for_the_arming_grace_and_then_answers():
    """The keystroke in flight across the handover must flash, not answer: a `y` that arrives inside
    CARD_GRACE_S of an unasked opening is discarded the way junk is, and the same `y` after the grace
    is a decision."""
    app = wired()
    drawn = cards.Approval("rm -rf build", "recursive-delete", "rm", can_always=False)
    app.approval = drawn
    app._card_not_before = time.monotonic() + 0.2
    fed = iter(["y", "", "y"])

    def feed(timeout: float = 0.25) -> str:
        time.sleep(0.12)
        return next(fed, "y")

    orig, keys.read_input = keys.read_input, feed
    try:
        t0 = time.monotonic()
        answer = app._read_approval(drawn)
    finally:
        keys.read_input = orig
    assert answer == "y"
    assert time.monotonic() - t0 >= 0.2, "the first y landed inside the grace and was refused"
    assert drawn.flash_until > t0, "a refused key flashes — it is seen, never swallowed"


def test_the_deferred_pass_arms_the_grace_only_when_it_was_opened_unasked():
    app = wired()
    app.screen.measure = lambda: 0
    seen: list[float] = []

    async def fake_answer(held_row):
        seen.append(app._card_not_before)
        app._settle(held_row, "n")

    app._answer_held = fake_answer

    async def unasked():
        app._hold(a_card())
        app._auto_opened = True
        return await app._deferred()

    assert asyncio.run(unasked()) is True
    assert seen and seen[0] > time.monotonic() - 1 + CARD_GRACE_S - 1.5
    assert seen[0] > 0.0 and app._auto_opened is False

    app2 = wired()
    app2.screen.measure = lambda: 0
    seen2: list[float] = []

    async def fake_answer2(held_row):
        seen2.append(app2._card_not_before)
        app2._settle(held_row, "n")

    app2._answer_held = fake_answer2

    async def asked():
        app2._hold(a_card())
        return await app2._deferred()

    assert asyncio.run(asked()) is True
    assert seen2 == [0.0], "an Enter the person pressed needs no grace — they asked for the card"
