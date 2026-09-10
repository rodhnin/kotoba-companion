"""She has to start on Windows, and one unguarded import used to stop every command there.

A module-scope POSIX-only import turned a plain version check into a hard `RuntimeError`
blaming the toolset for a missing header. Smaller bugs rode with it: an unguarded signal tuple,
an unresolvable subprocess name, stray POSIX imports, and telling Windows to install what it had.

Windows is simulated with a meta-path hook blocking the POSIX modules CPython omits on win32;
entry points run in a subprocess so the hook can't outlive its test. A global platform override
was rejected -- a Windows-only path type refuses to instantiate on Linux, killing the harness first.
"""
from __future__ import annotations

from kotoba import DIST_NAME

import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest
from conftest import needs_posix_terminal

from kotoba.cli import doctor, host, serve
from kotoba.core import atomic_file, perms

# Every POSIX-only module CPython does not ship on win32 and that this package touches anywhere.
BLOCKED = ("fcntl", "termios", "tty", "pty", "pwd", "grp", "resource")

_PRELUDE = f'''
import sys
from importlib.abc import MetaPathFinder

class _NoPosix(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {BLOCKED!r}:
            raise ModuleNotFoundError("No module named %r" % fullname, name=fullname)
        return None

for _name in list(sys.modules):
    if _name.split(".")[0] in {BLOCKED!r}:
        del sys.modules[_name]
sys.meta_path.insert(0, _NoPosix())
'''


def _as_windows(body: str, tmp_path: Path, *, timeout: float = 180.0) -> subprocess.CompletedProcess:
    """Run `body` in a fresh interpreter that cannot import the POSIX terminal or locking modules.

    HOME, the database and every configured path go to `tmp_path`: these run the real entry points,
    and the one thing a test of a first run must never do is land on somebody's real conversation."""
    env = dict(os.environ)
    env.update(
        HOME=str(tmp_path), USERPROFILE=str(tmp_path),
        DATABASE_URL=f"sqlite:///{tmp_path / 'throwaway.db'}",
        KOTOBA_HOME=str(tmp_path / ".kotoba"),
        KOTOBA_SETTINGS=str(tmp_path / ".kotoba" / "settings.yaml"),
        KOTOBA_KEYSTORE_KEY_FILE=str(tmp_path / ".kotoba" / ".keystore_key"),
        NO_COLOR="1", PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
    )
    env.pop("OPENAI_API_KEY", None)
    return subprocess.run([sys.executable, "-c", _PRELUDE + textwrap.dedent(body)],
                          capture_output=True, text=True, timeout=timeout, env=env, cwd=str(tmp_path))


# --- the one that killed everything ---------------------------------------------------------------

def test_the_toolset_loads_with_no_fcntl_to_import(tmp_path):
    """The whole defect in one assertion: discovery is what raised, and the words it raised blamed the
    toolset for a missing POSIX header."""
    done = _as_windows('''
        from kotoba.tools import TOOL_REGISTRY
        from kotoba.core import atomic_file
        assert atomic_file.fcntl is None, "the blocker did not take"
        print("TOOLS", len(TOOL_REGISTRY))
    ''', tmp_path)
    assert done.returncode == 0, done.stderr
    assert "crippled toolset" not in done.stderr
    assert int(done.stdout.split("TOOLS")[1]) > 20, done.stdout


@pytest.mark.parametrize("argv, expected", [
    (["--version"], 0),
    (["setup", "--help"], 0),
    (["serve", "--help"], 0),
])
def test_the_basic_entry_points_start_with_no_posix_modules(tmp_path, argv, expected):
    done = _as_windows(f'''
        from kotoba.cli.__main__ import main
        raise SystemExit(main({argv!r}))
    ''', tmp_path)
    assert done.returncode == expected, done.stderr
    assert "Traceback" not in done.stderr, done.stderr


def test_doctor_runs_and_reports_the_platform_with_no_posix_modules(tmp_path):
    """Doctor is the command a stranger runs when nothing else worked, so it is the one that must not
    be the thing that breaks. It exits 1 here for want of a key, which is a finding, not a crash."""
    done = _as_windows('''
        from kotoba.cli.__main__ import main
        raise SystemExit(main(["doctor"]))
    ''', tmp_path)
    assert "Traceback" not in done.stderr, done.stderr
    assert "kotoba doctor" in done.stdout
    assert "platform" in done.stdout and "terminal" in done.stdout
    assert "no interactive terminal" in done.stdout, done.stdout


def test_once_answers_with_no_posix_modules(tmp_path):
    """No key is configured, so what is proved is that she REACHES her own answer instead of an import
    error — the turn runs, the words come back, and the exit code is her own."""
    done = _as_windows('''
        from kotoba.cli.__main__ import main
        raise SystemExit(main(["--once", "hola"]))
    ''', tmp_path)
    assert "Traceback" not in done.stderr, done.stderr
    assert "fcntl" not in done.stderr and "crippled" not in done.stderr
    assert done.stdout.strip(), "she said nothing at all"


