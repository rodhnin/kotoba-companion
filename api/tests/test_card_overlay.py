"""The held card is transitory: it covers the screen and gives it back.

It paints over the transcript with absolute cursor moves, banks what it covered out of `Screen.tail`,
and restores it on close — nothing scrolls, so nothing has to come back. The region under a held card
never carries the card: it keeps the height the prompt released, and the card is an `Overlay` above it.

A card must show WHOLE or not at all, so the overlay is asked for the TALLEST it can get (`?` raises
it to `show_why`) before opening. When the rows above cannot hold that, `_overlay_open` leaves
`App.overlay` unset and the card falls back to the region's own grow-and-scroll flow."""
from __future__ import annotations

import asyncio
import io
import re
from dataclasses import replace
from types import SimpleNamespace

from rich.text import Text

from conftest import needs_posix_terminal
from kotoba.cli import state
from kotoba.cli.app import App
from kotoba.cli.input import menu as panels
from kotoba.cli.render import cards
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console

AT = re.compile(r"\x1b\[([0-9]+);1H")


def a_screen(height: int = 30, printed: int = 24, reserve: int = 8) -> Screen:
    caps = Caps(color="none", background="dark", unicode=True, interactive=True,
                width=96, height=height, g=dict(GLYPHS_UNICODE))
    screen = Screen(caps, console=build_console(caps, file=io.StringIO()),
                    portrait=Portrait(caps, wanted=False))
    for i in range(printed):
        screen.out(Text(f"transcript row {i}"))
    screen.reserve = reserve
    return screen


def emissions(monkeypatch) -> list[str]:
    out: list[str] = []
    monkeypatch.setattr(panels, "_emit", out.append)
    return out


def rows_written(payload: str) -> list[int]:
    """The 0-based rows one emission touched, in the order it touched them."""
    return [int(m) - 1 for m in AT.findall(payload)]


# --- the Overlay itself ----------------------------------------------------------------------------

def test_the_overlay_paints_the_card_above_the_region_and_close_restores_the_bank(monkeypatch):
    screen = a_screen()
    out = emissions(monkeypatch)
    over = panels.Overlay(screen, lambda: [Text("CARD TOP"), Text("CARD KEYS")])
    top = screen.caps.height - screen.reserve
    banked = {r: screen.frozen(r, top) for r in (top - 2, top - 1)}
    assert over.open(2)
    paint = out[-1]
    assert rows_written(paint) == [top - 2, top - 1]
    assert "CARD TOP" in paint and "CARD KEYS" in paint
    over.close()
    restore = out[-1]
    assert rows_written(restore) == [top - 2, top - 1]
    for r, (bytes_, _, _, _) in banked.items():   # re-recorded: the bank carries reach
        assert bytes_ in restore, f"row {r} did not come back"
    assert over.top < 0 and not over.bank and not over.last


def test_the_overlay_refuses_a_card_taller_than_the_room_above(monkeypatch):
    screen = a_screen(reserve=27)
    emissions(monkeypatch)
    over = panels.Overlay(screen, lambda: [Text("x")] * 5)
    assert not over.open(5), "3 rows of room cannot show a 5-row gate whole"


def test_the_overlay_refuses_when_the_bank_cannot_answer_for_a_covered_row(monkeypatch):
    """A row that is not banked is a row that would never be restored — stale card pixels in the
    transcript, which is worse than the scroll the fallback costs."""
    screen = a_screen(printed=3)
    emissions(monkeypatch)
    over = panels.Overlay(screen, lambda: [Text("x")] * 6)
    assert not over.open(6)


def test_an_unchanged_overlay_writes_nothing(monkeypatch):
    screen = a_screen()
    out = emissions(monkeypatch)
    over = panels.Overlay(screen, lambda: [Text("CARD")])
    assert over.open(1)
    n = len(out)
    over.sync()
    assert len(out) == n, "an unchanged card cost bytes at rest"


def test_the_why_growth_extends_upward_and_close_restores_every_covered_row(monkeypatch):
    screen = a_screen()
    out = emissions(monkeypatch)
    drawn = [Text("CMD"), Text("KEYS")]
    over = panels.Overlay(screen, lambda: list(drawn))
    top = screen.caps.height - screen.reserve
    assert over.open(4)
    drawn[:0] = [Text("WHY-A"), Text("WHY-B")]
    over.sync()
    grown = out[-1]
    assert set(rows_written(grown)) >= {top - 4, top - 3}, "the `?` rows go above, on new ground"
    over.close()
    assert rows_written(out[-1]) == [top - 4, top - 3, top - 2, top - 1]


