"""The two commands that decide a stranger's first five minutes: `doctor` and `serve`.

`doctor` has one job — say why she will not run, in the order that decides it — and two hard rules: it
must see a key that was saved in the Settings panel (that key lives encrypted in the database and never
in the environment, so only the `engine.start` path can decrypt it), and it must never put a secret on
screen, not even a prefix of one.

`serve` must leave nothing behind. `npm run dev` forks a Next worker that outlives npm, so the test that
matters kills a real two-deep process tree and checks the grandchild died with it. Nothing binds a port.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import time
from types import SimpleNamespace

import pytest
from conftest import posix_only

from kotoba import DIST_NAME
from kotoba.cli import doctor, serve


def _run(coro):
    return asyncio.run(coro)


def _check(sections, name):
    return next(c for _, checks in sections for c in checks if c.name == name)


@pytest.fixture(autouse=True)
def _no_ambient_key():
    """Decrypted keys are cached in a module global, so one test's key is the next test's lie."""
    from kotoba.core import llm

    llm._provider_keys.clear()
    yield
    llm._provider_keys.clear()


def test_a_configured_machine_reports_ready_and_exits_zero(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
    monkeypatch.setenv("KOTOBA_SANDBOX", "local")
    sections = _run(doctor.collect())
    assert _check(sections, "llm key").status == "ok"
    assert doctor.verdict_of(sections)[0] == 0, [c for _, cs in sections for c in cs if c.status == "fail"]


def test_a_missing_soul_file_is_reported_before_anything_downstream_is_guessed(monkeypatch, tmp_path):
    monkeypatch.setenv("SOUL_PATH", str(tmp_path / "nowhere.md"))
    sections = _run(doctor.collect())
    order = [c.name for c in sections[0][1]]
    assert order.index("soul") < order.index("database") < order.index("llm key")
    assert _check(sections, "soul").status == "fail"
    assert _check(sections, "llm key").status == "skip", "she cannot start, so the key is not guessed at"
    assert doctor.verdict_of(sections)[0] == 1


def test_a_key_saved_in_the_settings_panel_counts_as_configured():
    """It lives encrypted in the database, never in the environment — only the startup path decrypts it."""
    from kotoba.core import engine, llm

    async def save() -> None:
        eng = await engine.start(tickers=False)
        await eng.db.save_key("llm:openai:api_key", "sk-saved-from-the-panel")
        await engine.stop(eng)

    _run(save())
    llm._provider_keys.clear()

    check = _check(_run(doctor.collect()), "llm key")
    assert check.status == "ok"
    assert "saved in the app" in check.detail


def test_a_saved_key_that_cannot_be_decrypted_says_so_instead_of_no_key(monkeypatch):
    """Silence here is indistinguishable from 'never saved', and the panel goes on listing the key."""
    from kotoba.core import engine, llm

    async def save() -> None:
        eng = await engine.start(tickers=False)
        await eng.db.save_key("llm:openai:api_key", "sk-encrypted-under-the-old-master-key")
        await engine.stop(eng)

    monkeypatch.setenv("KOTOBA_MASTER_KEY", "the-master-key-it-was-encrypted-with")
    _run(save())
    monkeypatch.setenv("KOTOBA_MASTER_KEY", "a-master-key-that-was-rotated-in")
    llm._provider_keys.clear()

    check = _check(_run(doctor.collect()), "llm key")
    assert check.status == "fail"
    assert "cannot be decrypted" in check.detail


def test_no_key_at_all_names_the_environment_variable_to_set():
    check = _check(_run(doctor.collect()), "llm key")
    assert check.status == "fail"
    assert "OPENAI_API_KEY" in check.detail


def test_the_report_never_puts_a_secret_on_screen(monkeypatch):
    """Every window of it, not one prefix. Asked for `secret[:12]` this passed over a real leak: the
    report printed ten characters of the key and the guard had nothing to say about them."""
    secret = "sk-proj-QZWXJKVBNMQZWXJKVBNMQZWXJKVBNM"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    out = io.StringIO()
    doctor.run(out)
    printed = out.getvalue()
    assert secret not in printed
    seen = sorted({secret[i:i + 6] for i in range(len(secret) - 5) if secret[i:i + 6] in printed})
    assert not seen, f"a run of the secret reached the screen: {seen}"


@posix_only("file modes")
def test_a_world_readable_key_is_not_reported_as_private(monkeypatch, tmp_path):
    """The row that promises the secrets are kept to this account has to have looked at them. Answered
    from the platform instead, it printed 0600 over a master key sitting at 0644."""
    home = tmp_path / "home"
    home.mkdir()
    key = home / ".keystore_key"
    key.write_bytes(b"0" * 32)
    key.chmod(0o644)
    monkeypatch.setenv("KOTOBA_HOME", str(home))
    monkeypatch.setenv("KOTOBA_KEYSTORE_KEY_FILE", str(key))

    out = io.StringIO()
    doctor.run(out)
    row = next(ln for ln in out.getvalue().splitlines() if "permissions" in ln)
    assert "0644" in row and "not private" in row, row


class _Screen(io.StringIO):
    """A StringIO that claims to be a terminal, so `_paint`'s isatty half stops answering for it."""

    def isatty(self) -> bool:
        return True


