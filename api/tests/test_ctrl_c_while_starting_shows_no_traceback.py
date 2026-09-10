"""No interrupt during startup may put a traceback on the terminal.

Before the fix, Ctrl+C at 0.2s/0.5s/1.0s each printed ~40 lines of stack and killed the
process by signal. The window runs from interpreter boot to the moment prompt_toolkit takes
the keyboard; `except KeyboardInterrupt` exists in `__main__` but only inside `main()`,
unreached while imports are still running.

A sleep grid only hits whatever the machine happens to be doing, so the primary gate is a
`sitecustomize` that raises KeyboardInterrupt at a named point. A real-SIGINT sweep is kept
alongside it to confirm the two agree."""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import posix_only

SRC = Path(__file__).resolve().parents[1] / "src"

#: Raise where a press would land. A meta_path finder fires on the import itself, which is the whole
#: point: `openai` is reached only from `cli/__main__`'s own import block.
_SITECUSTOMIZE = '''
import os
import sys

_module = os.environ.get("KOTOBA_TEST_TRAP_IMPORT")
if _module:
    class _Trap:
        def find_spec(self, name, path=None, target=None):
            if name == _module:
                raise KeyboardInterrupt
            return None

    sys.meta_path.insert(0, _Trap())

if os.environ.get("KOTOBA_TEST_TRAP_PARSE"):
    import argparse

    argparse.ArgumentParser.parse_args = lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt())
'''

_SENTENCE = "stopped before she was up."


def _env(tmp_path: Path, **extra: str) -> dict:
    """A throwaway HOME, because ~80 KOTOBA_* paths default under it, and the trap on PYTHONPATH."""
    (tmp_path / "sitecustomize.py").write_text(_SITECUSTOMIZE)
    env = dict(os.environ)
    env.pop("KOTOBA_TEST_TRAP_IMPORT", None)
    env.pop("KOTOBA_TEST_TRAP_PARSE", None)
    env.update({
        "HOME": str(tmp_path),
        "KOTOBA_HOME": str(tmp_path),
        "PYTHONPATH": os.pathsep.join([str(tmp_path), str(SRC), env.get("PYTHONPATH", "")]),
        **extra,
    })
    return env


def _launch(env: dict, argv: list[str]) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "kotoba.cli", *argv],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env)


def _before_our_code(err: str) -> bool:
    """True when the press landed in Python's own startup, where nothing of ours exists yet to catch
    it: the interpreter importing `site`, or runpy resolving `-m` before the module runs. Every frame
    is a frozen module there — the moment one names a file, the stack is ours to answer for."""
    frames = re.findall(r'^\s+File "([^"]+)"', err, re.M)
    return bool(frames) and all(f.startswith("<frozen") for f in frames)


def _assert_no_stack(err: str, where: str) -> None:
    assert "Traceback" not in err, f"a Ctrl+C {where} printed a stack:\n{err}"
    assert "KeyboardInterrupt" not in err, f"a Ctrl+C {where} named the exception:\n{err}"


@pytest.mark.parametrize("argv", [[], ["setup"], ["doctor"], ["--once", "hola"]])
def test_interrupt_in_the_import_chain_says_one_sentence(tmp_path, argv):
    """The defect as reported: the press lands on `openai`, four modules deep in the import at the top
    of `cli/__main__`, with `main()` not yet called and every handler in the file out of scope."""
    proc = _launch(_env(tmp_path, KOTOBA_TEST_TRAP_IMPORT="openai"), argv)
    _, err = proc.communicate(timeout=120)
    _assert_no_stack(err, "in the import chain")
    assert err.strip() == _SENTENCE
    assert proc.returncode == 130


@posix_only("the import chain of her terminal, which `kotoba` with no arguments does not open here")
def test_interrupt_while_the_app_is_loading_says_one_sentence(tmp_path):
    """The second window, found by the same sweep: `_interactive` imports `cli.app` — rich,
    prompt_toolkit, markdown_it — inside a `try` that caught ImportError and nothing else.

    Skipped where there is no POSIX terminal: `kotoba` with no arguments starts the backend and the
    web UI instead, so `cli.app` is never imported, the trap never springs, and what the test measures
    is a server it then waits two minutes to kill."""
    proc = _launch(_env(tmp_path, KOTOBA_TEST_TRAP_IMPORT="kotoba.cli.app"), [])
    _, err = proc.communicate(timeout=120)
    _assert_no_stack(err, "while the app was loading")
    assert err.strip() == _SENTENCE
    assert proc.returncode == 130


def test_interrupt_between_the_imports_and_the_command_says_one_sentence(tmp_path):
    """What is left of startup once the imports are done: argparse, `logs.to_file()`, the lazy import
    of a subcommand. Narrow, but it reached `bin/kotoba` the same way the other two did."""
    proc = _launch(_env(tmp_path, KOTOBA_TEST_TRAP_PARSE="1"), [])
    _, err = proc.communicate(timeout=120)
    _assert_no_stack(err, "between the imports and the command")
    assert err.strip() == _SENTENCE
    assert proc.returncode == 130


@posix_only("SIGINT delivered through send_signal")
@pytest.mark.parametrize("delay", [0.05, 0.25, 0.55, 0.85])
def test_a_real_sigint_anywhere_in_startup_leaves_no_stack(tmp_path, delay):
    """A real signal, not a raise, across the window the sweep measured. Only the invariant is asserted:
    where the press lands depends on the machine, and an exit code pinned to a sleep is a flake.

    On a loaded machine the press can land inside Python's own startup — importing `site`, or runpy
    resolving `-m` — before any line of ours exists to catch it. That stack is the interpreter's and no
    Python program can prevent it, so it is recognised by having no frame of ours in it rather than
    demanded away; anything else printing a stack is still a failure."""
    proc = _launch(_env(tmp_path), ["--version"])
    time.sleep(delay)
    proc.send_signal(signal.SIGINT)
    _, err = proc.communicate(timeout=120)
    if _before_our_code(err):
        assert "KeyboardInterrupt" in err, f"a startup failure that was not the press:\n{err}"
        return
    _assert_no_stack(err, f"{delay}s into startup")
