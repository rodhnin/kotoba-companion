"""The report cover's chibi has to be centred, and the frame has to crop nothing.

The drawing was off-centre INSIDE ITS OWN FILE — 31 transparent columns on the left, 1 on the right —
and `object-fit: cover` centres the canvas, not the art, so on every report her face sat left of centre
with the right side of her hair cut off. Nothing in CSS can see that: the fix belongs to the asset, and
so does the pin. Both halves are asserted, because either one alone lets the other drift back.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL.Image", reason="pillow ships with the cli extra")

ROOT = Path(__file__).resolve().parents[2]
ASSETS = [ROOT / "soul" / "report-chibi.webp",
          ROOT / "api" / "src" / "kotoba" / "data" / "soul" / "report-chibi.webp"]
TEMPLATE = ROOT / "api" / "src" / "kotoba" / "data" / "soul" / "report_template.html"


def _frame_aspect() -> float:
    """The box the art is actually drawn into, read from the stylesheet rather than restated here.

    The sheet sets `box-sizing: border-box`, so the declared size is the frame PLUS its border and the
    art gets what is left. Comparing against the declared size read a frame 6px wider than the one
    that exists, and called a real crop no crop."""
    css = TEMPLATE.read_text(encoding="utf-8")
    assert "box-sizing:border-box" in css.replace(" ", ""), "the border no longer eats into the box"
    rule = re.search(r"\.chibi\{([^}]*)\}", css, re.S)
    assert rule, "the template no longer has a .chibi rule"
    w = re.search(r"width:(\d+)px", rule.group(1))
    h = re.search(r"height:(\d+)px", rule.group(1))
    border = re.search(r"border:(\d+)px", rule.group(1))
    assert w and h and border, "the .chibi box is no longer a fixed pixel size"
    assert "object-fit:cover" in rule.group(1), "this pin is about what cover crops"
    edge = 2 * int(border.group(1))
    return (int(w.group(1)) - edge) / (int(h.group(1)) - edge)


@pytest.mark.parametrize("path", ASSETS, ids=lambda p: p.parent.name)
def test_the_drawing_is_centred_in_its_own_canvas(path):
    im = PIL.open(path).convert("RGBA")
    box = im.split()[-1].getbbox()
    assert box, "the asset is fully transparent"
    left, top, right, bottom = box
    assert abs(left - (im.width - right)) <= 1, f"{left}px of margin on the left, {im.width - right} on the right"
    assert abs(top - (im.height - bottom)) <= 1, f"{top}px of margin on top, {im.height - bottom} below"


@pytest.mark.parametrize("path", ASSETS, ids=lambda p: p.parent.name)
def test_the_frame_crops_nothing(path):
    """Same aspect as the box means `cover` scales without cutting: her hair stays inside the border."""
    im = PIL.open(path)
    assert abs(im.width / im.height - _frame_aspect()) < 0.01


def test_both_copies_are_the_same_file():
    """The clone reads soul/, the wheel reads the packaged copy. One of them going stale is invisible."""
    assert len({p.read_bytes() for p in ASSETS}) == 1
