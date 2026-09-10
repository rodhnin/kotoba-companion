"""The sprites are geometry the renderer trusts, and nothing measured them until now.

Existing tests compare file bytes to file bytes, never asking whether a sprite is the SHAPE the
renderer reserved room for, so a re-imported batch that quietly changed size reached the screen with
nothing between it and the terminal. The boot header used to budget its rows from the literal
`neutral`, today the SMALLEST of a deliberately non-uniform set — composing a taller face against
that budget dropped her last row, chin and all, with no error anywhere.

What is pinned is the RELATIONSHIP, never the pixel count: every bound is a distance from the
reference sprite or a renderer constant, so redrawing the set moves the bounds with it."""
from __future__ import annotations

import math
import re

import pytest
from PIL import Image
from rich.cells import cell_len

from kotoba.cli.render import art
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import GUTTER, Portrait

SGR = re.compile(r"\x1b\[[0-9;]*m")
WALK_BACK = re.compile(r"\x1b7\x1b\[(\d+)A")

# The pixel size of a terminal cell is asked of the terminal (caps._cell), so no test may assume one.
# Six is the narrowest a legible font reaches; twenty is a large one on a HiDPI screen.
CELL_WIDTHS = range(6, 21)
CELLS = [(10, 21), (10, 20), (9, 18), (8, 16), (7, 15), (6, 13), (12, 24)]
REFERENCE = "neutral"


def size(emotion: str, outlined: bool = False) -> tuple[int, int]:
    with Image.open(art.path(emotion, outlined)) as im:
        return im.size


def box(cell: tuple[int, int], mode: str, monkeypatch) -> Portrait:
    """A portrait pinned to one tier, at a cell size this machine's terminal has no say in."""
    monkeypatch.setenv("KOTOBA_FORCE_PORTRAIT", mode)
    caps = Caps(color="truecolor", background="dark", unicode=True, interactive=True,
                sixel=(mode == "sixel"), width=120, height=40, cell=cell)
    return Portrait(caps)


def fake_chafa(monkeypatch) -> None:
    """chafa is a system package a fresh clone may not have, and this file is about arithmetic, not
    about sixel bytes. The stub answers with the sizes `art.cells` derives, which is what `_padded`
    hands the real one, so the reservation being measured is the real reservation."""
    def sixel(emotion, scale, cell, outlined=False):
        cols, rows = art.cells(emotion, scale, cell, outlined)
        return ("\x1bPq#0;2;0;0;0" + "?" * cols + "\x1b\\", cols, rows)

    monkeypatch.setattr(art, "sixel", sixel)


def test_a_tall_face_keeps_its_chin_when_the_boot_header_composes_it(monkeypatch):
    """The half-block tier, at the cell size `caps` defaults to. `screen.header` spreads its text down
    `portrait.rows` and hands the block over, so the realistic call has exactly that many rows of text
    beside her — and a face taller than the reference then has nowhere to put its last row."""
    p = box((10, 21), "blocks", monkeypatch)
    assert p.mode == "blocks"
    right = [f"row {i}" for i in range(p.rows)]

    lost = {}
    for emotion in art.EMOTIONS:
        drawn = p._blocks(emotion)
        composed = p.header(right, emotion)
        if drawn[-1] not in composed:
            lost[emotion] = len(drawn) - sum(1 for row in drawn if row in composed)
        assert len(p.last_rows) >= len(drawn), (
            f"{emotion} draws {len(drawn)} block rows and the header kept {len(p.last_rows)}")
    assert not lost, (
        "the header composed these faces against a budget measured from another sprite and their "
        f"bottom rows — her chin — went out with no error: {lost}")


def test_the_boot_sixel_reserves_the_rows_the_face_it_draws_actually_needs(monkeypatch):
    """The same trap one tier up, where it fails the other way round: the payload is not clipped, it
    is drawn past the rows the walk-back reserved, over whatever the caller prints next.

    The cell is 10x20 on purpose. At the 10x21 `caps` defaults to, every face rounds to seven rows and
    the bug is invisible; one pixel of cell height apart, `thinking` needs eight."""
    fake_chafa(monkeypatch)
    p = box((10, 20), "sixel", monkeypatch)
    assert p.mode == "sixel"
    right = [f"row {i}" for i in range(p.rows)]

    short = {}
    for emotion in art.EMOTIONS:
        needs = art.cells(emotion, art.BOOT_SCALE, p.caps.cell, p.outlined)[1]
        composed = p.header(right, emotion)
        walk = WALK_BACK.search(composed)
        assert walk, f"{emotion}: the header placed no sixel at all"
        if int(walk.group(1)) < needs:
            short[emotion] = (needs, int(walk.group(1)))
    assert not short, (
        "the header walked back fewer rows than the face it then drew, so the bottom of the image "
        f"lands under the block, on the caller's next line — needs vs reserved: {short}")


