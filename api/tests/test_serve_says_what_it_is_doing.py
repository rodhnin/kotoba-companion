"""`serve` has three shapes, and the one a stranger meets was the one nobody tested.

On a wheel the dev server cannot run and the backend serves the built UI itself — so nothing may be
spawned. It was: the branch fell through and launched `npm run dev` inside the home directory, which
exits 254 and takes the backend down with it. The end-to-end that proved this path had been run
BEFORE a later edit changed the branch, and never again.
"""
from __future__ import annotations

import pytest

from kotoba.cli import serve


@pytest.fixture
def spawns(monkeypatch):
    """Every child `run` would start, without starting any — and nothing to supervise afterwards."""
    started: list[str] = []

    class _Child:
        pid = 0

        def poll(self):
            return 0

    def _fake(cmd, cwd, env):
        started.append(" ".join(str(c) for c in cmd))
        return _Child()

    monkeypatch.setattr(serve, "_spawn", _fake)
    monkeypatch.setattr(serve, "_pump", lambda *a, **kw: None)
    monkeypatch.setattr(serve, "_supervise", lambda children: 0)
    monkeypatch.setattr(serve, "stop", lambda child: None)
    monkeypatch.setattr(serve, "drain", lambda pumps: None)
    return started


def _run(monkeypatch, *, blocked, carried, clone):
    monkeypatch.setattr(serve, "frontend_blocker", lambda root: blocked)
    monkeypatch.setattr(serve, "carried_web", lambda: carried)
    monkeypatch.setattr(serve, "_CLONE", clone)
    assert serve.run(port=18999, web_port=13999) == 0


def test_a_wheel_with_a_built_ui_starts_no_second_process(monkeypatch, spawns, capsys):
    """The whole point of shipping the build: there is nothing to run and nothing to install."""
    _run(monkeypatch, blocked="no web app at /home/someone/.kotoba", carried="built in, 69 files", clone=None)
    assert not [c for c in spawns if "npm" in c], f"a wheel tried to run the dev server: {spawns}"
    said = capsys.readouterr().err
    assert "/app" in said, "it never says where the app is"
    assert "npm install" not in said and "no dev server" not in said, \
        "an install with nothing to edit was told about a development server"


def test_a_clone_that_can_run_the_dev_server_runs_it(monkeypatch, spawns):
    _run(monkeypatch, blocked=None, carried=None, clone="/repo")
    assert spawns and any("npm" in c for c in spawns[-1:]), spawns


def test_a_clone_whose_dev_server_cannot_run_is_told_why(monkeypatch, spawns, capsys):
    """The built one covers for it, but a contributor waiting to see an edit has to hear that."""
    _run(monkeypatch, blocked="its dependencies are missing", carried="built in, 69 files", clone="/repo")
    assert not [c for c in spawns if "npm" in c]
    said = capsys.readouterr().err
    assert "no dev server" in said and "dependencies are missing" in said


def test_with_nothing_at_all_the_advice_fits_where_you_are(monkeypatch, spawns, capsys):
    """A clone can build one; an install can only be replaced. Each was told the other's answer."""
    _run(monkeypatch, blocked="no web app", carried=None, clone="/repo")
    assert "scripts/build_web.py" in capsys.readouterr().err

    _run(monkeypatch, blocked="no web app", carried=None, clone=None)
    said = capsys.readouterr().err
    assert "force-reinstall" in said and "build_web" not in said
