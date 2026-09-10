#!/usr/bin/env python3
"""Show her fourteen faces in the terminal at full sprite quality.

Blocks hold half a pixel per cell, so 1:1 there costs one column per source pixel; sixel paints real
pixels inside the cell, which is why the portrait can be both small and sharp.

The grid is sized from the WIDEST and TALLEST of the fourteen, never from one. The canvases are
deliberately not uniform, so a box cut to one of them clips the others and sends a reviewer off to
regenerate a sprite that was fine. Faces are bottom-anchored and centred the way the renderer pads
them, so what you compare here is what the CLI draws.
"""

import argparse
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from PIL import Image

FACES = Path(__file__).resolve().parent.parent / "assets/cli/faces"
EMOTIONS = ["neutral", "happy", "excited", "affectionate", "embarrassed", "confused", "thinking",
            "surprised", "sad", "crying", "angry", "scared", "sleepy", "determined"]
CORAL = "\x1b[38;2;255;90;60m"
DIM = "\x1b[2m"
OFF = "\x1b[0m"


def cell_size(timeout=0.2):
    """Ask the terminal how big a cell is (CSI 16 t) — guessing from the font is how images end up
    the wrong shape on somebody else's setup. Every read sits behind a select() timeout: plenty of
    terminals never answer, and a blocking read there hangs the CLI on startup."""
    try:
        import select
        import termios
        import time
        import tty
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return 11, 24
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            sys.stdout.write("\x1b[16t")
            sys.stdout.flush()
            buf = ""
            deadline = time.monotonic() + timeout
            while len(buf) < 32 and time.monotonic() < deadline:
                if not select.select([fd], [], [], max(0.0, deadline - time.monotonic()))[0]:
                    break
                ch = os.read(fd, 1).decode(errors="ignore")
                buf += ch
                if ch == "t":
                    break
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        parts = buf.strip("\x1b[t").split(";")
        return int(parts[2]), int(parts[1])          # width, height in px
    except Exception:
        return 11, 24


def grid_cells(emotions, scale, cell):
    """The cell box every face is pasted into: big enough for the widest and the tallest of them."""
    cw, ch = cell
    sizes = [Image.open(FACES / f"{e}.png").size for e in emotions]
    return -(-max(w for w, _ in sizes) * scale // cw), -(-max(h for _, h in sizes) * scale // ch)


def sixel(path, scale, cols, rows, cell):
    """Upscale by an integer factor with NEAREST first: chafa's own scaler would smooth the blocks,
    which is exactly what the pixel art exists to avoid. chafa's --size is in CELLS, not pixels."""
    im = Image.open(path).convert("RGBA")
    im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    cw, ch = cell
    # Pad to an exact cell multiple. Off by even two pixels and chafa resamples the whole thing,
    # which re-blurs the blocks we went to all this trouble to keep hard.
    pad = Image.new("RGBA", (cols * cw, rows * ch), (0, 0, 0, 0))
    pad.paste(im, ((pad.width - im.width) // 2, pad.height - im.height))
    buf = io.BytesIO()
    pad.save(buf, "PNG")          # keep RGBA: flattening it here is what paints her a black box
    out = subprocess.run(["chafa", "-f", "sixel", "--size", f"{cols}x{rows}",
                          "--animate", "off", "--probe=off", "--polite=on", "-"],
                         input=buf.getvalue(), capture_output=True)
    return out.stdout.decode("latin-1")


def blocks(path):
    """(width, lines) at 1:1 — one column per source pixel, one line per two rows of them. An odd
    height is padded at the TOP: dropping the spare row instead eats the chin."""
    im = Image.open(path).convert("RGBA")
    im = im.crop(im.getbbox())
    if im.height % 2:
        tall = Image.new("RGBA", (im.width, im.height + 1), (0, 0, 0, 0))
        tall.paste(im, (0, 1))
        im = tall
    px = im.load()
    rows = []
    for y in range(0, im.height, 2):
        line = []
        for x in range(im.width):
            t, b = px[x, y], px[x, y + 1]
            if t[3] >= 110 and b[3] >= 110:
                line.append(f"\x1b[38;2;{t[0]};{t[1]};{t[2]}m\x1b[48;2;{b[0]};{b[1]};{b[2]}m▀")
            elif t[3] >= 110:
                line.append(f"\x1b[49m\x1b[38;2;{t[0]};{t[1]};{t[2]}m▀")
            elif b[3] >= 110:
                line.append(f"\x1b[49m\x1b[38;2;{b[0]};{b[1]};{b[2]}m▄")
            else:
                line.append("\x1b[0m ")
        rows.append("".join(line) + OFF)
    return im.width, rows


def place(lines, width, w, h):
    """Centre horizontally, sit on the bottom edge — same anchoring the CLI uses, so the heads line
    up and a taller canvas grows upwards instead of shoving her down."""
    left = (w - width) // 2
    body = [" " * left + line + " " * (w - width - left) for line in lines]
    return [" " * w] * (h - len(body)) + body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--per-row", type=int, default=0)
    ap.add_argument("--blocks", action="store_true", help="half-blocks 1:1 instead of sixel")
    a = ap.parse_args()

    term_cols = shutil.get_terminal_size().columns
    if a.blocks:
        raw = {e: blocks(FACES / f"{e}.png") for e in EMOTIONS}
        w = max(width for width, _ in raw.values())
        h = max(len(lines) for _, lines in raw.values())
        art = {e: place(lines, width, w, h) for e, (width, lines) in raw.items()}
    else:
        if not shutil.which("chafa"):
            sys.exit("necesito chafa: sudo pacman -S chafa")
        cell = cell_size()
        w, h = grid_cells(EMOTIONS, a.scale, cell)
        art = {e: sixel(FACES / f"{e}.png", a.scale, w, h, cell) for e in EMOTIONS}

    per_row = a.per_row or max(1, (term_cols - 2) // (w + 3))
    kind = "medios bloques 1:1" if a.blocks else f"sixel x{a.scale}"
    print(f"\n{CORAL}言{OFF} {kind} — cada una ocupa {w} columnas x {h} filas\n")

    for i in range(0, len(EMOTIONS), per_row):
        chunk = EMOTIONS[i:i + per_row]
        if a.blocks:
            for r in range(h):
                print("  ".join(art[e][r] for e in chunk))
        else:
            # Reserve the row first so nothing scrolls, then place each face from a SAVED origin.
            # Advancing relatively after each sixel does not work: the cursor comes back to column
            # zero, so every face lands on the first one and only the last of the row survives.
            sys.stdout.write("\n" * h + f"\x1b[{h}A\x1b7")
            for j, e in enumerate(chunk):
                sys.stdout.write("\x1b8")
                if j:
                    sys.stdout.write(f"\x1b[{j * (w + 3)}C")
                sys.stdout.write(art[e])
            sys.stdout.write(f"\x1b8\x1b[{h}B\r")
        print("  ".join(f"{DIM}{e:<{w}}{OFF}" for e in chunk))
        print()


if __name__ == "__main__":
    main()
