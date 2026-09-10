"""A clone reads `soul/`; a wheel carries its own copy under the package. Both are her.

They drift the moment somebody edits one, and nothing says so: the clone keeps working, and the wheel
ships a personality that was corrected weeks ago. The whole packaged data directory has the same
shape, so the check is over every file, not only the one that has already caught us.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLONE = REPO / "soul"
PACKAGED = REPO / "api" / "src" / "kotoba" / "data" / "soul"


def _pairs():
    if not CLONE.is_dir() or not PACKAGED.is_dir():
        return []
    both = {p.relative_to(CLONE) for p in CLONE.rglob("*")
            if p.is_file() and not p.name.startswith(".")}
    both |= {p.relative_to(PACKAGED) for p in PACKAGED.rglob("*")
             if p.is_file() and not p.name.startswith(".")}
    return sorted(both)


@pytest.mark.parametrize("rel", _pairs(), ids=str)
def test_the_packaged_copy_matches_the_one_a_clone_reads(rel):
    theirs = PACKAGED / rel
    assert theirs.is_file(), f"{rel} is missing from the packaged copy, so a wheel ships without it"
    assert theirs.read_bytes() == (CLONE / rel).read_bytes(), (
        f"{rel} differs between soul/ and the packaged copy — a wheel would ship the stale one")


def test_there_is_something_to_compare():
    """An empty parametrize passes without asserting anything, which is not an instrument."""
    assert len(_pairs()) >= 2
