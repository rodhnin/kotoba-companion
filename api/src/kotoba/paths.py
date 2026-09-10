"""Where her data and her personality live, resolved once.

Five modules counted `Path(__file__).parents[N]` by hand. That is the one failure a package move makes
SILENT: a wrong depth still names a directory that exists, so the database is empty and the skills are
missing with nothing raised.

From a CLONE the tree is `<repo>/api/src/kotoba` and the database belongs beside the code in `api/`;
from a WHEEL there is no repo, so both fall back to `~/.kotoba`. DATA_DIR is the read-only copy a wheel
carries inside the package, tried after the clone location.
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"


def _clone_root() -> Path | None:
    """The repository this was installed from, or None when it came from a wheel."""
    src = PACKAGE_DIR.parent
    api = src.parent
    return api.parent if src.name == "src" and (api.parent / "soul").is_dir() else None


_CLONE = _clone_root()

HOME_DIR = Path(os.getenv("KOTOBA_HOME") or str(Path.home() / ".kotoba")).expanduser()


def home_dir() -> Path:
    """The same place, asked EACH TIME. `HOME_DIR` is settled at import, so anything that writes there
    keeps writing to the real home however the environment changes afterwards — which is how a test
    suite reaches the user's own files.

    An EMPTY value is not an answer: it resolved to the working directory, so a command run inside
    somebody's project took that project for her home."""
    return Path(os.getenv("KOTOBA_HOME") or str(Path.home() / ".kotoba")).expanduser()
API_DIR = PACKAGE_DIR.parents[1] if _CLONE else HOME_DIR
REPO_ROOT = _CLONE or HOME_DIR

DB_NAME = "kotoba.db"


def db_dir() -> Path:
    """Where a RELATIVE database url lands: her home for a new install, the checkout for an old one.

    A wheel already answered the home. A CLONE answered `api/`, so the keys, the memory and every
    conversation lived INSIDE the folder — the one thing an upgrade replaces. Unpacking a new version
    beside the old one therefore started empty, with nothing on screen saying why.

    One BESIDE THE PACKAGE always wins, and that direction is the whole point. Preferring the home
    whenever a file happened to be there was measured switching a live install onto a two-turn stray
    while 561 conversations and two saved keys went invisible — and doctor called it ok. Moving to the
    home is something a person does on purpose, by moving the file."""
    if _CLONE is not None:
        beside = PACKAGE_DIR.parents[1]
        if (beside / DB_NAME).is_file():
            return beside
    return home_dir()
