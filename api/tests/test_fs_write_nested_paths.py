"""A write whose path is a nested call must still ask, even under a saved execute_code grant.

`_DANGEROUS_PY`'s own header says a saved "always allow execute_code" family covers COMPUTATION, and
anything reaching disk still asks. The fs-write pattern used `[^)]*` between `open(` and the mode
string, which cannot cross a `)` — so every idiomatic form (`os.path.join(...)`, `expanduser(...)`,
`Path.home() / ...`) slipped past and appended to ~/.bashrc with no card. Reads must stay free: a gate
that asks about reading trains people to approve without looking.
"""
from __future__ import annotations

import pytest

from kotoba.core.approval import dangerous_code

WRITES = [
    'open("/tmp/x.txt", "w").write(data)',
    'open(os.path.join(base, "out.txt"), "w").write(data)',
    'with open(os.path.expanduser("~/.bashrc"), "a") as f: f.write(x)',
    'with open(Path.home() / ".ssh" / "authorized_keys", "a") as f: f.write(k)',
    'open(os.path.join(os.path.expanduser("~"), ".bashrc"), "a")',
    'open(f"{d}/notes.txt", mode="w").write("x")',
    "open(p, 'wb').write(blob)",
    'open(cfg["path"], "a+") as f',
    'open(get_path(a, b(c)), "x")',
    'Path("out.md").write_text(body)',
    'p.write_bytes(raw)',
]

READS_AND_COMPUTE = [
    'print(open("/etc/hostname").read())',
    'open(p, "r").read()',
    'open(p, encoding="utf-8").read()',
    'df = open(path).readlines()',
    'open("data.csv").read().split("w")',      # a "w" AFTER the call is not a mode
    'text = open(p).read() + "a"',
    'rows = [l for l in open(f)] ; mode = "w"',
    'x = sum(range(10))',
    'print("write")',
    'import json; json.loads(open("a.json").read())',
]


@pytest.mark.parametrize("code", WRITES)
def test_a_write_is_always_prompt_worthy(code):
    assert dangerous_code(code) == "fs-write", code


@pytest.mark.parametrize("code", READS_AND_COMPUTE)
def test_reading_and_computing_never_costs_a_prompt(code):
    assert dangerous_code(code) is None, code
