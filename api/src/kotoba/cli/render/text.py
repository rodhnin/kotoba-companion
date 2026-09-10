"""Fitting text into cells, and the two renderables Rich must not touch.

Every phrase she shows has short twins rather than an ellipsis: a truncated phrase reads as a bug, a
shorter one as a decision, so `fit` returns the longest variant that fits and otherwise nothing at all.
`column` is the opposite — a fixed cell count, because a menu whose second column wanders is a list.

`Unwrapped` and `Raw` both keep Rich's hands off a payload: a URL split across two rows stops being one
selectable run, and a sixel written straight to the file descriptor lands on the live region. That is
not trusting it — `Unwrapped` is a `Safe`, because an unbroken token is where an escape sequence hides.
"""
from __future__ import annotations

import re

from rich.cells import cell_len
from rich.segment import Segment

from kotoba.cli.render import paths
from kotoba.cli.render.safe import Safe

CSI = re.compile(r"\x1b(?:\[[0-9;?]*[A-Za-z~]|O[A-Za-z]|\][^\x07]*\x07)")


def fit(room: int, *variants: str) -> str:
    """Longest variant that fits, else ""."""
    for v in variants:
        if cell_len(v) <= room:
            return v
    return ""


def wrap(text: str, width: int) -> list[str]:
    """Breaks on spaces, and on cells when a single token has no space to break on. Scripts that do not
    space their words — and any path holding them — are one unbreakable token, so a rule that only ever
    splits on spaces puts a Japanese path outside the card that was measured to hold it. Nothing is
    dropped: it continues on the next row, which truncation would not."""
    out, line = [], ""
    for word in text.split(" "):
        if line and cell_len(line) + 1 + cell_len(word) > width:
            out.append(line)
            line = ""
        if cell_len(word) > width:
            for chunk in _by_cells(word, width):
                if line:
                    out.append(line)
                line = chunk
            continue
        line = f"{line} {word}".strip()
    return out + ([line] if line else [""])


def _by_cells(word: str, width: int) -> list[str]:
    out, part = [], ""
    for ch in word:
        if cell_len(part) + cell_len(ch) > width:
            out.append(part)
            part = ""
        part += ch
    return out + ([part] if part else [])


def head(text: str, width: int, unicode: bool) -> str:
    """The first line of it, and a mark when there was more. The mark is measured INTO the width: it is
    two cells (four in ASCII) and appending it to a line already fitted to `width` is how a row that was
    asked for twenty cells came back with twenty-two."""
    mark = " …" if unicode else " ..."
    lines = wrap(text, width)
    if len(lines) == 1:
        return lines[0]
    if cell_len(lines[0]) + cell_len(mark) <= width:
        return lines[0] + mark
    return wrap(text, max(1, width - cell_len(mark)))[0] + mark


def column(text: str, width: int, unicode: bool) -> str:
    """Exactly `width` cells, so the dim column after it starts on the same one in every row.

    Both branches pad: a wide script cannot always land on the cell the truncation was aiming for — one
    step of the loop drops two cells — so trimming alone returned `width - 1` for a Japanese value and
    that row's dim column sat a cell left of every other row's. Measured at `/settings`.

    A path gives up its middle before the trim runs, because trimming a path trims the half that
    identifies it — on the header's WORK row it also took the file count after it, worst at 96 columns.
    Under the elision's floor the mark is three dots, since `..` is path SYNTAX; a mark wider than the
    column it marks is dropped, three cells of mark in a two-cell column being the row overflowing."""
    if cell_len(text) > width:
        text = paths.shorten(text, width, unicode) or text
    tail = "" if cell_len(text) <= width else ("…" if unicode else "...")
    tail = tail if cell_len(tail) <= width else ""
    while text and cell_len(text) + cell_len(tail) > width:
        text = text[:-1]
    return text + tail + " " * max(0, width - cell_len(text) - cell_len(tail))


def duration(seconds: float) -> str:
    """The long job's clock, and its own format: a job whose default ceiling is an hour
    (`transport.WORK_TIMEOUT_SECONDS`) read in tenths of a second is not a duration. Minutes lead as soon
    as there is one, and the tool rows keep the turn's `4.1s` — two clocks measuring two different things."""
    n = max(0, int(seconds))
    if n < 60:
        return f"{n}s"
    if n < 3600:
        return f"{n // 60}m {n % 60:02d}s"
    return f"{n // 3600}h {n % 3600 // 60:02d}m"


class Unwrapped(Safe):
    """A row Rich must not wrap: the terminal does it, so the string stays one selectable run. Used for
    URLs and nothing else."""


def url_row(indent: int, url: str, width: int) -> Unwrapped:
    """A lifted URL on its own row, indented only while the indent is free.

    The indent is ours and the window is not, so the indent is what gives way: at forty columns the
    ElevenLabs key page is 43 cells and her gutter made the row 46, three of which the renderer had
    added to a string already too long for the screen it had to be read off.

    Wider than the window even there it wraps — but on the TERMINAL's fold, not on a newline of ours,
    which is the whole reason `Unwrapped` exists: a soft-wrapped row is one line in the terminal's
    buffer, so selecting it still yields the whole URL. A real newline does not."""
    return Unwrapped(" " * (indent if indent + cell_len(url) <= width else 0) + url)


class Raw:
    """Bytes Rich must pass through untouched — a sixel and the cursor walk that parks it beside text.
    It goes out as a zero-width control segment so Live sequences it exactly like any other print."""

    def __init__(self, payload: str) -> None:
        self.payload = payload

    def __rich_console__(self, console, options):
        yield Segment(self.payload, None, True)
