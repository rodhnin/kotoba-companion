#!/usr/bin/env python3
"""Rebuild THIRD_PARTY_NOTICES.md from the dependency tree, because a hand-written one is wrong.

    scripts/licenses.py            # check the file is current, exit 1 if not
    scripts/licenses.py --write    # rewrite it

Scope is the frontend's RUNTIME closure: what a browser bundle is compiled from. Build tooling is
excluded because none of it reaches a user, and the wheel carries the compiled output and no
node_modules. Over-including within that closure is safe; leaving something out is not.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NOTICES = REPO / "THIRD_PARTY_NOTICES.md"
START = "<!-- generated: do not edit below this line -->"
END = "<!-- end generated -->"

# The families that ask for something. Anything else is reported so it cannot pass unnoticed.
ORDER = ["Apache-2.0", "(Apache-2.0 AND BSD-3-Clause)", "BSD-3-Clause", "BSD-2-Clause",
         "ISC", "0BSD", "MIT", "CC-BY-4.0"]


def closure() -> dict[str, dict]:
    lock = json.loads((REPO / "package-lock.json").read_text(encoding="utf-8"))
    pkgs = lock["packages"]
    roots = list(json.loads((REPO / "package.json").read_text(encoding="utf-8"))["dependencies"])
    found: dict[str, dict] = {}
    queue = list(roots)
    while queue:
        name = queue.pop()
        if name in found:
            continue
        node = pkgs.get(f"node_modules/{name}")
        if node is None:
            continue
        found[name] = node
        queue += [d for d in list(node.get("dependencies", {}))
                  + list(node.get("peerDependencies", {})) if d not in found]
    return found


def notice(name: str) -> str:
    """The copyright line the package itself states, or nothing. Never invented."""
    base = REPO / "node_modules" / name
    for candidate in ("LICENSE", "LICENCE", "LICENSE.md", "LICENSE.txt", "license", "COPYING"):
        f = base / candidate
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            ok = re.match(r"\s*(SPDX-FileCopyrightText:|Copyright)\b", line)
            if ok and re.search(r"(19|20)\d\d", line) and "[" not in line:
                return line.strip().rstrip(".")
    return ""


#: What a licence's own opening words are, for a package whose metadata says nothing. Only phrases that
#: identify one licence and no other — a guess here would file somebody's terms under the wrong heading,
#: which is worse than the honest "unstated" it replaces.
_LICENCE_TEXT = (
    ("MIT", "permission is hereby granted, free of charge"),
    ("Apache-2.0", "apache license"),
    ("ISC", "permission to use, copy, modify, and/or distribute this software"),
    ("BSD-3-Clause", "neither the name of"),
    ("BSD-2-Clause", "redistributions in binary form must reproduce"),
)


def declared_license(name: str) -> str:
    """The licence a package's own LICENSE file names, when its metadata names none.

    `unstated` is a heading that reads as all rights reserved, and it was being printed over packages
    that ship plain MIT — the lockfile simply has no `license` field for them. Reading the file the
    package actually ships is the same rule `notice` already follows one line down."""
    base = REPO / "node_modules" / name
    for candidate in ("LICENSE", "LICENCE", "LICENSE.md", "LICENSE.txt", "license", "COPYING"):
        f = base / candidate
        if not f.is_file():
            continue
        body = f.read_text(encoding="utf-8", errors="replace").lower()
        for lic, phrase in _LICENCE_TEXT:
            if phrase in body:
                return lic
    return "unstated"


def rows() -> dict[str, list[tuple[str, str, str]]]:
    by: dict[str, list[tuple[str, str, str]]] = {}
    for name, node in closure().items():
        lic = node.get("license")
        lic = lic if isinstance(lic, str) else declared_license(name)
        by.setdefault(lic, []).append((name, node.get("version", ""), notice(name)))
    for v in by.values():
        v.sort()
    return by


def render() -> str:
    by = rows()
    out = [START, ""]
    total = sum(len(v) for v in by.values())
    out.append(f"The frontend's runtime dependency tree is **{total} packages**. Every one of them may be "
               "compiled")
    out.append("into the bundle in whole or in part, so every one is listed. Where a package states a "
               "copyright")
    out.append("of its own it is reproduced verbatim; where it states none, none is invented.")
    out.append("")
    for lic in ORDER + sorted(k for k in by if k not in ORDER):
        pack = by.get(lic)
        if not pack:
            continue
        out.append(f"### {lic} — {len(pack)}")
        out.append("")
        for name, version, line in pack:
            out.append(f"- **{name}** {version}{' — ' + line if line else ''}")
        out.append("")
    out.append(END)
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="rewrite the file instead of checking it")
    args = ap.parse_args()

    text = NOTICES.read_text(encoding="utf-8")
    if START not in text or END not in text:
        print(f"{NOTICES.name} has no generated block: add {START} and {END}", file=sys.stderr)
        return 1
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    fresh = head + render() + tail

    if fresh == text:
        print("third-party notices are current")
        return 0
    if not args.write:
        print("third-party notices are out of date — run scripts/licenses.py --write", file=sys.stderr)
        return 1
    NOTICES.write_text(fresh, encoding="utf-8")
    print(f"wrote {NOTICES.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
