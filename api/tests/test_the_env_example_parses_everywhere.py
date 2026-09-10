"""The example env file has two readers, and only one of them forgives an inline comment.

`load_dotenv` strips a trailing `# …`; Docker's `--env-file` does not, and hands the whole rest of the
line to the program. `KOTOBA_PER_TOOL_LIMIT=3   # calls per turn` therefore killed the container at
import with a ValueError, from a file the documentation tells everyone to copy verbatim.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from conftest import posix_only

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = [ROOT / "api" / ".env.example", ROOT / ".env.local.example"]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_no_assignment_carries_a_trailing_comment(path):
    if not path.is_file():
        pytest.skip(f"{path.name} is not in this tree")
    bad = [f"{i}: {line}" for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
           if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*\S\s+#", line)]
    assert not bad, "Docker would keep the comment as part of the value:\n  " + "\n  ".join(bad)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_every_number_in_it_is_a_number(path):
    """The failure is not hypothetical: these are read with `int()` at import time."""
    if not path.is_file():
        pytest.skip(f"{path.name} is not in this tree")
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(\d[^\s]*)\s*$", line)
        if m:
            int(m.group(2))


@posix_only("a child environment of PATH and HOME alone")
def test_emptying_any_shipped_variable_does_not_kill_the_import():
    """Blanking a variable is how anybody unsets one, and this file ships six of them uncommented.
    Read bare, an empty string raised at IMPORT: the server did not start, and the traceback named
    neither the variable nor the file that held it."""
    import subprocess
    import sys

    names = [ln.split("=", 1)[0].strip()
             for path in EXAMPLES
             for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.strip().startswith("#") and "=" in ln]
    assert names, "nothing in the example file is live, so this measures nothing"
    src = str(ROOT / "api" / "src")
    bad = []
    for name in names:
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": src, name: "",
               "HOME": os.environ.get("HOME", "")}
        done = subprocess.run([sys.executable, "-c", "import kotoba.core.loop, kotoba.core.mcp.client"],
                              capture_output=True, text=True, env=env, timeout=120)
        if done.returncode != 0:
            bad.append(f"{name}: {done.stderr.strip().splitlines()[-1][:90]}")
    assert not bad, "emptying these breaks the import:\n" + "\n".join(bad)
