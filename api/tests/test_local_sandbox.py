"""LocalSandbox — runs the model's code/commands on the HOST (the user's own
machine, their own agent), with safeguards: jailed workdir, scrubbed env (no secrets reach the child),
timeouts, output caps. No Docker, no E2B."""
from __future__ import annotations

import asyncio

import pytest
from conftest import posix_only

from kotoba.core.path_security import PathSecurityError
from kotoba.core.sandbox.local import LocalSandbox


def test_run_returns_stdout_and_exit(tmp_path):
    async def go():
        sb = LocalSandbox(tmp_path)
        await sb.start()
        return await sb.run("echo hello-local")
    res = asyncio.run(go())
    assert res.exit_code == 0 and "hello-local" in res.stdout


def test_run_code_python(tmp_path):
    async def go():
        sb = LocalSandbox(tmp_path)
        await sb.start()
        return await sb.run_code("print(2 + 2)")
    res = asyncio.run(go())
    assert res.ok and "4" in res.stdout


@posix_only("POSIX shell syntax (printf, ${VAR:-default})")
def test_env_is_scrubbed_no_secrets(tmp_path, monkeypatch):
    # A secret in the parent env must NOT reach the child process. The third name carries no marker
    # word on purpose: with only KEY-shaped names the marker tripwire answers for the allow-list, so
    # replacing the whole scrub with `dict(os.environ)` left this test green.
    monkeypatch.setenv("MY_API_KEY", "super-secret-value")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak")
    monkeypatch.setenv("MY_DB_DSN", "postgres://user:pw-leak@host/db")

    async def go():
        sb = LocalSandbox(tmp_path)
        await sb.start()
        return await sb.run(
            'printf "%s|%s|%s" "${MY_API_KEY:-EMPTY}" "${OPENAI_API_KEY:-EMPTY}" "${MY_DB_DSN:-EMPTY}"')
    res = asyncio.run(go())
    assert "super-secret-value" not in res.stdout and "sk-leak" not in res.stdout
    assert "pw-leak" not in res.stdout, "a secret whose NAME has no marker word still travelled"
    assert "EMPTY|EMPTY|EMPTY" in res.stdout


@posix_only("POSIX shell syntax (printf, ${VAR:-default})")
def test_desktop_session_reaches_the_child(tmp_path, monkeypatch):
    """Without these she cannot open anything: a GUI launch dies in milliseconds with `no DISPLAY
    environment variable specified`, and backgrounded behind `>/dev/null 2>&1 &` nobody ever learns."""
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")

    async def go():
        sb = LocalSandbox(tmp_path)
        await sb.start()
        return await sb.run('printf "%s|%s|%s|%s" "${DISPLAY:-X}" "${WAYLAND_DISPLAY:-X}"'
                            ' "${XDG_RUNTIME_DIR:-X}" "${DBUS_SESSION_BUS_ADDRESS:-X}"')
    assert asyncio.run(go()).stdout == ":0|wayland-1|/run/user/1000|unix:path=/run/user/1000/bus"


@posix_only("POSIX shell syntax (printf, ${VAR:-default})")
def test_xauthority_survives_the_secret_marker(tmp_path, monkeypatch):
    """`AUTH` is a secret marker and XAUTHORITY is not a secret — it names the cookie file, which a
    child running as this user could already find. An X11-only host is headless if the marker wins."""
    monkeypatch.setenv("XAUTHORITY", "/run/user/1000/xauth_abc")
    monkeypatch.setenv("AUTH_TOKEN", "must-not-travel")

    async def go():
        sb = LocalSandbox(tmp_path)
        await sb.start()
        return await sb.run('printf "%s|%s" "${XAUTHORITY:-X}" "${AUTH_TOKEN:-EMPTY}"')
    assert asyncio.run(go()).stdout == "/run/user/1000/xauth_abc|EMPTY"


def test_headless_host_is_unchanged(tmp_path, monkeypatch):
    """A server has none of these, so the allowlist must simply skip them rather than invent empties."""
    from kotoba.core.sandbox.local import _ENV_ALLOW_DESKTOP, _scrubbed_env

    for name in _ENV_ALLOW_DESKTOP:
        monkeypatch.delenv(name, raising=False)
    assert not set(_scrubbed_env()) & set(_ENV_ALLOW_DESKTOP)


def test_windows_gets_its_own_essentials(tmp_path, monkeypatch):
    """The base allowlist was written for a Unix shell. A Windows child without SYSTEMROOT or COMSPEC is
    broken for far more than opening a window, and X11 names mean nothing there."""
    from kotoba.core.sandbox import local

    monkeypatch.setattr(local.os, "name", "nt")
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\system32\cmd.exe")
    monkeypatch.setenv("DISPLAY", ":0")
    env = local._scrubbed_env()
    assert env.get("SYSTEMROOT") == r"C:\Windows" and env.get("COMSPEC")
    assert "DISPLAY" not in env


@posix_only("os.name == posix beneath a cygwin sys.platform")
def test_cygwin_gets_both_sets(tmp_path, monkeypatch):
    """POSIX emulation ON Windows: `os.name` is "posix", so the plain two-way split handed it the X11
    names and no SYSTEMROOT — the worst of both. It can genuinely need either."""
    from kotoba.core.sandbox import local

    monkeypatch.setattr(local.os, "name", "posix")
    monkeypatch.setattr(local.sys, "platform", "cygwin")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    env = local._scrubbed_env()
    assert env.get("DISPLAY") == ":0" and env.get("SYSTEMROOT") == r"C:\Windows"


def test_terminate_without_killpg_still_kills(tmp_path, monkeypatch):
    """`os.killpg` does not exist on Windows and AttributeError is not caught here, so a timeout used to
    take the turn down instead of the runaway — on the one path with nothing left to catch it."""
    from kotoba.core.sandbox import local

    monkeypatch.delattr(local.os, "killpg", raising=False)
    called = []

    class _Proc:
        pid = 1234

        def terminate(self):
            called.append("terminate")

        def kill(self):
            called.append("kill")

        async def wait(self):
            return 0

    asyncio.run(LocalSandbox(tmp_path)._terminate(_Proc()))
    assert called == ["terminate"]


def test_timeout_kills_process(tmp_path):
    async def go():
        sb = LocalSandbox(tmp_path)
        await sb.start()
        return await sb.run("sleep 10", timeout=1)
    res = asyncio.run(asyncio.wait_for(go(), timeout=8))
    assert res.exit_code != 0  # killed, not a clean 0


def test_write_read_jailed(tmp_path):
    async def go():
        sb = LocalSandbox(tmp_path)
        await sb.start()
        await sb.write("notes/a.txt", b"hi")
        return await sb.read("notes/a.txt")
    assert asyncio.run(go()) == b"hi"


def test_write_outside_jail_rejected(tmp_path):
    async def go():
        sb = LocalSandbox(tmp_path)
        await sb.start()
        await sb.write("../escape.txt", b"x")
    with pytest.raises(PathSecurityError):
        asyncio.run(go())
