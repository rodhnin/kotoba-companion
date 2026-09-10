"""PEP 517 wrapper that refuses to package a web UI that is absent, stale, or built for another version.

It never runs npm: an install must not need Node. `build_editable` is inherited untouched, so a
contributor's `pip install -e` works with no build at all.
"""
from __future__ import annotations

from pathlib import Path

from setuptools import build_meta as _orig
from setuptools.build_meta import *  # noqa: F401,F403

import _web_check

HERE = Path(__file__).resolve().parent
WEB = HERE / "src" / "kotoba" / "web"
STAGED = HERE / "build" / "lib"


def _clear_staged() -> None:
    """setuptools copies into `build/lib` and never cleans it, so an in-tree wheel carries every file
    of every earlier build: renamed chunks, and modules deleted from the sources months ago. Nothing
    downstream would notice, because every check anyone wrote reads the tree and not the staging."""
    import shutil

    shutil.rmtree(STAGED, ignore_errors=True)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    _web_check.mirror_licences(HERE, HERE.parent)
    _web_check.require("wheel", WEB, _web_check.version(HERE), HERE.parent)
    _clear_staged()
    return _orig.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_sdist(sdist_directory, config_settings=None):
    _web_check.mirror_licences(HERE, HERE.parent)
    _web_check.require("sdist", WEB, _web_check.version(HERE), HERE.parent)
    return _orig.build_sdist(sdist_directory, config_settings)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    """`license-files` names two files that only the mirror step puts here, and inheriting this hook
    left them absent: an editable install warns today and stops being supported outright in 2027.
    It is the documented way to work on this, so it cannot be the one path that skips the step.

    The built frontend is NOT required here — a contributor runs the dev server."""
    _web_check.mirror_licences(HERE, HERE.parent)
    return _orig.build_editable(wheel_directory, config_settings, metadata_directory)


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    _web_check.mirror_licences(HERE, HERE.parent)
    return _orig.prepare_metadata_for_build_editable(metadata_directory, config_settings)
