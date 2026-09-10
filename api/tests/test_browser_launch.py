"""Autostart the user's real browser for CDP. Kotoba connects to a real, logged-in browser over CDP
(KOTOBA_BROWSER_CDP, e.g. http://127.0.0.1:9222) so it passes anti-bot walls. But that only works if the
browser is ALREADY listening on the debug port. ensure_browser() closes that gap: if nothing is on the
port, it detects an installed Chromium-family browser and launches it with --remote-debugging-port +
a dedicated profile, then waits for the port. If something is already there, it's a no-op (never duplicates).
"""
from __future__ import annotations

import asyncio
import os
import signal
import socket
import types
from pathlib import Path

from conftest import posix_only
import kotoba.core.mcp.browser_launch as bl


def test_parse_cdp_host_port():
    assert bl._parse_cdp("http://127.0.0.1:9222") == ("127.0.0.1", 9222)
    assert bl._parse_cdp("http://localhost:9333/") == ("localhost", 9333)
    assert bl._parse_cdp("") is None
    assert bl._parse_cdp("not a url") is None


def test_detect_browser_env_override_wins(monkeypatch, tmp_path):
    exe = tmp_path / "my-brave"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("KOTOBA_BROWSER_EXECUTABLE", str(exe))
    assert bl._detect_browser() == str(exe)


def test_detect_browser_ignores_missing_override(monkeypatch):
    # A bogus override must NOT be returned (it would fail to launch); fall through to PATH detection.
    monkeypatch.setenv("KOTOBA_BROWSER_EXECUTABLE", "/nope/does-not-exist")
    monkeypatch.setattr(bl.shutil, "which", lambda name: "/usr/bin/brave" if name == "brave" else None)
    assert bl._detect_browser() == "/usr/bin/brave"


def test_detect_browser_prefers_order(monkeypatch):
    # Only chromium present → it's picked (brave absent).
    present = {"chromium": "/usr/bin/chromium"}
    monkeypatch.delenv("KOTOBA_BROWSER_EXECUTABLE", raising=False)
    monkeypatch.setattr(bl.shutil, "which", lambda name: present.get(name))
    assert bl._detect_browser() == "/usr/bin/chromium"


def test_detect_browser_none_when_nothing_installed(monkeypatch):
    monkeypatch.delenv("KOTOBA_BROWSER_EXECUTABLE", raising=False)
    monkeypatch.setattr(bl.shutil, "which", lambda name: None)
    assert bl._detect_browser() is None


def test_ensure_browser_noop_when_port_already_open(monkeypatch):
    launched = {"called": False}
    monkeypatch.setattr(bl, "_port_open", lambda h, p: True)  # something already on :9222
    monkeypatch.setattr(bl, "_detect_browser", lambda: (_ for _ in ()).throw(AssertionError("must not detect")))

    async def go():
        return await bl.ensure_browser("http://127.0.0.1:9222")

    assert asyncio.run(go()) is True
    assert launched["called"] is False  # never launched a second instance