def test_doctor_paints_a_real_terminal_and_stops_when_no_color_is_set(monkeypatch):
    """`_paint` has two independent reasons to go plain and only one of them was ever exercised: every
    test hands it a StringIO, so `not out.isatty()` was always true and the `NO_COLOR` term never
    decided anything. `_pin_the_terminal` deletes the variable for the whole suite (the render
    comparisons need it gone), so the pin sets it inside the test, where it wins over the fixture."""
    out = _Screen()
    doctor.run(out)
    assert "\033[" in out.getvalue(), "a real terminal got no colour at all"

    monkeypatch.setenv("NO_COLOR", "1")
    plain = _Screen()
    doctor.run(plain)
    assert "\033[" not in plain.getvalue(), "NO_COLOR was ignored"


def test_no_color_changes_the_paint_and_not_the_words(monkeypatch):
    """Turning colour off must not quietly change the report, only how it is inked."""
    import re

    doctor.run(io.StringIO())      # the first run CREATES the database, and says so on that line only
    coloured = _Screen()
    doctor.run(coloured)
    monkeypatch.setenv("NO_COLOR", "1")
    plain = _Screen()
    doctor.run(plain)
    assert re.sub(r"\033\[[0-9;]*m", "", coloured.getvalue()) == plain.getvalue()


def test_a_pipe_still_gets_no_escape_codes_without_the_variable():
    out = io.StringIO()
    doctor.run(out)
    assert "\033[" not in out.getvalue()


def test_every_missing_extra_says_what_it_costs_and_how_to_install_it(monkeypatch):
    monkeypatch.setattr(doctor, "_installed", lambda module: False)
    extras = dict(_run(doctor.collect()))["optional extras"]

    for check in extras:
        assert check.status == "warn", f"{check.name} is optional and must never fail the run"
        cost, _, install = check.detail.partition("  pip install")
        assert cost.strip(), f"{check.name} says it is missing but not what that costs"
        assert install.strip() == f'"{DIST_NAME}[{check.name}]"'
    web = next(c for c in extras if c.name == "web")
    assert "r.jina.ai" in web.detail, "a stranger must be told the pages she reads leave the machine"


def test_a_missing_cryptography_is_a_failure_because_no_key_can_be_stored(monkeypatch):
    monkeypatch.setattr(doctor, "_installed", lambda module: module != "cryptography")
    sections = _run(doctor.collect())
    assert _check(sections, "encryption").status == "fail"
    assert _check(sections, "llm key").status == "skip"
    assert doctor.verdict_of(sections)[0] == 1


def test_docker_matters_only_when_it_is_the_selected_sandbox(monkeypatch):
    from kotoba.core import sandbox

    monkeypatch.setattr(sandbox, "sandbox_available_sync", lambda: False)
    monkeypatch.setenv("KOTOBA_SANDBOX", "local")
    assert doctor._sandbox().status == "ok", "a missing daemon is irrelevant when nothing asks for it"

    monkeypatch.setenv("KOTOBA_SANDBOX", "docker")
    check = doctor._sandbox()
    assert check.status == "fail"
    assert "execute_code" in check.detail, "it must say what stops working, not just that docker is gone"


def test_the_database_line_names_the_file_it_will_actually_use():
    check = _run(doctor._database())
    assert check.status == "ok"
    assert check.detail.startswith(os.environ["DATABASE_URL"].removeprefix("sqlite:///"))
    assert "schema v" in check.detail


