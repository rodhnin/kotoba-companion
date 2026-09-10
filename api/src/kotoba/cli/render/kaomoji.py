"""Her fourteen faces as two characters and a mouth — the tier every terminal can draw.

The mouth is fed by the stream, so she is visibly speaking while text arrives and closes when it stops.
A new mood is held behind a 110 ms blink rather than swapped: a face that cuts straight from `thinking`
to `happy` reads as a glitch, and painting inside that window prints the mood she is LEAVING.

`family` is the one place colour is information: her nameplate is tinted by it, and that is the only
device carrying identity inside the transcript.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from rich.cells import cell_len

FACES_UNICODE = {
    "neutral": ("･", "･", ("ω", "o", "O"), ""),
    "happy": ("^", "^", ("ω", "o", "O"), ""),
    "excited": ("★", "★", ("ω", "o", "O"), "!"),
    "sad": ("･", "･", ("︵", "o", "O"), ""),
    "crying": ("╥", "╥", ("ω", "o", "O"), ""),
    "angry": ("`", "´", ("Д", "o", "O"), ""),
    "surprised": ("⊙", "⊙", ("O", "O", "O"), "!"),
    "embarrassed": (">", "<", ("ω", "o", "O"), ""),
    "thinking": ("￣", "￣", ("_", "o", "O"), "?"),
    "sleepy": ("－", "－", ("ω", "o", "O"), "z"),
    "affectionate": ("♡", "♡", ("ω", "o", "O"), "~"),
    "confused": ("･", "?", ("ω", "o", "O"), ""),
    "scared": ("ﾟ", "ﾟ", ("Д", "o", "O"), ""),
    "determined": ("•", "•", ("ω", "o", "O"), ""),
}
FACES_ASCII = {
    "neutral": (".", ".", ("w", "o", "O"), ""),
    "happy": ("^", "^", ("w", "o", "O"), ""),
    "excited": ("*", "*", ("w", "o", "O"), "!"),
    "sad": (".", ".", ("n", "o", "O"), ""),
    "crying": (";", ";", ("w", "o", "O"), ""),
    "angry": ("`", "'", ("A", "o", "O"), ""),
    "surprised": ("O", "O", ("O", "O", "O"), "!"),
    "embarrassed": (">", "<", ("w", "o", "O"), ""),
    "thinking": ("-", "-", ("_", "o", "O"), "?"),
    "sleepy": ("-", "-", ("w", "o", "O"), "z"),
    "affectionate": ("<", ">", ("w", "o", "O"), "~"),
    "confused": (".", "?", ("w", "o", "O"), ""),
    "scared": ("o", "o", ("A", "o", "O"), ""),
    "determined": ("*", "*", ("w", "o", "O"), ""),
}
EMOTIONS = tuple(FACES_UNICODE)
FAMILY = {
    "happy": "coral", "excited": "coral", "affectionate": "coral", "determined": "coral",
    "thinking": "grape", "confused": "grape", "surprised": "sun", "embarrassed": "sun",
    "angry": "live", "scared": "live", "crying": "live",
    "sad": "muted", "sleepy": "muted", "neutral": "edge",
}
PLATE_SLOT = {"coral": "coral", "grape": "grape", "sun": "sun", "live": "live",
              "muted": "ink", "edge": "coral"}
BLINK_S = 0.11


@dataclass
class Face:
    unicode: bool = True
    emotion: str = "neutral"
    _pending: str | None = None
    _blink_until: float = 0.0
    _open: float = 0.0
    _last_token: float = 0.0
    width: int = 0

    def __post_init__(self) -> None:
        table = self._table
        self.width = max(cell_len(self._draw(e, 0.0, table, blink=False)) for e in table)

    @property
    def _table(self) -> dict:
        return FACES_UNICODE if self.unicode else FACES_ASCII

    def set(self, emotion: str, *, instant: bool = False) -> None:
        if emotion not in FACES_UNICODE:
            return
        if instant:
            self.emotion, self._pending = emotion, None
        elif emotion != self.emotion or self._pending is not None:
            self._pending = emotion
            self._blink_until = time.monotonic() + BLINK_S

    def feed(self, chars: int) -> None:
        self._open = min(1.0, self._open + 0.35 + min(chars, 12) / 24)
        self._last_token = time.monotonic()

    def rest(self) -> None:
        self._open = 0.0
        self._last_token = 0.0

    def settle(self) -> None:
        if self._pending is not None:
            self.emotion, self._pending = self._pending, None

    @property
    def settled(self) -> bool:
        return self._pending is None

    @property
    def family(self) -> str:
        return FAMILY[self._pending or self.emotion]

    @property
    def style(self) -> str:
        return "face." + self.family

    @property
    def plate(self) -> str:
        return PLATE_SLOT[self.family]

    def render(self, *, still: bool = False) -> str:
        """The moving face: blinking through a mood change, mouth decaying since the last token.
        `still` is `--calm`'s — the same cells at the same width, mood settled at once, mouth shut,
        no blink frame — because that flag promises nothing moves and this was the one mark that did
        when the marks were audited (the prototype moved it too, and made no such promise)."""
        now = time.monotonic()
        blink = False
        if self._pending is not None:
            if still or now >= self._blink_until:
                self.emotion, self._pending = self._pending, None
            else:
                blink = True
        if self._last_token:
            self._open = max(0.0, self._open - (now - self._last_token) * 2.2)
        drawn = self._draw(self.emotion, 0.0 if still else self._open, self._table, blink=blink)
        return drawn + " " * max(0, self.width - cell_len(drawn))

    def still(self) -> str:
        """The face a printed row keeps: settled, mouth shut, no blink. Scrollback never repaints."""
        self.settle()
        return self._draw(self.emotion, 0.0, self._table, blink=False)

    def _draw(self, emotion: str, openness: float, table: dict, *, blink: bool) -> str:
        left, right, mouths, tail = table[emotion]
        if blink:
            left, right = ("－", "－") if self.unicode else ("-", "-")
        mouth = mouths[0] if openness < 0.30 else (mouths[1] if openness < 0.72 else mouths[2])
        return f"( {left}{mouth}{right} ){tail}"
