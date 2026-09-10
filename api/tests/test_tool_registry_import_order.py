"""Her toolset must not depend on which module the entry point imports first.

`core.mcp.client` imports `tools.registry`, whose `discover()` reaches `tools.action`. When an action
module imported back from `core.mcp.client` at module level, that import landed on a half-built module
and raised — and `discover()` caught it, so the process ran on with every action tool missing (shell,
write_file, patch, delegate, execute_code, mcp_find, mcp_install: 14 of 29). Nothing failed; she just
said she couldn't. The CLI is a second entry point, which is exactly how this resurfaces.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

API = Path(__file__).resolve().parent.parent / "src"

_CORE_ACTION_TOOLS = {
    "shell", "execute_code", "write_file", "read_file", "patch",
    "delegate", "mcp_find", "mcp_install", "search_files",
}

_PROBE = """
import sys
if {mcp_first}:
    import kotoba.core.mcp.client; from kotoba import core          # a new entry point that touches MCP before the registry
from kotoba.tools.registry import schemas_for
print(",".join(sorted(n for n in (s.get("name") for s in schemas_for(mode="work")) if n)))
"""


def _tools_for(mcp_first: bool) -> set[str]:
    out = subprocess.run(
        [sys.executable, "-c", _PROBE.format(mcp_first=mcp_first)],
        cwd=API, capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, f"probe crashed:\n{out.stderr[-2000:]}"
    return set(out.stdout.strip().split(","))


def test_the_same_tools_are_offered_whichever_module_loads_first():
    plain = _tools_for(False)
    mcp_first = _tools_for(True)
    assert _CORE_ACTION_TOOLS <= plain, f"missing in the normal order: {_CORE_ACTION_TOOLS - plain}"
    assert plain == mcp_first, (
        f"import order changed the toolset — missing when MCP loads first: {plain - mcp_first}"
    )


def test_no_action_module_imports_the_mcp_client_at_module_level():
    """The guard against reintroducing it: the import belongs inside the function that needs it.

    The directory comes from the package object, not from a hand-counted path: the hand-counted one
    named a directory that has never existed, so the scan read zero files and the guard passed over a
    planted offender. A scan that finds nothing now fails instead of reporting clean."""
    import kotoba.tools.action as action

    root = Path(action.__file__).parent
    scanned, offenders = 0, []
    for path in root.glob("*.py"):
        scanned += 1
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith(("from kotoba.core.mcp.client", "import kotoba.core.mcp.client")):
                offenders.append(f"{path.name}:{i}")
    assert scanned, f"scanned no action modules under {root} — the guard was reading an empty directory"
    assert not offenders, f"module-level import of core.mcp.client: {offenders}"


def test_a_broken_action_import_refuses_to_start_instead_of_losing_tools():
    """Loud beats crippled: core tools are not optional the way plugins are."""
    probe = (
        "import sys, types\n"
        "bad = types.ModuleType('kotoba.tools.action')\n"
        "bad.ACTION_TOOLS = property(lambda self: (_ for _ in ()).throw(RuntimeError('boom')))\n"
        "import builtins\n"
        "real_import = builtins.__import__\n"
        "def fake(name, *a, **k):\n"
        "    if name == 'kotoba.tools.action':\n"
        "        raise ImportError('simulated circular import')\n"
        "    return real_import(name, *a, **k)\n"
        "builtins.__import__ = fake\n"
        "from kotoba.tools.registry import discover\n"
        "discover()\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], cwd=API, capture_output=True, text=True, timeout=120)
    assert out.returncode != 0, "a failed action import must not be swallowed"
    assert "crippled toolset" in out.stderr