def test_a_missing_directory_is_no_longer_unusable(monkeypatch, tmp_path):
    """A first run whose data dir does not exist yet used to die in connect(); Database now creates it,
    which is what makes a wheel install pointed at a fresh ~/.kotoba boot at all. So doctor is right to
    pass here — this used to be the `fail` case below, and the path it used stopped being broken."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'not-yet' / 'kotoba.db'}")
    assert _run(doctor._database()).status == "ok"


def test_an_unusable_database_fails_with_the_path_it_tried(monkeypatch, tmp_path):
    """Parent is a FILE, so neither mkdir nor sqlite can do anything with it — and unlike a read-only
    directory it stays unusable when the suite happens to run as root."""
    blocker = tmp_path / "in-the-way"
    blocker.write_text("not a directory")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{blocker / 'kotoba.db'}")
    check = _run(doctor._database())
    assert check.status == "fail"
    assert "in-the-way" in check.detail


def test_the_verdict_fails_on_a_failure_and_forgives_a_missing_extra():
    ok = [("core", [doctor.Check("soul", "ok", "")])]
    warned = [("core", [doctor.Check("soul", "ok", ""), doctor.Check("mcp", "warn", "")])]
    broken = [("core", [doctor.Check("soul", "fail", ""), doctor.Check("mcp", "warn", "")])]
    assert doctor.verdict_of(ok)[0] == 0
    assert doctor.verdict_of(warned)[0] == 0
    assert doctor.verdict_of(broken)[0] == 1


def test_the_backend_is_uvicorn_on_loopback_in_this_interpreter():
    """Never 0.0.0.0: it runs her tools on this machine and must not be reachable from the network."""
    assert serve.backend_command(8123) == [
        sys.executable, "-m", "uvicorn", "kotoba.server:app", "--host", "127.0.0.1", "--port", "8123",
    ]


def test_the_web_ui_is_bound_to_loopback_too(monkeypatch):
    """The platform is forced: read off the host this asserted the POSIX argv on Windows, where the
    command is correctly `cmd.exe /c npm`. The Windows shape has its own test."""
    monkeypatch.setattr(serve, "_WINDOWS", False)
    assert serve.frontend_command(3123) == [
        "npm", "run", "dev", "--", "--port", "3123", "--hostname", "127.0.0.1",
    ]


def test_the_frontend_is_told_which_backend_to_proxy_to():
    """Next reads it once when the dev server starts; without it a non-default --port is a blank page."""
    assert serve.frontend_env(8123)["KOTOBA_BACKEND_URL"] == "http://127.0.0.1:8123"


def test_an_install_with_no_web_app_degrades_instead_of_crashing(tmp_path):
    blocked = serve.frontend_blocker(tmp_path)
    assert blocked and str(tmp_path) in blocked and "clone" in blocked


def test_dependencies_that_were_never_installed_are_named_as_the_reason(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(serve.shutil, "which", lambda name: "/usr/bin/npm")
    assert "npm install" in serve.frontend_blocker(tmp_path)

    (tmp_path / "node_modules").mkdir()
    assert serve.frontend_blocker(tmp_path) is None


def test_a_missing_server_extra_names_the_install_command_instead_of_crashing(monkeypatch, capsys):
    def refuse(*args, **kwargs):
        pytest.fail("nothing may be spawned when the backend cannot possibly run")

    monkeypatch.setattr(serve, "_installed", lambda module: module != "uvicorn")
    monkeypatch.setattr(serve, "_spawn", refuse)
    assert serve.run(port=8123, web_port=3123) == 1
    assert f'pip install "{DIST_NAME}[server]"' in capsys.readouterr().err


def test_control_c_stops_every_child_it_started(monkeypatch):
    started, stopped = [], []

    def fake_spawn(command, cwd, env):
        child = SimpleNamespace(pid=-1, returncode=None, poll=lambda: None)
        started.append(child)
        return child

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(serve, "_installed", lambda module: True)
    monkeypatch.setattr(serve, "frontend_blocker", lambda root: None)
    monkeypatch.setattr(serve, "_spawn", fake_spawn)
    monkeypatch.setattr(serve, "_pump",
                        lambda prefix, child, out: SimpleNamespace(join=lambda timeout=None: None))
    monkeypatch.setattr(serve, "stop", stopped.append)
    monkeypatch.setattr(serve.time, "sleep", interrupt)

    assert serve.run(port=8123, web_port=3123) == 130
    assert len(started) == 2, "the backend and the web UI"
    assert stopped == started, "not one of them may be left running"


def test_a_child_that_dies_on_its_own_brings_the_other_one_down(monkeypatch, capsys):
    started, stopped = [], []

    def fake_spawn(command, cwd, env):
        crashed = command[0] != "npm"
        child = SimpleNamespace(pid=-1, returncode=3 if crashed else None,
                                poll=(lambda: 3) if crashed else (lambda: None))
        started.append(child)
        return child

    monkeypatch.setattr(serve, "_installed", lambda module: True)
    monkeypatch.setattr(serve, "frontend_blocker", lambda root: None)
    monkeypatch.setattr(serve, "_spawn", fake_spawn)
    monkeypatch.setattr(serve, "_pump",
                        lambda prefix, child, out: SimpleNamespace(join=lambda timeout=None: None))
    monkeypatch.setattr(serve, "stop", stopped.append)

    assert serve.run(port=8123, web_port=3123) == 1
    assert stopped == started, "the survivor may not be left holding its port"
    assert "[api] exited with 3" in capsys.readouterr().err


@posix_only("POSIX process groups (os.getpgid)")
def test_stopping_a_child_takes_its_whole_process_tree_with_it(tmp_path):
    """`npm run dev` forks a Next worker that outlives npm, so a signal to npm alone leaves a server up."""
    forks_a_grandchild = (
        "import subprocess, sys, time\n"
        "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "print(kid.pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    child = serve._spawn([sys.executable, "-c", forks_a_grandchild], tmp_path, dict(os.environ))
    grandchild = int(child.stdout.readline())
    assert os.getpgid(child.pid) != os.getpgid(0), "a child must not share the terminal's process group"

    serve.stop(child)

    assert child.poll() is not None
    assert _reaped(grandchild), "the grandchild outlived `kotoba serve`"


def _reaped(pid: int, deadline: float = 10.0) -> bool:
    """It reparents to init when its parent goes, so poll until the kernel has actually collected it."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_serve_tears_down_on_sigterm_not_only_on_ctrl_c():
    """Measured before the fix: SIGTERM killed the parent and left the backend and a Next worker holding
    their ports. The children are in their own sessions on purpose — that is exactly why a parent which
    dies without running its teardown orphans them, and systemd, Docker and every supervisor send TERM."""
    import inspect
    import signal as signal_mod

    from kotoba.cli import serve

    src = inspect.getsource(serve._catch_termination)
    assert "SIGTERM" in src, "the signal every process manager sends must reach the teardown"

    caught = serve._catch_termination()
    try:
        assert any(sig == signal_mod.SIGTERM for _handler, sig in caught)
        installed = signal_mod.getsignal(signal_mod.SIGTERM)
        with pytest.raises(KeyboardInterrupt):
            installed(signal_mod.SIGTERM, None)
    finally:
        for handler, sig in caught:
            signal_mod.signal(sig, handler)
    assert signal_mod.getsignal(signal_mod.SIGTERM) is not installed, "the handler must be put back"


