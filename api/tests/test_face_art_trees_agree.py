"""Face art lives in two trees, and one command must leave both the same.

The resolver used to prefer the packaged copy even inside a checkout, so importing into the
checkout tree alone changed nothing on screen — it printed success repeatedly while the CLI kept
drawing a stale face, which is how a real batch once reached production only by hand-copying files.

The resolver is clone-first now, so the failure moved rather than vanished: an import into the
checkout looks right on the machine that did it and ships stale in the wheel. A file that exists in
only one tree — a new emotion, or a twin variant — passes a packaged-vs-repo comparison alone; this
checks the pairing in both directions instead."""
from __future__ import annotations

from pathlib import Path

import pytest

from kotoba import paths
from kotoba.paths import DATA_DIR, REPO_ROOT

REPO_FACES = REPO_ROOT / "assets" / "cli" / "faces"
PACKAGED_FACES = DATA_DIR / "cli" / "faces"

only_a_clone = pytest.mark.skipif(
    paths._CLONE is None, reason="a wheel install has no assets/ tree to compare against")


def disagreements(a: Path, b: Path) -> list[str]:
    """Every way two face trees can fail to be the same set of identical files."""
    names = {p.name for p in a.glob("*.png")} | {p.name for p in b.glob("*.png")}
    out = []
    for name in sorted(names):
        left, right = a / name, b / name
        if not left.is_file():
            out.append(f"{name}: missing from {a}")
        elif not right.is_file():
            out.append(f"{name}: missing from {b}")
        elif left.read_bytes() != right.read_bytes():
            out.append(f"{name}: different bytes in the two trees")
    return out


@only_a_clone
def test_both_face_trees_hold_the_same_sprites():
    problems = disagreements(REPO_FACES, PACKAGED_FACES)
    assert not problems, (
        "the two face trees have drifted, so the CLI is drawing something other than what "
        "assets/ holds — re-run scripts/import-faces.py <dir> --apply, which writes both:\n  "
        + "\n  ".join(problems))


@only_a_clone
def test_the_runtime_reads_one_of_the_trees_the_importer_writes(monkeypatch):
    """A third location would put the art back out of reach of the importer."""
    from kotoba.cli.render import art

    monkeypatch.delenv("KOTOBA_CLI_FACES", raising=False)
    assert art.faces_dir() in (REPO_FACES, PACKAGED_FACES)


def test_the_comparison_notices_a_one_sided_import(tmp_path):
    """The detector itself, on trees we control — an assertion that can only pass by working."""
    a, b = tmp_path / "assets", tmp_path / "packaged"
    a.mkdir()
    b.mkdir()
    for tree in (a, b):
        (tree / "neutral.png").write_bytes(b"same")
    assert disagreements(a, b) == []

    (a / "neutral.png").write_bytes(b"freshly imported")
    assert disagreements(a, b) == ["neutral.png: different bytes in the two trees"]

    (b / "neutral.png").write_bytes(b"freshly imported")
    (a / "angry-outlined.png").write_bytes(b"a twin the package never got")
    assert disagreements(a, b) == [f"angry-outlined.png: missing from {b}"]
