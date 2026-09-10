"""Which of her three faces this terminal earns, and the block that puts one beside text.

The ladder is measured, in this order: not a tty, `--no-face`, `KOTOBA_FORCE_PORTRAIT=none`, under 62
columns, no unicode or no colour at all -> nothing but the kaomoji; else sixel if DA1 advertised it;
else half-blocks if the terminal has 24-bit colour and the room for 26 of them. A mushed face is worse
than none, which is why the block tier has a floor and the sixel tier never resizes.

It is orthogonal to the colour ladder: sixel carries its own palette, so a 16-colour TERM that answers
DA1 with a `4` still gets a full-colour portrait.
"""
from __future__ import annotations

import logging
import os

from kotoba.cli.render import art

log = logging.getLogger("kotoba.cli")

#: Her left margin and the gap to the text. One column, not three: at three she read as pushed away
#: from the edge everything under her is aligned to.
INDENT, GUTTER = 1, 3

#: How much further right her REPLY face sits than the boot one. A separate margin per surface is only
#: safe because the column now travels WITH the art, banked and put back with each face. It did not,
#: once: a repainter that restores sixels by ROW cannot know which face was on which row, so it
#: returned them all to INDENT and typing `/` slid her two columns left.
INLINE_NUDGE = 2


class Portrait:
    def __init__(self, caps, *, wanted: bool = True) -> None:
        self.caps = caps
        self.mode = "none"
        self.cols = self.rows = 0
        self.icols = self.irows = 0
        # What the last composition left on the glass, for whoever has to put those rows back.
        self.last_rows: list[str] = []
        self.last_art = ""
        # The column that art was drawn at, banked with it so whoever repaints puts it back there —
        # and the rows it spans, so whoever paints UNDER it stops where this face does, not where
        # the tallest one would.
        self.last_col = INDENT
        self.last_reach = 0
        self.outlined = caps.background == "light"
        forced = os.getenv("KOTOBA_FORCE_PORTRAIT", "")
        if not wanted or not caps.interactive or forced == "none":
            return
        if caps.width < 62 or not caps.unicode or caps.color == "none" or not art.available():
            return
        if (caps.sixel or forced == "sixel") and forced != "blocks" and self._try_sixel():
            return
        self._try_blocks()

    @property
    def gutter_cols(self) -> int:
        return (self.cols + INDENT + GUTTER) if self.mode != "none" else 0

    @property
    def inline_indent(self) -> int:
        return INDENT + INLINE_NUDGE

    @property
    def inline_gutter(self) -> int:
        return self.icols + self.inline_indent + GUTTER

    @property
    def inline_ok(self) -> bool:
        """Only sixel earns a face on every reply. Half-blocks cost nine rows for one, which is a wall."""
        return self.mode == "sixel" and self.caps.width >= self.inline_gutter + 40

    def rows_for(self, emotion: str, scale: int = art.BOOT_SCALE) -> int:
        """How many rows THIS face needs on the tier that was earned. Both tiers answer out of their
        own cache, so asking is a dictionary lookup after the first render and `none` never asks."""
        if self.mode == "sixel":
            return self._sixel(emotion, scale)[2]
        if self.mode == "blocks":
            return len(self._blocks(emotion))
        return 0

    def header(self, right: list[str], emotion: str = "neutral") -> str:
        """Her rows with the boot-size face beside them, or just the rows when no tier was earned.

        The HEIGHT is asked of the emotion being drawn, never of `self.rows`. The canvases are
        deliberately not one size and the reference face is the smallest of them, so a budget taken
        from it clipped her chin off every taller one with no error: on the block tier the row was
        dropped, on the sixel tier the image was drawn over whatever the caller printed next.

        The WIDTH deliberately stays `self.cols`: the caller reads `gutter_cols` BEFORE composing, to
        work out how wide the text beside her may be, so re-deriving the width per emotion would push
        that text past the terminal edge. A wider face spends GUTTER instead."""
        return self._compose(right, art.BOOT_SCALE, max(self.rows_for(emotion), len(right)),
                             self.cols, emotion)

    def inline(self, right: list[str], emotion: str,
               painted: bool = False) -> tuple[str, int] | None:
        """Her face for one reply, with the reply's first rows beside it and the rest kept at the same
        indent after it ends. The height is asked per emotion rather than reserved at the tallest,
        because the canvases differ and which ones are tall is an art decision that moves.

        `painted` means the live region already put her on these rows: the text walks over her column
        instead of spacing across it, and no sixel goes out at all."""
        if not self.inline_ok:
            return None
        try:
            rows = self.rows_for(emotion, art.INLINE_SCALE)
        except Exception:
            log.debug("no inline sixel for %s", emotion, exc_info=True)
            return None
        return (self._compose(right, art.INLINE_SCALE, rows, self.icols, emotion, painted,
                              self.inline_indent), rows)

    def _try_sixel(self) -> bool:
        """Two different disciplines, and the difference is who reads them. `cols`/`rows` are the boot
        RESERVATION, measured from the reference face because the caller sizes its text block off
        `gutter_cols` before any emotion is known — they are a starting point, not the height of the
        face that ends up drawn, which is `rows_for`. `icols`/`irows` are the max across the whole set,
        because the inline gutter has to hold still while she changes mood mid-conversation."""
        try:
            _, self.cols, self.rows = self._sixel("neutral", art.BOOT_SCALE)
            sizes = [art.cells(e, art.INLINE_SCALE, self.caps.cell, self.outlined)
                     for e in art.EMOTIONS]
            self.icols = max(c for c, _ in sizes)
            self.irows = max(r for _, r in sizes)
            self.mode = "sixel"
            return True
        except Exception:
            log.debug("no sixel tier", exc_info=True)
            return False

    def _try_blocks(self) -> None:
        if self.caps.color != "truecolor" or self.caps.width < 88:
            return
        try:
            self.cols = art.MIN_BLOCK_COLS
            self.rows = len(self._blocks("neutral"))
            self.mode = "blocks"
        except Exception:
            log.debug("no block tier", exc_info=True)
            self.cols = self.rows = 0

    def art(self, emotion: str):
        """(payload, cols, rows) for one reply's face, without composing it — what the live region
        writes itself, once, instead of taking a composed block it would have to erase."""
        return self._sixel(emotion, art.INLINE_SCALE)

    def _sixel(self, emotion: str, scale: int):
        return art.sixel(emotion, scale, self.caps.cell, self.outlined)

    def _blocks(self, emotion: str) -> list[str]:
        return art.block_rows(emotion, art.MIN_BLOCK_COLS, self.caps.cell, self.outlined)

    def _compose(self, right: list[str], scale: int, rows: int, cols: int, emotion: str,
                 painted: bool = False, indent: int = INDENT) -> str:
        """Text block first, then the image dropped in beside it. Where a sixel leaves the cursor is
        terminal-dependent, so the walk back over the rows it was placed on is wrapped in DECSC/DECRC
        rather than guessed at. A space is a character and a character rubs out the sixel under it, so
        over a face already on the glass the indent goes out as a cursor move instead.

        A tier of `none` composes the text and nothing else. The ladder already refused this terminal —
        it is 40 columns wide, or piped, or has no art to read — and rendering the face anyway would put
        ten to fourteen kilobytes of DCS (inline tier to boot tier, measured at the default cell)
        somewhere nobody can scroll it back out of."""
        self.last_col = indent
        self.last_reach = rows if self.mode == "sixel" else 0
        col = indent + cols + GUTTER
        head, tail = right[:rows], right[rows:]
        out, keep = [], []
        if self.mode == "none":
            for line in right:
                keep.append(" " * indent + line)
                out.append(keep[-1] + "\x1b[K\n")
            self.last_rows, self.last_art = keep, ""
            return "".join(out)
        if self.mode == "blocks":
            drawn = self._blocks(emotion)
            for i in range(max(rows, len(head))):
                line = drawn[i] if i < len(drawn) else ""
                pad = " " * (col - indent - (cols if line else 0))
                keep.append(" " * indent + line + pad + (head[i] if i < len(head) else ""))
                out.append(keep[-1] + "\x1b[K\n")
        else:
            payload, _, _ = self._sixel(emotion, scale)
            step = f"\x1b[{col}C" if painted else " " * col
            for i in range(rows):
                body = head[i] if i < len(head) else ""
                keep.append(" " * col + body)
                out.append(step + body + "\x1b[K\n")
            if not painted:
                out.append("\x1b7" + f"\x1b[{rows}A" + f"\r\x1b[{indent}C" + payload + "\x1b8")
            self.last_art = payload
        for line in tail:
            keep.append(" " * col + line)
            out.append(keep[-1] + "\x1b[K\n")
        self.last_rows = keep
        return "".join(out)
