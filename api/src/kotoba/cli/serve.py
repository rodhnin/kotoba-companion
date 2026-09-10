"""`kotoba serve`: the backend, and her face.

Three shapes. In a clone with Node the development server runs as it always did, because a
contributor's edits have to show. Otherwise the backend serves the UI the package carries, from its
own port at `/app` — a wheel needs no Node at all. With neither, it says which piece is missing.

Each child gets its own process group: `npm run dev` forks a worker that outlives npm, so signalling
npm alone leaves something holding the port. The teardown covers everything from the first spawn on —
a raise between the two used to leave the backend holding its port where Ctrl+C could not reach it."""
from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import TextIO

from kotoba.paths import REPO_ROOT, _CLONE

log = logging.getLogger("kotoba.cli")

_GRACE = 5.0        # seconds a child gets to leave politely before it is killed
_POLL = 0.2         # how often the parent checks whether a child died on its own
_DRAIN = 1.0        # seconds the pumps get to finish writing what the dead children already said
_OPEN_WAIT = 90.0   # how long the browser waits for Next to bind before saying it gave up
_WINDOWS = os.name == "nt"

# io's own docs: "TextIOWrapper objects are not thread-safe", and both pumps write to one sys.stdout.
_write_lock = threading.Lock()


def has_server() -> bool:
    return all(_installed(m) for m in ("fastapi", "uvicorn"))


def run(*, port: int = 8000, web_port: int = 3000, open_browser: bool = False) -> int:
    if not has_server():
        from kotoba import DIST_NAME

        print(f'the backend needs the server extra:  pip install "{DIST_NAME}[server]"', file=sys.stderr)
        return 1

    # The one teardown, covering everything from the first spawn on: a raise between the two spawns
    # left the backend holding its port in its own session, where Ctrl+C cannot reach it either.
    children: list[tuple[str, subprocess.Popen]] = []
    pumps: list[threading.Thread] = []
    shutting_down = threading.Event()
    try:
        children.append(("[api]", _spawn(backend_command(port), None, _backend_env(web_port))))
        # The dev server wins whenever it can run — a contributor's edits have to show. Only when it
        # cannot do we ask whether this install carries a built UI the backend can serve by itself.
        blocked = frontend_blocker(REPO_ROOT)
        carried = carried_web() if blocked else None
        if not blocked:
            children.append(("[web]", _spawn(frontend_command(web_port), REPO_ROOT, frontend_env(port))))
        elif carried is None:
            print(f"[web] not started: {blocked}", file=sys.stderr)
            named = frontend_named()
            if named:
                print(f"[web] KOTOBA_FRONTEND_DIR points at {named}, which holds no app.html",
                      file=sys.stderr)
            else:
                print(f"[web] and this install carries no built one either — {_how_to_get_one()}",
                      file=sys.stderr)
        elif _CLONE is not None:
            # Only where somebody could be editing: the built one covers for the dev server, and
            # silence reads as "it is running" to a contributor waiting to see their change. On a
            # wheel there is nothing to edit and the line would be noise on a first run.
            print(f"[web] no dev server: {blocked}", file=sys.stderr)

        pumps = [_pump(prefix, child, sys.stdout) for prefix, child in children]
        print(f"[api] http://127.0.0.1:{port}/health", file=sys.stderr)
        url = f"http://127.0.0.1:{web_port}" if not blocked else f"http://127.0.0.1:{port}/app"
        if carried is not None:
            print(f"[web] {url}  ({carried})", file=sys.stderr)
        elif not blocked:
            print(f"[web] {url}", file=sys.stderr)
        if open_browser:
            wait_on = port if carried is not None else web_port
            _open_web_ui(url, wait_on, None if carried is not None else blocked, port, shutting_down)
        return _supervise(children)
    finally:
        shutting_down.set()
        for _, child in children:
            stop(child)
        drain(pumps)


def _open_web_ui(url: str, wait_port: int, blocked: str | None, backend_port: int,
                 shutting_down: threading.Event) -> None:
    """Say what is about to happen, then hand the waiting to a daemon thread.

    A browser window at a port that never came up is worse than a sentence, so nothing opens when the
    frontend is blocked and nothing opens before it answers. And nothing is JOINED: the wait belongs to
    a daemon thread watching `shutting_down`, so Ctrl+C goes straight through `_supervise` to the
    teardown with the opener neither delaying it nor holding the process afterwards."""
    if blocked:
        print(f"[web] no browser to open — the web UI is not running (see above). The backend alone "
              f"is at http://127.0.0.1:{backend_port}/health", file=sys.stderr)
        return
    print(f"[web] opening {url} in your browser once it answers", file=sys.stderr)
    threading.Thread(target=_open_when_ready, args=(url, wait_port, shutting_down), daemon=True).start()


