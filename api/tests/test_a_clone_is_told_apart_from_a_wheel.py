"""`_clone_root` decides where the database, the repo root and seventeen guards look.

Nothing tested it, and the skip those guards carry is gated on the very function under test — so a
mis-detection turns them into `SKIPPED ... the sources are not shipped` and the suite still reports
green. These build both layouts on disk instead of trusting the tree the suite happens to run in.
"""
from __future__ import annotations

from kotoba import paths


def _clone_at(tmp_path):
    pkg = tmp_path / "api" / "src" / "kotoba"
    pkg.mkdir(parents=True)
    (tmp_path / "soul").mkdir()
    return pkg


def test_a_checkout_is_recognised(tmp_path, monkeypatch):
    pkg = _clone_at(tmp_path)
    monkeypatch.setattr(paths, "PACKAGE_DIR", pkg)
    assert paths._clone_root() == tmp_path


def test_a_wheel_layout_is_not_a_clone(tmp_path, monkeypatch):
    pkg = tmp_path / "site-packages" / "kotoba"
    pkg.mkdir(parents=True)
    monkeypatch.setattr(paths, "PACKAGE_DIR", pkg)
    assert paths._clone_root() is None


def test_a_src_layout_without_the_sources_is_not_a_clone(tmp_path, monkeypatch):
    """The `src` name alone is not the evidence: what makes it a checkout is the sources beside it."""
    pkg = tmp_path / "api" / "src" / "kotoba"
    pkg.mkdir(parents=True)
    monkeypatch.setattr(paths, "PACKAGE_DIR", pkg)
    assert paths._clone_root() is None


def test_this_suite_is_running_in_the_checkout_it_thinks_it_is():
    """If this ever fails on a clone, the seventeen guards gated on it are skipping in silence."""
    expected = (paths.PACKAGE_DIR.parents[2] / "soul").is_dir()
    assert (paths._clone_root() is not None) is expected