def test_a_region_growing_a_row_shifts_the_overlay_with_the_glass(monkeypatch):
    """A band row appearing grows the region, and the growth scrolls the glass — card pixels
    included — one row up. The scroll has already moved the paint, so `sync` writes nothing; the
    bookkeeping rides along, or `close` restores one row low."""
    screen = a_screen()
    out = emissions(monkeypatch)
    over = panels.Overlay(screen, lambda: [Text("CARD")])
    top = screen.caps.height - screen.reserve
    assert over.open(1)
    n = len(out)
    screen.reserve += 1
    over.sync()
    assert len(out) == n, "the scroll moved the card already — a rewrite would be a second copy"
    assert set(over.last) == {top - 2} and set(over.bank) == {top - 2}
    over.close()
    assert rows_written(out[-1]) == [top - 2]


# --- the app wiring --------------------------------------------------------------------------------

def wired(screen: Screen) -> App:
    app = App(screen.caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1",
                                  events=SimpleNamespace(steps={}, settle=lambda: None))
    # The fake region renders the REAL LiveView, so reserve, content_h and the seat are the
    # arithmetic production runs, not numbers a fixture invented.
    app.region = SimpleNamespace(refresh=lambda: rendered(app, screen))
    return app


def a_held() -> state.Held:
    return state.Held(verb="run", cmd="echo listo > qa-aprobado.txt", danger="", family="echo",
                      intent="", blast=(), took=0.0, ok=False, detail="", rid="r1", state="ask",
                      asked=1e12, can_always=True)


def answer(app: App, key: str = "y") -> tuple[list, list]:
    """Drive `_answer_held` with the key ready, recording what the read saw and what committed."""
    seen, committed = [], []

    def read(drawn, held=None):
        seen.append((app.overlay, list(app.parts.approval_rows())))
        return key

    app._read_approval = read
    real_commit = app._commit

    def commit(row):
        committed.append((app.overlay, row))
        real_commit(row)

    app._commit = commit

    async def go():
        held = a_held()
        app.holds.append(held)
        app._held_keys[held.rid] = asyncio.get_running_loop().create_future()
        await app._answer_held(held)

    asyncio.run(go())
    return seen, committed


@needs_posix_terminal
def test_answer_held_draws_the_overlay_and_the_receipt_lands_after_it_closed(monkeypatch):
    screen = a_screen()
    emissions(monkeypatch)
    app = wired(screen)
    seen, committed = answer(app)
    over, in_flow = seen[0]
    assert over is not None, "the card should have opened as an overlay"
    assert in_flow == [], "the region's flow must never carry an overlaid card"
    assert app.overlay is None
    assert committed and committed[0][0] is None, "the receipt landed with the overlay still up"
    assert "yes, go ahead" in committed[0][1].plain


@needs_posix_terminal
def test_answer_held_falls_back_to_the_flow_when_the_overlay_cannot_fit(monkeypatch):
    """RE-RECORDED for the seat — the complaint was about position alone. A big reserve used to
    force the whole card above the region and `reserve=27` left no room; seated, the pad IS room, so
    the fallback is now only a window shorter than the card plus the band-frame-bar."""
    screen = a_screen(height=16, printed=10, reserve=12)
    emissions(monkeypatch)
    app = wired(screen)
    seen, _ = answer(app)
    over, in_flow = seen[0]
    assert over is None
    assert in_flow, "with no overlay the region's flow carries the card — the pre-overlay look"


@needs_posix_terminal
def test_the_whole_card_is_measured_with_its_why_panel_before_it_opens(monkeypatch):
    """`?` must never outgrow the room mid-read: the fit is asked about the `show_why` height,
    against seat plus cover — so a window one row too short for the WHOLE card refuses even though
    the bare card fits it."""
    screen = a_screen()
    emissions(monkeypatch)
    app = wired(screen)
    drawn = cards.Approval("echo listo > qa-aprobado.txt", "", "echo", held=True)
    whole = len(cards.approval_rows(screen.caps, replace(drawn, show_why=True), app.card_w))
    bare = len(cards.approval_rows(screen.caps, drawn, app.card_w))
    assert whole > bare
    rendered(app, screen)
    pinned = screen.content_h
    screen.caps.height = whole + pinned - 1
    screen.reserve = 12
    app._overlay_open(drawn)
    assert app.overlay is None, "room for the bare card is not room for its ? panel"
    screen.caps.height = whole + pinned
    screen.reserve = 12
    app._overlay_open(drawn)
    assert app.overlay is not None, "the exact room is room"
    app.overlay = None


# --- the seat: reported live, the card rode to the top instead of taking its place below ----------

