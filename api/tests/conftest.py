"""Shared test fixtures."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading

import pytest
from rich.style import Style

from kotoba.cli import host

#: The interactive terminal is POSIX and stays that way — the product promises `setup`, `doctor`,
#: `--once` and `serve` on Windows and nothing else. The condition is the PRODUCT's own probe rather
#: than a platform string, so this skips exactly when she would refuse to open the TUI, which is what
#: makes it provable here: block the modules on Linux and the guard fires without a Windows box.
needs_posix_terminal = pytest.mark.skipif(
    not host.terminal_ui_available(),
    reason="termios and tty, the POSIX terminal layer the interactive UI is built on")


def posix_only(what: str):
    """Skip a test whose SUBJECT is POSIX — a 0600 permission bit, a process group, a `/bin/sh` path.

    Naming the thing rather than the platform is the point: "needs Windows to be excused" invites a
    skip for anything awkward, and a whole leg that skips itself proves nothing at all."""
    return pytest.mark.skipif(sys.platform == "win32", reason=f"needs {what}")


def obeys_permissions(what: str):
    """Skip a test whose SUBJECT is a permission bit, when the user running it does not obey one.

    `chmod 0500` and then expecting the write to fail proves nothing as root: the bit is set, the
    write succeeds anyway, and the test reports a defect that is not there. Containers commonly run
    as root, so this is not a hypothetical."""
    return pytest.mark.skipif(
        getattr(os, "geteuid", lambda: 1)() == 0, reason=f"root ignores {what}")


#: Whether a test may leave this machine — download a package, pull a container image, call somebody
#: else's host. A clone that types `pytest` must do none of it, so the `network` marker deselects
#: these. This is the second lock, because a `-m` typed on the command line replaces the first one
#: silently, and nobody should download and RUN a stranger's package because of a flag.
NETWORK_ALLOWED = os.getenv("KOTOBA_ALLOW_NETWORK", "").strip().lower() in ("1", "true", "yes")

needs_network = pytest.mark.skipif(
    not NETWORK_ALLOWED,
    reason="leaves this machine; opt in with KOTOBA_ALLOW_NETWORK=1 and -m network")


#: The thread OBJECTS running when the test started, held by reference on purpose — see below.
_threads_at_start: set[threading.Thread] = set()


def pytest_runtest_logstart(nodeid, location):
    global _threads_at_start
    _threads_at_start = set(threading.enumerate())


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    """Names the test that leaves a non-daemon thread running, rather than blaming the next
    thread-counting test downstream.

    CPython joins non-daemon threads before atexit, so a leaked one is how a green run stops
    REPORTING: the summary prints and the process sits until a timeout kills it — met twice via
    aiosqlite's worker. A hook, not a fixture, so it runs after every finalizer.

    Comparison is by THREAD OBJECT, never `ident`: CPython reuses a joined thread's id (six joined
    threads in a row reported the same ident here), so an ident comparison let a leaked thread hide
    behind its own predecessor. The join is what keeps the guard honest rather than racy."""
    result = yield
    started = [t for t in threading.enumerate()
               if t not in _threads_at_start and not t.daemon]
    for t in started:
        t.join(5.0)
    left = [t.name for t in started if t.is_alive()]
    assert not left, f"this test left a non-daemon thread running; the interpreter cannot exit: {left}"
    return result


@pytest.fixture(autouse=True)
def _close_wizard_databases():
    """`run_wizard` hands its database back OPEN so the test can read it, and other modules import
    that helper. An autouse fixture only covers the module that DEFINES it, so the last database
    opened by any other importer stayed open — and aiosqlite's worker, not being a daemon thread,
    kept the interpreter from exiting.

    Measured: the suite passed all 3680 tests in 237 s, then sat until `timeout` killed it at 1800 s,
    exit 124 — a green run that never returns is the same lost gate as a red one that hangs.

    Reached by NAME: a rename would quietly stop draining anything and bring the hang back with no
    signal at all, so failing loud here beats failing silent."""
    yield
    helper = sys.modules.get("test_cli_first_run")
    if helper is None:
        return
    drain = getattr(helper, "_drain", None)
    assert callable(drain), (
        "test_cli_first_run._drain was renamed or removed; this fixture is the only thing closing the "
        "databases run_wizard hands back to its OTHER importers, and without it pytest passes and "
        "then never exits")
    drain()


@pytest.fixture(autouse=True)
def _pin_the_terminal(monkeypatch):
    """The render tests assert on painted bytes, so rich's own terminal detection decides their verdict.
    Measured: `FORCE_COLOR` (which several terminal harnesses export) makes rich paint bold/dim into a
    StringIO and
    breaks three plain-string comparisons; no `COLORTERM` drops the nameplate from truecolor to 256 and
    breaks a fourth; `TERM=dumb` unstyles everything and breaks ten. A suite whose verdict moves with the
    shell it was launched from is not a gate, so the shell is pinned here rather than assumed."""
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TTY_COMPATIBLE", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    # COLUMNS is the same class of input and was the one left ambient: rich reads it before falling
    # back to 80, so a shell that exports it moved every wrap point in every painted-bytes assertion.
    monkeypatch.setenv("COLUMNS", "80")
    # `host._ANSI` is a PROCESS-wide cache with no reset: whichever test asks first decides for every
    # test after it, and on Windows the answer under pytest is False because there is no console to
    # turn the codes on in — so `doctor` stopped painting and two colour assertions failed by test
    # order. The suite already claims a colour-capable terminal above; this is the same claim.
    monkeypatch.setattr(host, "_ANSI", True)
    # rich asks the REAL stdout handle whether it understands escape codes, and a captured buffer
    # answers no — so on Windows every Console it builds went legacy: 16 colours whatever palette it
    # was handed, and boxes composed differently. Two failures, one cause, and neither about us.
    monkeypatch.setattr("rich.console.detect_legacy_windows", lambda: False)
    # rich keeps a Style's rendered escape ON THE STYLE, and Styles are shared by content across
    # consoles — so without this the first console of the run decides the colour depth every painted
    # assertion after it reads, and the verdict moves with the test ORDER.
    for cache in (Style.parse, Style._add, Style.clear_meta_and_links):
        cache.cache_clear()


@pytest.fixture(autouse=True)
def _pin_the_shell_dialect(monkeypatch):
    """Which shell the approval gate PARSES for is a property of the sandbox, not of the machine running
    the suite — and the default sandbox is the host, so on Windows the whole gate silently switched to
    the PowerShell reading and ~180 tests written in POSIX stopped meaning what they say. One of them
    hangs rather than fails, waiting for a card that a POSIX auto-approval was supposed to make
    unnecessary.

    Pinned, not assumed, for the same reason the terminal above is. The Windows reading is not lost: it
    has its own file, which sets this the other way per test and owns those assertions."""
    monkeypatch.setattr("kotoba.core.sandbox.local.shell_is_windows", lambda: False)


@pytest.fixture(autouse=True)
def _no_inherited_api_key():
    """`llm._provider_keys` and the client built from it are MODULE state, and `/api/settings/llm-key`
    writes straight into them — so a test that saves a key handed it to every test after it in the
    process, and `llm.get_client()` came back live where the next test asserts None. Five test files had
    each grown their own copy of this clear; it belongs here once, so a new file cannot forget it.

    The voice key is the same shape and was missed: saving one sets a process-wide override, so posting
    a key over the web made the terminal wizard skip its whole voice step for the rest of the run."""
    from kotoba.core import llm

    def clear():
        llm._provider_keys.clear()
        llm._client = llm._client_key = None
        try:
            from kotoba.core.voice import config as voice_config
        except ImportError:
            return
        voice_config.set_api_key("")

    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
def _no_inherited_tool_availability():
    """`registry._check_cache` memoises `check()` for 30 s, and a test's own environment decides the
    answer — so `KOTOBA_SANDBOX=none` in one file withdrew shell/execute_code from the NEXT file, whose
    monkeypatch had rolled the variable back and could not roll back the memo. The cache is process
    state like `llm._provider_keys` above, and belongs here for the same reason: a suite whose verdict
    moves with the file list is not a gate."""
    from kotoba.tools import registry

    registry._check_cache.clear()
    yield
    registry._check_cache.clear()


@pytest.fixture(autouse=True)
def _isolate_user_state(tmp_path_factory):
    """HARD ISOLATION: a test must never reach the running install's state or a real browser. Every path
    that otherwise falls back to HOME or the cwd is redirected per test, so the suite can run beside a
    live Kotoba — the keystore key above all, since reading it decrypts the operator's saved keys. A
    test that sets one of these itself still wins.

    Its OWN MonkeyPatch, not the shared fixture: a test calling `monkeypatch.undo()` to put one thing
    back was undoing THIS WHOLE BLOCK with it, and then ran the real path against the operator's real
    state — measured twice: it wrote their live MCP config and read the real master key."""
    monkeypatch = pytest.MonkeyPatch()
    # ACLs are state that OUTLIVES the test. On Windows `restrict_file` runs `icacls /inheritance:r`
    # on the database, the keystore key, the log and every atomic write — thousands of files inside
    # pytest's own tmp tree — and the next run cannot prune what the last one locked: measured, a whole
    # suite died with PermissionError on the tmp root before a single test ran, and the tree could not
    # be deleted by hand either. The two tests whose subject IS the ACL force the flag back on.
    from kotoba.core import perms

    monkeypatch.setattr(perms, "_WINDOWS", False)
    base = tmp_path_factory.mktemp("kotoba_state")
    # The ROOT, not just the paths below it: anything that learns to write under the home later gets
    # this for free, instead of the suite reaching the user's own files until somebody notices.
    monkeypatch.setenv("KOTOBA_HOME", str(base))
    # The FALLBACK too, not only the override: every KOTOBA_* path here defaults under `Path.home()`,
    # which reads USERPROFILE on Windows and HOME nowhere else — so a child process that lost one of
    # the variables below still landed in the user's real profile. HOME is left alone: too much on a
    # POSIX box hangs off it, and there the overrides above already answer first.
    monkeypatch.setenv("USERPROFILE", str(base / "profile"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(base / "test.db"))
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(base / "memory"))
    monkeypatch.setenv("KOTOBA_FILES_DIR", str(base / "files"))  # the on-disk file library
    monkeypatch.setenv("KOTOBA_MODELS_DIR", str(base / "models"))  # never read the user's own avatars
    monkeypatch.setenv("KOTOBA_SETTINGS", str(base / "settings.yaml"))
    monkeypatch.setenv("KOTOBA_MCP_CONFIG", str(base / "mcp.yaml"))
    monkeypatch.setenv("KOTOBA_PENDING_MCP", str(base / "pending_mcp.yaml"))
    monkeypatch.setenv("KOTOBA_PLUGINS_PATH", str(base / "plugins"))
    monkeypatch.setenv("KOTOBA_VISUAL_MEMORY_DIR", str(base / "visual-memory"))
    monkeypatch.setenv("KOTOBA_KEYSTORE_KEY_FILE", str(base / ".keystore_key"))  # never READ the real master key
    monkeypatch.setenv("KOTOBA_TMP_DIR", str(base / "tmp"))
    # `tempfile` itself, not only our own paths: tests that build a scratch database with
    # `mktemp(suffix=".db")` leave it behind, and six files alone dropped 76 of them into the system
    # /tmp every run — 43 MB of the operator's conversation schema sitting outside any tmp tree.
    (base / "tmp").mkdir(exist_ok=True)
    monkeypatch.setenv("TMPDIR", str(base / "tmp"))
    monkeypatch.setattr(tempfile, "tempdir", str(base / "tmp"))
    monkeypatch.setenv("KOTOBA_BROWSER_OUTPUT_DIR", str(base / "browser-output"))
    monkeypatch.setenv("KOTOBA_BROWSER_PROFILE", str(base / "browser-profile"))
    monkeypatch.setenv("KOTOBA_CLI_HISTORY", str(base / "cli_history"))  # never append to the real one
    monkeypatch.setenv("KOTOBA_CLI_LOG", str(base / "cli.log"))  # nor to the real terminal log
    # Never let a stray env point work at a real project folder or connect a real browser during tests.
    monkeypatch.delenv("KOTOBA_WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("KOTOBA_BROWSER_CDP", raising=False)
    # The app's lifespan calls load_dotenv(), which leaks the REAL api/.env into the test process (it only
    # skips vars already set). KOTOBA_WEB_PASSWORD there would switch the /api/* auth gate ON and make every
    # endpoint test 401. Neutralize it (empty = gate open); a test that needs the gate sets it explicitly
    # (load_dotenv won't override an already-set var).
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "")
    monkeypatch.setenv("KOTOBA_GATE_PASSWORD", "")  # its alias must not re-enable the gate either
    # Prevent the real api/.env API keys from leaking into the test process via load_dotenv(). setenv(""),
    # NOT delenv: load_dotenv(override=False) skips only keys ALREADY PRESENT, so deleting one is exactly
    # what lets .env win. Measured — with delenv the suite ran holding the real OPENAI, ELEVENLABS, E2B and
    # GITHUB keys, in a process with unrestricted network. An empty value is present, so .env loses.
    for _secret in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "KOTOBA_API_KEY", "XAI_API_KEY",
                    "E2B_API_KEY", "GITHUB_MCP_TOKEN", "KOTOBA_MASTER_KEY", "DISCORD_BOT_TOKEN"):
        monkeypatch.setenv(_secret, "")
    # Ambient authority, which is worse than an ambient key: the owner id is what decides who may
    # reach the host, so a developer's own must never be the thing that makes a test pass. Same
    # setenv("") for the same reason as above.
    for _who in ("KOTOBA_DISCORD_OWNER_ID", "KOTOBA_DISCORD_GUILDS", "KOTOBA_DISCORD_HOME_CHANNELS"):
        monkeypatch.setenv(_who, "")
    # The three inputs to audio_tags_enabled(), which decides whether [audio tags] are asked for and kept.
    # Left ambient, the suite's result depended on the developer's own shell: KOTOBA_TTS_ENGINE=fast
    # made a code-fence test in the stream filters fail. The frozen voice-prompt snapshots promise that
    # the voice register does not move, and that promise is only measurable from a pinned environment.
    for _voice_var in ("KOTOBA_EXPRESSIVE", "KOTOBA_VOICE_MODE", "KOTOBA_TTS_ENGINE", "KOTOBA_TZ"):
        monkeypatch.delenv(_voice_var, raising=False)
    # The prompt's scaffolding gate reads this one, so it now decides the BYTES of the companion prompt
    # — and `kotoba.server` calls load_dotenv() at IMPORT time, which would put the developer's real
    # api/.env value into os.environ for the rest of the process and flip the frozen snapshots from
    # whichever test happened to import the server first. setenv, not delenv, for the same reason as the
    # secrets above: a value that is PRESENT beats load_dotenv(override=False). Pinned to the SHIPPED
    # DEFAULT rather than to empty, so what the suite measures is what a stranger installs.
    from kotoba.core.app_settings import DEFAULT_REASONING_EFFORT

    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", DEFAULT_REASONING_EFFORT)
    # The same class, and the three that were left ambient. `kotoba.server` calls load_dotenv() at
    # IMPORT, before any fixture exists, so the operator's own api/.env decided which sandbox ran, which
    # model was asked for and which provider answered — and a stranger's clone, having no .env, measured
    # something else. Pinned to the SHIPPED defaults, so what the suite proves is what they install.
    monkeypatch.setenv("KOTOBA_SANDBOX", "local")
    monkeypatch.setenv("KOTOBA_LLM_PROVIDER", "openai")
    monkeypatch.delenv("KOTOBA_MODEL", raising=False)
    yield
    monkeypatch.undo()


@pytest.fixture(autouse=True)
def _reap_session_sandboxes():
    """Per-session sandboxes persist across turns by design (so files survive). In tests that means a
    sandbox could linger in the registry between tests (and leak a real Docker container). Release them
    all after each test so the suite stays isolated and clean."""
    yield
    try:
        import kotoba.core.session_sandbox as ss

        sids = list(ss._live.keys())
        for sid in sids:
            try:
                asyncio.run(ss.release(sid))
            except Exception:
                ss._live.pop(sid, None)
    except Exception:
        pass


def make_symlink(link, target, *, target_is_directory: bool = False) -> None:
    """Point LINK at TARGET, or skip the test that needed one.

    Windows refuses without Developer Mode or elevation, and 43 tests whose subject is the JAIL died
    on their own fixture instead of saying the machine would not let them build it — a red suite that
    proved nothing either way. Skipping is the honest answer: the jail is untested there, and says so."""
    try:
        os.symlink(str(target), str(link), target_is_directory=target_is_directory)
    except (OSError, NotImplementedError, AttributeError) as e:
        pytest.skip(f"this machine will not create a symlink: {e}")


def shell_that(kind: str, *, code: int = 3, marker: str = "ran") -> str:
    """A command with the same OBSERVABLE behaviour in the shell each platform really runs.

    These tests are about the outcome plumbing, never about a shell, and they were written in one
    dialect: `sh -c '…'` does not exist on Windows, and PowerShell's `ls <missing>` writes to stderr
    while still exiting 0 — so eight "a failure is reported as a failure" tests read `ok` on the first
    real Windows run, against code that was doing exactly the right thing."""
    win = sys.platform == "win32"
    if kind == "fails":
        return f"exit {code}" if win else f"sh -c 'exit {code}'"
    if kind == "fails_naturally":
        return ("cmd /c dir no-existe-esto-12345.pdf" if win
                else "ls no-existe-esto-12345.pdf")
    if kind == "prints":
        return "echo hi-there"
    if kind == "prints_deferred":
        # Wrapped so the gate cannot wave it through: a bare `echo` is auto-approved and runs inline,
        # which is the other half of the plumbing and not the half a deferred test is watching.
        return "& { echo hi-there }" if win else "sh -c 'echo hi-there'"
    if kind == "sleeps":
        return "Start-Sleep -Seconds 5" if win else "sleep 5"
    if kind == "touches":
        return (f"New-Item -ItemType File -Name {marker} | Out-Null" if win
                else f"sh -c 'touch {marker}'")
    if kind == "touches_then_sleeps":
        return (f"New-Item -ItemType File -Name {marker} | Out-Null; Start-Sleep -Seconds 30" if win
                else f"sh -c 'touch {marker}; sleep 30'")
    if kind == "many_wide_lines":                      # 169 lines of a 16-digit number
        return ("1..169 | ForEach-Object { '{0:D16}' -f $_ }" if win
                else 'seq -f "%016.0f" 1 169')
    if kind == "many_wide_lines_then_fails":
        tail = "cannot stat the last one"
        return (f"{shell_that('many_wide_lines')}; [Console]::Error.WriteLine('{tail}'); exit 3" if win
                else f"{shell_that('many_wide_lines')}; echo \"{tail}\" >&2; exit 3")
    raise ValueError(f"no dialect for {kind!r}")
