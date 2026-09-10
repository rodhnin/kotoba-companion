"""No test may drop the hard isolation, on purpose or by accident.

`_isolate_user_state` used to take the SHARED `monkeypatch` fixture, so any test that called
`monkeypatch.undo()` to put one thing back tore the whole block down with it — and whatever ran after
that in the same test met the operator's real home. Measured twice on a live machine: one test wrote a
server into their real `mcp.yaml` pointing at a pytest temp directory, and another read the real master
key file, which is the one thing the isolation says in capitals it must never touch.
"""
from __future__ import annotations

import os
from pathlib import Path

from kotoba.core import keystore
from kotoba.paths import db_dir, home_dir


def _redirected() -> dict[str, str]:
    return {name: os.environ[name] for name in
            ("KOTOBA_HOME", "DATABASE_URL", "KOTOBA_MCP_CONFIG", "KOTOBA_KEYSTORE_KEY_FILE")
            if name in os.environ}


def test_undoing_your_own_patch_does_not_undo_the_isolation(monkeypatch, tmp_path):
    """The exact shape of the defect: a test patches one thing, undoes it, and carries on."""
    before = _redirected()
    assert before, "the isolation set nothing at all, so this proves nothing"

    monkeypatch.setattr(os, "_kotoba_probe", object(), raising=False)
    monkeypatch.undo()

    assert _redirected() == before, (
        "a test's own undo() tore down the hard isolation; anything it did next met the real home")


def test_after_an_undo_the_real_home_is_still_out_of_reach(monkeypatch):
    """The consequence, not the mechanism: the paths that decide where she writes must not move."""
    monkeypatch.setenv("KOTOBA_PROBE_ONLY", "1")
    monkeypatch.undo()

    real = Path.home() / ".kotoba"
    assert home_dir() != real, "her home resolved to the operator's own"
    assert db_dir() != real, "the database resolved into the operator's own home"
    assert Path(keystore._key_file()) != real / ".keystore_key", "the real master key was in reach"