def rendered(app, screen):
    """The region's lines as text, exactly as `LiveView` lays them out."""
    from rich.console import Console

    from kotoba.cli.render.region import LiveView

    console = Console(file=io.StringIO(), width=screen.caps.width, height=screen.caps.height,
                      force_terminal=True)
    view = LiveView(screen, app.spin, app._state, app.parts)
    # No `height` in the options, as in production: rich pads every nested render to a set height.
    lines = console.render_lines(view, pad=False)
    return ["".join(seg.text for seg in line if not seg.control) for line in lines]


@needs_posix_terminal
def test_the_seated_card_sits_directly_above_the_band_with_the_void_above_it(monkeypatch):
    """The card's place is the flow's old place: its last row against the pinned block, next to the
    box that answers it and the rail that names the keys. The region's pad — the void the card was
    reported floating above — goes back OVER the card, where the idle filler always was."""
    screen = a_screen(reserve=20)
    emissions(monkeypatch)
    app = wired(screen)
    app.approval = cards.Approval("echo listo > qa-aprobado.txt", "", "echo", held=True)
    app.overlay = panels.Overlay(screen, lambda: [])
    lines = rendered(app, screen)
    keys_at = next(i for i, l in enumerate(lines) if "go ahead" in l)
    shadow_at = keys_at + 1
    frame_at = next(i for i, l in enumerate(lines) if l.startswith("┏"))
    assert frame_at == shadow_at + 1, "the card must sit ON the band-frame block, not float"
    tab_at = next(i for i, l in enumerate(lines) if "NEEDS YOU" in l)
    assert all(not l.strip() for l in lines[:tab_at - 1]), "the void belongs above the card"
    assert app.parts.seated_k > 0
    assert len(lines) == screen.reserve, "seating must never grow the region"


@needs_posix_terminal
def test_the_head_is_only_what_the_seat_could_not_take(monkeypatch):
    screen = a_screen(reserve=20)
    emissions(monkeypatch)
    app = wired(screen)
    drawn = cards.Approval("echo listo > qa-aprobado.txt", "", "echo", held=True)
    app.approval, app.overlay = drawn, panels.Overlay(screen, lambda: [])
    rendered(app, screen)
    rows_ = cards.approval_rows(screen.caps, drawn, app.card_w)
    head = app._card_head(drawn)
    assert app.parts.seated_k > 0
    assert head == rows_[:len(rows_) - app.parts.seated_k]


@needs_posix_terminal
def test_a_fully_seated_card_covers_no_transcript_at_all(monkeypatch):
    """The tall-void case: the pad is deeper than the whole card, so the overlay banks nothing,
    paints nothing, and the transcript is never touched."""
    screen = a_screen(height=40, reserve=28)
    out = emissions(monkeypatch)
    app = wired(screen)
    seen, committed = answer(app)
    over, in_flow = seen[0]
    assert over is not None and in_flow == []
    assert not out, "a card the pad can hold entirely has nothing to cover or restore"
    assert "yes, go ahead" in committed[0][1].plain


# --- the inline card: reported live, EVERY card must behave this way, without exception -----------
# The turn's own card rode the region's flow, which was fine only while the turn kept committing rows
# after the answer — each one paid the grown reserve down. A turn that answers and then sits in a long
# silent command commits nothing, and the card's height stayed on the glass as blank rows. The oracle
# is the acceptance test verbatim: the glass after the answer is the glass before the card, plus the
# one receipt row. Same machinery as the held card — seat, cover, restore, flow as the whole-or-not
# fallback — because two implementations of one card is how surfaces learn to disagree.

def drive_inline(app: App, monkeypatch, key: str = "y"):
    """Drive `_inline` with the key ready, recording what the read saw and what committed."""
    import kotoba.cli.app as appmod
    from kotoba.cli import approvals

    monkeypatch.setattr(appmod, "hazard", lambda cmd: ("", ()))
    monkeypatch.setattr(app, "_ring", lambda: None)
    seen, committed = [], []

    def read(drawn, held=None):
        seen.append((app.overlay, list(app.parts.approval_rows()), app.screen.reserve))
        return key

    app._read_approval = read
    real_commit = app._commit

    def commit(row):
        committed.append((app.overlay, row))
        real_commit(row)

    app._commit = commit
    card = approvals.Card(request_id="r-in", mode="approval",
                          label="echo listo > qa-aprobado.txt", family="echo", can_always=True)
    out = asyncio.run(app._inline(card))
    return seen, committed, out


