"""The basename fallback must be jailed too.

`_safe_target` falls back to the basename when a path is absolute or escapes — deliberately, so a file
is never lost. But `validate_within_dir` RESOLVES symlinks, so handing back `dir / name` unchecked
returns the very link it just rejected, and `save_text`/`save_image` follow it out of the library.
"""
from __future__ import annotations

import pytest
from conftest import make_symlink

from kotoba.core import file_library


@pytest.fixture
def library(tmp_path, monkeypatch):
    lib, outside = tmp_path / "lib", tmp_path / "outside"
    lib.mkdir()
    outside.mkdir()
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(lib))
    return lib, outside


def test_a_symlink_pointing_out_is_refused(library):
    lib, outside = library
    victim = outside / "victim.txt"
    victim.write_text("ORIGINAL")
    make_symlink(lib / "notes.txt", victim)

    assert file_library._safe_target("notes.txt") is None
    file_library.save_text("notes.txt", "INJECTED")
    assert victim.read_text(encoding="utf-8") == "ORIGINAL", "the write must not follow the link out"


def test_traversal_still_falls_back_to_the_basename(library):
    """The fallback exists so a file is never lost — that behaviour must survive the fix."""
    lib, _ = library
    target = file_library._safe_target("../../outside/victim.txt")
    assert target is not None
    assert target.parent == lib.resolve()
    assert target.name == "victim.txt"


def test_an_ordinary_relative_path_is_untouched(library):
    lib, _ = library
    target = file_library._safe_target("research/report.md")
    assert target is not None and lib.resolve() in target.parents
