"""The modules that decide must import nothing from the Discord library.

That is what keeps most of the suite runnable on an install without the extra, and it is the only
reason the routing, the splitter and the plan differ are unit-testable at all. A module that acts —
one that sends, edits or listens — may import it, but only inside a function, so importing the
package costs an install without the extra nothing.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "src" / "kotoba" / "discord"

# The ones that only decide. Everything else may reach the library, lazily.
PURE = ("config", "state", "authority", "people", "text", "history", "plan", "media", "audio")


def _imports(path: Path) -> list[tuple[str, bool]]:
    """Every import in the file, with whether it sits at module scope."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top = {id(n) for n in tree.body}
    out: list[tuple[str, bool]] = []
    for node in ast.walk(tree):
        at_top = id(node) in top
        if isinstance(node, ast.Import):
            out += [(a.name.split(".")[0], at_top) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.module.split(".")[0], at_top))
    return out


@pytest.mark.parametrize("name", PURE)
def test_a_module_that_decides_never_names_the_library(name):
    hits = [mod for mod, _ in _imports(PKG / f"{name}.py") if mod == "discord"]
    assert not hits, f"{name}.py reaches for the library it is not allowed to need"


def test_no_module_in_the_package_imports_it_at_module_scope():
    """Lazy is the whole contract: `import kotoba.discord.client` must work with no extra installed."""
    guilty = [p.name for p in sorted(PKG.glob("*.py"))
              if any(mod == "discord" and at_top for mod, at_top in _imports(p))]
    assert not guilty, f"these would fail to import without the extra: {guilty}"


def test_the_package_imports_with_the_library_gone():
    """The oracle, in a subprocess: an in-process block is a no-op once another test has cached it.

    It proves the block fires before it proves anything else — an instrument that cannot fail is not
    an instrument.
    """
    probe = """
import sys


class Deny:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] == "discord":
            raise ImportError("blocked")
        return None


sys.meta_path.insert(0, Deny())
try:
    import discord
except ImportError:
    pass
else:
    raise SystemExit("the block did not fire")

import importlib
for m in ("config", "state", "authority", "people", "text", "history", "plan", "media",
          "audio", "guild", "actions", "cards", "bridge", "client", "run"):
    importlib.import_module("kotoba.discord." + m)
print("ok")
"""
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr.strip()[-800:]
    assert done.stdout.strip() == "ok"
