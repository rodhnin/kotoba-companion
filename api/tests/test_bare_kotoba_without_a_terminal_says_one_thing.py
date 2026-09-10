from __future__ import annotations

from types import SimpleNamespace

from kotoba import DIST_NAME
from kotoba.cli import __main__ as entry
from kotoba.cli import host, serve


def _args() -> SimpleNamespace:
    return SimpleNamespace(sessions=False, settings=False)


def test_without_the_server_extra_no_browser_is_promised(monkeypatch, capsys):
    monkeypatch.setattr(host, "missing_terminal_modules", lambda: ["termios", "tty"])
    monkeypatch.setattr(serve, "has_server", lambda: False)
    monkeypatch.setattr(serve, "run", lambda **kw: (_ for _ in ()).throw(AssertionError("serve.run called")))
    assert entry._web_instead(_args()) == 1
    err = capsys.readouterr().err
    assert f'pip install "{DIST_NAME}[server]"' in err
    assert "browser opens" not in err
    assert err.count("kotoba:") == 1


def test_with_the_server_extra_the_browser_is_promised_once_and_serve_runs(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(host, "missing_terminal_modules", lambda: ["termios", "tty"])
    monkeypatch.setattr(serve, "has_server", lambda: True)
    monkeypatch.setattr(serve, "run", lambda **kw: ran.append(kw) or 0)
    assert entry._web_instead(_args()) == 0
    assert ran == [{"open_browser": True}]
    assert "browser opens" in capsys.readouterr().err