def test_the_canvas_is_her_bounding_box_so_placing_the_canvas_places_her(monkeypatch):
    """`art._padded` centres the SPRITE in its cells, and every surface then places that block. Both
    halves are only true of her ink while the sprite is cropped to it: three transparent rows under
    her chin and what is centred is a rectangle she is sitting at the top of, on every tier at once —
    and the set would still pass every byte-comparison in the suite, because nothing else looks."""
    for emotion in art.EMOTIONS:
        for outlined in (False, True):
            with Image.open(art.path(emotion, outlined)) as im:
                w, h = im.size
                assert im.convert("RGBA").getbbox() == (0, 0, w, h), (
                    f"{emotion}{'-outlined' if outlined else ''} has transparent margin inside its "
                    f"canvas ({w}x{h}) — centring the canvas no longer centres her")

    for cell in CELLS:
        for emotion in art.EMOTIONS:
            out, cols, rows = art._padded(emotion, False, art.BOOT_SCALE, cell)
            assert out.size == (cols * cell[0], rows * cell[1])
            # Centred, so the slack is split, and the split is what makes her read as placed rather
            # than as pushed into a corner. Off by at most the odd pixel a division by two leaves.
            box = out.getbbox()
            assert abs((out.width - box[2]) - box[0]) <= 1, (
                f"{emotion} is not centred across its cells: {box[0]}px left, "
                f"{out.width - box[2]}px right")
            assert abs((out.height - box[3]) - box[1]) <= 1, (
                f"{emotion} is not centred down its cells: {box[1]}px above, "
                f"{out.height - box[3]}px below")


def test_every_face_is_placed_by_the_same_rule_whatever_it_is(monkeypatch):
    """One rule for fourteen faces, so none of them is a special case somebody tuned by hand.

    Her block is drawn at a fixed column and her slack is split, so what must hold for every face is
    that the slack IS split — not that it is the same number, which it cannot be: the remainder a face
    leaves inside its last cell is a property of that face. Measured at a 10-px cell and BOOT_SCALE:
    1 px for crying and confused, none for excited and surprised, 3 for the other ten.

    That spread is the cost of centring and it is bounded here, because the version of this that goes
    wrong is one face drifting far enough to read as misaligned under a column of text that never
    moves. Asserted on both scales and every cell size — the remainder is a function of all three."""
    for cell in CELLS:
        for scale in (art.BOOT_SCALE, art.INLINE_SCALE):
            starts = {}
            for e in art.EMOTIONS:
                out, _, _ = art._padded(e, False, scale, cell)
                box = out.getbbox()
                starts[e] = box[0]
                assert abs((out.width - box[2]) - box[0]) <= 1, (
                    f"{e} at cell {cell} scale {scale} is not centred across its cells")
            assert max(starts.values()) - min(starts.values()) <= cell[0] // 2, (
                f"at cell {cell} scale {scale} the faces start more than half a cell apart, which "
                f"reads as one of them sliding: {starts}")


def test_the_boot_upscale_is_an_integer_block_grid_and_nothing_resamples(monkeypatch):
    """One source pixel becomes an exact BOOT_SCALE x BOOT_SCALE block and no filter ever touches it —
    the whole reason the sixel tier renders the sprite instead of the 1536x1024 render it came from.
    Compared against a NEAREST resize of the source rather than against a remembered size."""
    cell = (10, 21)
    for emotion in art.EMOTIONS:
        with Image.open(art.path(emotion)) as im:
            src = im.convert("RGBA")
        want = src.resize((src.width * art.BOOT_SCALE, src.height * art.BOOT_SCALE), Image.NEAREST)
        out, cols, rows = art._padded(emotion, False, art.BOOT_SCALE, cell)
        x0 = (cols * cell[0] - want.width) // 2
        y0 = (rows * cell[1] - want.height) // 2
        assert out.crop((x0, y0, x0 + want.width, y0 + want.height)).tobytes() == want.tobytes(), (
            f"{emotion} came out of the boot upscale resampled")


