"""How a path is written when the row it lives on is narrower than the path is.

A path gives up its MIDDLE, `/home/…/files`, never its end: the end is the only part that identifies
it. The ASCII mark is three dots: `..` is path syntax, and `/home/../files` is a different directory.
A URL is never elided. `FLOOR` is 8 cells: below it a fragment of the final name identifies a file no
better than the caller's own clip. A space does not end a path, so a run joins across one only via a
CONNECTOR carrying no separator — the `-` in `My Projects - Archive`. Two separator-bearing pieces
side by side are likelier `cp SRC DST`, and absorbing an argument invents a path a person is asked to
consent to; absorbing too little costs nothing, because the untaken tail prints verbatim.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from rich.cells import cell_len

FLOOR = 8

_PIECE = re.compile(r"\S+")
_BREAK = re.compile(r"""["'`|;&<>()·•]|://|^\.{1,2}$""")
_EDGE = """"'`,;:()[]{}<>·•"""


def shown(path) -> str:
    """`~` for the home prefix, and only on a real boundary: `/home/userX` is not inside
    `/home/user` and must not come back as `~X`."""
    text, home = str(path), str(Path.home())
    if not home or home == os.sep or not text.startswith(home):
        return text
    rest = text[len(home):]
    return "~" + rest if not rest or rest.startswith(os.sep) else text


def shorten(text: str, room: int, unicode: bool = True) -> str:
    """`text` inside `room` CELLS with the paths in it elided through the middle, or "" when no path in
    it can give up enough — which leaves the caller's own clip to answer, as it did before.

    Widest path first, and the budget is re-asked after each one, so a row carrying two of them spends
    the elision where it buys the most and stops as soon as the row fits."""
    mark = "…" if unicode else "..."
    out = text
    for token in _paths(text):
        if cell_len(out) <= room:
            break
        short = _middle(token, room - (cell_len(out) - cell_len(token)), mark)
        if short:
            out = out.replace(token, short, 1)
    return out if out != text and cell_len(out) <= room else ""


def _paths(text: str) -> list[str]:
    """The path-like tokens in it, widest first. Two separators, because a name with one has no middle
    to give up. Each one is an exact SLICE of `text`, so `shorten` can put the short form back where it
    stood however the line was spaced."""
    found = [text[a:b] for a, b in _runs(text)]
    found = [t for t in found if t.count("/") >= 2 and "://" not in t]
    return sorted(dict.fromkeys(found), key=cell_len, reverse=True)


def _runs(text: str) -> list[tuple[int, int]]:
    """The span of every path in the line, spaces and all.

    A run opens on the first piece carrying a separator and stays open across ordinary words; a rooted
    piece or a break — a quote, a shell operator, the `·` between two facts, a URL — closes it, so the
    words and the paths after one are never inside it. `_reach` then says how much of the open run is
    really the path."""
    runs: list[list[tuple[int, int, str]]] = []
    run: list[tuple[int, int, str]] = []
    for match in _PIECE.finditer(text):
        piece = match.group()
        core = piece.strip(_EDGE)
        broke = not core or bool(_BREAK.search(core))
        if run and (broke or _rooted(core)):
            runs.append(run)
            run = []
        if broke:
            continue
        lead = match.start() + len(piece) - len(piece.lstrip(_EDGE))
        if run or "/" in core:
            run.append((lead, lead + len(core), core))
    runs.append(run)
    return [span for r in runs for span in _spans(r)]


def _spans(run: list[tuple[int, int, str]]) -> list[tuple[int, int]]:
    """One open run cut into the paths it actually holds. What `_reach` declines to join is not thrown
    away: the pieces past it are read again from the start, so `cp SRC DST` offers BOTH arguments and
    the elision goes to whichever buys the row more room."""
    out = []
    while run:
        last = _reach(run)
        out.append((run[0][0], run[last][1]))
        run = run[last + 1:]
    return out


def _reach(run: list[tuple[int, int, str]]) -> int:
    """How far the run reaches: the last piece still carrying a separator, or the first piece alone.

    Alone is the answer unless the run is ROOTED and a connector stands between the two — one piece
    carrying no separator, which is what a directory name with a space in it looks like from here and
    what an argument list never has."""
    last = max((i for i, p in enumerate(run) if "/" in p[2]), default=0)
    if last and _rooted(run[0][2]) and any("/" not in run[i][2] for i in range(1, last)):
        return last
    return 0


def _rooted(core: str) -> bool:
    """Absolute, or written from home. Only a rooted run may join across a space: a run that opens on a
    relative piece is the one most likely to have opened on a word."""
    return core.startswith(("/", "~/")) or core == "~"


def _middle(path: str, budget: int, mark: str) -> str:
    """As much of `path` as `budget` holds, taken from both ENDS: the root and its first name, then as
    many trailing names as fit.

    The head is what gives way when not even one trailing name fits beside it — `/…/files` still says
    the path is absolute. The last resort drops the separator too and keeps the right-hand end of the
    final name alone, because an extension and the characters before it identify a file where its
    directories no longer can; a name reduced to a FRAGMENT there is held to `FLOOR`."""
    lead = "/" if path.startswith("/") else ""
    parts = [p for p in path.split("/") if p]
    if len(parts) < 3 or budget < 1:
        return ""
    for stem in (f"{lead}{parts[0]}/", lead):
        best = ""
        for n in range(1, len(parts) - 1):
            candidate = f"{stem}{mark}/{'/'.join(parts[-n:])}"
            if cell_len(candidate) > budget:
                break
            best = candidate
        if best:
            return best if cell_len(best) < cell_len(path) else ""
    name = tail = parts[-1]
    while tail and cell_len(mark) + cell_len(tail) > budget:
        tail = tail[1:]
    if not tail or (tail != name and cell_len(tail) < FLOOR):
        return ""
    return f"{mark}{tail}" if cell_len(mark) + cell_len(tail) < cell_len(path) else ""
