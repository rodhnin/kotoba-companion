"""Five seams between the prompt, the card region and the beat, each reproduced on the real binary.

- A released region kept the prompt's stale size, scrolling header rows away for good.
- A resize serviced while a card owned the region hit a null gutter and crashed the commit.
- Ctrl+C at a held card ended the whole session instead of just the turn.
- A builder raising inside the beat reached prompt_toolkit's own handler and ate the next Enter.
- Even made survivable, ctrl-c still tore the glass: the tty flushes output on INTR unless `NOFLSH`
  is held for as long as a card region stands."""
from __future__ import annotations

import asyncio
import io
import time
from types import SimpleNamespace

from rich.text import Text

from conftest import needs_posix_terminal
from kotoba.cli import state
from kotoba.cli.app import App
from kotoba.cli.input import keys
from kotoba.cli.render import cards
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console


def a_screen(printed: int = 12) -> Screen:
    caps = Caps(color="none", background="dark", unicode=True, interactive=True,
                width=96, height=30, g=dict(GLYPHS_UNICODE))
    caps.sync_size = lambda: None
    caps.fps = 12
    screen = Screen(caps, console=build_console(caps, file=io.StringIO()),
                    portrait=Portrait(caps, wanted=False))
    for i in range(printed):
        screen.out(Text(f"transcript row {i}"))
    screen.measure = lambda: 0
    return screen


def wired(screen: Screen) -> App:
    app = App(screen.caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1",
                                  events=SimpleNamespace(steps={}, settle=lambda: None))
    return app


def quiet_keyboard(monkeypatch) -> None:
    """`read_input` with nothing arriving, quickly: the reader thread lets go the moment the card is
    cleared under it instead of parking a quarter second per poll."""
    monkeypatch.setattr(keys, "read_input", lambda timeout=0.0: (time.sleep(0.005), "")[1])


# --- the room the next prompt is pinned with ---------------------------------------------------------

@needs_posix_terminal
def test_rows_printed_after_a_region_released_come_off_the_room():
    screen = a_screen()
    app = wired(screen)
    app._release(SimpleNamespace(released=8))
    assert app._pin_room() == 8, "nothing printed since: the released room is the room"
    app._release(SimpleNamespace(released=8))
    screen.chrome("sandbox → docker — everything she runs stays in a container")
    screen.blank()
    assert app._pin_room() == 6, "two rows went out under the cursor, so two fewer stand below it"
    assert app.room == 0, "consumed — the next prompt asks again"


@needs_posix_terminal
def test_a_room_printed_past_the_foot_is_the_last_row_and_never_negative():
    screen = a_screen()
    app = wired(screen)
    app._release(SimpleNamespace(released=3))
    for _ in range(5):
        screen.blank()
    assert app._pin_room() == 1


@needs_posix_terminal
def test_with_no_room_released_the_terminal_is_asked():
    screen = a_screen()
    app = wired(screen)
    asked = []
    app._still_pinned = lambda: asked.append(True) or 0
    assert app._pin_room() == 0 and asked


# --- a resize over a rail at the prompt --------------------------------------------------------------

@needs_posix_terminal
def test_a_resize_over_the_confirm_rail_before_any_turn_keeps_the_console_printing():
    """`_on_resize` re-pins the region and pushes the gutter guard back; `_deferred` and
    `_confirm_card` must hand it the guard they opened, or it pushes `None` (no turn yet) or the last
    turn's guard bound to a dead Region."""
    screen = a_screen()
    app = wired(screen)
    hooks = []

    def read(card):
        app._resized = True
        app._on_resize()
        hooks.append([type(h).__name__ for h in screen.console._render_hooks])
        return "y"

    app._read_confirm = read
    card = cards.Confirm("sandbox local → docker", "that moves everything into a container")
    assert asyncio.run(app._confirm_card(card)) is True
    assert hooks and "NoneType" not in hooks[0]
    assert hooks[0].count("Gutter") == 1
    assert screen.console._render_hooks == [], "the region and its guard both left with the rail"


@needs_posix_terminal
def test_a_resize_over_the_held_card_pushes_the_guard_of_this_region_and_not_the_last_turns():
    screen = a_screen()
    app = wired(screen)
    stale = SimpleNamespace(live=None)
    app.gutter = stale
    seen = []

    def read(drawn, held=None):
        app._resized = True
        app._on_resize()
        seen.append(app.gutter)
        return "y"

    app._read_approval = read
    held = state.Held(verb="run", cmd="echo listo", danger="", family="echo", intent="", blast=(),
                      took=0.0, ok=False, detail="", rid="r1", state="ask",
                      asked=time.monotonic(), can_always=True)
    app.holds.append(held)

    async def go():
        app._held_keys[held.rid] = asyncio.get_running_loop().create_future()
        return await app._deferred()

    assert asyncio.run(go()) is True
    assert seen and seen[0] is not stale and seen[0] is not None
    assert seen[0].live is not None


