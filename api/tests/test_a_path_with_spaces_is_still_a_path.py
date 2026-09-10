"""A space does not end a path, and the seven-bit mark is never a parent-directory hop.

`render/paths` split the line on whitespace, so a directory whose name has spaces was never seen as
a path — some widths fell back to the tail clip, others elided the wrong half into a path that isn't
one. Joining across every space is not the fix either: two real paths side by side would merge into
one path naming nothing.

The sweep is a PAIR, measured in `cell_len`: a spaced tree must reach the same last name as its
unspaced twin at every width, and a two-path line must keep both paths and the words between them.
A separate trim mark `..` is path SYNTAX too, so `~/work/kotoba/..` reads as the wrong directory."""
from __future__ import annotations

import pytest
from rich.cells import cell_len

from kotoba.cli.render import paths
from kotoba.cli.render.text import column

#: Every width the header, a tool row or a listing is ever handed.
WIDTHS = range(30, 161)

REAL = "/home/jordan/Projects - Security Audits/Sample/sample/docs/release-notes-v12.md"
FLAT = "/home/jordan/Projects-SecurityAudits/Sample/sample/docs/release-notes-v12.md"
TWO = "/home/jordan/My Big Documents/Sample/sample/docs/release-notes-v12.md"
CJK = "/home/jordan/資料室 - 保管/古い記録/notes.md"
URL = "https://example.com/a/b/c/d/e/f/g/h/report.html"
BARE = "/release-notes-v12.md"

#: line, the last name that must survive, the words beside it that must survive whole.
SHAPES = [
    ("operator tree", f"read_file {REAL}", "release-notes-v12.md", ["read_file"]),
    ("no spaces", f"read_file {FLAT}", "release-notes-v12.md", ["read_file"]),
    ("two spaces", f"read_file {TWO}", "release-notes-v12.md", ["read_file"]),
    ("cjk directory", f"read_file {CJK}", "notes.md", ["read_file"]),
    ("no directories", f"read_file {BARE}", BARE, ["read_file"]),
    ("two paths", "read_file /a/b/first-file.md and then /c/d/second-file.md",
     "second-file.md", ["read_file", "and then"]),
    ("work row", f"{REAL} · 128 files", "128 files", ["· 128 files"]),
    ("shell arguments", "$ cp /home/jordan/vault/archive/notes.md backup/copies/notes.md",
     "backup/copies/notes.md", ["$ cp", "backup/copies/notes.md"]),
    ("url beside a path", f"see /a/b/c/notes.md and {URL}", "report.html", ["and", URL]),
]


def _sweep(line: str) -> dict[int, str]:
    return {w: paths.shorten(line, w, True) for w in WIDTHS}


@pytest.mark.parametrize("name,line,tail,keep", SHAPES, ids=[s[0] for s in SHAPES])
def test_nothing_beside_the_path_is_ever_swallowed(name, line, tail, keep):
    """The words the elision did not touch are the reader's only proof of what the row says. A rule
    that joins across a bare space eats them: `and then` disappears and two paths become one that
    names nothing."""
    lost = [(w, out) for w, out in _sweep(line).items() if out
            for word in keep if word not in out]
    assert not lost, f"{name}: {len(lost)} widths dropped a word beside the path: {lost[:3]}"


@pytest.mark.parametrize("name,line,tail,keep", SHAPES, ids=[s[0] for s in SHAPES])
def test_an_elision_never_overflows_the_room_it_was_given(name, line, tail, keep):
    over = [(w, cell_len(out)) for w, out in _sweep(line).items() if out and cell_len(out) > w]
    assert not over, f"{name}: {over[:3]}"


def test_the_operators_tree_reaches_the_same_names_as_its_twin_without_spaces():
    """The headline, and the twin is the control: `FLAT` is the same tree with the spaces taken out,
    a path every build of this module has always seen. Wherever the twin names the file, `REAL` has
    to name it too — anything less is the tail clip coming back only for people whose directory names
    contain spaces.

    Not the same STRING: the spaced directory name is two cells wider than the flattened one, so the
    width at which that directory is given back differs by two, and the assertion is about what is
    readable rather than about matching lengths."""
    real, flat = _sweep(f"read_file {REAL}"), _sweep(f"read_file {FLAT}")
    clipped = [(w, flat[w]) for w in WIDTHS if flat[w] and not real[w]]
    assert not clipped, f"{len(clipped)} widths fell back to the clip only with spaces: {clipped[:3]}"
    named = [(w, real[w], flat[w]) for w in WIDTHS
             if flat[w].endswith("release-notes-v12.md")
             and not real[w].endswith("release-notes-v12.md")]
    assert not named, f"{len(named)} widths lost the file name only with spaces: {named[:3]}"
    assert real[40] == "read_file /home/…/release-notes-v12.md"
    assert real[50] == "read_file /home/…/sample/docs/release-notes-v12.md"


def test_two_paths_on_one_line_stay_two_paths():
    """`/a/b and then /c/d` is the rule's other half: a rooted piece closes the run before it, so the
    words in between are never inside a path and neither path can absorb the other."""
    line = "read_file /a/b/first-file.md and then /c/d/second-file.md"
    assert paths._paths(line) == ["/c/d/second-file.md", "/a/b/first-file.md"]
    joined = [(w, out) for w, out in _sweep(line).items() if out and " and then " not in out]
    assert not joined, joined[:3]


