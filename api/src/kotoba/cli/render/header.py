"""The header: what she is, stated once, and never repainted.

Nothing here asks anything and nothing here may wrap: the facts arrive built, and every row is clamped
to the width. The column origin is a fixed PITCH, never a minimum gap — a minimum lets a long model
name push the next column wherever it lands, and three rows on three cells is a list, not a layout.

Cells fill COLUMN-major, and rank drops the decorative pair rather than reordering everything above it.
`● LIVE` is the one claim about the process, so it is spent only on a terminal that can answer — a
piped transcript saying LIVE is a lie left in a file. Never repainted: two banners are two starts.
"""
from __future__ import annotations

from math import ceil

from rich.cells import cell_len
from rich.text import Text

from kotoba.cli.render.safe import Safe
from kotoba.cli.render.text import column, fit

CONFETTI = (("▴", "coral"), ("▪", "grape"), ("◦", "mint"), ("▸", "sun"))
COLW = 52
RULE_HUES = {"dark": ((0xff, 0x5a, 0x3c), (0xd8, 0x40, 0x28)),
             "mid": ((0xe1, 0x3c, 0x21), (0xbc, 0x2f, 0x18)),
             "light": ((0xc7, 0x3a, 0x1e), (0xa3, 0x2d, 0x16))}


def header_rows(caps, plate: Text, width: int, stats: list[tuple[str, str, int]],
                note: str = "", portrait_rows: int = 0, face: Text | None = None,
                live: bool = True) -> list[Text]:
    """Her plate, the tagline, the grid and the sandbox clause — spread down the portrait's height when
    there is one beside them, and clamped to `width` whatever happens.

    `live=False` is first run asking for the same banner without the one claim it cannot make yet: no
    key exists, nothing is connected, and `just type` is an instruction that would not work."""
    head = [_top(caps, plate, width, face, live)]
    if not portrait_rows and caps.color != "none" and caps.unicode:
        head.append(_underline(caps, head[0]))
    head.append(_tagline(caps, width))
    cols = 3 if width >= 126 else 2 if width >= 64 else 1
    lines = ceil(len(stats) / cols) if cols > 1 else (4 if width >= 46 else 3)
    grid = _stat_grid(caps, stats, width, lines, cols)
    clause = [_sandbox_line(caps, note, width)] if note else []
    gaps = max(0, portrait_rows - (len(head) + len(grid) + len(clause)))
    out = head + [Text()] * min(1, gaps) + grid + [Text()] * max(0, gaps - 1) + clause
    for row in out:
        if row.cell_len > width:
            row.truncate(width, overflow="ellipsis")
    return out


def confetti(caps, n: int = 3, seed: int = 11) -> Text:
    """The pips after the tagline. The seed is fixed on purpose: a scatter that lands differently on
    every launch is an animation nobody asked for, and this one is hers."""
    out = Text()
    r = seed
    for _ in range(n):
        r = (r * 1103515245 + 12345) & 0x7fffffff
        glyph, slot = CONFETTI[r % len(CONFETTI)]
        out.append(" " * (1 + (r >> 8) % 2))
        out.append(glyph if caps.unicode else "*", style=f"d.{slot}")
    return out


def rule(caps) -> Text:
    """Coral, hers, fading to the background at both ends so the frame reads as chrome and is never
    the brightest thing on the screen. One hue: it is the same coral as the 言 sigil and her plate."""
    n = caps.width
    if caps.color != "truecolor":
        return Text(caps.g["rule"] * n, style="chrome")
    head, tail = RULE_HUES[caps.background]
    base = 0xfb if caps.background == "light" else 0x21
    out = Text()
    for i in range(n):
        towards = i / max(1, n - 1)
        fade = min(1.0, 2.5 * min(i / n, 1 - i / n) + 0.22)
        rgb = tuple(int(base + (head[j] + (tail[j] - head[j]) * towards - base) * fade)
                    for j in range(3))
        out.append(caps.g["rule"], style="#%02x%02x%02x" % rgb)
    return out


def _top(caps, plate: Text, width: int, face: Text | None = None, live: bool = True) -> Text:
    """Plate, then LIVE, then her face, then the clause — that order. The chip belongs to the plate it
    qualifies; a face wedged between the two reads as two separate stickers."""
    head = plate.copy()
    if not caps.interactive or not live:
        if face is not None:
            head.append("  ")
            head.append_text(face)
        return head
    head.append("  ")
    head.append(f" {caps.g['dot']} LIVE ", style="chip.live")
    if caps.color != "none":
        head.append(caps.g["shadow"], style="shadow.live")
    if face is not None:
        head.append("  ")
        head.append_text(face)
    clause = fit(width - head.cell_len - 2,
                 caps.t("you're connected — just type"), caps.t("just type"))
    if clause:
        head.append("  " + clause, style="chrome")
    return head


def _underline(caps, top: Text) -> Text:
    """The one row that makes the plate read as a sticker on the page instead of a box on a line. It is
    only drawn when nothing else sits beside the header — a portrait already anchors it."""
    under = Text(" ")
    under.append(caps.g["under"] * (cell_len(top.plain.split("  ")[0]) - 1), style="shadow.coral")
    return under


def _tagline(caps, width: int) -> Text:
    trio = Text()
    for phrase, slot in (("She talks.", "coral"), ("She listens.", "grape"), ("She does.", "mint")):
        trio.append(phrase, style=slot)
        trio.append(" ")
    if width >= 52 and caps.color != "none":
        trio.append_text(confetti(caps, 3 + (width >= 108) + (width >= 136)))
    return trio


def _sandbox_line(caps, note: str, width: int) -> Text:
    """One clause that explains every approval the user will ever see."""
    tail = fit(width - 14, caps.t(" — I ask before anything that isn't a plain read"),
               caps.t(" — reads pass, the rest asks"), caps.t(" — I ask first"))
    line = Safe()
    line.append(caps.t(note), style="sun")
    line.append(tail, style="chrome")
    return line


def _stat_grid(caps, stats: list[tuple[str, str, int]], width: int, lines: int,
               cols: int) -> list[Text]:
    """Drops the least useful pairs, never the most: KEYS is the only place `@file` and alt-enter are
    advertised outside `/help`, so MEM goes first. `colw` is the pitch every column starts on, and the
    label is six cells whatever it says — LABEL, value and a two-cell gutter, always adding to `colw`.

    The value is cut by `column` rather than by rich's ellipsis: rich only has `…`, and the ASCII fold
    turns that one cell into three on its way out — the row overflowing after it measured as fitting.

    The rows are `Safe` because these facts are strings the process was handed — a model name, a path
    off the disk — and `rich.Text` carries an ESC inside one of them to the terminal, which obeys it.
    No facts is not one empty row: first run states none of them."""
    if not stats:
        return []
    pairs = sorted(sorted(stats, key=lambda p: p[2])[:lines * cols], key=stats.index)
    per = max(1, ceil(len(pairs) / cols))
    colw = min(width // cols, COLW)
    out = []
    for i in range(min(lines, per)):
        row = Safe()
        for c in range(cols):
            k = c * per + i
            if k >= len(pairs):
                continue
            label, value, _ = pairs[k]
            row.append(f"{label:<6}", style="chrome")
            row.append(column(caps.t(value), colw - 8, caps.unicode))
            row.append("  ")
        out.append(row)
    return out