@needs_posix_terminal
def test_the_inline_card_opens_as_an_overlay_and_never_grows_the_region(monkeypatch):
    screen = a_screen()
    emissions(monkeypatch)
    app = wired(screen)
    before = screen.reserve
    seen, committed, out = drive_inline(app, monkeypatch)
    over, in_flow, reserve_under_card = seen[0]
    assert over is not None, "the turn's own card should cover, exactly as the held one does"
    assert in_flow == [], "the region's flow must never carry an overlaid card"
    assert reserve_under_card == before, "the open card must not move the region's ratchet"
    assert out == ("y", None)
    assert app.overlay is None
    assert committed and committed[0][0] is None, "the receipt landed with the cover still up"
    assert "yes, go ahead" in committed[0][1].plain
    assert screen.reserve == before - 1, "the receipt row is the only thing that may spend reserve"


@needs_posix_terminal
def test_the_inline_card_falls_back_to_the_flow_when_the_cover_cannot_fit(monkeypatch):
    """The deliberate fallback, pinned for the inline path too: a gate is shown whole or not at all.
    Refusal needs `height < whole + content_h` — the inline card (no held clause) is 10 rows with its
    `?` counted, over a 4-row empty region, so 13 rows cannot show it whole."""
    screen = a_screen(height=13, printed=10, reserve=12)
    emissions(monkeypatch)
    app = wired(screen)
    seen, _, out = drive_inline(app, monkeypatch)
    over, in_flow, _ = seen[0]
    assert over is None
    assert in_flow, "with no cover the region's flow carries the card — the pre-overlay look"
    assert out == ("y", None)


@needs_posix_terminal
def test_a_turn_ending_over_the_inline_card_still_restores_the_cover(monkeypatch):
    """`_read_approval` answers "" when the card is cleared under the reader: the card goes back on
    `holds` for `_deferred`, and the cover must already be off the glass — the region is about to be
    torn down and an orphaned bank would restore over whatever comes next."""
    screen = a_screen()
    emissions(monkeypatch)
    app = wired(screen)
    seen, committed, out = drive_inline(app, monkeypatch, key="")
    assert seen[0][0] is not None
    assert app.overlay is None
    assert out[0] == "" and isinstance(out[1], state.Held)
    assert not committed, "an unanswered card leaves no receipt here — _deferred owns its ending"
    assert app.holds and app.holds[0].rid == "r-in"


# --- the confirm rail (`/stop`, sandbox, provider) --------------------------------------------------

def confirm_drive(app: App, monkeypatch, key: str = "y"):
    seen, committed = [], []

    def read(card):
        seen.append((app.overlay, list(app.parts.confirm_rows())))
        return key

    app._read_confirm = read
    real_commit = app._commit

    def commit(row):
        committed.append((app.overlay, row))
        real_commit(row)

    app._commit = commit
    card = cards.Confirm("stop the long job?", "it is mid-flight and will not resume",
                         "stop it", "let it run", "stopped", "left it running")
    yes = asyncio.run(app._answer_confirm(card))
    return seen, committed, yes, card


@needs_posix_terminal
def test_the_confirm_rail_covers_and_the_receipt_lands_after_it_closed(monkeypatch):
    screen = a_screen()
    emissions(monkeypatch)
    app = wired(screen)
    seen, committed, yes, card = confirm_drive(app, monkeypatch)
    over, in_flow = seen[0]
    assert over is not None, "the two-key rail is a card and covers like one"
    assert in_flow == [], "the region's flow must never carry an overlaid card"
    assert yes is True and app.confirm is None and app.overlay is None
    assert committed and committed[0][0] is None, "the receipt landed with the cover still up"
    assert "stopped" in committed[0][1].plain


@needs_posix_terminal
def test_the_confirm_rail_rides_the_flow_when_the_cover_cannot_fit(monkeypatch):
    # The rail is 7 rows whole over a 4-row empty region: 10 rows cannot show it whole.
    screen = a_screen(height=10, printed=8, reserve=9)
    emissions(monkeypatch)
    app = wired(screen)
    seen, committed, yes, _ = confirm_drive(app, monkeypatch, key="n")
    over, in_flow = seen[0]
    assert over is None
    assert in_flow, "with no cover the region's flow carries the rail — whole, as a gate must be"
    assert yes is False
    assert "left it running" in committed[0][1].plain


@needs_posix_terminal
def test_slash_confirm_routes_through_the_card_machinery(monkeypatch):
    """One mechanism: `slash._confirm` hands the card to the app rather than opening a second Live."""
    from kotoba.cli import slash

    screen = a_screen()
    emissions(monkeypatch)
    app = wired(screen)
    got = []

    async def fake(card):
        got.append(card)
        return True

    monkeypatch.setattr(app, "_confirm_card", fake, raising=False)

    async def run():
        return await asyncio.wait_for(slash._confirm(app, "sandbox local → none",
                                                     "that turns the gate off"), timeout=3.0)

    assert asyncio.run(run()) is True
    assert got and got[0].head == "sandbox local → none"