def _open_when_ready(url: str, web_port: int, shutting_down: threading.Event) -> None:
    """Wait for something to accept a connection on the port, then open it — or say it never came.

    A TCP connect is the question, not an HTTP request: Next binds before it has compiled a page, and
    the browser is a better thing to be waiting in front of a compile than a terminal is."""
    deadline = time.monotonic() + _OPEN_WAIT
    while not shutting_down.is_set() and time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", web_port), timeout=0.5):
                pass
        except OSError:
            shutting_down.wait(_POLL)
            continue
        # webbrowser answers a headless machine with False and a broken opener with an exception, and
        # both mean the same thing to whoever is reading this log: the address is still theirs to open.
        try:
            opened = webbrowser.open(url)
        except Exception:
            opened = False
        if not opened:
            print(f"[web] could not open a browser here — go to {url}", file=sys.stderr)
        return
    if not shutting_down.is_set():
        print(f"[web] nothing answered on {url} within {_OPEN_WAIT:g}s, so no browser was opened — "
              "open it yourself once the line above stops compiling", file=sys.stderr)


def backend_command(port: int) -> list[str]:
    """uvicorn through THIS interpreter, so it lands in the environment the CLI was installed into.
    Loopback only: the backend runs her tools on this machine and must never be reachable from the LAN."""
    return [sys.executable, "-m", "uvicorn", "kotoba.server:app",
            "--host", "127.0.0.1", "--port", str(port)]


def frontend_command(web_port: int) -> list[str]:
    """`npm run dev`, spelled so the platform can actually start it.

    On Windows npm is `npm.cmd`, a batch file, and CreateProcess runs only real executables — so the
    bare name that works everywhere else failed there even after npm had been found, because
    `shutil.which` resolves `npm.cmd` through PATHEXT and CreateProcess will not. The interpreter for
    a batch file is the command processor, so it is named.

    `npm` stays UNQUALIFIED rather than resolved: `cmd /c` strips the outer pair of quotes when its
    first token carries them, which is exactly how a real path — `C:\\Program Files\\nodejs\\npm.cmd` —
    comes apart at the space. A bare name has no quotes to strip, and cmd resolves it through PATH."""
    launcher = [os.environ.get("COMSPEC", "cmd.exe"), "/c", "npm"] if _WINDOWS else ["npm"]
    return [*launcher, "run", "dev", "--", "--port", str(web_port), "--hostname", "127.0.0.1"]


def frontend_blocker(web_root: Path) -> str | None:
    """What stops the web UI from starting here, in plain words, or None when nothing does."""
    if not (web_root / "package.json").is_file():
        return (f"no web app at {web_root} — there is nothing to run in development here, "
                "so clone the repository if you want to work on her face")
    if shutil.which("npm") is None:
        return "npm is not installed — the web UI needs Node.js (https://nodejs.org)"
    if not (web_root / "node_modules").is_dir():
        return f"its dependencies are missing — run `npm install` in {web_root}"
    return None


def frontend_named() -> str:
    """The directory somebody pointed KOTOBA_FRONTEND_DIR at when it holds no build, or empty."""
    from kotoba.core import frontend

    return str(frontend.describe().get("named") or "")


def _how_to_get_one() -> str:
    """A clone can build one; an install can only be replaced. Telling a contributor to reinstall sends
    them away from the repository they already have."""
    from kotoba import DIST_NAME

    if _CLONE is not None:
        return "build it with `python scripts/build_web.py`"
    return f"pip install --force-reinstall {DIST_NAME}"


def carried_web() -> str | None:
    """A one-line description of the UI this install can serve with no Node, or None if it has none."""
    try:
        from kotoba.core import frontend
    except Exception:
        return None
    seen = frontend.describe()
    if seen.get("named"):
        # An override wins over whatever is packaged, so a typo in it is the whole reason and saying
        # the install carries nothing would send somebody reinstalling for no reason.
        return None
    if seen.get("kind") not in ("packaged", "export"):
        return None
    version = str(seen.get("version") or "")
    built = f"built in, {seen.get('files', 0)} files"
    return f"{built}, kotoba {version}" if version else built


def frontend_env(port: int) -> dict[str, str]:
    """Next reads KOTOBA_BACKEND_URL when the dev server starts, for its /api/* rewrite and for the voice
    WebSocket URL it hands the browser. We started that backend, so we overrule whatever was configured.

    Next's own telemetry is on by default and the packaged build already turns it off; a dev server
    started by us would otherwise be the one path where something leaves the machine unasked."""
    return dict(_base_env(), KOTOBA_BACKEND_URL=f"http://127.0.0.1:{port}",
                NEXT_TELEMETRY_DISABLED="1")


def _supervise(children: list[tuple[str, subprocess.Popen]]) -> int:
    """Wait for the first child to fall over, or for Ctrl+C. Stopping them is `run`'s job and only
    `run`'s: a raise between the two spawns never reaches here, so a teardown that lived in this
    function alone had a way to be skipped entirely."""
    code = 1
    previous = _catch_termination()
    try:
        while all(child.poll() is None for _, child in children):
            time.sleep(_POLL)
        gone = [f"{p} exited with {c.returncode}" for p, c in children if c.poll() is not None]
        print(f"stopping — {', '.join(gone)}", file=sys.stderr)
    except KeyboardInterrupt:
        code = 130
    finally:
        for handler, sig in previous:
            signal.signal(sig, handler)
    return code


