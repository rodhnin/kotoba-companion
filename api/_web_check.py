"""Is the packaged web UI present, current, and built for this version — asked without setuptools.

Beside the build backend rather than inside it, because a guard that cannot be exercised where
setuptools is absent is a guard nobody has ever seen refuse.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIRED = ("app.html", "login.html", "setup.html", "404.html")
STAMP_NAME = "kotoba-build.json"


def version(here: Path = HERE) -> str:
    text = (here / "src" / "kotoba" / "__init__.py").read_text(encoding="utf-8")
    found = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return found.group(1) if found else ""


LICENCES = ("LICENSE", "THIRD_PARTY_NOTICES.md")


def mirror_licences(here: Path = HERE, repo: Path | None = None) -> None:
    """Put the licence beside the manifest, which cannot reach outside its own directory.

    The copies are not kept in the repository — they would drift — so a fresh checkout has none and
    would otherwise produce a wheel carrying no licence at all. An unpacked sdist is the other way
    round: it has the copies and no original, and that is not an error."""
    repo = repo if repo is not None else here.parent
    for name in LICENCES:
        original, beside = repo / name, here / name
        if original.is_file():
            if not beside.is_file() or beside.read_bytes() != original.read_bytes():
                beside.write_bytes(original.read_bytes())
        elif not beside.is_file():
            raise SystemExit(f"refusing to build: {name} is nowhere to be found, so the wheel would "
                             f"ship unlicensed")


def digest(web: Path) -> tuple[str, int]:
    """The stamp cannot cover itself, so it is the one file left out.

    Ordered by the path TEXT: `PurePath` compares case-folded on Windows and not elsewhere, and this
    digest is written on one machine to be checked on another."""
    h = hashlib.sha256()
    n = 0
    for f in sorted((p for p in web.rglob("*") if p.is_file() and p.name != STAMP_NAME),
                    key=lambda p: p.relative_to(web).as_posix()):
        h.update(f.relative_to(web).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
        n += 1
    return h.hexdigest(), n


def sources_digest(repo: Path) -> str | None:
    """The frontend as it stands, or None when the sources are not here — an sdist has none, and a
    wheel built from one is not stale for lacking them."""
    import importlib.util

    script = repo / "scripts" / "build_web.py"
    if not script.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_kotoba_build_web", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.sources_digest()


def problems(web: Path, expected: str, repo: Path | None = None) -> list[str]:
    """Everything wrong with this build, in the words the person who has to fix it needs."""
    if not web.is_dir():
        return [f"{web} does not exist"]
    found = [f"{web / name} is missing" for name in REQUIRED if not (web / name).is_file()]
    if not any((web / "_next" / "static").rglob("*.js")):
        found.append("no javascript under web/_next/static")
    stamp_file = web / STAMP_NAME
    if not stamp_file.is_file():
        found.append(f"{stamp_file} is missing")
        return found
    stamp = json.loads(stamp_file.read_text(encoding="utf-8"))
    made, count = digest(web)
    if stamp.get("version") != expected:
        found.append(f"the build is stamped for kotoba {stamp.get('version')!r}, "
                     f"this package is {expected!r}")
    if stamp.get("web_sha256") != made or stamp.get("files") != count:
        found.append("web/ no longer matches its stamp — files changed, arrived or went")
    # The other half of stale, and the one that actually ships: the build is intact but the frontend
    # moved on since. Only askable where the sources are.
    if repo is not None:
        current = sources_digest(repo)
        if current is not None and stamp.get("sources_sha256") != current:
            found.append("the frontend has changed since this build was made")
    return found


def require(what: str, web: Path, expected: str, repo: Path | None = None) -> None:
    """A wheel carrying an empty or stale build is worse than one carrying none: the static layer
    denies every path by design, so the person gets a blank page and no reason for it."""
    found = problems(web, expected, repo)
    if found:
        raise SystemExit(
            f"refusing to build the {what}: the packaged web UI is absent or stale.\n  - "
            + "\n  - ".join(found)
            + "\n  build it first:  python scripts/build_web.py   (needs Node)")
