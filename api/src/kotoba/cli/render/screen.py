"""The surface she is printed on: a header, her blocks, the machine's rows, and one blank between them.

Scrollback-native, and that is a refusal as much as a choice — no alternate screen anywhere, because
the alt screen takes the user's selection and their history and hands it back empty on exit. She takes
the top by SCROLLING what was there up into the scrollback, one wheel-turn away. Everything she SAYS
is measured at `w` — the gutter plus a reading line capped at `MEASURE` cells — and nothing else is;
rows, cards and command output take `rw`, the terminal's own right edge. Hence a two-column grid and
not padding: padding leaves the measure to the console, and prose that reflows
with the window is prose the live region and the committed copy disagree about.
"""
from __future__ import annotations

import io
import os
import sys
import time

from rich.cells import cell_len
from rich.console import Console, Group
from rich.live import Live
from rich.segment import Segment
from rich.styled import Styled
from rich.table import Table
from rich.text import Text

from kotoba.cli.render.caps import cursor_pos
from kotoba.cli.render.header import header_rows, rule
from kotoba.cli.render.kaomoji import Face
from kotoba.cli.render.markdown import lift_urls, prose
from kotoba.cli.render.portrait import INDENT, Portrait
from kotoba.cli.render.region import LiveView, Region
from kotoba.cli.render.safe import Safe
from kotoba.cli.render.text import Raw, Unwrapped, url_row
from kotoba.cli.render.theme import build_console, fold
from kotoba.core.text_security import scrub

MEASURE = 112
FLAT_INDENT = 3
RECEIPT_INSET = 5   # where a receipt's own lines read, under a bracket at column 0
TAIL_ROWS = 40


def _measure() -> int:
    raw = os.getenv("KOTOBA_MEASURE", "")
    return int(raw) if raw.isdigit() else MEASURE


class Trim:
    """The last thing every printed row passes through.

    Rich justifies a paragraph out to the full measure, so in a scrollback every line she says would end
    in twenty spaces and selecting one would take them with it. And rich draws its own glyphs — the `•`
    of a list bullet is rich's, not ours — so the ASCII fold has to happen here too or `--ascii` emits
    characters the terminal that asked for it cannot draw.

    Being the last fold is also why `keep_wide` is passed this far down: `caps.detect` keeps 言 for the
    ambiguous-width terminal, and every row it is on ends here."""

    def __init__(self, renderable, ascii_only: bool = False, keep_wide: bool = False) -> None:
        self.renderable = renderable
        self.ascii_only = ascii_only
        self.keep_wide = keep_wide

    def __rich_console__(self, console, options):
        for line in console.render_lines(self.renderable, options, pad=False):
            for seg in _rstrip(line):
                text = (fold(seg.text, keep_wide=self.keep_wide)
                        if self.ascii_only and not seg.control else seg.text)
                yield Segment(text, seg.style, seg.control)
            yield Segment("\n")


def _rstrip(line: list[Segment]) -> list[Segment]:
    out = list(line)
    while out and not out[-1].control and not out[-1].text.strip():
        out.pop()
    if out and not out[-1].control:
        last = out[-1]
        out[-1] = Segment(last.text.rstrip(), last.style, last.control)
    return out


