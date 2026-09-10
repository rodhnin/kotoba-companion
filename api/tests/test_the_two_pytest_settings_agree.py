"""Two files configure pytest and only one of them applies, depending on where you stand: the root
`pytest.ini` wins from a clone, `api/pyproject.toml` wins from inside the package. pytest has no
include directive, so the settings are copied by hand — and a copy that drifts silently changes what
a contributor's run deselects. The network marker is the one that matters: lose it on one side and a
stranger's first `pytest` reaches the npm registry without being asked."""
from __future__ import annotations

import configparser
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INI = ROOT / "pytest.ini"
PYPROJECT = ROOT / "api" / "pyproject.toml"

pytestmark = pytest.mark.skipif(not INI.is_file(), reason="installed, not a clone")


def _both() -> tuple[dict, dict]:
    parser = configparser.ConfigParser()
    parser.read(INI, encoding="utf-8")
    packaged = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return dict(parser["pytest"]), packaged["tool"]["pytest"]["ini_options"]


def test_both_deselect_the_network_tests():
    ini, packaged = _both()
    assert ini["addopts"] == packaged["addopts"]


def test_both_declare_the_same_markers():
    ini, packaged = _both()
    assert ini["markers"].splitlines() == [m.strip() for m in packaged["markers"]]


def test_both_point_at_the_same_directory():
    ini, packaged = _both()
    assert [(ROOT / p).resolve() for p in ini["testpaths"].split()] == [
        (PYPROJECT.parent / p).resolve() for p in packaged["testpaths"]
    ]