def test_a_shell_argument_is_never_absorbed_into_the_path_before_it():
    """`cp SRC DST` with a relative DST is the shape a bare greedy join turns into one path, on the
    surface where a person is consenting to a command. Two separator-bearing pieces side by side are
    an argument list; only a connector between them licenses the join."""
    line = "$ cp /home/jordan/vault/archive/notes.md backup/copies/notes.md"
    assert paths._paths(line) == ["/home/jordan/vault/archive/notes.md", "backup/copies/notes.md"]
    assert not [t for t in paths._paths(line) if " " in t]
    assert paths.shorten(line, 44, True) == "$ cp /home/…/notes.md backup/copies/notes.md"


def test_a_directory_name_with_two_spaces_is_one_path():
    assert paths._paths(f"read_file {TWO}") == [TWO]
    assert paths.shorten(f"read_file {TWO}", 40, True) == "read_file /home/…/release-notes-v12.md"


def test_a_quoted_path_keeps_its_quotes_and_gives_up_its_middle():
    """A shell quotes a path that has a space inside it, and the quote is the one delimiter the string
    really carries. It ends the run, so the argument after it stays its own."""
    line = '$ cp "/home/jordan/Projects - Security Audits/x.md" /tmp/y/z.md'
    assert paths._paths(line) == ["/home/jordan/Projects - Security Audits/x.md", "/tmp/y/z.md"]
    assert paths.shorten(line, 46, True) == '$ cp "/home/…/x.md" /tmp/y/z.md'


def test_a_wide_directory_name_is_measured_in_cells_and_not_characters():
    """`資料室 - 保管` is one directory whose name has spaces in it and whose characters are two cells
    each. Counted as characters the row goes out four cells wide."""
    line = f"read_file {CJK}"
    assert paths._paths(line) == [CJK]
    for w in WIDTHS:
        out = paths.shorten(line, w, True)
        assert not out or cell_len(out) <= w
    assert paths.shorten(line, 34, True) == "read_file /home/…/notes.md"


def test_a_url_is_never_elided_and_never_joined_to_the_path_beside_it():
    """A URL is one unbroken token and half of one is a link nobody can follow — so it is never a
    candidate, and it never ends up inside somebody else's."""
    assert paths._paths(f"open {URL}") == []
    assert all(paths.shorten(f"open {URL}", w, True) == "" for w in range(10, 161))
    line = f"see /a/b/c/notes.md and {URL}"
    assert paths._paths(line) == ["/a/b/c/notes.md"]
    kept = [out for out in _sweep(line).values() if out]
    assert kept and all(URL in out for out in kept)


def test_a_path_with_no_directories_leaves_the_clip_to_answer():
    """One separator is no middle to give up, so `shorten` declines and says so — which is what lets
    the caller fall back to its own clip instead of drawing a mark over nothing."""
    assert paths._paths(f"read_file {BARE}") == []
    assert all(paths.shorten(f"read_file {BARE}", w, True) == "" for w in range(10, 161))


def test_a_space_in_the_last_name_still_leaves_a_path_the_reader_can_finish():
    """The rule declines to join here — `report.md` carries no separator, so nothing confirms the run —
    and declining costs nothing: what was not absorbed is printed straight after the elision, so the
    whole tail is still on the row."""
    line = "read_file /home/jordan/vault/archive/my report.md"
    kept = [(w, out) for w, out in _sweep(line).items() if out]
    assert kept and all(out.endswith("my report.md") for _, out in kept)


ASCII_ROWS = (f"~/Projects - Security Audits/Sample/sample · 128 files",
              "~/work/kotoba/vault/archive · 128 files",
              "~/Projects - Security Audits/Sample/sample",
              "~/work/kotoba/vault/archive/notes.md")


@pytest.mark.parametrize("row", ASCII_ROWS)
def test_the_column_trim_is_never_read_as_a_parent_directory_hop(row):
    """Under the elision's floor the trim is what answers, and it marked itself `..`. `~/work/kotoba/..`
    is not a shortened path: it is a complete and readable name for the directory above the one the row
    was talking about."""
    hops = [(w, out) for w in range(10, 161)
            for out in [column(row, w, False).rstrip()]
            if out.endswith("..") and not out.endswith("...")]
    assert not hops, f"{len(hops)} ASCII widths ended in a parent hop: {hops[:4]}"
    assert not [w for w in range(10, 161) if "/../" in column(row, w, False)]


@pytest.mark.parametrize("row", ASCII_ROWS)
def test_the_column_is_exactly_its_width_in_both_alphabets(row):
    """The promise the dim column after it is aligned on. A three-cell mark in a two-cell column is
    how widening the mark would have broken it."""
    bad = [(w, u, cell_len(column(row, w, u))) for w in range(1, 200) for u in (True, False)
           if cell_len(column(row, w, u)) != w]
    assert not bad, bad[:4]


def test_the_work_row_keeps_its_count_on_the_operators_own_tree():
    """The header writes the path and the file count on one row, and the count is the only number that
    row carries. On a spaced tree it was the first thing the tail clip took."""
    row = f"{REAL} · 128 files"
    for w in WIDTHS:
        out = column(row, w, True)
        assert cell_len(out) == w
        if w >= cell_len("/home/…/sample · 128 files"):
            assert out.rstrip().endswith("· 128 files"), (w, out)
            assert "sample" in out.split("·")[0] or "…" in out
