#!/usr/bin/env python3
"""Import the generated expression sprites into every face tree.

Snaps each render to its native pixel grid, checks how far its head drifted from the master — they
must be interchangeable or the portrait jumps when the emotion changes — and writes it under the
canonical emotion name.

TWO trees are load-bearing: the checkout's and the copy the wheel ships. A batch written to only one
looks right locally and ships a stale face, so every sprite goes to both from the same bytes and the
run exits non-zero if they disagree. `--grid` forces one block size for a batch containing SCALED
renders, whose period is no longer 20 px.
"""

import argparse
import difflib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets/cli/_source"
TREES = (ROOT / "assets/cli/faces", ROOT / "api/src/kotoba/data/cli/faces")
SNAP = Path(__file__).resolve().parent / "snap-pixelart.py"

# The emotion vocabulary belongs to the prompt and the renderer together — these names are not ours to pick.
EMOTIONS = ["neutral", "happy", "excited", "sad", "crying", "angry", "surprised",
            "embarrassed", "thinking", "sleepy", "affectionate", "confused", "scared", "determined"]


def canonical(stem):
    raw = stem.split("-", 1)[-1].lower().strip()
    if raw in EMOTIONS:
        return raw, False
    near = difflib.get_close_matches(raw, EMOTIONS, n=1, cutoff=0.72)
    return (near[0], True) if near else (None, False)


def head(im):
    """Biggest connected blob. Measuring the whole silhouette instead would let a floating "?" or
    a "z z" drag the alignment and report a perfectly good sprite as broken."""
    px = im.load()
    pts = {(x, y) for y in range(im.height) for x in range(im.width) if px[x, y][3] >= 110}
    seen, best = set(), set()
    for p in pts:
        if p in seen:
            continue
        stack, blob = [p], set()
        while stack:
            q = stack.pop()
            if q in seen or q not in pts:
                continue
            seen.add(q)
            blob.add(q)
            x, y = q
            stack += [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
        if len(blob) > len(best):
            best = blob
    return best


def overlap(a, b0):
    """Best head overlap over small shifts — 1.0 means the two heads are interchangeable. It says
    nothing about SIZE on its own: a head 11% bigger still scores 0.90, which reads as fine, so the
    table prints the area next to it."""
    best = (0.0, 0, 0)
    for dy in range(-6, 7):
        for dx in range(-6, 7):
            b = {(x + dx, y + dy) for x, y in b0}
            iou = len(a & b) / max(len(a | b), 1)
            if iou > best[0]:
                best = (iou, dx, dy)
    return best


def install(built, name):
    """One sprite into every tree, from one buffer — byte-identical by construction, not by luck."""
    data = built.read_bytes()
    for tree in TREES:
        tree.mkdir(parents=True, exist_ok=True)
        (tree / name).write_bytes(data)


def disagreements():
    """Every way the trees can fail to be the same set of identical files."""
    names = set()
    for tree in TREES:
        names |= {p.name for p in tree.glob("*.png")}
    bad = []
    for name in sorted(names):
        blobs = {t: (t / name).read_bytes() if (t / name).is_file() else None for t in TREES}
        missing = [t for t, b in blobs.items() if b is None]
        if missing:
            bad.append(f"{name}: missing from {', '.join(str(t.relative_to(ROOT)) for t in missing)}")
        elif len(set(blobs.values())) > 1:
            bad.append(f"{name}: the two trees hold different bytes")
    return bad


def check_trees():
    """Print the verdict and say whether the trees agree. Loud on purpose: a silent import that
    updates one tree is indistinguishable from a working one until the CLI draws the old face."""
    bad = disagreements()
    names = " ↔ ".join(str(t.relative_to(ROOT)) for t in TREES)
    if bad:
        print(f"\n× the two face trees do NOT agree ({names}):")
        for line in bad:
            print(f"    {line}")
        print("  fix with: scripts/import-faces.py <dir> --apply")
        return False
    total = len(list(TREES[0].glob("*.png")))
    print(f"\n✓ {total} identical files in both trees ({names})")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src_dir", nargs="?")
    ap.add_argument("--pattern", default="kotobapixel-*.png")
    ap.add_argument("--apply", action="store_true", help="write files (otherwise just report)")
    ap.add_argument("--check", action="store_true", help="only compare the trees, import nothing")
    ap.add_argument("--grid", type=int, default=0,
                    help="force this block size for every file (0 = auto-detect per file)")
    a = ap.parse_args()

    if a.check:
        sys.exit(0 if check_trees() else 1)
    if not a.src_dir:
        sys.exit("no source directory given (or use --check)")

    files = sorted(Path(a.src_dir).expanduser().glob(a.pattern))
    if not files:
        sys.exit(f"nothing matched {a.pattern} in {a.src_dir}")

    SOURCE.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="kotoba-faces-"))
    try:
        rows = run(files, tmp, a.apply, a.grid)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    absent = [e for e in EMOTIONS if not any(r[1] == e for r in rows)]
    if absent:
        print(f"\nmissing {len(absent)}: {', '.join(absent)}")
    if not a.apply:
        print("\n(dry run — nothing written; add --apply)")
    sys.exit(0 if check_trees() else 1)


def run(files, tmp, apply, grid=0):
    master, rows = None, []
    for f in files:
        emotion, fuzzy = canonical(f.stem)
        if not emotion:
            rows.append((f.name, "?", "—", None, "UNRECOGNISED NAME"))
            continue
        snapped = tmp / f"{emotion}.png"
        outlined = tmp / f"{emotion}-outlined.png"
        forced = ["--grid", str(grid)] if grid else []
        subprocess.run([sys.executable, str(SNAP), str(f), str(snapped), *forced],
                       check=True, capture_output=True)
        subprocess.run([sys.executable, str(SNAP), str(f), str(outlined), "--outline", *forced],
                       check=True, capture_output=True)
        im = Image.open(snapped).convert("RGBA")
        blob = head(im)
        if emotion == "neutral":
            master = blob
        rows.append((f.name, emotion, f"{im.width}x{im.height}", blob,
                     "typo corrected" if fuzzy else ""))

        if apply:
            shutil.copy(f, SOURCE / f"{emotion}.png")
            install(snapped, f"{emotion}.png")
            install(outlined, f"{emotion}-outlined.png")

    print(f"{'file':34} {'emotion':14} {'sprite':9} {'head':16} {'drift vs neutral':20} note")
    for name, emotion, size, blob, note in rows:
        area, d = "", ""
        if blob is not None:
            area = f"{len(blob)}px"
            if master is not None and emotion != "neutral":
                area = f"{len(blob)}px ({len(blob) / len(master) - 1:+.1%})"
                iou, dx, dy = overlap(master, blob)
                flag = "ok" if iou >= 0.90 else ("check" if iou >= 0.80 else "BAD")
                d = f"IoU {iou:.2f} ({dx:+d},{dy:+d}) {flag}"
        print(f"{name:34} {emotion:14} {size:9} {area:16} {d:20} {note}")
    return rows


if __name__ == "__main__":
    main()
