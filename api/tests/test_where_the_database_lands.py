"""A DATABASE_URL the driver spells differently must not become a directory named after the scheme.

`_path_from_url` stripped exactly `sqlite:///`. SQLAlchemy also writes the driver into the scheme —
`sqlite+aiosqlite:///…`, which is what its own docs show — and that spelling fell through to be read as
a RELATIVE PATH: the whole URL became a folder name under `api/`, holding a brand-new empty database.
No memory, no saved keys, no history, and nothing said why. That is the exact failure the function was
written to prevent, arriving through the one spelling nobody stripped.

The slash count is the part that is easy to get wrong twice: three means the path is relative, four
means the fourth slash belongs to the path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kotoba.db.database import _path_from_url
from kotoba import paths
from kotoba.paths import db_dir

def here() -> str:
    """Asked each time: which directory answers depends on what is on disk."""
    return str(db_dir() / "kotoba.db")


@pytest.mark.parametrize("url", [
    "sqlite:///./kotoba.db",
    "sqlite:///kotoba.db",
    "sqlite+aiosqlite:///./kotoba.db",
    "sqlite+aiosqlite:///kotoba.db",
    "sqlite+pysqlite:///kotoba.db",
    "SQLite+AioSQLite:///kotoba.db",
])
def test_every_spelling_of_the_default_lands_on_the_same_file(url):
    """The whole point: the CLI and the server must open ONE file however the URL was written."""
    assert _path_from_url(url) == here()


@pytest.mark.parametrize("url,expected", [
    ("sqlite:////abs/a.db", "/abs/a.db"),
    ("sqlite+aiosqlite:////abs/a.db", "/abs/a.db"),
])
def test_the_fourth_slash_still_means_absolute(url, expected):
    """Absolute means "not re-rooted into the package", which is the property, not a spelling.

    A leading slash with no drive is not absolute on Windows, it is relative to whichever drive you are
    standing on, so the same URL resolves to two different roots depending on where the process started
    — and asserting one exact string picked the wrong one on a runner whose workspace is not on C:."""
    got = Path(_path_from_url(url))
    tail = Path(expected)
    assert got.is_absolute()
    assert got.parts[-len(tail.parts) + 1:] == tail.parts[1:]
    assert db_dir() not in got.parents


@pytest.mark.parametrize("url", ["sqlite:///:memory:", "sqlite+aiosqlite:///:memory:", "sqlite://"])
def test_in_memory_survives_every_spelling(url):
    assert _path_from_url(url) == ":memory:"


def test_no_scheme_ever_becomes_a_directory_name():
    """The defect made visible: a folder called `sqlite+aiosqlite:` appeared inside the package."""
    for url in ("sqlite+aiosqlite:///tmp/x/y.db", "sqlite+pysqlite:///a.db", "sqlite://"):
        assert "sqlite" not in _path_from_url(url).rsplit("/", 1)[0].split("/")[-1], url


def test_a_bare_path_is_still_accepted_and_still_relative_to_her_home():
    """Not every caller writes a URL — the fallback has to resolve where the database lives, or
    launching from another directory silently opens a different, empty one. `here()` is that place,
    which is `api/` only while a database already sits there."""
    assert _path_from_url("./kotoba.db") == here()
    given = Path(_path_from_url("/tmp/somewhere.db"))
    assert given.is_absolute()
    assert given.parts[-2:] == ("tmp", "somewhere.db")
    assert db_dir() not in given.parents


# --- and WHICH directory that is ------------------------------------------------------------------

def test_a_clone_with_nothing_yet_puts_the_database_in_her_home(tmp_path, monkeypatch):
    """The whole reason this moved. A database inside the checkout is lost the moment somebody unpacks
    a new version into a new folder — keys, memory and every conversation — and the folder is precisely
    what an upgrade replaces. Measured on a real reinstall, which came up with no saved keys."""
    monkeypatch.setenv("KOTOBA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(paths, "_CLONE", tmp_path / "clone")
    monkeypatch.setattr(paths, "PACKAGE_DIR", tmp_path / "clone" / "api" / "src" / "kotoba")

    assert paths.db_dir() == tmp_path / "home"


def test_one_that_already_exists_beside_the_package_is_adopted_where_it_lies(tmp_path, monkeypatch):
    """Nobody's database is relocated behind their back: another process may hold it open, and a move
    that half-succeeds costs the history it was meant to protect."""
    beside = tmp_path / "clone" / "api"
    beside.mkdir(parents=True)
    (beside / paths.DB_NAME).write_bytes(b"")
    monkeypatch.setenv("KOTOBA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(paths, "_CLONE", tmp_path / "clone")
    monkeypatch.setattr(paths, "PACKAGE_DIR", beside / "src" / "kotoba")

    assert paths.db_dir() == beside


def test_the_one_beside_the_package_keeps_winning_even_once_a_home_one_exists(tmp_path, monkeypatch):
    """The dangerous direction, and the reason the rule is this way round.

    A stray database in the home is easy to acquire — a wheel in another venv, a second account, a
    test that escaped its isolation. Letting it win switched a live install onto it: measured, 561
    conversations and two saved keys went invisible behind a two-turn stray, and doctor said ok."""
    beside = tmp_path / "clone" / "api"
    beside.mkdir(parents=True)
    (beside / paths.DB_NAME).write_bytes(b"")
    home = tmp_path / "home"
    home.mkdir()
    (home / paths.DB_NAME).write_bytes(b"")
    monkeypatch.setenv("KOTOBA_HOME", str(home))
    monkeypatch.setattr(paths, "_CLONE", tmp_path / "clone")
    monkeypatch.setattr(paths, "PACKAGE_DIR", beside / "src" / "kotoba")

    assert paths.db_dir() == beside, "a stray in the home must never take a live install's place"


def test_a_wheel_never_looks_beside_the_package(tmp_path, monkeypatch):
    """There is no checkout there to look in, and `parents[1]` inside site-packages is somebody else's.

    The decoy is the whole test. Without a database planted beside the package this passed whatever the
    code did, on any machine that did not happen to have a stray one there."""
    beside = tmp_path / "site-packages"
    (beside / "src" / "kotoba").mkdir(parents=True)
    (beside / "kotoba.db").write_bytes(b"")
    monkeypatch.setenv("KOTOBA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(paths, "_CLONE", None)
    monkeypatch.setattr(paths, "PACKAGE_DIR", beside / "src" / "kotoba")

    assert paths.db_dir() == tmp_path / "home"


def test_an_empty_home_is_not_the_working_directory(tmp_path, monkeypatch):
    """The rule `home_dir` documents, with nothing that ran it. Empty, the variable resolved to `.`, so
    a command started inside somebody's project took that project for her home and wrote there."""
    monkeypatch.setenv("KOTOBA_HOME", "")
    monkeypatch.chdir(tmp_path)
    assert paths.home_dir() == Path.home() / ".kotoba"
