"""Text that somebody else wrote, made safe to DRAW — never safe to trust.

The first family MOVES THE CURSOR: `rm -rf ~` plus erase-line and carriage-return paints the dangerous
half, wipes the row and paints a harmless one over it, so the card asks about a command on screen for
no frames at all. Those become a SPACE, so every readable byte still shows rather than being deleted.

The second REORDERS OR HIDES: an RLO reverses everything after it, a ZWSP splits one token into two.
Those are removed outright — a visible space would hand the attacker the word boundary. Left alone,
because the line is OVERRIDE vs SCRIPT: RTL scripts themselves, ZWNJ/ZWJ, variation selectors and
combining marks. Every codepoint below is written as an escape, never as itself."""
from __future__ import annotations

import re

# Cursor moves and line ends: C0, DEL, the C1 block a terminal may still decode as a single-byte CSI,
# and the two separators that end a line for a browser but not for a terminal.
_MOVES = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")

# No glyph, no script role. Written out one range at a time, because a set this file widens by accident
# is the bug on the other side of the one it fixes.
_INVISIBLE = re.compile(
    "["
    r"\xad"                      # soft hyphen — a break opportunity that shows nothing until it breaks
    r"\u061c\u200e\u200f"        # ALM, LRM, RLM — direction marks over neutral runs
    r"\u202a-\u202e"             # LRE RLE PDF LRO RLO — the Trojan Source family
    r"\u2066-\u2069"             # LRI RLI FSI PDI — the isolates that do the same job
    r"\u180e\u200b"              # Mongolian vowel separator, zero-width space
    r"\u2060-\u2064"             # word joiner and the invisible math operators
    r"\ufeff"                    # BOM / zero-width no-break space
    r"\ufff9-\ufffb"             # interlinear annotation — text hidden behind other text
    r"\U000e0000-\U000e007f"     # the tag block: readable ASCII, invisibly
    "]"
)


def scrub(value: object, *, newlines: bool = False) -> str:
    """One string, safe for any renderer that does not escape.

    `newlines` keeps `\\n` — the approval card is built as a block of lines and splits on them — while
    still neutralising the carriage return beside it, which is a cursor move and not a line end."""
    text = str(value if value is not None else "")
    if newlines:
        return "\n".join(_scrub_line(line) for line in text.split("\n"))
    return _scrub_line(text)


def _scrub_line(line: str) -> str:
    return _INVISIBLE.sub("", _MOVES.sub(" ", line))
