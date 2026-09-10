"""LocalSandbox — run the model's commands/code on the HOST, no container, no cloud. Safeguards:
  • jailed workdir — every path goes through core.path_security.validate_within_dir
  • scrubbed env — the child gets ONLY a safe allowlist; nothing whose name looks secret, so the
    user's API keys never reach model-run code
  • own process group — killed cleanly on timeout (SIGTERM, then SIGKILL after a grace period)
  • output caps — stdout/stderr bounded so a runaway cannot flood the context
  • a NAMED interpreter — POSIX gets `sh -c`, Windows gets PowerShell by argv, never COMSPEC

`shell_is_windows()` is the one origin for a question three places must agree on: the shell the prompt
teaches, the interpreter launched here, and what the gate can read. Disagreement is a security hole."""
from __future__ import annotations

import asyncio
import base64
import os
import re
import signal
import sys
from pathlib import Path

from kotoba.core.path_security import validate_within_dir
from kotoba.core.sandbox.base import ExecResult

# Only these pass through to child processes; everything else (and anything secret-looking) is dropped.
_ENV_ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "SHELL", "PYTHONPATH", "VIRTUAL_ENV", "TZ", "TERM")
_ENV_ALLOW_DESKTOP = (
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
    "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "DESKTOP_SESSION",
    "KDE_FULL_SESSION", "KDE_SESSION_VERSION",
    "XDG_DATA_DIRS", "XDG_DATA_HOME", "XDG_CONFIG_DIRS", "XDG_CONFIG_HOME",
)
_ENV_ALLOW_WINDOWS = (
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE", "HOMEDRIVE", "HOMEPATH",
    "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
    # PowerShell finds the cmdlets that ARE the shell through PSModulePath; it rebuilds a default
    # when the name is missing, which is a fallback and not a promise.
    "PSMODULEPATH",
)
_ENV_ALLOW_EXEMPT = ("XAUTHORITY",)
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH")
_MAX_OUT = 200_000  # bytes cap per stream (the tools truncate further for the model)
_KILL_GRACE = 5.0
_DRAIN_GRACE = 0.5
_EXIT_TICK = 0.05

_PS_EXE = r"System32\WindowsPowerShell\v1.0\powershell.exe"
_PS_FLAGS = ("-NoProfile", "-NonInteractive", "-EncodedCommand")
_PS_EPILOGUE = "\nexit $LASTEXITCODE"
_CLIXML_HEAD = "#< CLIXML"
_CLIXML_DOC = re.compile(r"#<\s*CLIXML\s*\r?\n?(<Objs\b.*?</Objs>)", re.DOTALL)
_XML_ESCAPE = re.compile(r"_x([0-9A-Fa-f]{4})_")


def is_windows_host() -> bool:
    """Whether a child started HERE is launched by Windows. Read at call time and never cached, so the
    platform can be simulated in one place instead of being frozen at import into three copies."""
    return os.name == "nt"


def shell_is_windows() -> bool:
    """Whether the command the model asks for will be interpreted by WINDOWS.

    The platform that matters is the SANDBOX's, never this process's. With `KOTOBA_SANDBOX=docker` her
    commands land in a Linux container on a Windows host, so answering with the host would teach her
    the wrong shell and would lock the approval gate against a platform nothing runs on; with `none`
    nothing runs anywhere. Only the `local` backend puts a command in front of the host's own shell.

    The host test comes first on purpose: on POSIX this returns without reading a setting, so the whole
    question costs one attribute comparison on the platform every current install runs on."""
    if not is_windows_host():
        return False
    from kotoba.core.sandbox import backend_name

    return backend_name() == "local"


def _powershell_argv(command: str) -> list[str]:
    """The explicit PowerShell invocation for `command` — an argv, not an inherited interpreter.

    `create_subprocess_shell` on Windows runs whatever COMSPEC names (cmd.exe), so the shell she is
    taught and the shell that ran matched only by luck. -EncodedCommand carries base64 UTF-16LE, so the
    command reaches the interpreter byte for byte instead of being re-parsed twice into something else.

    The epilogue is one statement on its own LINE, never appended to the model's last, where a trailing
    `#` would comment it out: powershell.exe reports success as 0 or 1, so without it a native program
    that failed comes back as exit 0. The absolute SYSTEMROOT path stops the interpreter being chosen
    by something the model wrote."""
    root = (os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or "C:\\Windows").rstrip("\\/")
    payload = base64.b64encode((command + _PS_EPILOGUE).encode("utf-16-le")).decode("ascii")
    # A literal Windows path, not a pathlib join: on a POSIX machine that separator comes out wrong.
    return [f"{root}\\{_PS_EXE}", *_PS_FLAGS, payload]


