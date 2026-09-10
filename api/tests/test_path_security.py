"""The filesystem jail: validate_within_dir()."""
from __future__ import annotations

import os

import pytest
from conftest import make_symlink

from kotoba.core.path_security import PathSecurityError, validate_within_dir


def test_relative_path_within_root_ok(tmp_path):
    got = validate_within_dir("a/b.txt", tmp_path)
    assert got == (tmp_path / "a" / "b.txt").resolve()


def test_root_itself_is_allowed(tmp_path):
    assert validate_within_dir(".", tmp_path) == tmp_path.resolve()


def test_dotdot_escape_rejected(tmp_path):
    with pytest.raises(PathSecurityError):
        validate_within_dir("../../etc/passwd", tmp_path)


def test_absolute_outside_root_rejected(tmp_path):
    with pytest.raises(PathSecurityError):
        validate_within_dir("/etc/passwd", tmp_path)


def test_absolute_inside_root_ok(tmp_path):
    inside = tmp_path / "sub" / "f.txt"
    assert validate_within_dir(str(inside), tmp_path) == inside.resolve()


def test_symlink_escape_rejected(tmp_path):
    # A symlink that lives inside the jail but points outside must be rejected after resolve().
    outside = tmp_path.parent / "outside_target"
    outside.mkdir()
    link = tmp_path / "escape"
    make_symlink(link, outside)
    with pytest.raises(PathSecurityError):
        validate_within_dir("escape/secret.txt", tmp_path)


def test_nested_deep_path_ok(tmp_path):
    got = validate_within_dir("x/y/z/deep.txt", tmp_path)
    assert str(got).startswith(str(tmp_path.resolve()))
