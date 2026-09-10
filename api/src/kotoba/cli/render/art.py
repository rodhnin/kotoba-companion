"""Her pixel art in a terminal.

Two tiers off the same PNGs. **sixel**: integer NEAREST upscale, pad to whole cells, then chafa — crisp
because nothing ever resamples, one source pixel becoming an exact scale x scale block. **blocks**: the
same PNG as half-blocks, half a pixel per cell across, so she is only legible while she is wide.

`<emotion>-outlined.png` is the light-terminal twin: on a cream background her chin dissolves without
the ink ring. The canvases are deliberately not one size, and WHICH ones are tall is an art decision
that moves, so nothing here names them and nothing reserves room from one of them.
"""
from __future__ import annotations

import math
import os
import subprocess
from pathlib import Path

from kotoba.cli.render.kaomoji import EMOTIONS
from kotoba.paths import DATA_DIR, REPO_ROOT

BOOT_SCALE = 3
INLINE_SCALE = 2

# The whole render, not the exit status: a wedged chafa may not hold a launch open past this.
BUDGET = 5.0

# Measured, not guessed: below this she still has hair and clips, but every expression collapses to the
# same blonde blob.
MIN_BLOCK_COLS = 26

_sixel_cache: dict = {}
_block_cache: dict = {}


def faces_dir() -> Path:
    """The checkout keeps the art beside the frontend that also uses it; a wheel carries its own copy
    under DATA_DIR. The clone is tried FIRST, like every other path resolver — preferring the package
    means an import that wrote only `assets/` is not merely ignored but silently overridden, with the
    tool still reporting success. The importer writes both trees from one buffer and refuses to finish
    while they differ; the order here is what makes a forgotten copy degrade instead of disappear."""
    override = os.getenv("KOTOBA_CLI_FACES")
    if override:
        return Path(override).expanduser()
    repo = REPO_ROOT / "assets" / "cli" / "faces"
    return repo if repo.is_dir() else DATA_DIR / "cli" / "faces"


def path(emotion: str, outlined: bool = False) -> Path:
    """The emotion outranks the ring. `-outlined` exists for legibility on a light background; the
    emotion is the message, and a bring-your-own face set is allowed to be
    partial. A missing outlined face used to fall straight to plain `neutral.png` — a legible face
    wearing the WRONG emotion — so the ladder drops the ring first and the emotion last, and only
    once the emotion is gone in both forms does the ring preference resume."""
    name = emotion if emotion in EMOTIONS else "neutral"
    base = faces_dir()
    ladder = ([f"{name}-outlined.png", f"{name}.png", "neutral-outlined.png"]
              if outlined else [f"{name}.png"])
    for step in ladder:
        found = base / step
        if found.exists():
            return found
    return base / "neutral.png"


def available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return path("neutral").exists()


def cells(emotion: str, scale: int, cell: tuple[int, int], outlined: bool = False) -> tuple[int, int]:
    """(cols, rows) this face will occupy, without rendering it."""
    from PIL import Image

    with Image.open(path(emotion, outlined)) as im:
        return (math.ceil(im.width * scale / cell[0]), math.ceil(im.height * scale / cell[1]))


def sixel(emotion: str, scale: int, cell: tuple[int, int], outlined: bool = False) -> tuple[str, int, int]:
    """(payload, cols, rows). The payload ends with no newline — the caller owns the cursor. Raises when
    chafa is missing, so callers fall back a tier."""
    key = (emotion, scale, cell, outlined)
    if key not in _sixel_cache:
        im, cols, rows = _padded(emotion, outlined, scale, cell)
        _sixel_cache[key] = (_chafa(im, cols, rows, cell), cols, rows)
    return _sixel_cache[key]