def _catch_termination() -> list[tuple[object, int]]:
    """Ctrl+C is not the only way this ends. systemd, Docker and every supervisor send SIGTERM, and the
    children are in their own sessions precisely so a terminal signal cannot reach them — so a parent
    that dies without running its teardown leaves a backend and a Next worker holding their ports.

    The names are looked up BEFORE the try and that is what used to break Windows: building the tuple
    `(signal.SIGTERM, signal.SIGHUP)` reads SIGHUP, which does not exist there, so the AttributeError
    was raised outside the guard the line below already described as covering "the platform has no
    such signal". The guard was right; it was one expression too late."""
    def raise_interrupt(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    caught = []
    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue    # the platform has no such signal
        try:
            caught.append((signal.signal(sig, raise_interrupt), sig))
        except (ValueError, OSError):
            pass        # not the main thread
    return caught


def _spawn(command: list[str], cwd: Path | None, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        command, cwd=str(cwd) if cwd else None, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1, **_own_group(),
    )


def _own_group() -> dict[str, object]:
    """Put the child out of reach of the terminal's own Ctrl+C, so shutdown happens once, through us.

    POSIX calls that a new session. Windows has no session and its equivalent is a new process GROUP,
    which a console Ctrl+C is likewise not delivered into — and the same flag is what makes
    CTRL_BREAK_EVENT deliverable to the whole tree in `_ask_group_to_stop`, so it is load-bearing at
    both ends and not merely the local translation of `start_new_session`."""
    if _WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def stop(child: subprocess.Popen) -> None:
    """Terminate a child and everything it forked. Idempotent — the supervisor may already have seen it die."""
    if child.poll() is not None:
        return
    _ask_group_to_stop(child)
    try:
        child.wait(timeout=_GRACE)
        return
    except subprocess.TimeoutExpired:
        _kill_group(child)
    try:
        child.wait(timeout=_GRACE)
    except subprocess.TimeoutExpired:
        log.warning("pid %s outlived `kotoba serve`", child.pid)


def _ask_group_to_stop(child: subprocess.Popen) -> None:
    """The polite half: SIGTERM to the POSIX session, CTRL_BREAK to the Windows process group.

    Windows has no SIGTERM to deliver. `Popen.send_signal(SIGTERM)` there is TerminateProcess, which
    ends `cmd.exe` and leaves the Next worker it started holding port 3000 — the exact orphan the new
    group exists to prevent, and the reason the two halves of this teardown are named rather than
    picked by which signal constant happens to exist."""
    if _WINDOWS:
        try:
            os.kill(child.pid, signal.CTRL_BREAK_EVENT)
        except (AttributeError, OSError, ValueError):
            _kill_group(child)
        return
    _signal_session(child, signal.SIGTERM)


def _kill_group(child: subprocess.Popen) -> None:
    """The impolite half. `taskkill /T` is Windows' `killpg`: it walks the tree by parent id, which is
    what a signal to a session does and what ending the command processor alone does not."""
    if _WINDOWS:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(child.pid)],
                           capture_output=True, timeout=_GRACE)
        except (OSError, subprocess.SubprocessError):
            try:
                child.kill()
            except (OSError, ValueError):
                pass
        return
    _signal_session(child, getattr(signal, "SIGKILL", signal.SIGTERM))


def _signal_session(child: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(os.getpgid(child.pid), sig)
    except (AttributeError, OSError):
        try:
            child.send_signal(sig)      # no process groups here, or the group is already gone
        except (OSError, ValueError):
            pass


def _pump(prefix: str, child: subprocess.Popen, out: TextIO) -> threading.Thread:
    def forward() -> None:
        try:
            for line in child.stdout:
                with _write_lock:
                    out.write(f"{prefix} {line}")
                    out.flush()
        except (ValueError, OSError):
            pass    # the parent closed its stdout while this thread was still draining

    thread = threading.Thread(target=forward, daemon=True)
    thread.start()
    return thread


def drain(pumps: list[threading.Thread]) -> None:
    """Let the pumps finish before the process that owns them exits."""
    deadline = time.monotonic() + _DRAIN
    for thread in pumps:
        thread.join(max(0.0, deadline - time.monotonic()))
    with _write_lock:
        try:
            sys.stdout.flush()
        except (ValueError, OSError):
            pass


def _base_env() -> dict[str, str]:
    return dict(os.environ, PYTHONUNBUFFERED="1")


def _backend_env(web_port: int) -> dict[str, str]:
    """The voice socket is exempt from CORS, so the backend keeps its own allowlist — and it only knew
    port 3000. Serving the page anywhere else left the handshake refused with a 403 the UI does not
    show, so voice simply looked dead. Whatever port we just put the page on is named here."""
    env = _base_env()
    named = [o for o in env.get("CORS_ORIGINS", "").split(",") if o.strip()]
    ours = [f"http://127.0.0.1:{web_port}", f"http://localhost:{web_port}"]
    env["CORS_ORIGINS"] = ",".join(dict.fromkeys(named + ours))
    return env


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None