def test_the_widest_face_still_leaves_the_boot_gutter_a_column():
    """The column axis is the one budget that must NOT follow the emotion, and this is why it is safe.

    `portrait.gutter_cols` is read by the caller BEFORE composing, to work out how wide the text
    beside her may be; re-deriving the width per emotion would push that text past the terminal edge.
    So the boot header reserves the reference sprite's width and a wider face spends `GUTTER` — three
    blank columns — instead. That is a real budget with a real floor: the widest of the fourteen
    (`excited` and `surprised`, 60 px against the reference 58) take one of the three at the
    narrowest cell a legible terminal reports. A sprite three columns wider would be touching her
    own text — the 62-px `angry` of before this set took two."""
    worst = (0, None)
    for cw in CELL_WIDTHS:
        for outlined in (False, True):
            ref = math.ceil(size(REFERENCE, outlined)[0] * art.BOOT_SCALE / cw)
            for emotion in art.EMOTIONS:
                over = math.ceil(size(emotion, outlined)[0] * art.BOOT_SCALE / cw) - ref
                if over > worst[0]:
                    worst = (over, (emotion, cw, outlined))
    assert worst[0] <= GUTTER - 1, (
        f"{worst[1]} overhangs the reserved width by {worst[0]} columns and the gutter is {GUTTER}, "
        "so at that cell size her face reaches the text beside her")


def test_the_block_floor_is_a_floor_and_not_the_thing_holding_her_up():
    """`MIN_BLOCK_COLS` is a measured legibility floor on the WIDTH; the height that comes out of it is
    the sprite's aspect ratio and nothing else (`art.block_rows` derives `sub` from it). A batch whose
    shape drifted far enough would come back as a stripe, and `block_rows` has a `max(2, ...)` that
    would hand back a one-row face rather than raise. Nine to eleven rows is where the set sits."""
    for cell in CELLS:
        for emotion in art.EMOTIONS:
            rows = art.block_rows(emotion, art.MIN_BLOCK_COLS, cell)
            assert len(rows) >= 6, (
                f"{emotion} collapses to {len(rows)} rows at {art.MIN_BLOCK_COLS} columns on a "
                f"{cell[0]}x{cell[1]} cell — the aspect ratio has drifted, not the width")
            assert {cell_len(SGR.sub("", row)) for row in rows} == {art.MIN_BLOCK_COLS}, (
                f"{emotion} does not fill the {art.MIN_BLOCK_COLS} columns the tier reserved")


def test_every_face_has_an_outlined_twin_exactly_one_pixel_of_ink_larger():
    """A light terminal draws `<emotion>-outlined.png` and the tier reserves the same room for both, so
    the ring has to be one pixel and no more. A twin generated any other way than
    `snap-pixelart.py --outline` shows up here as a canvas that is not +2 by +2."""
    for emotion in art.EMOTIONS:
        plain, outlined = size(emotion), size(emotion, outlined=True)
        assert (outlined[0] - plain[0], outlined[1] - plain[1]) == (2, 2), (
            f"{emotion}: {plain[0]}x{plain[1]} rings to {outlined[0]}x{outlined[1]}, which is not one "
            "pixel of ink each way")


def test_no_face_leaves_the_shape_band_the_block_tier_was_measured_at():
    """Both tiers size her from one number — `MIN_BLOCK_COLS` columns, or the reference sprite's
    width — and let the aspect ratio decide the rest, so the fourteen have to stay one shape. The set
    spans -6.3% (`thinking`, raised hand) to +3.4% (`surprised`, widest) around the reference; twelve
    points either way passes that with room and still catches a batch re-rendered at another ratio."""
    ref = size(REFERENCE)[0] / size(REFERENCE)[1]
    for emotion in art.EMOTIONS:
        w, h = size(emotion)
        drift = (w / h) / ref - 1
        assert abs(drift) <= 0.12, (
            f"{emotion} is {w}x{h}, {drift:+.1%} off the reference shape — one floor cannot size a "
            "set this far apart")


@pytest.mark.parametrize("emotion", ["done", "ecstatic", ""])
def test_a_face_that_does_not_exist_is_still_measurable(emotion):
    """A hallucinated mood must measure as the REFERENCE face and no other, because the reference is
    what the boot tier reserved its width from: falling back to any other sprite would hand the
    renderer a footprint it never reserved, and `art.path` returning something that is not on disk
    would raise into the render, where the tier ladder has no rung below `none`."""
    assert art.cells(emotion, art.BOOT_SCALE, (10, 21)) == art.cells(REFERENCE, art.BOOT_SCALE, (10, 21))
    assert len(art.block_rows(emotion, art.MIN_BLOCK_COLS, (10, 21))) == \
        len(art.block_rows(REFERENCE, art.MIN_BLOCK_COLS, (10, 21)))