def block_rows(emotion: str, cols: int, cell: tuple[int, int], outlined: bool = False,
               alpha: int = 120) -> list[str]:
    """Half-blocks: one SGR run per colour change, transparent cells left to the terminal's own
    background so she sits on the user's theme instead of a rectangle of ours."""
    from PIL import Image

    key = (emotion, cols, cell, outlined, alpha)
    if key in _block_cache:
        return _block_cache[key]
    im = _load(emotion, outlined)
    sub = max(2, round(cols * (im.height / im.width) * (cell[0] / (cell[1] / 2))))
    sub -= sub % 2
    im = im.resize((cols, sub), Image.NEAREST)
    px = im.load()
    out = []
    for y in range(0, sub, 2):
        row, prev = [], None
        for x in range(cols):
            top, bot = px[x, y], px[x, y + 1]
            top = top[:3] if top[3] > alpha else None
            bot = bot[:3] if bot[3] > alpha else None
            if not top and not bot:
                if prev is not None:
                    row.append("\x1b[0m")
                    prev = None
                row.append(" ")
                continue
            if top and bot:
                sgr = f"\x1b[38;2;{top[0]};{top[1]};{top[2]};48;2;{bot[0]};{bot[1]};{bot[2]}m"
                glyph = "▀"
            else:
                seen = top or bot
                sgr = f"\x1b[49;38;2;{seen[0]};{seen[1]};{seen[2]}m"
                glyph = "▀" if top else "▄"
            if sgr != prev:
                row.append(sgr)
                prev = sgr
            row.append(glyph)
        out.append("".join(row) + "\x1b[0m")
    _block_cache[key] = out
    return out


def _load(emotion: str, outlined: bool):
    from PIL import Image

    return Image.open(path(emotion, outlined)).convert("RGBA")


def _padded(emotion: str, outlined: bool, scale: int, cell: tuple[int, int]):
    """Her face centred on a whole number of cells.

    A face is never an exact multiple of a cell, so a few pixels always have to go somewhere, and where
    they go is where she appears to sit: flush to a corner reads as pushed into it — bottom-anchored,
    she looked low — and the centred version was picked off three photographs at 96 columns.

    The remainder differs from face to face, so centring costs up to 2 px of difference between one
    face and the next: at a 10-px cell and BOOT_SCALE, 1 px for crying and confused, none for excited
    and surprised, 3 for the other ten. Half of what flush-left removes, and chosen with that on the
    table. The block tier does not come through here at all."""
    from PIL import Image

    cw, ch = cell
    im = _load(emotion, outlined)
    im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    cols, rows = math.ceil(im.width / cw), math.ceil(im.height / ch)
    out = Image.new("RGBA", (cols * cw, rows * ch), (0, 0, 0, 0))
    out.alpha_composite(im, ((cols * cw - im.width) // 2, (rows * ch - im.height) // 2))
    return out, cols, rows


def _chafa(im, cols: int, rows: int, cell: tuple[int, int]) -> str:
    """chafa reads the cell size off its own stdout, so it gets a pty sized to the real terminal —
    through a pipe it assumes 10x20, rescales to fit, and every hard pixel edge turns to porridge.

    `--probe=off --polite=on`: without them chafa interrogates the terminal once per face, and the
    replies arrive on stdin, inside whatever the user is typing.

    The read is on a clock. Reading a pty to EOF waits for the child to close it, so a chafa that
    wedges holds the launch forever with nothing on the screen — measured, no output, no timeout. A
    face costs 22-62 ms, so BUDGET seconds is only ever spent by something that is not coming back."""
    import fcntl
    import io
    import pty
    import select
    import struct
    import termios
    import time

    buf = io.BytesIO()
    im.save(buf, "PNG")
    mfd, sfd = pty.openpty()
    proc = None
    try:
        fcntl.ioctl(sfd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", 24, 80, 80 * cell[0], 24 * cell[1]))
        mode = termios.tcgetattr(sfd)
        mode[1] &= ~termios.OPOST
        termios.tcsetattr(sfd, termios.TCSANOW, mode)
        proc = subprocess.Popen(
            ["chafa", "--format=sixels", "--probe=off", "--polite=on", "--relative=off",
             "--animate=off", "--colors=240", "--dither=none", f"--size={cols}x{rows}", "-"],
            stdin=subprocess.PIPE, stdout=sfd, stderr=subprocess.DEVNULL)
        os.close(sfd)
        sfd = -1
        proc.stdin.write(buf.getvalue())
        proc.stdin.close()
        out = b""
        deadline = time.monotonic() + BUDGET
        while time.monotonic() < deadline:
            if not select.select([mfd], [], [], deadline - time.monotonic())[0]:
                break
            try:
                chunk = os.read(mfd, 1 << 16)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    finally:
        if sfd != -1:
            os.close(sfd)
        os.close(mfd)
        if proc is not None:
            _reap(proc)
    start = out.find(b"\x1bP")
    end = out.find(b"\x1b\\", start)
    if start < 0 or end < 0:
        raise RuntimeError("chafa produced no sixel")
    return out[start:end + 2].decode("latin-1")


def _reap(proc) -> None:
    for grace in (0.5, 1.0):
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            proc.kill()
