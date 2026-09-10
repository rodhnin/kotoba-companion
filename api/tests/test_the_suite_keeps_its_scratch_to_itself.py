"""Nothing a test writes may land outside pytest's own tmp tree.

A scratch database built with `tempfile.mktemp(suffix=".db")` is not covered by any KOTOBA_* path, so
six files alone dropped 76 of them into the system /tmp on every run and nothing ever removed them.
"""
from __future__ import annotations

import tempfile
from pathlib import Path


def test_tempfile_itself_is_redirected(tmp_path):
    here = Path(tempfile.gettempdir()).resolve()
    assert here != Path("/tmp"), "the suite is still writing scratch into the system /tmp"
    assert any(p.startswith("kotoba_state") for p in here.parts), (
        f"scratch must live under pytest's tmp tree, not {here}")


def test_a_bare_mktemp_lands_inside_that_tree():
    made = Path(tempfile.mktemp(suffix=".db")).resolve()
    assert any(p.startswith("kotoba_state") for p in made.parts), (
        f"a stray database would be left at {made}")
