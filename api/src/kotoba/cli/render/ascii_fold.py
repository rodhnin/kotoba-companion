"""The ASCII fold, and the one glyph that is exempt from it.

Apart from the theme because two callers need it on an install with no renderer at all — first run's
plain twin and the doctor report, neither of which may import rich. Every glyph below is cmap-verified
in JetBrains Mono NF, CaskaydiaCove NF and Noto CJK, covering every non-ASCII character her copy holds.

`WIDE` is the exemption, and it is a width argument rather than a favour to the sigil: every other
entry measures one cell, and the East Asian *Ambiguous* ones among them take two on a CJK-configured
terminal. 言 is unambiguously *Wide* to both sides, so it is the one that can stay when they cannot.
"""
from __future__ import annotations

ASCII_FOLD = (
    ("—", "--"), ("–", "-"), ("…", "..."), ("·", "."), ("•", "*"), ("’", "'"), ("‘", "'"),
    ("“", '"'), ("”", '"'), ("→", "->"), ("←", "<-"), ("⏎", "<-'"), ("▸", ">"), ("◂", "<"),
    ("▴", "^"), ("▪", "*"), ("◦", "o"), ("✓", "+"), ("×", "x"), ("◇", "~"), ("●", "*"),
    ("○", "o"), ("言", "K"), ("─", "-"), ("│", "|"), ("█", "|"), ("▌", " "), ("▀", "-"),
    ("▄", "_"), ("›", ">"), ("♡", "<3"), ("ω", "w"), ("▏", "["), ("▕", "]"), ("□", "["),
    ("└", "+"),
)
WIDE = ("言",)


def fold(s: str, keep_wide: bool = False) -> str:
    """The DECORATIVE half of `--ascii`: every glyph in the table above, and nothing beyond it.

    `--ascii` exists for a terminal that cannot DRAW our chrome, and what she WRITES is not chrome: a
    catch-all turning every remaining non-ASCII character into `?` made `¡Qué gusto verte por aquí!`
    read `?Qu? gusto verte por aqu?!` — broken Spanish from a product whose whole claim is that it
    speaks your language, and every one of her words comes through this one function.

    `keep_wide` is the other fallback and not `--ascii` at all: a UTF-8 terminal that draws ambiguous
    glyphs two cells wide loses the chrome measured as one cell and keeps `WIDE`. A terminal that
    cannot ENCODE `é` is answered at the stream instead, not guessed at here."""
    for wide, plain in ASCII_FOLD:
        if keep_wide and wide in WIDE:
            continue
        s = s.replace(wide, plain)
    return s