def test_ensure_browser_launches_then_waits_for_port(monkeypatch, tmp_path):
    state = {"open": False, "argv": None}

    def fake_port_open(host, port):
        return state["open"]

    def fake_popen(argv, **kw):
        state["argv"] = argv
        state["open"] = True  # the browser "came up" on the port
        return types.SimpleNamespace(pid=4242)

    monkeypatch.setattr(bl, "_port_open", fake_port_open)
    monkeypatch.setattr(bl, "_detect_browser", lambda: "/usr/bin/brave")
    monkeypatch.setattr(bl.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("KOTOBA_BROWSER_PROFILE", str(tmp_path / "prof"))

    async def go():
        return await bl.ensure_browser("http://127.0.0.1:9222", timeout=3.0, poll=0.01)

    assert asyncio.run(go()) is True
    argv = state["argv"]
    assert argv[0] == "/usr/bin/brave"
    assert "--remote-debugging-port=9222" in argv
    assert any(a.startswith("--user-data-dir=") and "prof" in a for a in argv)


def test_ensure_browser_false_when_no_browser_found(monkeypatch):
    monkeypatch.setattr(bl, "_port_open", lambda h, p: False)
    monkeypatch.setattr(bl, "_detect_browser", lambda: None)

    async def go():
        return await bl.ensure_browser("http://127.0.0.1:9222", timeout=0.1, poll=0.01)

    assert asyncio.run(go()) is False  # nothing installed → graceful False (caller falls back / reports)


def test_ensure_browser_false_on_empty_cdp(monkeypatch):
    async def go():
        return await bl.ensure_browser("", timeout=0.1, poll=0.01)

    assert asyncio.run(go()) is False  # no CDP configured → nothing to ensure


def test_is_local_host_only_loopback_and_localhost():
    """A browser we launch binds its debug port on THIS machine, so only a loopback/localhost CDP can be
    served by launching one."""
    assert bl._is_local_host("127.0.0.1") is True
    assert bl._is_local_host("127.5.6.7") is True
    assert bl._is_local_host("::1") is True
    assert bl._is_local_host("localhost") is True
    assert bl._is_local_host("db.localhost") is True
    assert bl._is_local_host("10.255.255.1") is False
    assert bl._is_local_host("192.168.1.5") is False
    assert bl._is_local_host("example.com") is False
    assert bl._is_local_host("") is False


def test_ensure_browser_refuses_remote_cdp_without_launching(monkeypatch):
    """A dead REMOTE CDP endpoint must not open a useless local window: a local --remote-debugging-port
    can never answer a CDP URL that lives on another host, so ensure_browser refuses without detecting."""
    monkeypatch.setattr(bl, "_port_open", lambda h, p: False)
    monkeypatch.setattr(bl, "_detect_browser",
                        lambda: (_ for _ in ()).throw(AssertionError("must not try to launch for a remote host")))

    async def go():
        return await bl.ensure_browser("http://10.255.255.1:9222", timeout=0.2, poll=0.01)

    assert asyncio.run(go()) is False


def test_ensure_browser_still_launches_for_a_local_cold_port(monkeypatch, tmp_path):
    """The local path is unaffected by the remote guard: a cold LOCAL CDP still detects and launches."""
    state = {"open": False}
    monkeypatch.setattr(bl, "_port_open", lambda h, p: state["open"])
    monkeypatch.setattr(bl, "_detect_browser", lambda: "/usr/bin/brave")

    def fake_popen(argv, **kw):
        state["open"] = True
        return types.SimpleNamespace(pid=1)

    monkeypatch.setattr(bl.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("KOTOBA_BROWSER_PROFILE", str(tmp_path / "prof"))

    async def go():
        return await bl.ensure_browser("http://localhost:9333", timeout=2.0, poll=0.01)

    assert asyncio.run(go()) is True


def _write_fake_browser(dir_path: Path, counter: Path):
    """A real, launchable stand-in that records each launch (its PID) before anything else, then opens
    the debug port after a short delay (widening the race window), then idles. The N3 proof counts real
    Popen, not a mock — the lock has to hold across genuine concurrent subprocess launches."""
    fake = dir_path / "fake-browser"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, socket, time\n"
        "port = None\n"
        "for a in sys.argv[1:]:\n"
        "    if a.startswith('--remote-debugging-port='):\n"
        "        port = int(a.split('=', 1)[1])\n"
        f"open({str(counter)!r}, 'a').write(str(os.getpid()) + '\\n')\n"
        "time.sleep(0.6)\n"
        "try:\n"
        "    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "    s.bind(('127.0.0.1', port)); s.listen(5); time.sleep(5)\n"
        "except Exception:\n"
        "    time.sleep(5)\n"
    )
    fake.chmod(0o755)
    return fake


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@posix_only("a #!/bin/sh executable and SIGKILL")
def test_concurrent_cold_port_launches_exactly_one_browser(monkeypatch, tmp_path):
    """N3: two concurrent callers on a cold local port launch exactly ONE real browser (scratch profile,
    an ephemeral free port, never 9222). Every launched PID is killed afterwards."""
    counter = tmp_path / "launches.txt"
    counter.write_text("")
    fake = _write_fake_browser(tmp_path, counter)
    port = _free_port()
    monkeypatch.setenv("KOTOBA_BROWSER_EXECUTABLE", str(fake))
    monkeypatch.setenv("KOTOBA_BROWSER_PROFILE", str(tmp_path / "profile"))
    cdp = f"http://127.0.0.1:{port}"

    def _pids():
        return [int(x) for x in counter.read_text(encoding="utf-8").split() if x.strip()]

    async def go():
        return await asyncio.gather(
            bl.ensure_browser(cdp, timeout=3.0, poll=0.02),
            bl.ensure_browser(cdp, timeout=3.0, poll=0.02),
        )

    try:
        results = asyncio.run(go())
        assert results == [True, True], "both callers end up connected"
        assert len(_pids()) == 1, "only one browser was ever launched"
    finally:
        for pid in _pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
