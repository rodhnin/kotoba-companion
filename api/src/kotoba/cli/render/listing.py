"""How much of a printed listing this window holds, and when to say what it could not.

`/settings`, `/help`, `/sessions`, `/approvals` and `/helpers` print straight into the transcript, and
a listing taller than the window puts its FIRST rows in the scrollback — the half somebody came for.
Fit what the window holds, then say what is not on it; two passes, since that sentence costs rows.

CHROME is six rows, measured against the real CLI at 80x24 — trailing blank, three-row prompt box,
footer, one clear row above — which is why the budget is never `caps.height`. Every caller keeps its
head: dropping the tail is the only cut that leaves `/approvals rm 3` pointing at the row it names.
"""
from __future__ import annotations

from collections.abc import Sequence

FLOOR = 1
CHROME = 6


def room(caps, spent: int = 0) -> int:
    """Rows a printed block may take with its heading still on the screen, `spent` set aside for
    whatever the caller prints under it. Never zero — a listing that answers with a heading and nothing
    at all is worse than one row over.

    Under about twelve rows the floor is what the arithmetic returns anyway: a window that small is
    smaller than the heading, the closing line and the four rows of prompt beneath them, so no budget
    can hold the view inside it and the floor decides to keep a row of content rather than none."""
    return max(FLOOR, caps.height - CHROME - spent)


def plan(caps, sizes: Sequence[int], *, spent: int = 0, note: int = 1) -> tuple[int, int]:
    """(blocks to draw whole, rows spare for a partial one after them).

    `spent` is what the caller prints when the listing fits; `note` what it prints instead when it does
    not. The spare is for a first block bigger than the whole window — `/help`'s nineteen commands at
    24 rows — where drawing none of it would be a heading with nothing under it."""
    whole, _ = _walk(sizes, room(caps, spent))
    if whole == len(sizes):
        return whole, 0
    return _walk(sizes, room(caps, note))


def _walk(sizes: Sequence[int], budget: int) -> tuple[int, int]:
    used = 0
    for n, size in enumerate(sizes):
        if used + size > budget:
            return n, budget - used
        used += size
    return len(sizes), budget - used
