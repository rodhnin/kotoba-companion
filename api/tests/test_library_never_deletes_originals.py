"""The file cap may only ever delete COPIES, never the user's originals.

In the default setup `resolve_workdir()` returns the library itself, so every file in it is the user's
only copy. A file COUNT is not evidence that anything is disposable: cloning a repo or installing
dependencies in the workdir pushes the count past the cap, and pruning on it deletes real work.
The cap stays meaningful in the advanced setup, where the library mirrors a separate workdir.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "library"))
    monkeypatch.delenv("KOTOBA_WORKSPACE_DIR", raising=False)
    import kotoba.core.file_library as fl

    importlib.reload(fl)
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    return fl, tmp_path / "library"


def _fill(d, n: int, prefix: str = "f"):
    for i in range(n):
        (d / f"{prefix}{i}.txt").write_text("x")


def _count(d) -> int:
    """The user's files. The index and its cross-process lock are sidecars, not content — both are
    dotfiles, which is also what keeps them out of the Files panel."""
    return sum(1 for p in d.rglob("*") if p.is_file() and not p.name.startswith(".index.json"))


def test_publishing_one_file_does_not_delete_a_cloned_repo(lib):
    """The reported failure: `write_file` publishes via touch(), which pruned the whole workdir."""
    fl, root = lib
    repo = root / "cloned-repo"
    (repo / ".git" / "objects").mkdir(parents=True)
    _fill(repo / ".git" / "objects", 700, "obj")
    (repo / "README.md").write_text("important")
    (root / "notes.md").write_text("my notes")

    before = _count(root)
    assert before > fl._MAX_FILES, "the scenario needs to be over the cap to be meaningful"

    fl.touch("notes.md")

    assert _count(root) == before, "publishing a file must not delete anything"
    assert (repo / "README.md").read_text(encoding="utf-8") == "important"


def test_a_direct_save_in_the_default_setup_also_keeps_everything(lib):
    """save_text/save_image share the prune call — the guard belongs in _prune, not in one caller."""
    fl, root = lib
    _fill(root, fl._MAX_FILES + 120)
    before = _count(root)

    fl.save_text("another.md", "content")

    assert _count(root) >= before, "no original may be deleted to make room"
    assert (root / "another.md").read_text(encoding="utf-8") == "content"


def test_the_cap_still_bites_when_the_library_is_only_a_mirror(tmp_path, monkeypatch):
    """The cap is not dead — in the advanced setup the library holds copies and stays bounded."""
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(tmp_path / "mirror"))
    monkeypatch.setenv("KOTOBA_WORKSPACE_DIR", str(tmp_path / "work"))
    (tmp_path / "work").mkdir()
    (tmp_path / "mirror").mkdir()
    import kotoba.core.file_library as fl

    importlib.reload(fl)

    _fill(tmp_path / "mirror", fl._MAX_FILES + 100, "copy")
    fl.save_text("new.txt", "hello")

    assert _count(tmp_path / "mirror") <= fl._MAX_FILES + 1
