"""`kotoba discord` without the extra prints the command that installs it, and there are two.

A clone installs it with an editable pointer at a folder; a wheel installs it by distribution name.
Printing the clone's command to somebody who ran `pip install kotoba-companion` sends them to a
directory they never downloaded — the message reads like an answer and leaves them exactly as stuck.
The shape of the install is already known: `paths._CLONE` is the same thing that decides where the
database and the memory live."""
from __future__ import annotations

import pytest

from kotoba import paths
from kotoba.discord import run


@pytest.fixture
def shape(monkeypatch):
    def use(clone):
        monkeypatch.setattr(paths, "_CLONE", clone)
    return use


def test_a_wheel_is_told_the_distribution_name(shape):
    shape(None)
    assert 'pip install "kotoba-companion[discord]"' in run._missing()


def test_a_clone_is_told_the_folder(shape, tmp_path):
    shape(tmp_path)
    assert 'pip install -e "api/[discord]"' in run._missing()


def test_neither_shape_is_offered_the_other_command(shape, tmp_path):
    shape(None)
    assert "api/[discord]" not in run._missing()
    shape(tmp_path)
    assert "kotoba-companion[discord]" not in run._missing()


def test_the_extra_it_names_is_one_the_project_declares():
    """A command naming an extra that does not exist fails with pip's error, not ours."""
    import tomllib

    manifest = tomllib.loads((paths.REPO_ROOT / "api" / "pyproject.toml").read_text(encoding="utf-8"))
    assert "discord" in manifest["project"]["optional-dependencies"]
