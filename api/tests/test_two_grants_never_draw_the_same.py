"""No two rows of `/approvals` draw the same thing, at any width.

`/approvals rm N` revokes BY NUMBER. Three deploy scripts under different directories elided
to the same `bash …/deploy.sh` line at 36 columns, so the row a person recognised and the row
they meant to revoke were the same string with a different ordinal in front — they keep the
one they knew and leave the other two live.

The identity under test is the row MINUS its ordinal: `1.` and `2.` are what makes the
strings differ, and exactly what a person cannot use to tell the grants apart.
"""
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest

from kotoba.cli import slash
from kotoba.cli.app import App
from kotoba.cli.input import commands
from kotoba.cli.render import rows as rows_mod
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console
from kotoba.db.database import Database

#: Three grants whose only difference is a directory in the MIDDLE — the part an elision spends first.
TWINS = ("bash /home/jordan/work/staging/deploy.sh",
         "bash /home/jordan/work/production/deploy.sh",
         "bash /home/jordan/work/customer-a/deploy.sh")
WIDTHS = tuple(range(30, 121))


def _wired(height: int, width: int) -> App:
    caps = Caps(color="none", background="dark", unicode=True, width=width, height=height,
                g=dict(GLYPHS_UNICODE))
    console = build_console(caps, file=io.StringIO())
    console.width = caps.width
    screen = Screen(caps, console=console, portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1",
                                  engine=SimpleNamespace(db=None, mcp=None),
                                  events=SimpleNamespace(steps={}, settle=lambda: None))
    return app


def _listed(tmp_path, width: int, height: int, grants) -> list[str]:
    app = _wired(height, width)

    async def go() -> None:
        db = Database("sqlite:///" + str(tmp_path / f"grants{width}x{height}.db"))
        await db.connect()
        for pattern, scope in grants:
            await db.save_approved_command(pattern, scope)
        app.session.engine.db = db
        try:
            await slash.run(app, commands.Command("/approvals", ""))
        finally:
            await db.close()

    asyncio.run(go())
    return app.screen.console.file.getvalue().split("\n")[:-1]


def _grants(rows: list[str], count: int) -> dict[int, str]:
    """What each numbered grant draws, ordinal removed, continuation and sentence rows folded in.

    Read off the printed bytes rather than off the builders: a listing that is unambiguous in the row
    objects and ambiguous on the screen is the defect, not a passing test.

    The blank row ENDS the block, and that is not tidiness: without it the closing `these run without a
    card…` sentence was folded onto the last grant, which made the last grant unique whatever it drew —
    two identical rows passed this file at three widths before the stop was put in."""
    out, at = {}, 0
    for line in rows:
        if at and not line.strip():
            break
        head = line.strip().split(" ", 1)[0]
        if head[:-1].isdigit() and head.endswith(".") and 1 <= int(head[:-1]) <= count:
            at = int(head[:-1])
            out[at] = line.strip()[len(head):].strip()
        elif at and line.strip():
            out[at] += " " + line.strip()
    return out


@pytest.mark.parametrize("width", WIDTHS)
def test_three_grants_that_differ_only_in_the_middle_draw_three_different_rows(tmp_path, width):
    drawn = _grants(_listed(tmp_path, width, 40, [(p, "exact") for p in TWINS]), len(TWINS))
    assert len(drawn) == len(TWINS), f"only {sorted(drawn)} of the three grants reached the screen"
    assert len(set(drawn.values())) == len(TWINS), (
        f"at {width} columns two grants draw the same row: {drawn}")


@pytest.mark.parametrize("width", (30, 36, 44, 62, 80))
def test_a_grant_drawn_whole_says_which_directory_it_runs_in(tmp_path, width):
    """Distinct is not enough on its own — `bash …a` and `bash …b` differ and name nothing. The
    directory is the only thing that tells these three apart, so it has to be on the screen."""
    drawn = _grants(_listed(tmp_path, width, 40, [(p, "exact") for p in TWINS]), len(TWINS))
    for want in ("staging", "production", "customer-a"):
        assert any(want in text for text in drawn.values()), (
            f"no grant at {width} columns says {want!r}: {drawn}")


@pytest.mark.parametrize("width", (30, 44, 80))
def test_a_family_grant_that_carries_a_path_is_told_apart_too(tmp_path, width):
    """`command_family()` is the first TOKEN, so a script's own path is saved as a family grant. Two of
    them elide together exactly the way two exact ones do."""
    grants = [("/home/jordan/work/staging/deploy.sh", "command"),
              ("/home/jordan/work/production/deploy.sh", "command")]
    drawn = _grants(_listed(tmp_path, width, 40, grants), len(grants))
    assert len(set(drawn.values())) == len(grants), (
        f"at {width} columns two family grants draw the same row: {drawn}")


def test_a_grant_drawn_whole_gives_back_every_character_it_was_given():
    """The whole point of `whole` is that nothing is dropped, so the rows it returns must rejoin to the
    line that went in. The CJK case is also where the loop that cuts an over-wide segment by cells could
    take NOTHING: a two-cell character against one cell of room left the remainder unchanged and the
    while never ended (verified by running the unguarded version under a three-second alarm)."""
    for pattern in ("bash /home/jordan/work/staging/deploy.sh", "sh /家/工作/演出台/deploy.sh"):
        for room in range(1, 40):
            out = rows_mod._lines(pattern, room)
            assert all(part for part in out), f"{pattern!r} at {room} came back with an empty row"
            assert "".join(out).replace(" ", "") == pattern.replace(" ", ""), (
                f"{pattern!r} at {room} came back as {out}")


def test_grants_that_already_differ_keep_their_one_row(tmp_path):
    """The listing spends rows on ambiguity and never on tidiness: a set with nothing to confuse is
    drawn exactly as densely as before, or every grant past the window pays for a repair it did not
    need."""
    rows = _listed(tmp_path, 80, 40, [(f"cmd{i:02d}", "command") for i in range(6)])
    numbered = [r for r in rows if r.strip()[:2] in {f"{n}." for n in range(1, 7)}]
    assert len(numbered) == 6, f"six unambiguous grants took {len(numbered)} numbered rows: {rows}"