class Screen:
    def __init__(self, caps, console: Console | None = None, portrait: Portrait | None = None) -> None:
        self.caps = caps
        self.console = console or build_console(caps)
        self.face = Face(unicode=caps.unicode)
        self.name = "KOTOBA"
        self.portrait = portrait if portrait is not None else Portrait(caps)
        self.said_plate = False
        self.resumed = False        # machine rows landed after her plate: the next block re-anchors
        self.held = ""              # her opening paragraphs, staged until they fill the art beside them
        self.plate_mode = "turn"    # `/plate` toggles this; see plate()
        self.owes_gap = False
        self.rows_committed = False
        self.pending: list = []
        self.last_blank = True
        # Both stay None until her portrait is drawn INSIDE the live region; the Gutter hook is inert
        # while they are, and a full-width erase is correct because there is no sixel to spare.
        self.erase_span: tuple[int, frozenset, int] | None = None
        self.face_art = None
        self.reserve = 0
        self.content_h = 0
        self.printed = 0
        self.tail: list[tuple[str, str, int, int]] = []
        # The measuring console takes the VISIBLE one's palette, never its own guess. rich renders a
        # Style's escape once and keeps it on the object, and Styles are shared by content — so
        # whichever console paints first decides the colour depth for both. Asked separately, this one
        # answers off a redirected handle, and on Windows that reads as no truecolor: it painted her
        # nameplate at 256 and the console with a real palette then reused the cached codes.
        self._off = build_console(caps, file=io.StringIO(), force_terminal=caps.interactive,
                                  color_system=self.console.color_system)

    @property
    def gutter(self) -> int:
        return self.portrait.inline_gutter if self.portrait.inline_ok else FLAT_INDENT

    @property
    def w(self) -> int:
        """Right edge of her column: the gutter plus a reading line capped at `MEASURE` cells, so a
        maximised window does not hand her a paragraph the eye cannot track back to the next line.
        `KOTOBA_MEASURE=N` moves the cap; `KOTOBA_MEASURE=0` drops it and gives the window back."""
        cap = _measure()
        room = self.caps.width - self.gutter - 2
        return self.gutter + max(24, min(room, cap) if cap else room)

    @property
    def measured(self) -> int:
        """`w`, held to the width rich is actually going to lay out at. The two can disagree: `w` comes
        off `caps.width`, refreshed by the frame that services SIGWINCH — up to a frame late, half a
        second of it under `--calm` — while rich asks the terminal itself at every print. A grid wider
        than the room rich has is not clipped, it is REDUCED, and it reduces every column: measured with
        caps at 120 against a 44-column window, her three-cell gutter went to one and the block landed
        at the wrong indent, in a transcript that never repaints."""
        return min(self.w, self.console.width)

    @property
    def rw(self) -> int:
        """Rows are chrome, not prose. Their tail — the clock — tracks the right edge of the terminal,
        so a wide window gets a column of timings instead of sixty blank columns.

        Held to what rich will lay out at, for the reason `measured` gives: a row wider than the room
        rich has is not clipped, it is FOLDED, and a folded row is two. `w` carries a floor of 24 that
        belongs to her PROSE staying readable, and `rw` inherited it — so every window under 27 columns
        drew every row at 27 and folded it. Measured at 24: the SECURITY listing planned 11 rows and
        drew 14, which makes every height budget in the CLI wrong at once rather than one view's."""
        return min(max(self.w, self.caps.width - 2), self.console.width)

    def take_screen(self) -> None:
        r"""Start at the top without costing anybody a line of what was there. From row `r`,
        `height - 1` newlines walk to the bottom and scroll exactly `r` times — the rows that had
        something on them, and no more — so a launch straight after `clear` scrolls nothing at all.
        `\x1b[3J` would be easier and would throw away the session the user was in the middle of."""
        if not self.caps.interactive:
            return
        row, col, typed = cursor_pos()
        if row < 0:
            return
        self.caps.sync_size()
        sys.__stdout__.write("\n" * (self.caps.height - (1 if col < 1 else 0)) + "\x1b[H")
        sys.__stdout__.flush()
        self.caps.leftover += typed

    def curtain(self) -> None:
        """Stage lights opening from the centre of the WINDOW, then her three dots under them.
        Transient — it leaves nothing in the scrollback, which is why it may move at all.

        The rule spans the terminal and is centred in it: a fixed 54 cells reads as a stub shoved into
        the left corner on a 160-column window. The colour bands are struck off the rule's own
        half-width, so the mint end-caps stay on the edges at every size. Below fourteen columns there
        is no curtain to draw at all — two end-caps and three dots do not fit, and the boot is not the
        place to find that out. Nor is there one without unicode: these four glyphs are written here
        rather than taken from `caps.g`, and `▁` has no entry in the fold, so a terminal that cannot
        draw them got a screenful anyway — 2,745 bytes above 0x7f under `LC_ALL=C` on a real tty."""
        if (not self.caps.interactive or self.caps.color == "none" or self.caps.reduced_motion
                or not self.caps.unicode or self.caps.width < 14):
            return
        w = self.caps.width - 2
        pad = (self.caps.width - w) // 2
        slots = ("coral", "sun", "grape", "mint")
        with Live(Text(""), console=self.console, transient=True, refresh_per_second=24,
                  auto_refresh=False, redirect_stdout=False, redirect_stderr=False) as live:
            for f in range(1, 13):
                reach = min(1.0, f / 8) * (w / 2)
                bar = Text(" " * pad)
                for x in range(w):
                    d = abs(x + 0.5 - w / 2)
                    if d > reach:
                        bar.append(" ")
                        continue
                    band = slots[min(3, int(d / (w / 2) * 4))]
                    bar.append("▄" if reach - d < 1.6 else "▁", style=band)
                dots = Text(" " * max(0, pad + w // 2 - 3))
                for i, slot in enumerate(("coral", "grape", "mint")):
                    up = (f - i) % 6 in (1, 2)
                    dots.append(("● " if up else "○ ") if f > 6 else "  ", style=slot)
                live.update(Group(bar, dots), refresh=True)
                time.sleep(0.04)

    def header(self, facts: list[tuple[str, str, int]], note: str = "", live: bool = True) -> None:
        """Printed once, and it can never repaint: the portrait is dropped in beside the text block.

        First run takes the same banner with `facts=[]` and `live=False`: her plate and her tagline are
        the only two facts that are true before a key exists."""
        tall = self.portrait.rows if self.portrait.mode != "none" else 0
        width = self.caps.width - self.portrait.gutter_cols
        face = Text(self.face.still(), style=self.face.style)
        rows = header_rows(self.caps, self.plate(slot="coral", face=False), width, facts, note, tall,
                           face=face, live=live)
        if self.portrait.mode != "none":
            self._write(self.portrait.header(self._paint(rows, width), "neutral"))
            self.kept(self.portrait.last_rows, self.portrait.last_art,
                      self.portrait.last_col, self.portrait.last_reach)
        else:
            for row in rows:
                self.out(row)
        self.out(rule(self.caps))
        self.blank()

    def plate(self, live: bool = False, slot: str | None = None, face: bool = True) -> Text:
        """Her nameplate, tinted by the family her mood belongs to — the single device that carries
        identity inside the transcript, and the only colour here that is information.

        The header pins `slot` to coral: that plate is printed once and never repaints, so letting it
        take the mood she happened to be in at boot leaves the wrong hue on the screen all session.
        `live` is the copy the region repaints — her face blinking through a mood change, her mouth
        decaying since the last token. Every other caller wants `still()`, which settles the pending
        mood first, and settling is exactly what the blink is made of: a printed plate may ask for it
        and a repainted one may never. `plate_mode == "quiet"` keeps the sticker only when her mood is
        not the everyday coral; the header pins a slot and never quietens."""
        if self.plate_mode == "quiet" and slot is None and self.face.plate == "coral":
            bare = Text()
            bare.append(self.caps.g["sigil"] + " ", style="coral")
            bare.append(self.face.render(still=self.caps.reduced_motion) if live else self.face.still(), style=self.face.style)
            return bare
        slot = slot or self.face.plate
        out = Text()
        out.append(f" {self.caps.g['sigil']} {self.name} ", style=f"chip.{slot}")
        if self.caps.color != "none":
            out.append(self.caps.g["shadow"], style=f"shadow.{slot}")
        if face:
            out.append("  ")
            out.append(self.face.render(still=self.caps.reduced_motion) if live else self.face.still(), style=self.face.style)
        return out

    def called(self, name: str) -> None:
        """The name on her plate, which is whatever `soul_config.name` says — first run lets a person
        rename her and a plate that kept saying KOTOBA would be a control that only did half.

        Clamped and scrubbed on the way in: the plate is a sticker beside the header grid, not a title
        bar, and this string came off a database row somebody typed into."""
        clean = scrub((name or "").strip()).upper()
        while clean and cell_len(clean) > 16:
            clean = clean[:-1]
        self.name = clean or "KOTOBA"

    def commit_user(self, text: str) -> None:
        """His own line, back in the transcript. `erase_when_done` wipes the whole footer on submit, so
        without this the thing he typed is simply gone. Anything of hers still staged goes out above it:
        she said it before he typed, and a transcript out of order is worse than one held back."""
        self.flush_held()
        self.out(Safe.assemble((self.caps.g["prompt"] + " ", "coral"), (self.caps.t(text), "")))

    def say(self, block: str, last: bool = False) -> None:
        """One finished block of hers, held back until the first is tall enough to fill the art beside
        it. A four-row face over a one-line opener left two dead rows in the middle of one utterance;
        held, the next paragraph lands in them and the whole reply reads as one voice. Nothing is
        hidden by the wait — the live region draws the held text at the row it will keep."""
        if not block.strip():
            return
        box = self.portrait
        if self.said_plate or self.resumed or not (box and box.inline_ok):
            self._commit_prose(block)
            return
        self.held = f"{self.held}\n\n{block}" if self.held else block
        if last or self.rows_of(self.at_gutter(prose(self.held, self.caps))) >= box.irows - 1:
            self.flush_held()

    def flush_held(self) -> None:
        """Cleared BEFORE the print, always: the print repaints the live region, and anything still
        staged there is drawn a second time under the copy just committed."""
        if self.held:
            block, self.held = self.held, ""
            self._commit_prose(block)

    def _commit_prose(self, block: str) -> None:
        """Her URLs go out under the block that mentions them, unwrapped, and before the gap it owes —
        they belong to it.

        `resumed` is her voice coming back after machine rows: announce, then the tool rows at column
        0, then a closing paragraph — committed bare at the gutter, that paragraph read as a fragment
        of nobody's. So a machine row landing over a said plate hands the NEXT block a fresh nameplate
        on a clear row, with no second portrait — the reply's one sixel is already up, and the same
        face twice is a tear."""
        block, urls = lift_urls(block, self.w - self.gutter)
        body = prose(block, self.caps)
        if self.said_plate:
            self.out(body, at_gutter=True)
        elif self.resumed:
            self.resumed, self.said_plate = False, True
            self.separate()
            self.out(self.plate(), at_gutter=True)
            self.out(body, at_gutter=True)
        else:
            self.said_plate = True
            self._opening(body)
        for url in urls:
            self.out(url_row(self.gutter, url, self.caps.width))
        self.owes_gap = True
        self.flush_rows()

    def row(self, renderable) -> None:
        """Machine output: column 0, never the gutter — there is no face on those rows. It lands after
        whatever she had staged, and it is what `lead` reads to know her block needs a clear row.

        While her live block owns the region's top rows and nothing of it is committable yet — no
        staged block to flush, no plate on the glass, and her portrait already painted up there
        (`erase_span`) — a print here would land on the region's first row, which IS her face: the
        reply's one sixel cannot be moved or repainted, so the row waits in `pending` instead. The
        region draws it under her block meanwhile, and it lands the moment her block does, in the order
        the glass showed."""
        self.flush_held()
        if self.erase_span and not self.said_plate and not self.resumed:
            self.pending.append(renderable)
            return
        self.flush_rows()
        self.rows_committed = True
        self.out(renderable)
        if self.said_plate:
            self.said_plate, self.resumed = False, True

    def flush_rows(self) -> None:
        """The machine rows `row` held back behind her un-committed block, landing now that it has —
        and before any newer machine row may, so the transcript keeps the order they arrived in."""
        if not self.pending:
            return
        queue, self.pending = self.pending, []
        for renderable in queue:
            self.rows_committed = True
            self.out(renderable)
        if self.said_plate:
            self.said_plate, self.resumed = False, True

    def chrome(self, text: str, *, at_gutter: bool = False) -> None:
        """The machine's own voice — and a `Safe`, because half of what it says quotes something back:
        `/open` prints the target of a gift, and its refusals print the path it would not open."""
        self.out(Safe(self.caps.t(text), style="chrome"), at_gutter=at_gutter)

    def footer(self, elapsed: float, tools: int) -> None:
        """What the turn cost, and only when it cost something worth saying."""
        if elapsed <= 3.0 or not tools:
            return
        word = "tool" if tools == 1 else "tools"
        self.chrome(f"{elapsed:.1f}s {self.caps.g['bullet']} {tools} {word}", at_gutter=True)
        self.blank()

    def out(self, renderable, *, at_gutter: bool = False) -> None:
        self.gap()
        self.last_blank = False
        if isinstance(renderable, Unwrapped):
            # The terminal does the wrapping, not rich: `Trim` renders line by line and puts a real
            # newline in, which is a URL that has stopped being one selectable run.
            self.grew(self.rows_of(renderable))
            self.console.print(renderable, no_wrap=True, crop=False, overflow="ignore")
            self.kept(self.painted(renderable))
            return
        body = self.at_gutter(renderable) if at_gutter else self._trim(renderable)
        self.grew(self.rows_of(body))
        self.console.print(body)
        self.kept(self.painted(body))

    def blank(self) -> None:
        self.grew(1)
        self.console.print()
        self.last_blank = True
        self.kept([""])

    def grew(self, n: int) -> None:
        """n rows joined the transcript above the region, so it starts n rows lower and the pad gives up
        the same n — before the print, because the print repaints the region and it has to already be
        shorter.

        One is the floor, not the content height: a tall region floored at its own height left that pad
        behind after the content was committed, and the terminal scrolled the end of a long reply away
        to honour it. It is also the only place that sees every row the transcript takes, which is where
        `printed` comes from — the count a landing hands the prompt so the first frame back is already
        pinned instead of asking the terminal where the cursor went."""
        self.printed += n
        if self.reserve:
            self.reserve = max(self.reserve - n, 1)

    def rows_of(self, renderable) -> int:
        if isinstance(renderable, Unwrapped):
            return max(1, -(-cell_len(renderable.plain) // self.caps.width))
        return len(self.console.render_lines(renderable, self.console.options, pad=False))

    def kept(self, rows: list[str], art: str = "", col: int = INDENT, reach: int = 0) -> None:
        """The last rows the transcript printed, verbatim, so a list drawn on top of them can put them
        back exactly. A sixel is remembered against the row it is anchored to, because a payload put
        back one row early is scrubbed off by the row under it.

        And against its COLUMN, because the header face and the reply face do not share one: banking the
        art alone made the repainter guess one column for all of them, and typing `/` moved her. And
        against its HEIGHT — `reach`, the rows the face spans — for the same reason one field over: the
        two faces are not one size either, and a painter measuring every face as the tallest stopped two
        rows short of a reply face's real chin. Zero means "not said", and the painter then falls back
        to the tallest, the honest answer for a row banked by a caller that did not know."""
        if not self.caps.interactive:
            return
        self.tail += [(row, art if not i else "", col, reach if not i else 0)
                      for i, row in enumerate(rows)]
        del self.tail[:-TAIL_ROWS]

    def painted(self, renderable) -> list[str]:
        """The exact bytes a print just put on the glass — measured off the LIVE console, so the rows
        banked are the rows the terminal has, wrapping and all."""
        c = self.console
        return [c._render_buffer(line) for line in c.render_lines(renderable, c.options, pad=False)]

    def frozen(self, row: int, top: int) -> tuple[str, str, int, int] | None:
        """(bytes, art, the column that art was drawn at, the rows it spans), or None when this bank
        cannot answer for the row. Rows from `top` down belong to the app, and the app's own band is
        blank.

        The None is the difference between "that row was blank" and "I do not know what was there", and
        they are not the same answer: a painter that reads the second as the first ERASES a row of her
        transcript to put back a blank it never saw. It happens on every width change, where the list's
        painter empties this bank on purpose, and what showed was a gap between her last line and the
        input box."""
        if row >= top:
            return ("", "", INDENT, 0)
        i = len(self.tail) - (top - row)
        return self.tail[i] if 0 <= i < len(self.tail) else None

    def ansi(self, row) -> str:
        """One row's bytes without printing it. Measured offscreen: the live console cannot be captured
        while a region is up without splicing the region's own erase into the answer."""
        self._off.width = max(self.caps.width, 20)
        with self._off.capture() as cap:
            self._off.print(row, end="", no_wrap=True, crop=False)
        return cap.get()

    def face_frame(self, lead: int):
        """Her portrait inside the live region: (first row, rows, payload to write now). The payload
        comes back once — the first frame she speaks — and never again, because ten kilobytes of sixel
        per frame (the inline tier, measured at the default cell) is not a repaint."""
        box = self.portrait
        if lead < 0 or not (box and box.inline_ok):
            return None
        if self.face_art:
            return (lead, self.face_art[1], None)
        # Painting inside the 110 ms blink prints the face she is leaving.
        if not self.face.settled:
            return None
        try:
            payload, _, rows = box.art(self.face.emotion)
        except Exception:
            return None
        self.face_art = (payload, rows, self.face.emotion)
        return (lead, rows, payload)

    def measure(self) -> int:
        """How many rows the live region has to fill to reach the last one. Whatever was typed through
        the CSI 6n comes back as `leftover`, the way every other probe hands it back — dropped, it is a
        keystroke the person pressed and never saw."""
        self.reserve = 0
        if not self.caps.interactive:
            return 0
        self.caps.sync_size()
        row, _, typed = cursor_pos()
        self.caps.leftover += typed
        if row >= 0:
            self.reserve = max(0, self.caps.height - row)
        return self.reserve

    def separate(self) -> None:
        """One clear row before a block that did not come from a turn, and only one."""
        if not self.last_blank:
            self.blank()

    def gap(self) -> None:
        """The blank row her block ends on, spent lazily — before the next paragraph, the footer or the
        next prompt, whichever comes first. Printed eagerly it doubled with the portrait's own overhang
        and one reply read as three messages."""
        if self.owes_gap:
            self.owes_gap = False
            self.blank()

    def begin_turn(self) -> None:
        """Her plate is printed once per turn, on her first block. Resetting it at the END of a turn is
        not the same thing: the greeting is said outside one, and every reply after it then arrived with
        no nameplate and no face at all.

        The MOOD is reset here for the same reason — a new turn starts clean, and her face is about this
        reply or about nothing. With the only reset living in the prompt's toolbar, every path reaching
        a turn without drawing a prompt first (a queued line replayed, a launch menu, a non-interactive
        run) carried the LAST reply's mood onto this nameplate. Nothing of this turn is lost by
        resetting: her opening tag and the tool's focus emotion both land after this point. The blank is
        the turn's own room and goes out before `measure()`, so the region gives up that row itself."""
        self.said_plate = self.rows_committed = self.resumed = False
        self.face.set("neutral", instant=True)
        self.face.rest()
        self.blank()

    def end_turn(self) -> None:
        self.flush_held()
        self.flush_rows()
        self.said_plate = self.resumed = False
        self.face.rest()
        self.face.settle()

    def region(self, spin, get_state, parts) -> Region:
        """The turn's live region: what she is doing, a pad, and the frame on the last rows. It is a
        `Region` and not a plain `Live` because rich ends a transient one with a newline — a row of
        scroll per turn, which walks the header off the top — and erases the frame before
        prompt_toolkit has drawn its own."""
        return Region(LiveView(self, spin, get_state, parts), console=self.console,
                      refresh_per_second=self.caps.fps, transient=True, auto_refresh=False,
                      redirect_stdout=False, redirect_stderr=False)

    def _opening(self, body) -> None:
        """Plate, face and her first block as one composition, on the rows the live region drew them.

        The face is only taken over while the region still declares a spared gutter — it stops declaring
        one the frame she stops speaking, and past that the next erase has already rubbed her out.
        `drawn` also carries the emotion that was actually painted, and the plate is settled onto it, so
        the transcript keeps the row the region drew glyph for glyph. Repainting instead — dropping the
        span so a full-width erase takes the sixel off, then painting her settled mood — is one portrait
        swapped for another in the middle of a reply, which is the flash this was meant to fix. A block
        wide enough to soft-wrap would add a row the sixel's walk-back knows nothing about and land her
        face a row off, so anything that does not compose cleanly falls back to the plain indented path."""
        self.gap()
        lead = self.rows_committed
        self.face.settle()
        self.last_blank = False
        drawn = self.face_art if self.erase_span else None
        if drawn:
            self.face.set(drawn[2], instant=True)
        block = self._paint([self.plate(), body], self.measured - self.gutter)
        placed = self.portrait.inline(block, drawn[2] if drawn else self.face.emotion,
                                      painted=bool(drawn)) if self.portrait.inline_ok else None
        self.face_art = None
        if placed is None:
            if lead:
                self.blank()
            for row in (self.at_gutter(self.plate()), self.at_gutter(body)):
                self.grew(self.rows_of(row))
                self.console.print(row)
                self.kept(self.painted(row))
            return
        # Her composed block joins the transcript like any other print, and the region has to give up
        # exactly its rows or the pad holds a screenful she has already filled.
        self.grew(placed[0].count("\n") + bool(lead))
        self._write(("\n" if lead else "") + placed[0])
        if lead:
            self.kept([""])
        self.kept(self.portrait.last_rows, self.portrait.last_art, self.portrait.last_col,
                  self.portrait.last_reach)

    def _paint(self, renderables: list, width: int) -> list[str]:
        """The exact ANSI rows a renderable will occupy, measured offscreen — the live console cannot be
        captured while a region is up without splicing the erase into the answer."""
        self._off.width = max(width, 20)
        self._off.file = io.StringIO()
        for item in renderables:
            self._off.print(self._trim(item))
        return [line.rstrip("\r") for line in self._off.file.getvalue().split("\n")][:-1]

    def _write(self, payload: str) -> None:
        """The payload carries its own newlines, so rich must not add one — and it goes through the
        console, not the fd, or a live region above it is drawn over instead of around."""
        self.console.print(Raw(payload), end="")
        self.last_blank = False

    def _trim(self, renderable) -> Trim:
        return Trim(renderable, ascii_only=not self.caps.unicode,
                    keep_wide=self.caps.encodes_unicode)

    def _indent(self, renderable) -> Table:
        """Her column and her measure in one renderable. The second column is `measured - gutter` wide, so
        the live draw, the composed opening and every paragraph after it wrap at the same place — padding
        would leave the width to the console and each of the three would pick a different one. `Trim`
        takes the trailing spaces the grid pads with back off.

        `fold` is not a preference: a table cell ELLIPSISES what does not fit, and the one token in her
        prose long enough to reach the edge is a link, so every URL she gave you lost its tail. Folding
        is what markdown does everywhere else here, which is the only way the committed copy keeps
        agreeing with the live one. The width is `measured` and not `w`, because a grid the console has
        already outgrown loses its gutter."""
        grid = Table.grid(padding=0)
        grid.add_column(width=self.gutter)
        grid.add_column(width=max(1, self.measured - self.gutter), overflow="fold")
        grid.add_row(Text(), renderable)
        return grid

    def at_gutter(self, renderable) -> Trim:
        """Indented and trimmed, the way everything she SAYS goes out."""
        return self._trim(self._indent(renderable))

    def receipt_rows(self, md: str, inset: int = RECEIPT_INSET) -> list[Safe]:
        """Her markdown under a receipt — the long job's summary in `/work N`, and on a job that did not
        end well — one renderable per ROW, at the cells a receipt's own lines read at.

        Through `prose`, like every block she says: drawn with a plain wrap the summary showed its own
        `**asterisks**`, `|---|` and `<br>` and lost its newlines. Rows rather than one renderable
        because a printed listing budgets in rows and the summary comes last — cut by rows it loses only
        its tail, and a table that stops under a note is still a table, where a summary dropped whole
        for being tall was nothing at all. Each row is a `Safe` like every other printed row, so the
        fold gate can measure it."""
        grid = Table.grid(padding=0)
        grid.add_column(width=inset)
        grid.add_column(width=max(1, self.measured - inset), overflow="fold")
        grid.add_row(Text(), Styled(prose(md, self.caps), "chrome"))
        lines = self.console.render_lines(grid, self.console.options, pad=False)
        while lines and not "".join(seg.text for seg in lines[-1]).strip():
            lines.pop()
        return [Safe.assemble(*[(seg.text, seg.style or "") for seg in line if not seg.control])
                for line in lines]
