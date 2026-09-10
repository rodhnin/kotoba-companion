"""A discovered MCP server's local name must be the SAME name after a restart.

`mcp_find._unreserved` was written against exactly this: a name made unique with `hash()` moves with
PYTHONHASHSEED, so the same package is a different server on every boot — the saved one in `mcp.yaml`
and the freshly-discovered one never line up. The rule was written down there and the OTHER place that
renames a server, the live-collision branch in `execute`, kept an `abs(hash(...)) % 1000` of its own.

Two processes, two seeds, one expected answer.
"""
from __future__ import annotations

import os
import subprocess
import sys

_PROG = (
    "import kotoba.tools.action.mcp_find as mf;"
    "print(mf._disambiguate('spotify', 'io.github.acme/spotify-mcp'))"
)


def _name_under(seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run([sys.executable, "-c", _PROG], capture_output=True, text=True, env=env, check=True)
    return out.stdout.strip()


def test_the_suffix_does_not_move_with_the_hash_seed():
    assert _name_under("0") == _name_under("12345")


def test_both_renaming_paths_go_through_the_same_helper():
    import inspect

    import kotoba.tools.action.mcp_find as mf

    src = inspect.getsource(mf)
    assert "hash(c.name)" not in src, "the collision branch grew its own seed-randomised suffix again"
    assert src.count("_disambiguate(") >= 3   # the def, the reserved guard, the collision branch


def test_a_reserved_name_still_gets_suffixed():
    import kotoba.tools.action.mcp_find as mf

    got = mf._local_name("browser-mcp")
    assert got.startswith("browser-") and got != "browser"
    assert got == mf._local_name("browser-mcp")