def test_the_render_modules_import_and_decline(tmp_path):
    """`caps` and `keys` belong to a terminal that does not run here — but `kotoba setup` IMPORTS caps
    to ask what the terminal can do, so an unimportable module cost first run its chrome on an install
    that had rich the whole time. Importing and answering "nothing" is the contract."""
    done = _as_windows('''
        from kotoba.cli.render import caps
        from kotoba.cli.input import keys
        c = caps.detect()
        assert isinstance(c, caps.Caps)
        assert caps.cursor_pos()[:2] == (-1, -1)
        assert keys.read_input(timeout=0.01) == ""
        with keys.Keys(lambda _c: None) as k:
            assert not k.enabled
        print("OK")
    ''', tmp_path)
    assert done.returncode == 0, done.stderr
    assert "OK" in done.stdout


# --- the lock, where Windows has no flock ----------------------------------------------------------

class _FakeMsvcrt:
    LK_NBLCK, LK_UNLCK = 2, 0

    def __init__(self, fail_with: int | None = None) -> None:
        self.calls: list[tuple[int, int, int]] = []
        self.fail_with = fail_with

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        self.calls.append((fd, mode, nbytes))
        if self.fail_with is not None and mode == self.LK_NBLCK:
            raise OSError(self.fail_with, "locking violation")


def _windows_lock(monkeypatch, msvcrt) -> None:
    monkeypatch.setattr(atomic_file, "fcntl", None)
    monkeypatch.setattr(atomic_file, "msvcrt", msvcrt)