def readable_stderr(text: str) -> str:
    """PowerShell's stderr the way a person reads it, not the way it serialises itself.

    powershell.exe encodes ERROR RECORDS as CLIXML the moment stderr is a pipe, so `nosuchcommand`
    arrived as several hundred characters of `<Objs Version="1.1.0.1" …>` — handed to the model as the
    reason a command failed. A native program writes raw bytes to that same pipe, so a capture is
    often part XML and part plain text: each document is unwrapped where it sits and everything around
    it is left alone. Anything unparseable is kept whole, because unreadable beats lost."""
    if not text or _CLIXML_HEAD not in text:
        return text
    return _no_lone_surrogates(_CLIXML_DOC.sub(_decoded_record, text))


def _decoded_record(match: "re.Match[str]") -> str:
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(match.group(1))
    except Exception:
        return match.group(0)
    said = "".join(n.text or "" for n in root.iter() if n.tag == "S" or n.tag.endswith("}S"))
    return _XML_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), said) if said else match.group(0)


def _no_lone_surrogates(text: str) -> str:
    """CLIXML escapes lone surrogates because XML cannot hold them, and `chr()` hands one straight back.

    That is a string Python cannot encode, so it raised wherever the turn was persisted or sent — a
    failure the raw capture could never have, since `decode(errors="replace")` yields U+FFFD instead.
    The same replacement character keeps that promise."""
    if not any("\ud800" <= ch <= "\udfff" for ch in text):
        return text
    return "".join("\ufffd" if "\ud800" <= ch <= "\udfff" else ch for ch in text)