# --- ctrl-c at a card ------------------------------------------------------------------------------

@needs_posix_terminal
def test_ctrl_c_at_the_held_card_is_the_cards_own_no_and_not_the_sessions_end(monkeypatch):
    """asyncio.run answers SIGINT by cancelling the main task, which is asleep on the reader's thread.
    A card is a gate: the cancel is the `n` the rail already draws, the receipt says so, the ask that
    was sleeping on the card gets the same `n`, and the loop goes on to the next prompt."""
    quiet_keyboard(monkeypatch)
    screen = a_screen()
    app = wired(screen)
    committed = []
    real_commit = app._commit

    def commit(row):
        committed.append(row.plain)
        real_commit(row)

    app._commit = commit
    held = state.Held(verb="run", cmd="echo listo > qa-aprobado.txt", danger="", family="echo",
                      intent="", blast=(), took=0.0, ok=False, detail="", rid="r1", state="ask",
                      asked=time.monotonic(), can_always=True)
    app.holds.append(held)

    async def go():
        fut = asyncio.get_running_loop().create_future()
        app._held_keys[held.rid] = fut
        task = asyncio.create_task(app._deferred())
        while app.approval is None:
            await asyncio.sleep(0.005)
        task.cancel()
        drew = await task
        return drew, await fut

    drew, key = asyncio.run(go())
    assert drew is True, "the pass finished on its own — the cancel never left it"
    assert key == "n"
    assert not app.holds and app.overlay is None and app.approval is None
    assert any("no" in row and "echo listo" in row for row in committed), committed


@needs_posix_terminal
def test_ctrl_c_at_the_confirm_rail_leaves_it_as_it_is(monkeypatch):
    quiet_keyboard(monkeypatch)
    screen = a_screen()
    app = wired(screen)
    committed = []
    real_commit = app._commit

    def commit(row):
        committed.append(row.plain)
        real_commit(row)

    app._commit = commit
    card = cards.Confirm("sandbox local → docker", "that moves everything into a container")

    async def go():
        task = asyncio.create_task(app._confirm_card(card))
        while app.confirm is None:
            await asyncio.sleep(0.005)
        task.cancel()
        return await task

    assert asyncio.run(go()) is False
    assert app.overlay is None and app.confirm is None and app.region is None
    assert any("left as it was" in row for row in committed), committed


@needs_posix_terminal
def test_a_region_keeps_the_ttys_output_queue_across_an_interrupt(monkeypatch):
    """`NOFLSH` while the region stands, and the terminal's own flags back afterwards."""
    import os
    import termios

    from kotoba.cli.app import keep_output_on_interrupt

    master, slave = os.openpty()
    try:
        before = termios.tcgetattr(slave)
        assert not before[3] & termios.NOFLSH, "a fresh pty flushes on INTR — the default this guards"
        monkeypatch.setattr("sys.stdin", SimpleNamespace(fileno=lambda: slave))
        with keep_output_on_interrupt():
            assert termios.tcgetattr(slave)[3] & termios.NOFLSH
        after = termios.tcgetattr(slave)
        assert not after[3] & termios.NOFLSH and after[3] == before[3]
    finally:
        os.close(master)
        os.close(slave)


def test_without_a_terminal_the_guard_is_a_no_op(monkeypatch):
    from kotoba.cli.app import keep_output_on_interrupt

    monkeypatch.setattr("sys.stdin", io.StringIO())
    with keep_output_on_interrupt():
        pass


# --- the beat --------------------------------------------------------------------------------------

@needs_posix_terminal
def test_a_wake_that_raises_neither_ends_the_beat_nor_reaches_prompt_toolkit(monkeypatch):
    """prompt_toolkit prints a background task's exception over the transcript and waits on
    `Press ENTER to continue...` — the next Enter eaten, the beat dead. A raising builder is logged
    and the next wake is tried, the discipline `_clock` already keeps for a frame."""
    from kotoba.cli.render import footer

    monkeypatch.setattr(footer, "CARD_S", 0.001)
    screen = a_screen()
    app = wired(screen)
    wakes = iter([0.001, 0.001, 0.001, 0.001, None])   # `_beat` itself asks once before the task starts
    app._wake = lambda: next(wakes)
    calls = []

    def bar_state():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("a builder that raised on this wake")
        return ("kind",)

    app._bar_state = bar_state
    app._painted = ("kind",)
    tasks = []
    fake = SimpleNamespace(app=SimpleNamespace(_is_running=True, invalidate=lambda: None,
                                               create_background_task=lambda c: tasks.append(
                                                   asyncio.ensure_future(c))))
    app.prompt.session = fake

    async def go():
        app._beat()
        await asyncio.gather(*tasks)

    asyncio.run(go())
    assert len(calls) == 3, "the beat woke again after the raise and ran to its own end"
    assert app._beating is False
