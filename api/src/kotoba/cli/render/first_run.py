"""Her first-run stage: the chrome a session wears, drawn before there is a session.

Nothing here chooses a word — the wizard owns every sentence, because first run is the one screen whose
copy is hardcoded English in her voice. The banner is the header with no facts and `live=False`: that
header states the model, the tool count, `● LIVE` and `just type`, none of which is true yet.

The prompt row is the one thing printed without a newline, so the caret sits at the end of her question;
it still goes through the Console and is counted into `grew`, or the live region opens a row too low.
`answered` is the other half — a terminal echoes the Enter itself and a pipe echoes nothing.
"""
from __future__ import annotations

from rich.cells import cell_len

from kotoba.cli.render.cards import _rail
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.safe import Safe
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.markdown import lift_urls
from kotoba.cli.render.text import fit, url_row, wrap

RAIL = 3        # the rail glyph and the two cells after it, which every option row starts past
SLOT = "sun"    # the hue the product already spends on "this one is waiting for you"


def option_rows(caps, items, width: int) -> list:
    """A whole list of things that can be picked, so their clauses share one column.

    Built from the list rather than a row at a time on purpose: a clause that starts wherever its own
    label happened to end is a list of unrelated things, and which one to pick is exactly the
    comparison these rows exist to make.

    Nothing is ellipsised. The clause is the whole reason one option beats another, so where the row
    cannot hold it, it wraps onto its own rail rows underneath — at forty columns a truncation would
    hide the half a person came to read."""
    room = max(8, width - RAIL)
    heads = []
    for key, label, _note in items:
        head = Safe()
        head.append(f"{caps.t(key)}  ", style="hard")
        head.append(caps.t(label), style="hard")
        heads.append(head)
    pitch = max(h.cell_len for h in heads) + 2 if heads else 0
    clauses = [caps.t(note) for _k, _l, note in items]
    inline = all(pitch + cell_len(c) <= room for c in clauses if c)
    out = []
    for head, clause in zip(heads, clauses):
        if inline and clause:
            head.append(" " * (pitch - head.cell_len))
            head.append(clause, style="chrome")
        out.append(_rail(caps, head, SLOT))
        if inline or not clause:
            continue
        for line in wrap(clause, max(8, room - 4)):
            out.append(_rail(caps, Safe("    " + line, style="chrome"), SLOT))
    return out


class Stage:
    """Where the wizard prints. Owns the screen when `kotoba setup` was run on its own; borrows the
    app's when the interactive CLI found no key and opened this before its own header."""

    def __init__(self, caps, screen: Screen | None = None, *, face: bool = True) -> None:
        self.caps = caps
        self.owns = screen is None
        self.screen = (screen if screen is not None
                       else Screen(caps, portrait=Portrait(caps, wanted=face)))

    @property
    def width(self) -> int:
        return max(24, self.screen.rw - 2)

    def open(self) -> None:
        if self.owns:
            self.screen.take_screen()
            self.screen.curtain()
        self.screen.header([], live=False)

    def she(self, text: str, emotion: str = "neutral") -> None:
        """One of her blocks, on a clear row, wearing the mood this question is asked in."""
        self.screen.begin_turn()
        self.screen.face.set(emotion, instant=True)
        self.screen.say(text, last=True)

    def options(self, items) -> None:
        for row in option_rows(self.caps, items, self.width):
            self.screen.row(row)

    def mark(self, glyph: str, text: str, style: str = "chrome") -> None:
        """A machine row at column 0, wrapped here rather than by the console: a translated provider
        failure is a whole sentence, and its second line reading from the very edge of the window
        stops looking like the same row.

        A URL too wide for the window drops below the sentence unwrapped, and at column 0 as soon as
        this mark's own indent would cost it cells — on this screen that URL is the page a person has
        to open to finish the install, so the window is spent on it and nothing else.

        Wider than the window even there it wraps, and that is the TERMINAL folding one buffer line
        rather than a newline of ours: selecting it still yields the whole URL."""
        mark = self.caps.g[glyph] + " "
        room = max(8, self.width - cell_len(mark))
        body, urls = lift_urls(self.caps.t(text), room)
        for i, line in enumerate(wrap(body, room)):
            row = Safe()
            row.append(mark if i == 0 else " " * cell_len(mark), style=style)
            row.append(line)
            self.screen.row(row)
        for url in urls:
            self.screen.row(url_row(cell_len(mark), url, self.caps.width))

    def chrome(self, text: str) -> None:
        """The machine's aside, under her column rather than at the rows' own margin — it belongs to
        what she just said, and column 0 would make it read as something that happened."""
        self.screen.flush_held()
        self.screen.chrome(text, at_gutter=True)

    def prompt(self, label: str, *hints: str) -> None:
        """Her question and the caret on one row. The hint has short twins and is fitted, never cut: at
        forty columns `[Enter skips]` is the whole of how you skip, so what gives way is the long form
        of it and never the row."""
        self.screen.flush_held()
        self.screen.gap()
        row = Safe()
        row.append("  " + self.caps.g["prompt"] + " ", style="coral")
        row.append(self.caps.t(label), style="hard")
        hint = fit(max(0, self.width - row.cell_len - 3), *(self.caps.t(h) for h in hints if h))
        if hint:
            row.append("  " + hint, style="chrome")
        row.append(" ")
        self.screen.console.print(row, end="")
        self.screen.grew(1)
        self.screen.last_blank = False

    def answered(self) -> None:
        """The newline a terminal's own echo already supplied and a pipe never does."""
        if not self.caps.interactive:
            self.broke()

    def called(self, name: str) -> None:
        self.screen.called(name)

    def broke(self) -> None:
        """Off the prompt row after a Ctrl+C, which leaves the caret where the answer would have been."""
        self.screen.console.print()
        self.screen.last_blank = False

    def close(self) -> None:
        self.screen.end_turn()
        self.screen.gap()