async def _await_exit(proc, exited: asyncio.Task, timeout: float) -> int:
    """The command's exit status, or asyncio.TimeoutError once `timeout` seconds of it are gone.

    `proc.wait()` on its own is not that. CPython's asyncio.base_subprocess wakes its exit waiters only
    from `_call_connection_lost`, which it reaches once every pipe is DISCONNECTED — so a process the
    command left running behind it delays `wait()` for exactly as long as it delays `communicate()`, and
    for exactly the same wrong reason. `proc.returncode` is filled in the moment the child is reaped, by
    both implementations. Watching the future keeps the ordinary case instant; watching the attribute
    alongside it is what makes the answer true, at the cost of a tick while a command is running."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while proc.returncode is None:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        await asyncio.wait({exited}, timeout=min(_EXIT_TICK, remaining))
    return proc.returncode


async def _pump(stream, buf: bytearray) -> None:
    """Drain one of the child's pipes into `buf` until EOF, keeping at most _MAX_OUT bytes.

    This has to run WHILE the command does and never after it. A pipe holds ~64 KB and a child that
    fills it blocks on write until somebody reads — the deadlock asyncio.subprocess documents for
    waiting on a process whose output you have not consumed. Past the cap it keeps reading and throws
    the bytes away, so a runaway can neither flood our memory nor wedge itself against a reader that
    walked off."""
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        if len(buf) < _MAX_OUT:
            buf += chunk[: _MAX_OUT - len(buf)]


def _group_alive(pgid: int) -> bool:
    """Signal 0 delivers nothing and only reports whether the group still has anyone in it."""
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _scrubbed_env() -> dict[str, str]:
    """A minimal, secret-free environment for child processes.

    The desktop names (`_ENV_ALLOW_DESKTOP`) are the difference between "open that PDF" working and
    dying with `no DISPLAY environment variable specified`; backgrounded, that failure reached nobody
    and she reported success on a window that never existed. None IS a credential, but what they add is
    REACH: an approved command can paint a window, read the clipboard and talk to the session bus,
    which on a typical desktop includes the keyring. The gate is the only thing in front of that.

    XAUTHORITY collides with the secret markers and is exempted BY NAME: it holds the path to the X11
    cookie, which a child under this HOME could already find. The base set is chosen by platform."""
    if is_windows_host():
        allow = _ENV_ALLOW_WINDOWS
    elif sys.platform.startswith("cygwin"):
        # POSIX emulation ON Windows: os.name is "posix", so the plain split handed it the X11 set and
        # no SYSTEMROOT — the worst of both. It can genuinely need either, so it gets both.
        allow = (*_ENV_ALLOW_DESKTOP, *_ENV_ALLOW_WINDOWS)
    else:
        allow = _ENV_ALLOW_DESKTOP
    env = {k: os.environ[k] for k in (*_ENV_ALLOW, *allow) if k in os.environ}
    # Defensive, currently catching nothing (XAUTHORITY is the only colliding name, and it is exempt) —
    # the tripwire for the day someone adds a name like AWS_SECRET_ACCESS_KEY to a list above.
    env = {k: v for k, v in env.items()
           if k in _ENV_ALLOW_EXEMPT or not any(m in k.upper() for m in _SECRET_MARKERS)}
    # Temp files go to Kotoba's own scratch dir (~/.kotoba/tmp) — like a normal shell's /tmp, but not the
    # system /tmp and not the Files library. Best-effort: never block exec on this.
    try:
        from kotoba.core.workspace import scratch_dir

        tmp = str(scratch_dir())
        env["TMPDIR"] = env["TMP"] = env["TEMP"] = tmp
    except Exception:
        pass
    return env


class LocalSandbox:
    """Host execution backend implementing the Sandbox protocol."""

    def __init__(self, workdir: str | Path) -> None:
        self.workdir = Path(workdir)

    async def start(self) -> None:
        await asyncio.to_thread(lambda: self.workdir.mkdir(parents=True, exist_ok=True))

    async def run(self, command: str, cwd: str = ".", timeout: int = 60) -> ExecResult:
        """Execute `command` in the jail. The process group dies with this call on BOTH abnormal exits:
        timeout (killed, reported as exit 124) and CANCELLATION. Handling only the timeout left the OS
        process orphaned — cancelling a coroutine never kills the process it spawned — and the
        CancelledError is re-raised after the kill, or a barge-in's teardown wait collides two turns.

        The deadline is spent on the COMMAND, and a command is over when its process exits — NOT when
        its pipes reach EOF, which is what `communicate()` waits for. Anything left running inherits
        those pipes, so `sleep 400 &` pinned communicate() for the whole budget and was then killed, and
        the trail read "timed out" for a command that returned in milliseconds. Redirecting is no
        defence under uvloop. Hence `_await_exit`, which watches the process rather than its pipes."""
        run_dir = validate_within_dir(cwd, self.workdir) if cwd not in (".", "") else self.workdir
        proc = await self._spawn(command, run_dir)
        out, err = bytearray(), bytearray()
        exited = asyncio.create_task(proc.wait())
        pumps = [asyncio.create_task(_pump(proc.stdout, out)),
                 asyncio.create_task(_pump(proc.stderr, err))]
        try:
            code = await _await_exit(proc, exited, timeout)
        except asyncio.TimeoutError:
            for t in (*pumps, exited):
                t.cancel()
            await self._terminate(proc)
            return ExecResult(stdout="", stderr=f"timed out after {timeout}s and was killed", exit_code=124)
        except asyncio.CancelledError:
            for t in (*pumps, exited):
                t.cancel()
            await self._terminate(proc)
            raise
        await asyncio.wait(pumps, timeout=_DRAIN_GRACE)
        for t in (*pumps, exited):
            t.cancel()
        return ExecResult(
            stdout=out.decode("utf-8", "replace"),
            stderr=readable_stderr(err.decode("utf-8", "replace")),
            exit_code=code if code is not None else -1,
        )

    async def _spawn(self, command: str, run_dir: Path):
        """Start `command` under a NAMED interpreter, with everything `run` needs to supervise it.

        POSIX is untouched: `create_subprocess_shell` is `/bin/sh -c`, which is the shell the prompt
        teaches and the shell the approval gate parses for. Windows is the branch that had to change —
        the same call there means cmd.exe via COMSPEC, an interpreter nobody chose and nobody taught."""
        kwargs = dict(
            cwd=str(run_dir),
            env=_scrubbed_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # own process group → we can kill the whole tree (POSIX only)
        )
        if is_windows_host():
            return await asyncio.create_subprocess_exec(*_powershell_argv(command), **kwargs)
        return await asyncio.create_subprocess_shell(command, **kwargs)

    async def run_code(self, code: str, lang: str = "python", timeout: int = 60) -> ExecResult:
        await self.write("_run.py", code.encode("utf-8"))
        # The active interpreter (venv-aware, shlex-safe). PowerShell PRINTS a quoted path instead
        # of running it, so there the call operator goes in front.
        call = "& " if is_windows_host() else ""
        return await self.run(f'{call}"{sys.executable}" _run.py', timeout=timeout)

    async def read(self, path: str) -> bytes:
        target = validate_within_dir(path, self.workdir)
        return await asyncio.to_thread(target.read_bytes)

    async def write(self, path: str, data: bytes) -> None:
        target = validate_within_dir(path, self.workdir)
        await asyncio.to_thread(lambda: target.parent.mkdir(parents=True, exist_ok=True))
        await asyncio.to_thread(target.write_bytes, data)

    async def kill(self) -> None:
        # Nothing persistent to tear down (no container); per-run processes are killed on timeout.
        return None

    async def _terminate(self, proc) -> None:
        """SIGTERM the process group, then SIGKILL after a grace period.

        Windows has neither `killpg` nor the process group `start_new_session` asks for, so the
        whole-tree kill is a POSIX capability and the fallback reaches only the child. Calling killpg
        anyway raised AttributeError, which this method does not catch — a timeout took the turn down
        instead of killing the runaway, on the one path with nothing left to catch it.

        Escalation keys off the GROUP, not the direct child: `sh -c 'ignores_term & sleep 100'` lets
        proc.wait() return while the grandchild that trapped SIGTERM runs on. Signal 0 asks whether
        anyone is left. A cancel during the grace period is absorbed and re-raised once the tree is down."""
        if not hasattr(os, "killpg"):
            for stop in (proc.terminate, proc.kill):
                try:
                    stop()
                except ProcessLookupError:
                    return
                try:
                    await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE)
                    return
                except asyncio.TimeoutError:
                    continue
            return
        cancelled: BaseException | None = None
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                break
            try:
                await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=_KILL_GRACE)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError as exc:
                cancelled = exc
                continue
            if not _group_alive(proc.pid):
                break
        if cancelled is not None:
            raise cancelled