def _configure_server(monkeypatch, tmp_path, name: str, command: str) -> None:
    config = tmp_path / "mcp.yaml"
    config.write_text(f"mcp_servers:\n  {name}:\n    command: {command}\n    args: []\n", encoding="utf-8")
    monkeypatch.setenv("KOTOBA_MCP_CONFIG", str(config))


def test_a_configured_mcp_server_that_cannot_start_is_reported_instead_of_hidden(monkeypatch, tmp_path):
    """Its own stderr goes to the log file now, so doctor is the only thing left that will say this."""
    _configure_server(monkeypatch, tmp_path, "blender", "kotoba-no-such-binary")
    servers = dict(_run(doctor.collect()))["mcp servers"]

    broken = _check([("mcp servers", servers)], "blender")
    assert broken.status == "warn", "a broken server must not stop her running either"
    assert "does not start" in broken.detail


def test_the_report_says_where_a_servers_own_words_went(monkeypatch, tmp_path):
    """A server that starts and then complains — blender with no Blender — is only readable there."""
    from kotoba.core import logs

    _configure_server(monkeypatch, tmp_path, "blender", "kotoba-no-such-binary")
    servers = dict(_run(doctor.collect()))["mcp servers"]

    assert str(logs.path()) in _check([("mcp servers", servers)], "log").detail


def test_a_broken_mcp_server_does_not_change_the_exit_code():
    sections = [("mcp servers", [doctor.Check("blender", "warn", "does not start: nope")])]
    assert doctor.verdict_of(sections)[0] == 0


def test_a_machine_with_no_mcp_servers_says_so_rather_than_nothing():
    servers = dict(_run(doctor.collect()))["mcp servers"]
    assert _check([("mcp servers", servers)], "servers").status == "ok"
    assert "none configured" in _check([("mcp servers", servers)], "servers").detail


def test_the_name_the_code_says_is_the_name_on_the_index():
    """`pip install kotoba` fetches somebody else's library, so the distribution is named apart from
    the import, the command and her. Two spellings of it that can drift is the whole risk."""
    import tomllib

    from kotoba.paths import PACKAGE_DIR

    pyproject = PACKAGE_DIR.parents[1] / "pyproject.toml"
    if not pyproject.is_file():
        import pytest
        pytest.skip("the manifest is not shipped")
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["name"]
    assert declared == DIST_NAME, "the install command she prints would fetch nothing"


def test_no_extra_asks_the_index_for_the_old_name():
    """`dev` depends on the package's own extras. Spelled with the name that is taken on the index, it
    would fetch a stranger's library instead of this one."""
    import tomllib

    from kotoba.paths import PACKAGE_DIR

    pyproject = PACKAGE_DIR.parents[1] / "pyproject.toml"
    if not pyproject.is_file():
        import pytest
        pytest.skip("the manifest is not shipped")
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    for extra, requires in data["project"].get("optional-dependencies", {}).items():
        for one in requires:
            head = one.split("[")[0].split(">")[0].split("=")[0].strip()
            assert head != "kotoba", f"the {extra} extra asks the index for `kotoba`, which is not us"
