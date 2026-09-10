#!/usr/bin/env python3
"""Turn an AI-generated "pixel art" PNG into a real sprite at its native resolution.

Image models draw pixel art at high resolution with soft edges: the blocks are there, but each one
is a fuzzy ~14x14 patch instead of one pixel. Downscaling that with any normal filter destroys it —
so find the block grid, take one colour per block, and write the sprite at its true size.

    snap-pixelart.py in.png out.png [--grid N] [--colors N]
"""

import argparse
import colorsys
from PIL import Image


def block_colour(px, bx, by, k, w, h):
    """Median colour of one block, sampled away from the soft edges."""
    m = max(1, k // 4)
    step = max(1, (k - 2 * m) // 3)
    cells = [px[bx * k + dx, by * k + dy]
             for dy in range(m, k - m + 1, step)
             for dx in range(m, k - m + 1, step)
             if bx * k + dx < w and by * k + dy < h]
    if not cells:
        return (0, 0, 0, 0)
    return tuple(sorted(c[i] for c in cells)[len(cells) // 2] for i in range(4))


def _edge_profile(im, axis):
    """Per-column (or per-row) total colour change — block boundaries show up as spikes."""
    px = im.load()
    w, h = im.size
    n, cross = (w, range(0, h, 2)) if axis == 0 else (h, range(0, w, 2))
    prof = [0.0] * n
    for i in range(1, n):
        s = 0.0
        for j in cross:
            a = px[i - 1, j] if axis == 0 else px[j, i - 1]
            b = px[i, j] if axis == 0 else px[j, i]
            if a[3] < 128 and b[3] < 128:
                continue
            s += abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) + abs(a[3] - b[3])
        prof[i] = s
    return prof


def detect_grid(im, lo=6, hi=40):
    """Block size = the period of the edge spikes. Intra-block variance has no knee to find;
    autocorrelation does, and its harmonics (2k, 3k) confirm the fundamental."""
    periods = []
    for axis in (0, 1):
        prof = _edge_profile(im, axis)
        mean = sum(prof) / len(prof)
        p = [v - mean for v in prof]
        den = sum(v * v for v in p) or 1.0
        ac = {lag: sum(p[i] * p[i + lag] for i in range(len(p) - lag)) / den
              for lag in range(lo, hi + 1)}
        best = max(ac.values())
        periods.append(min(l for l in sorted(ac) if ac[l] >= best * 0.85))
    return min(periods)


def is_art(c):
    r, g, b, a = c
    if a <= 128:
        return False
    _, light, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return light > 0.40 and (sat > 0.20 or light > 0.76)


def flood_background(grid, gw, gh, use_alpha):
    """Mark background from the border inwards, so a dark block INSIDE her survives."""
    bg = [[False] * gw for _ in range(gh)]
    stack = ([(x, 0) for x in range(gw)] + [(x, gh - 1) for x in range(gw)]
             + [(0, y) for y in range(gh)] + [(gw - 1, y) for y in range(gh)])
    seen = set()
    while stack:
        x, y = stack.pop()
        if not (0 <= x < gw and 0 <= y < gh) or (x, y) in seen:
            continue
        seen.add((x, y))
        keep = grid[y][x][3] > 128 if use_alpha else is_art(grid[y][x])
        if keep:
            continue
        bg[y][x] = True
        stack += [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
    return bg


def add_outline(im, hex_colour):
    """Ring the silhouette in one pixel of ink. Art without an outline dissolves into a LIGHT
    terminal background — her pale skin and the cream theme are nearly the same colour."""
    c = hex_colour.lstrip("#")
    ink = tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    out = Image.new("RGBA", (im.width + 2, im.height + 2), (0, 0, 0, 0))
    out.paste(im, (1, 1))
    px = out.load()
    ring = [(x, y) for y in range(out.height) for x in range(out.width)
            if px[x, y][3] < 110
            and any(0 <= x + dx < out.width and 0 <= y + dy < out.height
                    and px[x + dx, y + dy][3] >= 110
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
    for x, y in ring:
        px[x, y] = ink
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--grid", type=int, default=0, help="block size in px (0 = auto-detect)")
    ap.add_argument("--colors", type=int, default=0, help="quantise to N colours (0 = keep)")
    ap.add_argument("--outline", nargs="?", const="#52351e", default=None,
                    help="ring the silhouette in this colour (default her ink #52351e)")
    a = ap.parse_args()

    im = Image.open(a.src).convert("RGBA")
    w, h = im.size
    k = a.grid or detect_grid(im)
    px = im.load()
    gw, gh = w // k, h // k
    grid = [[block_colour(px, x, y, k, w, h) for x in range(gw)] for y in range(gh)]

    use_alpha = min(p[3] for row in grid for p in row) < 128
    bg = flood_background(grid, gw, gh, use_alpha)

    out = Image.new("RGBA", (gw, gh), (0, 0, 0, 0))
    o = out.load()
    for y in range(gh):
        for x in range(gw):
            if not bg[y][x]:
                o[x, y] = grid[y][x][:3] + (255,)
    out = out.crop(out.getbbox())

    if a.colors:
        alpha = out.getchannel("A")
        out = out.convert("RGB").quantize(colors=a.colors, method=Image.MEDIANCUT).convert("RGBA")
        out.putalpha(alpha)

    if a.outline:
        out = add_outline(out, a.outline)

    out.save(a.dst)
    print(f"{a.src}: rejilla {k}px ({gw}x{gh} bloques) -> sprite {out.size[0]}x{out.size[1]}"
          f" | alfa={'si' if use_alpha else 'no'} | colores={len(out.getcolors(1 << 24))}")


if __name__ == "__main__":
    main()
