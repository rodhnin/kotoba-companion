"""An install from a wheel has to have a personality file somebody can open.

Her default lives inside the package, where a reinstall overwrites it and nobody should be editing
anyway — so "edit soul/default.md, or start from a template" was addressed to a file that, on a wheel,
is on nobody's disk. Setup now puts a copy and the templates in the home, once, and never again: an
edit is the whole point and a second run must not undo one.
"""
from __future__ import annotations

import pytest

from kotoba.soul import loader


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_HOME", str(tmp_path))
    return tmp_path


def test_a_wheel_gets_a_file_and_the_templates(home):
    where = loader.seed_home_copies()
    assert where == home / "soul" / "default.md" and where.is_file()
    names = sorted(p.name for p in (home / "soul" / "templates").glob("*.md"))
    assert names == ["assistant.md", "companion.md", "study.md"], \
        "the README sends people to these three and a wheel would not have them"


def test_an_edit_survives_the_next_setup(home):
    loader.seed_home_copies().write_text("hers", encoding="utf-8")
    (home / "soul" / "templates" / "study.md").write_text("hers too", encoding="utf-8")
    loader.seed_home_copies()
    assert (home / "soul" / "default.md").read_text(encoding="utf-8") == "hers"
    assert (home / "soul" / "templates" / "study.md").read_text(encoding="utf-8") == "hers too"


def test_a_home_that_cannot_be_written_does_not_fail_a_setup(tmp_path, monkeypatch):
    """Seeding is a courtesy, not a step: a read-only home must not stop somebody configuring her."""
    monkeypatch.setenv("KOTOBA_HOME", str(tmp_path / "nope"))
    monkeypatch.setattr(loader.Path, "mkdir",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("read-only")))
    assert loader.seed_home_copies() is None


def test_the_home_copy_is_found_when_there_is_no_clone(home, monkeypatch):
    """The order that matters: a clone's own file still wins, and the packaged one is the last word."""
    loader.seed_home_copies()
    monkeypatch.setattr(loader, "API_DIR", home / "nowhere")
    monkeypatch.setattr(loader, "REPO_ROOT", home / "nowhere")
    assert loader.resolve_soul_path(loader.DEFAULT_SOUL_PATH) == (home / "soul" / "default.md")


def test_setup_seeds_before_it_asks_anything():
    """The wiring, which is the half a unit test of the function cannot see."""
    from pathlib import Path

    src = Path(loader.__file__).parent.parent / "cli" / "wizard.py"
    text = src.read_text(encoding="utf-8")
    assert "seed_home_copies()" in text, "setup no longer puts a file where it can be edited"
    assert text.index("seed_home_copies()") < text.index("_choose_provider(stage")
