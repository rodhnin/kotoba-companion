"""`sandbox: none` is a promise about processes, and one tool was not keeping it.

`shell` and `execute_code` gate on sandbox availability and vanish under `none`. But `search_files`
spawns ripgrep — a read-only walk with a fixed argv, no shell, jailed cwd, so it is not "execution" as
the gate means it, yet it IS a child process. The tool already ships a pure-Python fallback (for the
Docker image, which lacks ripgrep), so the promise is kept rather than narrowed: under `none` the walk
still answers, nothing spawns, at the cost of a literal substring search instead of a regex. Not covered
by `none`: an MCP stdio server and the browser are separately installed and approved, and still launch.
The sweep below matches on the IMPORT, not a mention of "sandbox", to stay honest for the next tool
reaching for a subprocess."""
from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from kotoba.core import sandbox
from kotoba.tools import ToolContext
from kotoba.tools.action import execute_code, search_files, shell

# Unambiguous by name, plus the two whose bare names ("run", "call") are far too common to match on
# their own — a guard that cries wolf is a guard someone deletes.
_SPAWNERS = {"create_subprocess_exec", "create_subprocess_shell", "Popen", "posix_spawn",
             "check_output", "check_call", "system", "execv", "execvp"}
_QUALIFIED_SPAWNERS = {("subprocess", "run"), ("subprocess", "call")}


def _ctx(tmp_path, mode="work"):
    return ToolContext(db=None, workdir=tmp_path, mode=mode)


@pytest.fixture
def no_spawning(monkeypatch):
    """Any child process started from here is the defect, so make one loud."""
    started: list[tuple] = []

    async def _boom(*args, **kwargs):
        started.append(args)
        raise AssertionError(f"spawned a host process: {args!r}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _boom)
    return started


@pytest.fixture
def with_ripgrep(monkeypatch):
    """Pin the fast path ON, so the test measures the gate and not the machine it runs on."""
    monkeypatch.setattr(search_files.shutil, "which", lambda name: "/usr/bin/rg")


def _rg_spy(spawned: list):
    """Record the argv and hand back a process that behaves — never spawn the pinned path.

    The fixture above invents `/usr/bin/rg`, so really running it measured the machine after all: it
    does not exist on Windows and need not exist on Linux either. What these two tests assert is WHICH
    binary the gate chose, and that is in the argv."""
    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"", b""

        def kill(self):
            pass

    async def spy(*args, **kwargs):
        spawned.append(args)
        return _Proc()

    return spy


def test_search_files_spawns_nothing_when_execution_is_off(tmp_path, monkeypatch, with_ripgrep,
                                                           no_spawning):
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    (tmp_path / "one.txt").write_text("needle here\nother\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "two.txt").write_text("no match\nneedle again\n")

    res = asyncio.run(search_files.execute({"query": "needle"}, _ctx(tmp_path)))

    assert res and "one.txt" in res and "two.txt" in res
    assert no_spawning == []


def test_search_files_still_uses_ripgrep_on_the_local_backend(tmp_path, monkeypatch, with_ripgrep):
    """The gate must not cost the default install its fast path."""
    monkeypatch.setenv("KOTOBA_SANDBOX", "local")
    (tmp_path / "one.txt").write_text("needle here\n")
    spawned: list = []

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _rg_spy(spawned))
    asyncio.run(search_files.execute({"query": "needle"}, _ctx(tmp_path)))

    assert spawned and spawned[0][0] == "/usr/bin/rg"


def test_docker_deliberately_keeps_ripgrep(tmp_path, monkeypatch, with_ripgrep):
    """A decision, not an oversight: the file toolset reads the host workdir on EVERY backend
    (file_read/file_write do), so the container was never this tool's boundary. `docker` is a claim
    about isolating what she RUNS; `none` is the only one that is a claim about processes."""
    monkeypatch.setenv("KOTOBA_SANDBOX", "docker")
    (tmp_path / "one.txt").write_text("needle here\n")
    spawned: list = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _rg_spy(spawned))
    asyncio.run(search_files.execute({"query": "needle"}, _ctx(tmp_path)))

    assert spawned, "the docker backend lost its fast search for no gain"


def test_the_exec_tools_stay_off_the_table(monkeypatch):
    """The reference line the other tools are measured against."""
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    assert sandbox.backend_name() == "none"
    assert sandbox.sandbox_available_sync() is False
    assert shell.check() is False
    assert execute_code.check() is False


def test_no_tool_spawns_a_process_without_consulting_the_backend():
    """Source sweep: a tool that starts a child process must reference core.sandbox.

    The gap this closes was invisible because it was an ABSENCE — backend_name() had four callers and
    not one of them was under tools/. A grep answers that question every run instead of every audit."""
    root = pathlib.Path(search_files.__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        spawns, asks = False, False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                mod = node.func.value.id if isinstance(node.func.value, ast.Name) else ""
                if node.func.attr in _SPAWNERS or (mod, node.func.attr) in _QUALIFIED_SPAWNERS:
                    spawns = True
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "kotoba.core.sandbox"):
                asks = True
        if spawns and not asks:
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], (
        f"these tools start a host process without asking whether execution is allowed: {offenders}")
