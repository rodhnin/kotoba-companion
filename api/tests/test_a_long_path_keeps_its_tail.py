"""A path is cut through the MIDDLE, so its tail and the fact standing after it always survive.

Nothing decided what survives INSIDE a pair, so the header's WORK row was
tail-clipped like any string, and its only number — the file count — went with it. Measured, the
loss is worst at 96 columns, where the portrait's gutter lands in a column just halved. The sweep
is over the header's own measure, not the terminal's, so no art file need exist for it to hold.

The opt-out is `approval_row`, which keeps the plain clip on purpose: a clipped path reads as
visibly unfinished, an elided one as a complete path that is not the one about to run — worth
paying for only where a person is consenting."""
from __future__ import annotations

import pytest
from conftest import posix_only
from rich.cells import cell_len
from rich.text import Text

from kotoba.cli.render.art import MIN_BLOCK_COLS
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.header import header_rows
from kotoba.cli.render.portrait import GUTTER, INDENT
from kotoba.cli.render.rows import approval_row, tool_text
from kotoba.cli.render.text import column
from kotoba.cli.state import Tool

#: What a boot-size face takes out of the terminal before the header is handed the rest.
FACE = MIN_BLOCK_COLS + INDENT + GUTTER
WORKDIRS = ("/home/jordan/.kotoba/files",
            "~/.kotoba/files",
            "~/projects/kotoba-project/vault/archive/2026/files")


def _caps(width: int, unicode: bool = True) -> Caps:
    return Caps(color="truecolor", background="dark", unicode=unicode, encodes_unicode=unicode,
                interactive=True, width=width, height=30)


def _stats(workdir: str) -> list[tuple[str, str, int]]:
    return [("MODEL", "gpt-5.4-mini · OpenAI", 2),
            ("WORK", f"{workdir} · 128 files", 3),
            ("VOICE", "local · expressive", 5),
            ("TOOLS", "29 ready · 7 skills", 4),
            ("MEM", "6 topics · 1.2k turns", 6),
            ("KEYS", "/help · @file · alt-enter", 1)]


def _work_row(width: int, workdir: str) -> str:
    caps = _caps(width + 10)
    rows = header_rows(caps, Text("言 kotoba"), width, _stats(workdir), "local sandbox",
                       portrait_rows=0, face=Text("(·ω·)"), live=True)
    return next((r.plain for r in rows if "WORK" in r.plain), "")


@pytest.mark.parametrize("workdir", WORKDIRS)
def test_the_work_row_keeps_its_count_at_every_width(workdir):
    """The count is the fact standing after the path and `files` is the path's last name. Losing
    either is the defect."""
    lost = [(width, row.strip()) for width in range(30, 161)
            for row in [_work_row(width, workdir)]
            if "128 files" not in row or "files" not in row.split("·")[0].replace("…", "")]
    assert not lost, f"{len(lost)} of the swept widths lost the tail or the count: {lost[:4]}"


@pytest.mark.parametrize("workdir", WORKDIRS)
def test_ninety_six_columns_with_a_face_is_the_measured_low_point(workdir):
    """The width he reported it at, and the narrowest that column ever gets: two columns and a
    portrait, which is where the count used to go."""
    row = _work_row(96 - FACE, workdir)
    assert "128 files" in row and "files" in row.split("·")[0].replace("…", "")


def test_a_path_gives_up_its_middle_and_never_its_tail():
    fitted = column("/home/jordan/.kotoba/files · 128 files", 24, True)
    assert cell_len(fitted) == 24
    assert fitted.strip().endswith("/files · 128 files")
    assert "…" in fitted


def test_the_ascii_mark_is_never_a_parent_directory_hop():
    """`..` is path syntax: `/home/../files` is a different directory, so the seven-bit mark is three
    dots and the elision can never be read as a hop."""
    fitted = column("/home/jordan/.kotoba/files · 128 files", 24, False)
    assert "/../" not in fitted
    assert "..." in fitted and fitted.strip().endswith("/files · 128 files")


def test_a_wide_name_is_measured_in_cells():
    """A CJK directory is two cells per character. Counting characters is how a row fitted to `width`
    goes out wider than the column that measured it."""
    fitted = column("/home/jordan/資料室/保管/notes.md · 3 files", 26, True)
    assert cell_len(fitted) == 26
    assert fitted.strip().endswith("/notes.md · 3 files")


def test_a_url_keeps_every_one_of_its_parts():
    """A URL is one token and half of one is a link nobody can follow."""
    from kotoba.cli.render import paths

    url = "https://example.com/a/b/c/d/e/f/g/h/report.html"
    assert paths.shorten(f"open {url}", 24, True) == ""


def test_a_whole_last_name_needs_no_floor_and_a_fragment_does():
    """`…/files` names one thing however little is left above it, so the floor does not touch it. A
    FRAGMENT of the last name is the ambiguous one, and under `FLOOR` cells the plain clip stands."""
    from kotoba.cli.render import paths

    assert paths.shorten("/home/jordan/.kotoba/files", 8, True) == "/…/files"
    assert paths.shorten("/home/jordan/.kotoba/reports.md", paths.FLOOR + 1, True).endswith("ports.md")
    assert paths.shorten("/home/jordan/.kotoba/reports.md", paths.FLOOR - 1, True) == ""


def test_a_tool_row_still_names_the_file_it_touched():
    tool = Tool("file", "read_file /home/jordan/.kotoba/files/reports/audit-2026-08-29.md")
    tool.state, tool.stopped = "done", tool.started
    row = tool_text(_caps(70), tool, 70).plain
    assert "audit-2026-08-29.md" in row


def test_the_approval_row_keeps_the_plain_clip():
    """The one surface that would rather be incomplete than mistakable."""
    card = type("Card", (), {"label": "rm -rf /home/jordan/.kotoba/files/archive/2026/old",
                             "family": "shell"})()
    row = approval_row(_caps(52), card, 52).plain
    assert "/…/" not in row and "…/" not in row


@posix_only("$HOME as the home directory")
def test_the_home_prefix_is_only_taken_on_a_boundary(monkeypatch):
    from kotoba.cli.render import paths

    monkeypatch.setenv("HOME", "/home/jordan")
    assert paths.shown("/home/jordan/.kotoba") == "~/.kotoba"
    assert paths.shown("/home/jordanX/.kotoba") == "/home/jordanX/.kotoba"
