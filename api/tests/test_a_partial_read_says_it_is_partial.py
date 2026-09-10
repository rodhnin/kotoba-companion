"""A result that was CUT must say it was cut — read_file's character cap and search_files' result cap.

Both tools bounded their output and returned it bare, so a partial answer looked exactly like a
complete one. `read_file` was the sharper case: `_MAX_CHARS` slices the joined window, and the "(N
more lines)" tail is empty exactly when that slice bites (a file of few LONG lines fits the 400-line
window but overflows the characters) — 20,000 characters came back as 8,000, mid-word, unannounced.

The arithmetic underneath was also wrong: the remainder was `len(lines) - start - limit`, so a
model-supplied negative `limit` ADDED to it — 15 lines of a 20-line file returned claiming "25 more
lines"."""
from __future__ import annotations

import asyncio

from kotoba.tools import ToolContext
from kotoba.tools.action import file_read, search_files


def _ctx(tmp_path):
    return ToolContext(db=None, session_id="t", workdir=tmp_path, mode="work")


def _quoted(out: str) -> str:
    """What the file itself held. It comes back inside a marker pair — the announcement of a cut is
    OURS, so it sits outside, where it cannot read as the file admitting it was truncated."""
    import re

    m = re.search(r"<<<QUOTED ([0-9a-f]{6})>>>\n(.*)\n<<<END \1>>>", out, re.S)
    assert m, f"nothing was quoted: {out[:200]!r}"
    return m.group(2)


def test_the_character_cap_is_announced(tmp_path):
    body = "\n".join("x" * 1999 for _ in range(10))          # 10 lines, ~20k chars
    (tmp_path / "wide.txt").write_text(body)
    out = asyncio.run(file_read.execute({"path": "wide.txt"}, _ctx(tmp_path)))
    assert len(_quoted(out)) == file_read._MAX_CHARS           # the budget applies to the file's text
    assert "cut here" in out
    assert "cut here" not in _quoted(out)                      # the file never said it was cut
    assert str(len(body) - file_read._MAX_CHARS) in out        # how much was withheld
    assert "end of the file" in out                            # and what not to conclude from it


def test_a_whole_file_that_fits_says_nothing_extra(tmp_path):
    (tmp_path / "small.txt").write_text("alpha\nbeta\n")
    out = asyncio.run(file_read.execute({"path": "small.txt"}, _ctx(tmp_path)))
    assert _quoted(out) == "alpha\nbeta"
    assert "cut here" not in out and "more lines" not in out


def test_the_remainder_is_counted_from_what_was_returned(tmp_path):
    (tmp_path / "a.txt").write_text("\n".join(f"line{i}" for i in range(20)))
    out = asyncio.run(file_read.execute({"path": "a.txt", "limit": -5}, _ctx(tmp_path)))
    shown = [ln for ln in _quoted(out).splitlines() if ln.startswith("line")]
    assert f"{20 - len(shown)} more lines" in out             # never more than the file holds
    assert "25 more lines" not in out


def test_search_says_when_the_list_was_cut(tmp_path):
    (tmp_path / "big.txt").write_text("\n".join(f"needle {i}" for i in range(200)))
    out = asyncio.run(search_files.execute({"query": "needle"}, _ctx(tmp_path)))
    assert "list cut at" in out
    assert len([ln for ln in out.splitlines() if "needle" in ln]) == search_files._MAX_RESULTS


def test_the_walk_fallback_says_it_too(tmp_path, monkeypatch):
    monkeypatch.setattr(search_files.shutil, "which", lambda _n: None)
    (tmp_path / "big.txt").write_text("\n".join(f"needle {i}" for i in range(200)))
    out = asyncio.run(search_files.execute({"query": "needle"}, _ctx(tmp_path)))
    assert "list cut at" in out
    # The walk stops at the cap, so it must not claim a total it never counted.
    assert "there may be more" in out


def test_a_search_that_fits_is_unchanged(tmp_path):
    (tmp_path / "s.txt").write_text("needle here\nnothing\n")
    out = asyncio.run(search_files.execute({"query": "needle"}, _ctx(tmp_path)))
    assert "list cut at" not in out and "needle here" in out
