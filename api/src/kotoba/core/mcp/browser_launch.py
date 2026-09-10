"""Autostart the user's real browser so Kotoba can drive it over CDP.

Kotoba connects to a REAL, logged-in browser (KOTOBA_BROWSER_CDP): a non-headless one passes anti-bot
walls a fresh headless one does not. @playwright/mcp --cdp-endpoint only CONNECTS, so this launches
one when nothing is listening — a Chromium-family browser on a DEDICATED profile, visible, and left
running, since it is theirs. Something already on the port is a no-op.

Two rails on the launch: it only fires when the CDP host is LOCAL, since a browser we start binds its
port on THIS machine and launching one for a remote endpoint just opens a useless window; and
check-then-launch sits behind a per-loop lock, or two cold-port callers each open a window."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import shutil
import socket
import subprocess
import weakref
from pathlib import Path
from urllib.parse import urlparse

from kotoba.core import logs
from kotoba.paths import home_dir

log = logging.getLogger("kotoba")

# Detection order (Linux/macOS PATH names) — any Chromium-family browser exposes the same CDP, so any
# of these works.
_BROWSER_CANDIDATES = (
    "brave-browser", "brave", "brave-browser-stable",
    "chromium", "chromium-browser",
    "google-chrome", "google-chrome-stable", "chrome",
    "microsoft-edge", "microsoft-edge-stable",
)


def _parse_cdp(cdp_url: str) -> tuple[str, int] | None:
    """('127.0.0.1', 9222) from a CDP URL, or None if it isn't a usable host:port."""
    if not cdp_url or not cdp_url.strip():
        return None
    u = urlparse(cdp_url.strip())
    if not u.hostname or not u.port:
        return None
    return (u.hostname, u.port)


def _port_open(host: str, port: int) -> bool:
    """True if something is already accepting TCP on host:port (a browser's debug endpoint)."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _is_local_host(host: str) -> bool:
    """True when a browser we launch here (its debug port binds on this machine) could actually serve
    this CDP host — i.e. loopback or localhost. A non-local host means the endpoint lives elsewhere, so
    launching a LOCAL window for it is useless: connecting still fails and the user gets a stray window."""
    h = (host or "").strip().lower()
    if not h:
        return False
    if h == "localhost" or h.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


_launch_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


def _get_launch_lock() -> asyncio.Lock:
    """One launch lock per running event loop (server, CLI, each test loop). Keyed by loop so a Lock is
    never reused across loops — which would raise 'attached to a different loop' — while still
    serializing every concurrent caller within the one loop that matters (the app's)."""
    loop = asyncio.get_running_loop()
    lock = _launch_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _launch_locks[loop] = lock
    return lock


def _detect_browser() -> str | None:
    """Path to a launchable Chromium-family browser: KOTOBA_BROWSER_EXECUTABLE if it's a real file,
    else the first candidate found on PATH. None if nothing is installed."""
    override = os.getenv("KOTOBA_BROWSER_EXECUTABLE", "").strip()
    if override and Path(override).is_file():
        return override
    # A bogus override is ignored (don't return a path that can't launch) — fall through to PATH search.
    for name in _BROWSER_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def _profile_dir() -> str:
    """Dedicated profile for Kotoba's debug browser, separate from the user's everyday browser window
    (Chromium can't open the same profile twice). Override with KOTOBA_BROWSER_PROFILE."""
    p = os.getenv("KOTOBA_BROWSER_PROFILE", "").strip()
    return p or str(home_dir() / "browser-profile")


async def ensure_browser(cdp_url: str, timeout: float = 20.0, poll: float = 0.25) -> bool:
    """Make sure a browser is reachable on the CDP debug port; launch one if not. Idempotent and safe to
    call before every browser connect. Returns True if the port is up (already running or just launched),
    False if there's no CDP configured, the endpoint is remote (nothing to launch locally), no browser is
    installed, or it didn't come up in time.

    Concurrency-safe: the launch is serialized behind a per-loop lock and re-checks the port under it, so
    two callers hitting a cold port launch exactly one browser, not two."""
    hostport = _parse_cdp(cdp_url)
    if hostport is None:
        return False
    host, port = hostport

    # Already running (the user's own browser, or one we launched earlier) → connect to it, don't duplicate.
    if _port_open(host, port):
        return True

    if not _is_local_host(host):
        log.warning(
            "ensure_browser: CDP %s is a remote host — not launching a local browser it can't serve", cdp_url
        )
        return False

    async with _get_launch_lock():
        if _port_open(host, port):
            return True

        exe = _detect_browser()
        if not exe:
            log.warning("ensure_browser: no Chromium-family browser found to launch for CDP %s", cdp_url)
            return False

        profile = _profile_dir()
        try:
            Path(profile).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        argv = [
            exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        try:
            # Detached: it's the user's own browser, it should outlive our request (start_new_session so a
            # backend restart / request cancel doesn't take it down). Not headless — they watch it work.
            # Chromium narrates GPU and DBus over stderr, which under the CLI is a drawn screen.
            subprocess.Popen(argv, start_new_session=True, **logs.child_streams())
        except Exception:
            log.exception("ensure_browser: failed to launch %s", exe)
            return False

        # Wait for the debug port to come up (first launch also unpacks a fresh profile → give it a beat).
        waited = 0.0
        while waited < timeout:
            if _port_open(host, port):
                log.info("ensure_browser: launched %s on CDP port %d", Path(exe).name, port)
                return True
            await asyncio.sleep(poll)
            waited += poll
    log.warning("ensure_browser: %s didn't open CDP port %d within %.0fs", exe, port, timeout)
    return False
