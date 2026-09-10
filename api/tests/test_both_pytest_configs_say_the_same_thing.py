"""Two pytest configs, and only one of them is ever read.

Which one loads is decided by where somebody is standing: `pytest api/tests` finds `api/pyproject.toml`
first, a bare `pytest` at the top finds the root `pytest.ini` first. So a setting added to the
authoritative file reaches CI and misses the contributor the root file exists for — which is exactly
what happened to the timeout that stops a hung wait from being a green summary that never returns.
The two are compared as SETTINGS, never as text, and `testpaths` is compared by where it points, since
the same directory has two spellings from two rootdirs."""
from __future__ import annotations

import configparser
import tomllib

import pytest

from kotoba import paths

pytestmark = pytest.mark.skipif(
    paths._CLONE is None, reason="both configs live in the repository, not the wheel")

ROOT = paths.REPO_ROOT
API = ROOT / "api"


def _ini() -> dict[str, str]:
    parser = configparser.ConfigParser()
    parser.read(ROOT / "pytest.ini", encoding="utf-8")
    return dict(parser["pytest"])


def _toml() -> dict:
    return tomllib.loads((API / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pytest"][
        "ini_options"]


def _norm(value) -> str:
    """An ini value is always text; a toml value may be a list or a number. One shape, whitespace out.
    A setting the other file never declares reads as absent rather than raising, so the failure names
    the two values instead of the lookup that missed."""
    if value is None:
        return "<not declared>"
    if isinstance(value, (list, tuple)):
        return "\n".join(str(v).strip() for v in value)
    return " ".join(str(value).split())


def test_the_two_configs_declare_the_same_settings():
    assert set(_ini()) == set(_toml())


@pytest.mark.parametrize("key", ["markers", "timeout", "addopts"])
def test_a_shared_setting_says_the_same_thing_in_both(key):
    assert _norm(_ini().get(key)) == _norm(_toml().get(key))


def test_both_testpaths_point_at_the_same_directory():
    assert (ROOT / _ini()["testpaths"]).resolve() == (API / _toml()["testpaths"][0]).resolve()