def test_the_windows_lock_pins_the_range_it_takes(tmp_path, monkeypatch):
    """msvcrt locks at the CURRENT file position, unlike flock, which locks the whole description. The
    seek is what makes the range the same one byte every time instead of wherever the fd was left."""
    fake = _FakeMsvcrt()
    _windows_lock(monkeypatch, fake)
    fd = os.open(str(tmp_path / "x.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.lseek(fd, 500, os.SEEK_SET)
        atomic_file._lock(fd)
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0, "the lock was taken at a stale position"
        assert fake.calls == [(fd, fake.LK_NBLCK, atomic_file._RANGE)]
        atomic_file._unlock(fd)
        assert fake.calls[-1] == (fd, fake.LK_UNLCK, atomic_file._RANGE)
    finally:
        os.close(fd)


def test_a_locking_violation_is_contention_and_anything_else_is_not(tmp_path, monkeypatch):
    """`_acquire` retries a BlockingIOError to its deadline and DEGRADES on any other OSError. Windows
    reports both through the same call, so the errno is what separates "wait" from "this filesystem
    will never answer" — mapped wrong, an unsupported volume would hang for the whole 15 seconds."""
    import errno

    fd = os.open(str(tmp_path / "y.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _windows_lock(monkeypatch, _FakeMsvcrt(fail_with=errno.EACCES))
        with pytest.raises(BlockingIOError):
            atomic_file._lock(fd)

        _windows_lock(monkeypatch, _FakeMsvcrt(fail_with=errno.EINVAL))
        with pytest.raises(OSError) as raised:
            atomic_file._lock(fd)
        assert not isinstance(raised.value, BlockingIOError)
        atomic_file._acquire(fd, "y")   # degrades rather than raising, exactly as flock's OSError does
    finally:
        os.close(fd)


def test_a_platform_with_neither_lock_degrades_instead_of_waiting(tmp_path, monkeypatch):
    monkeypatch.setattr(atomic_file, "fcntl", None)
    monkeypatch.setattr(atomic_file, "msvcrt", None)
    fd = os.open(str(tmp_path / "z.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        with pytest.raises(OSError):
            atomic_file._lock(fd)
        atomic_file._acquire(fd, "z")
        atomic_file._unlock(fd)
    finally:
        os.close(fd)


def test_exclusive_still_serializes_two_threads_here(tmp_path):
    """The POSIX path is untouched, and this is the property it was written for: threads count, not
    just processes, and two of them opening the sentinel separately must serialize."""
    order: list[str] = []
    inside = threading.Semaphore(0)
    release = threading.Event()
    target = tmp_path / "shared.json"

    def hold() -> None:
        with atomic_file.exclusive(target):
            order.append("in")
            inside.release()
            release.wait(5)
            order.append("out")

    def contend() -> None:
        with atomic_file.exclusive(target):
            order.append("second")

    first = threading.Thread(target=hold)
    first.start()
    inside.acquire(timeout=5)
    second = threading.Thread(target=contend)
    second.start()
    release.set()
    first.join(10)
    second.join(10)

    assert order == ["in", "out", "second"], order


# --- permissions, where chmod writes no ACL --------------------------------------------------------

def test_restrict_file_does_nothing_off_windows(tmp_path, monkeypatch):
    """The POSIX path is the chmod beside every call and nothing else, so this must never run there.

    The platform is FORCED, not inherited: read off the host, this asserted the Windows branch on
    Windows and failed there — a test whose whole subject is the other branch."""
    ran: list[list[str]] = []
    monkeypatch.setattr(perms, "_WINDOWS", False)
    monkeypatch.setattr(perms.subprocess, "run", lambda argv, **kw: ran.append(argv))
    perms.restrict_file(tmp_path / "anything")
    assert ran == []


class _Done:
    def __init__(self, code: int = 0, out: str = "", err: str = ""):
        self.returncode, self.stdout, self.stderr = code, out, err


_SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"
_WHOAMI = f'"desktop\\ada","{_SID}"\n'


def test_restrict_file_names_one_account_and_drops_what_was_inherited(tmp_path, monkeypatch):
    """`/inheritance:r` is half the point: on a clone under `C:\\` the parent hands down access this
    would otherwise merely add to. `/grant:r` replaces rather than appends."""
    seen: list[list[str]] = []

    def fake_run(argv, **kw):
        seen.append(argv)
        return _Done(out=_WHOAMI) if argv[0] == "whoami" else _Done()

    monkeypatch.setattr(perms, "_WINDOWS", True)
    monkeypatch.setattr(perms, "_HOLDER", None)
    monkeypatch.setattr(perms.subprocess, "run", fake_run)
    monkeypatch.setenv("USERDOMAIN", "WORKGROUP")
    target = tmp_path / "kotoba.db"
    perms.restrict_file(target)

    assert seen[-1] == ["icacls", str(target), "/inheritance:r", "/grant:r", f"*{_SID}:(F)"]


def test_the_account_is_named_by_sid_and_not_out_of_the_environment(tmp_path, monkeypatch):
    """USERDOMAIN is the WORKGROUP on a standalone machine, not the computer, so `WORKGROUP\\ada`
    named no account and icacls answered 1332 — every file kept the ACL it inherited, and the only
    trace was a log line. Measured on a machine where that is exactly what happened."""
    granted: list[str] = []

    def fake_run(argv, **kw):
        if argv[0] == "whoami":
            return _Done(out=_WHOAMI)
        granted.append(argv[-1])
        return _Done(code=1332, err="No mapping between account names and security IDs was done.")

    monkeypatch.setattr(perms, "_WINDOWS", True)
    monkeypatch.setattr(perms, "_HOLDER", None)
    monkeypatch.setattr(perms.subprocess, "run", fake_run)
    monkeypatch.setenv("USERDOMAIN", "WORKGROUP")
    monkeypatch.setenv("USERNAME", "ada")
    perms.restrict_file(tmp_path / "kotoba.db")

    assert granted == [f"*{_SID}:(F)"], "the environment named the account instead of the token"
    assert "WORKGROUP" not in granted[0]


def test_a_machine_whose_whoami_says_nothing_falls_back_to_the_bare_name(tmp_path, monkeypatch):
    """icacls resolves a bare name against the default domain, which is right here and was never the
    bug. Without this the account has no name at all and the grant is skipped entirely."""
    granted: list[str] = []

    def fake_run(argv, **kw):
        if argv[0] == "whoami":
            return _Done(code=1)
        granted.append(argv[-1])
        return _Done()

    monkeypatch.setattr(perms, "_WINDOWS", True)
    monkeypatch.setattr(perms, "_HOLDER", None)
    monkeypatch.setattr(perms.subprocess, "run", fake_run)
    monkeypatch.setenv("USERNAME", "ada")
    perms.restrict_file(tmp_path / "kotoba.db")

    assert granted == ["ada:(F)"]


def test_the_account_is_resolved_once_however_many_files_are_written(tmp_path, monkeypatch):
    """`restrict_file` runs on every atomic write, and a second process per write is a cost nobody
    asked for — the answer cannot change while this one lives."""
    calls: list[str] = []

    def fake_run(argv, **kw):
        calls.append(argv[0])
        return _Done(out=_WHOAMI) if argv[0] == "whoami" else _Done()

    monkeypatch.setattr(perms, "_WINDOWS", True)
    monkeypatch.setattr(perms, "_HOLDER", None)
    monkeypatch.setattr(perms.subprocess, "run", fake_run)
    for n in range(5):
        perms.restrict_file(tmp_path / f"f{n}")

    assert calls.count("whoami") == 1, calls
    assert calls.count("icacls") == 5, calls


def test_a_failed_icacls_is_logged_and_never_raised(tmp_path, monkeypatch, caplog):
    """A machine that cannot run icacls must still be able to save a setting. Silence is what this
    module exists to end, so the failure is loud in the log and invisible to the caller."""
    def fake_run(argv, **kw):
        return _Done(out=_WHOAMI) if argv[0] == "whoami" else _Done(code=5, err="Access is denied.")

    monkeypatch.setattr(perms, "_WINDOWS", True)
    monkeypatch.setattr(perms, "_HOLDER", None)
    monkeypatch.setattr(perms.subprocess, "run", fake_run)
    with caplog.at_level("WARNING", logger="kotoba"):
        perms.restrict_file(tmp_path / "kotoba.db")     # returns; never raises
    said = [r.getMessage() for r in caplog.records]
    assert any("could not restrict" in m and "Access is denied." in m for m in said), said


def test_every_chmod_that_guards_something_has_an_acl_beside_it(tmp_path, monkeypatch):
    """Three places used `os.chmod(path, 0o600)` for a real property and got one read-only bit on
    Windows. A test that only proved `perms` compiles would prove nothing about them, so this asserts
    each of the three actually CALLS it, with the path it was protecting."""
    asked: list[str] = []
    monkeypatch.setattr(perms, "restrict_file", lambda p: asked.append(str(p)))
    monkeypatch.setattr(atomic_file.perms, "restrict_file", lambda p: asked.append(str(p)))

    from kotoba.core import keystore
    from kotoba.db import database

    monkeypatch.setattr(keystore.perms, "restrict_file", lambda p: asked.append(str(p)))
    monkeypatch.setattr(database.perms, "restrict_file", lambda p: asked.append(str(p)))

    target = tmp_path / "notes.md"
    atomic_file.write_text(target, "hello")
    assert any(a.startswith(str(tmp_path)) for a in asked), "the atomic writer set no ACL"

    asked.clear()
    database.Database(f"sqlite:///{tmp_path / 'db.sqlite'}")._prepare_file()
    assert asked == [str(tmp_path / "db.sqlite")], asked

    asked.clear()
    monkeypatch.setenv("KOTOBA_KEYSTORE_KEY_FILE", str(tmp_path / "kek"))
    monkeypatch.delenv("KOTOBA_MASTER_KEY", raising=False)
    keystore._master_key(create=True)
    assert asked == [str(tmp_path / "kek")], asked


# --- serve: the four smaller ones ------------------------------------------------------------------

def test_termination_is_caught_where_there_is_no_sighup(monkeypatch):
    """The tuple `(signal.SIGTERM, signal.SIGHUP)` was built BEFORE the try that already said "or the
    platform has no such signal", so the AttributeError was raised one expression too early."""
    monkeypatch.delattr(signal, "SIGHUP", raising=False)
    caught = serve._catch_termination()
    try:
        assert [sig for _, sig in caught] == [signal.SIGTERM]
    finally:
        for handler, sig in caught:
            signal.signal(sig, handler)


def test_the_frontend_command_names_the_command_processor_on_windows(monkeypatch):
    """`npm` is `npm.cmd`, which CreateProcess will not run — while `shutil.which` finds it through
    PATHEXT, so `frontend_blocker` said yes and the spawn then failed."""
    monkeypatch.setattr(serve, "_WINDOWS", False)
    assert serve.frontend_command(3000)[0] == "npm"

    monkeypatch.setattr(serve, "_WINDOWS", True)
    monkeypatch.setenv("COMSPEC", r"C:\WINDOWS\system32\cmd.exe")
    argv = serve.frontend_command(3210)
    assert argv[:4] == [r"C:\WINDOWS\system32\cmd.exe", "/c", "npm", "run"]
    assert "3210" in argv
    # Unquoted and unresolved on purpose: `cmd /c` strips the outer quote pair off its first token,
    # which is how a real `C:\Program Files\nodejs\npm.cmd` would come apart at the space.
    assert not any(a.endswith(".cmd") for a in argv[2:])


def test_each_child_gets_its_own_group_on_either_platform(monkeypatch):
    """`on either platform` was the claim and the host was the input: on Windows the POSIX half of
    this ran against the Windows branch. Both halves are forced now, so it tests what it says."""
    monkeypatch.setattr(serve, "_WINDOWS", False)
    assert serve._own_group() == {"start_new_session": True}
    monkeypatch.setattr(serve, "_WINDOWS", True)
    assert "creationflags" in serve._own_group()
    assert "start_new_session" not in serve._own_group()


def test_the_windows_teardown_asks_the_group_then_walks_the_tree(monkeypatch):
    """Ending `cmd.exe` alone leaves the Next worker it started holding port 3000. CTRL_BREAK reaches
    the whole group; `taskkill /T` is the `killpg` of the impolite half."""
    monkeypatch.setattr(serve, "_WINDOWS", True)
    monkeypatch.setattr(signal, "CTRL_BREAK_EVENT", 1, raising=False)
    signalled: list[tuple[int, int]] = []
    ran: list[list[str]] = []
    monkeypatch.setattr(serve.os, "kill", lambda pid, sig: signalled.append((pid, sig)))
    monkeypatch.setattr(serve.subprocess, "run", lambda argv, **kw: ran.append(argv))

    class _Child:
        pid = 4321

        def poll(self):
            return None

    serve._ask_group_to_stop(_Child())
    assert signalled == [(4321, signal.CTRL_BREAK_EVENT)]
    serve._kill_group(_Child())
    assert ran == [["taskkill", "/F", "/T", "/PID", "4321"]]


# --- the browser -----------------------------------------------------------------------------------

def test_the_browser_waits_for_the_port_and_then_opens_it(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(serve.webbrowser, "open", lambda url: bool(opened.append(url)) or True)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        serve._open_when_ready(f"http://127.0.0.1:{port}", port, threading.Event())
    finally:
        listener.close()
    assert opened == [f"http://127.0.0.1:{port}"]


def test_nothing_opens_at_a_port_that_never_came_up(monkeypatch, capsys):
    """A window on a dead port is worse than a sentence."""
    opened: list[str] = []
    monkeypatch.setattr(serve.webbrowser, "open", lambda url: bool(opened.append(url)) or True)
    monkeypatch.setattr(serve, "_OPEN_WAIT", 0.3)
    monkeypatch.setattr(serve, "_POLL", 0.05)
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    port = dead.getsockname()[1]
    dead.close()

    serve._open_when_ready(f"http://127.0.0.1:{port}", port, threading.Event())
    assert opened == []
    assert "nothing answered" in capsys.readouterr().err


def test_nothing_opens_when_the_frontend_is_blocked(monkeypatch, capsys):
    opened: list[str] = []
    monkeypatch.setattr(serve.webbrowser, "open", lambda url: bool(opened.append(url)) or True)
    serve._open_web_ui("http://127.0.0.1:3000", 3000, "npm is not installed", 8000, threading.Event())
    said = capsys.readouterr().err
    assert opened == []
    assert "no browser to open" in said and "8000/health" in said


def test_shutdown_releases_the_waiting_browser(monkeypatch):
    """The opener must never sit in front of Ctrl+C."""
    opened: list[str] = []
    monkeypatch.setattr(serve.webbrowser, "open", lambda url: bool(opened.append(url)) or True)
    monkeypatch.setattr(serve, "_OPEN_WAIT", 30.0)
    stopping = threading.Event()
    stopping.set()
    serve._open_when_ready("http://127.0.0.1:1", 1, stopping)
    assert opened == []


def test_serve_only_opens_a_browser_when_asked(monkeypatch):
    """`serve` is a server command on every platform: quiet unless given `--open`."""
    from kotoba.cli import __main__ as entry

    asked: dict[str, object] = {}
    monkeypatch.setattr(serve, "run", lambda **kw: asked.update(kw) or 0)
    entry._main(["serve", "--port", "8123"])
    assert asked == {"port": 8123, "web_port": 3000, "open_browser": False}
    entry._main(["serve", "--open"])
    assert asked["open_browser"] is True


# --- what bare `kotoba` does where her terminal cannot run -----------------------------------------

def test_bare_kotoba_starts_the_web_ui_where_the_terminal_cannot_run(monkeypatch, capsys):
    """Telling somebody their command is unsupported while a working path sits one line away is a bad
    answer. Explicit commands keep their meaning; only the argumentless form changes."""
    from kotoba.cli import __main__ as entry

    asked: dict[str, object] = {}
    monkeypatch.setattr(entry.host, "terminal_ui_available", lambda: False)
    monkeypatch.setattr(entry.host, "missing_terminal_modules", lambda: ["termios", "tty"])
    monkeypatch.setattr(serve, "run", lambda **kw: asked.update(kw) or 0)

    code = entry._main([])
    said = capsys.readouterr().err
    assert code == 0
    assert asked == {"open_browser": True}
    assert "termios" in said and "web UI" in said and "Ctrl+C" in said


def test_a_terminal_only_flag_is_named_rather_than_swallowed(monkeypatch, capsys):
    from kotoba.cli import __main__ as entry

    monkeypatch.setattr(entry.host, "terminal_ui_available", lambda: False)
    monkeypatch.setattr(entry.host, "missing_terminal_modules", lambda: ["termios", "tty"])
    monkeypatch.setattr(serve, "run", lambda **kw: 0)
    entry._main(["--sessions"])
    assert "--sessions" in capsys.readouterr().err


def test_the_missing_extra_still_gets_its_own_answer(monkeypatch, capsys):
    """Two situations, two sentences. Where the terminal COULD run and the import failed anyway, the
    `cli` extra is genuinely what is missing — and that is the message Windows used to get too.

    Refusing the module takes BOTH removals below, and the first version of this test had only one: a
    submodule any earlier test imported is also an ATTRIBUTE of its package, so `from kotoba.cli import
    app` reads it from there without ever consulting sys.modules or a finder. Run alone it passed;
    inside the suite it sailed past the branch it was written for and launched the real terminal."""
    import kotoba.cli
    from kotoba.cli import __main__ as entry

    class _NoApp:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "kotoba.cli.app":
                raise ImportError("No module named 'rich'")
            return None

    monkeypatch.setattr(entry.host, "terminal_ui_available", lambda: True)
    monkeypatch.delitem(sys.modules, "kotoba.cli.app", raising=False)
    monkeypatch.delattr(kotoba.cli, "app", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_NoApp(), *sys.meta_path])
    code = entry._interactive(entry._parse([]))
    assert code == 1
    assert f'pip install "{DIST_NAME}[cli]"' in capsys.readouterr().err


# --- doctor says which machine this is -------------------------------------------------------------

def test_doctor_names_the_platform(monkeypatch):
    check = doctor._machine_name()
    assert check.status == "ok"
    assert check.name == "platform"
    assert host.describe() == check.detail
    assert "Python" in check.detail


@needs_posix_terminal
def test_doctor_says_the_interactive_terminal_cannot_run_here(monkeypatch):
    """The one command whose job is to explain why she will not run was the one that would not say it —
    and the extras section below reports `cli ok installed` in good faith while nothing can open."""
    assert doctor._terminal().status == "ok"

    monkeypatch.setattr(doctor.host, "missing_terminal_modules", lambda: ["termios", "tty"])
    check = doctor._terminal()
    assert check.status == "warn"
    assert "termios" in check.detail
    assert "web UI" in check.detail
    assert "`cli` extra" in check.detail
    assert "--once" in check.detail and "serve" in check.detail


@needs_posix_terminal
def test_the_terminal_question_is_asked_of_the_import_system(monkeypatch):
    """Not of `sys.platform`: the modules are the real condition, and a name comparison would have to
    be kept in step with every platform that turns out to lack them."""
    assert host.terminal_ui_available() is True
    monkeypatch.setattr(host, "_present", lambda name: False)
    assert host.missing_terminal_modules() == ["termios", "tty"]
    assert host.terminal_ui_available() is False


# --- her third face has to start there too --------------------------------------------------------

def _discord_installed() -> bool:
    import importlib.util

    return all(importlib.util.find_spec(m) for m in ("discord", "nacl", "davey"))


@pytest.mark.skipif(not _discord_installed(), reason="the discord extra is not installed")
def test_every_module_of_the_discord_surface_imports_with_no_posix(tmp_path):
    """Nineteen modules and nine tools, each a chance to reach for `fcntl` through a helper.

    Imported one by one rather than through the package, so a failure names the file rather than the
    first import in `__init__`."""
    done = _as_windows('''
        import importlib, pkgutil
        import kotoba.discord as pkg

        names = sorted(m.name for m in pkgutil.iter_modules(pkg.__path__))
        for name in names:
            importlib.import_module("kotoba.discord." + name)
        import kotoba.tools.action as actions
        tools = sorted(m.name for m in pkgutil.iter_modules(actions.__path__)
                       if m.name.startswith("discord_"))
        for name in tools:
            importlib.import_module("kotoba.tools.action." + name)
        print("MODULES", len(names), "TOOLS", len(tools))
    ''', tmp_path)
    assert done.returncode == 0, done.stderr
    counts = [line for line in done.stdout.splitlines() if line.startswith("MODULES")]
    assert counts, done.stdout
    modules, tools = int(counts[0].split()[1]), int(counts[0].split()[3])
    assert modules >= 18 and tools >= 9, counts[0]


@pytest.mark.skipif(not _discord_installed(), reason="the discord extra is not installed")
def test_the_discord_subcommand_parses_where_the_terminal_cannot_open(tmp_path):
    """`kotoba discord` is one of the commands promised on Windows, so it must reach its own parser
    rather than the apology for a terminal it does not need."""
    done = _as_windows('''
        from kotoba.cli.__main__ import _parse

        args = _parse(["discord", "--guild", "17", "--guild", "18"])
        assert args.command == "discord" and args.guild == [17, 18], args
        print("PARSED")
    ''', tmp_path)
    assert done.returncode == 0, done.stderr
    assert "PARSED" in done.stdout


@pytest.mark.skipif(not _discord_installed(), reason="the discord extra is not installed")
def test_windows_is_not_told_to_install_a_system_library_it_cannot(tmp_path):
    """libopus ships inside the wheel on Windows and is loaded on demand, so the refusal that names it
    must not fire there — it used to, and the fix it asked for does not exist on that platform."""
    done = _as_windows('''
        import types
        import discord.opus as opus
        import kotoba.discord.speak as speak

        asked = []
        opus.is_loaded = lambda: False
        opus.load_opus = lambda name: asked.append(name)

        speak.os = types.SimpleNamespace(name="nt")
        speak.ensure_opus()
        assert asked == [], asked

        speak.os = types.SimpleNamespace(name="posix")
        speak.ensure_opus()
        assert asked and asked[0], "the POSIX side stopped naming a library at all"
        print("OPUS OK")
    ''', tmp_path)
    assert done.returncode == 0, done.stderr
    assert "OPUS OK" in done.stdout


# --- the escape codes a Windows console prints instead of obeying -------------------------------------

def test_colour_is_asked_for_before_it_is_written(monkeypatch):
    """Measured on a real Windows 11 console: `kotoba doctor` printed `←[32mok ←[0m platform` on every
    row. The codes are correct and the console simply arrives with them turned off, so the check has to
    be "will this paint", not "is this a terminal"."""
    import io

    from kotoba.cli import doctor, host

    class _Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(host, "ansi_available", lambda: False)
    assert "\033[" not in doctor._paint("ok", _Tty())

    monkeypatch.setattr(host, "ansi_available", lambda: True)
    assert "\033[" in doctor._paint("ok", _Tty())


def test_a_console_that_refuses_the_flag_is_not_an_error(monkeypatch):
    """The probe is best-effort by design: a handle that will not take the mode returns False and the
    report is printed plain, rather than a traceback where a diagnosis should be."""
    from kotoba.cli import host

    monkeypatch.setattr(host, "_ANSI", None)
    monkeypatch.setattr(host, "_enable_ansi", lambda: False)
    assert host.ansi_available() is False
    monkeypatch.setattr(host, "_ANSI", None)


def test_posix_never_pays_for_the_windows_probe(monkeypatch):
    """On anything but Windows the answer is yes without touching ctypes at all."""
    import os

    from kotoba.cli import host

    monkeypatch.setattr(os, "name", "posix")
    assert host._enable_ansi() is True


# --- what a failure SAYS on Windows ------------------------------------------------------------------

_CLIXML = (
    '#< CLIXML\r\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
    '<S S="Error">sh : The term \'sh\' is not recognized as the name of a cmdlet._x000D__x000A_</S>'
    '<S S="Error">    + FullyQualifiedErrorId : CommandNotFoundException_x000D__x000A_</S></Objs>'
)


def test_a_powershell_error_reaches_her_as_a_sentence_not_as_xml():
    """powershell.exe serialises ERROR RECORDS into CLIXML the moment stderr is a pipe. Measured on a
    real Windows run: a missing command came back as 275 characters of `<Objs Version="1.1.0.1" …>`,
    which is what the model was handed as the reason and what the screen printed."""
    from kotoba.core.sandbox.local import readable_stderr

    said = readable_stderr(_CLIXML)
    assert "CLIXML" not in said and "<Objs" not in said and "_x000D_" not in said
    assert "is not recognized as the name of a cmdlet" in said
    assert said.count("\n") >= 1, "the record's own lines are kept"


def test_ordinary_stderr_is_never_touched():
    """The POSIX half runs through the same call on every turn, and a shell error is not XML."""
    from kotoba.core.sandbox.local import readable_stderr

    for raw in ("ls: cannot access 'x': No such file or directory", "", "  ", "<Objs> without the head"):
        assert readable_stderr(raw) == raw


def test_an_unparseable_clixml_is_handed_over_whole_rather_than_swallowed():
    """Unreadable beats lost: the reason a command failed must reach somebody even in the shape it
    arrived in."""
    from kotoba.core.sandbox.local import readable_stderr

    for broken in ("#< CLIXML\r\n<Objs unclosed", "#< CLIXML\r\n", "#< CLIXML\r\n<Objs></Objs>"):
        assert readable_stderr(broken) == broken


# --- publishing over a file somebody else is holding -----------------------------------------------

def _flaky_replace(monkeypatch, refusals: int | None) -> list[int]:
    """`refusals=None` is a holder that never lets go — a count large enough to stand for one is not,
    because the retry spins with the pause patched out and outran ten thousand of them."""
    tries: list[int] = []
    real = os.replace

    def replace(src, dst):
        tries.append(1)
        if refusals is None or len(tries) <= refusals:
            raise PermissionError(5, "Acceso denegado")
        return real(src, dst)

    monkeypatch.setattr(atomic_file.os, "replace", replace)
    return tries


def test_a_replace_windows_refuses_is_retried_rather_than_lost(tmp_path, monkeypatch):
    """MoveFileEx answers ACCESS_DENIED while another handle holds the destination, and an ordinary
    Python reader in a second process is enough — the write raised where POSIX would have succeeded,
    which is a fact she reported saving and did not."""
    monkeypatch.setattr(atomic_file, "_WINDOWS", True)
    monkeypatch.setattr(atomic_file, "_REPLACE_PAUSE", 0)
    tries = _flaky_replace(monkeypatch, refusals=3)
    target = tmp_path / "data.json"
    atomic_file.write_text(target, "the fact")

    assert target.read_text(encoding="utf-8") == "the fact"
    assert len(tries) == 4


def test_the_retry_gives_up_and_leaves_no_temporary_behind(tmp_path, monkeypatch):
    """A holder that never lets go must still end as an error the caller sees, and the deadline is
    shorter than the lock's own so the waiter above it is not the one that reports the failure.

    Run in a THREAD with a wall-clock bound, because the failure this guards against is an unbounded
    retry: asserted inline, deleting the deadline made this test HANG rather than go red, and a job
    that hangs is worse than one that fails — nothing names the cause."""
    monkeypatch.setattr(atomic_file, "_WINDOWS", True)
    monkeypatch.setattr(atomic_file, "_REPLACE_PAUSE", 0)
    monkeypatch.setattr(atomic_file, "_REPLACE_WAIT", 0.05)
    _flaky_replace(monkeypatch, refusals=None)
    target = tmp_path / "data.json"
    raised: list[BaseException] = []

    def attempt() -> None:
        try:
            atomic_file.write_text(target, "the fact")
        except BaseException as e:      # noqa: BLE001 - the point is WHAT it raised, and that it did
            raised.append(e)

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    worker.join(10.0)

    assert not worker.is_alive(), "the retry never gave up; with no deadline this spins for ever"
    assert raised and isinstance(raised[0], PermissionError), f"gave up with {raised!r}"
    assert not target.exists()
    assert list(tmp_path.iterdir()) == [], "the temporary outlived the failure"


def test_the_soul_file_is_placed_through_the_same_refusable_rename(tmp_path, monkeypatch):
    """`_place` writes her personality at first run. Left on a bare `os.replace` it was the one write
    that could be refused on Windows with nothing catching it, and it is the file she boots from."""
    from kotoba.soul import loader

    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(atomic_file, "publish",
                        lambda src, dst: seen.append((src, dst)) or os.replace(src, dst))
    source = tmp_path / "from.md"
    source.write_text("hello", encoding="utf-8")
    loader._place(source, tmp_path / "default.md")

    assert seen and seen[0][1].endswith("default.md"), "the soul file went round `publish`"
    assert (tmp_path / "default.md").read_text(encoding="utf-8") == "hello"
    assert list(tmp_path.glob("*.part")) == [], "the temporary outlived the write"


def test_a_model_swap_goes_through_the_same_rename_and_says_where_the_old_one_went(tmp_path, monkeypatch):
    """Two renames, either refusable. When the rollback is refused too, the person is left with no
    model at all and the old one in a dot-named folder nothing sweeps — so it has to be NAMED."""
    from kotoba.core import model_install

    models = tmp_path / "models"
    (models / "mao").mkdir(parents=True)
    (models / "mao" / "old.model3.json").write_text("{}", encoding="utf-8")
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.setattr(model_install.model_library, "models_dir", lambda: models)

    calls: list[int] = []

    def refuse_the_second(src, dst):
        calls.append(1)
        if len(calls) == 1:
            return os.replace(src, dst)     # the old one is moved aside
        raise PermissionError(5, "Acceso denegado")

    monkeypatch.setattr(model_install, "publish", refuse_the_second)
    with pytest.raises(model_install.InstallError) as caught:
        model_install._publish(fresh, "mao")

    assert "replaced-" in str(caught.value), f"the folder it was left in is not named: {caught.value}"


def test_off_windows_the_same_refusal_is_the_callers_to_answer(tmp_path, monkeypatch):
    """POSIX rename has no such window, so a refusal there is a real permission problem and retrying
    it would only delay the report by five seconds."""
    monkeypatch.setattr(atomic_file, "_WINDOWS", False)
    tries = _flaky_replace(monkeypatch, refusals=1)
    with pytest.raises(PermissionError):
        atomic_file.write_text(tmp_path / "data.json", "the fact")

    assert len(tries) == 1


# --- the console the operator actually watches ------------------------------------------------------

def test_a_browser_asking_for_the_favicon_is_answered_rather_than_refused():
    """Every traceback in the operator's console followed a `GET /favicon.ico 404`: a browser asks for
    it whatever the page declares, and the raw file previews declare nothing. The 404 made it drop the
    connection mid-shutdown, and Windows turned that into six lines of asyncio internals."""
    from fastapi.testclient import TestClient

    from kotoba.core import frontend
    from kotoba.server import app

    with TestClient(app) as client:
        answer = client.get("/favicon.ico")

    assert answer.status_code in (200, 204), answer.text[:200]
    if frontend.resolve("/icon.svg") is not None:
        assert answer.status_code == 200
        assert answer.headers["content-type"].startswith("image/svg+xml")


def test_the_favicon_does_not_lock_the_operator_out_of_their_own_login(monkeypatch):
    """Gated, this answered 401 AND spent one of the twenty auth failures a minute. A browser asks for
    it unbidden, on the login page itself, so twenty reloads met the RIGHT password with 429: the tab
    shutting its own operator out. The bytes are the already-public icon; nothing was being protected."""
    from fastapi.testclient import TestClient

    from kotoba.core import gate
    from kotoba.server import _FAILED_AUTH, app

    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "s3cret")
    _FAILED_AUTH.clear()
    with TestClient(app) as client:
        answers = [client.get("/favicon.ico").status_code for _ in range(20)]
        letting_in = client.post("/gate", json={"password": "s3cret"})

    assert set(answers) <= {200, 204}, f"a gated install refused its own favicon: {sorted(set(answers))}"
    assert not _FAILED_AUTH, f"asking for an icon counted as failing to log in: {dict(_FAILED_AUTH)}"
    assert letting_in.status_code == 200, (
        f"the right password was refused after twenty favicon requests: {letting_in.status_code}")
    assert gate.enabled(), "the password was not in force, so this measured an open install"


def test_a_reset_while_the_proactor_shuts_down_is_not_reported_as_a_crash(monkeypatch):
    """The reset arrives in a callback no `except` of ours can reach, so it is dropped at the loop's
    own handler — and ONLY that shape, or a real failure would go the same silent way."""
    import asyncio

    from kotoba.server import _quieten_the_proactor

    seen: list = []
    loop = asyncio.new_event_loop()
    try:
        loop.set_exception_handler(lambda _l, ctx: seen.append(ctx))
        _quieten_the_proactor(loop)
        handler = loop.get_exception_handler()
        handler(loop, {"message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
                       "exception": ConnectionResetError(10054, "forcibly closed")})
        handler(loop, {"message": "Task exception was never retrieved",
                       "exception": ConnectionResetError(10054, "forcibly closed")})
        handler(loop, {"message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
                       "exception": ValueError("something of ours")})
    finally:
        loop.close()

    assert [c["message"] for c in seen] == ["Task exception was never retrieved",
                                            "Exception in callback _ProactorBasePipeTransport._call_connection_lost()"]
    assert isinstance(seen[-1]["exception"], ValueError)


def test_a_model_wears_the_same_face_on_either_platform(tmp_path, monkeypatch):
    """Two entry files at one depth, and the docstring promises "the same one every time". Tie-broken
    on the Path object that promise stopped at the platform boundary — Windows compares those
    case-folded, so the same download wore a different face there."""
    from kotoba.core import model_install

    staged = tmp_path / "staged"
    (staged / "a").mkdir(parents=True)
    for name in ("Zebra.model3.json", "apple.model3.json"):
        (staged / "a" / name).write_text("{}", encoding="utf-8")

    chosen = model_install._entry_file(staged).name

    assert chosen == "Zebra.model3.json", (
        f"upper-case sorts first by text and last under a case fold; got {chosen}")
