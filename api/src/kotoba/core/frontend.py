"""Which of the built frontend's files a browser may have, and on what terms.

DENY BY DEFAULT, and derived from the build output rather than from a list of URL prefixes: a path is
answerable only when it resolves to a real file whose page name is named here. An allowlist of
prefixes is a rule somebody has to keep in step with a route table forever, and that is the shape
that has already failed open on this API twice.

Classification ALLOWS only on the RESOLVED path, never on the URL. A case-insensitive filesystem opens
`app.html` for `/APP.HTML`, so judging the spelling instead of the bytes reached is a way in.
"""
from __future__ import annotations

import enum
import os
from pathlib import Path

from kotoba import paths

HOME = "/app"

#: Pages that cost a signed session. Everything a browser needs BEFORE it has one is public, because
#: that is where a credential comes from; everything else is denied by falling off both lists.
GATED = frozenset({"app", "setup"})
PUBLIC = frozenset({
    "_next", "login", "_not-found", "404", "icon.svg", "live2dcubismcore.min.js",
    "worklets", "subagents", "scene", "art",
})


class Access(enum.Enum):
    PUBLIC = "public"
    GATED = "gated"
    DENIED = "denied"


_UNSET = object()
_ROOT: object = _UNSET


def _find_root() -> Path | None:
    """The clone check is not cosmetic: from a wheel `REPO_ROOT` is the home directory that also holds
    her file library, so an unguarded fallback would aim a public static server at the user's files."""
    named = os.getenv("KOTOBA_FRONTEND_DIR")
    if named:
        # Held to the same marker as the others: a typo here used to disable the UI in silence, and
        # the env answer WINS, so it would do it even on an install that carries a perfectly good one.
        d = Path(named).expanduser()
        # RESOLVED, like the two below: `resolve()` checks containment against a candidate it has
        # resolved, so a base reached through a symlink matched nothing and every path was refused —
        # a blank page with no message, which is the failure this function exists to prevent.
        return _real(d) if (d / "app.html").is_file() else None
    # A directory is not a build. An empty `web/` would become the root and then deny every path,
    # which on the glass is a blank page with nothing to say why.
    packaged = paths.PACKAGE_DIR / "web"
    if (packaged / "app.html").is_file():
        return _real(packaged)
    if paths._CLONE is not None:
        built = paths.REPO_ROOT / ".next-export"
        if (built / "app.html").is_file():
            return _real(built)
    return None


def _real(d: Path) -> Path:
    try:
        return d.resolve()
    except OSError:
        return d


def describe() -> dict:
    """What is on disk and where it came from, for the two commands whose job is to explain.

    `serve` used to tell a wheel's user to clone the repository while it was already serving them the
    app, and `doctor` said nothing at all about the UI."""
    import json

    where = root()
    if where is None:
        named = os.getenv("KOTOBA_FRONTEND_DIR")
        return {"kind": "none", "named": named or ""}
    kind = "packaged" if where == paths.PACKAGE_DIR / "web" else "export"
    out = {"kind": kind, "path": str(where), "version": "", "files": 0}
    try:
        stamp = json.loads((where / "kotoba-build.json").read_text(encoding="utf-8"))
        out["version"] = str(stamp.get("version") or "")
        out["files"] = int(stamp.get("files") or 0)
    except Exception:
        # Only a packaged build is stamped. A local export and a directory somebody pointed at are
        # builds too, and reporting them as nought files read as an empty one.
        out["files"] = sum(1 for p in where.rglob("*") if p.is_file())
    return out


def root() -> Path | None:
    global _ROOT
    if _ROOT is _UNSET:
        _ROOT = _find_root()
    return _ROOT if isinstance(_ROOT, Path) else None


def _page(first: str) -> str:
    """`app.html`, `app.txt` and `app/…` are three spellings of one route and get one answer."""
    for suffix in (".html", ".txt"):
        if first.endswith(suffix):
            return first[: -len(suffix)]
    return first


def resolve(url_path: str) -> Path | None:
    """A URL to a file inside the build, or None. Two independent guards: the segments are checked
    before anything touches disk, and containment is re-checked after `.resolve()` collapses symlinks.
    The syntactic pass is deliberately segment-wise — a real chunk is named `0k90..qj97h~j.js`, so a
    substring test for `..` would refuse a file the app needs."""
    base = root()
    if base is None:
        return None
    segments = [s for s in url_path.split("/") if s]
    if not segments:
        return None
    for s in segments:
        if s in (".", "..") or "\0" in s or "\\" in s:
            return None
    rel = "/".join(segments)
    for candidate in (rel, rel + ".html", rel + "/index.html"):
        try:
            found = (base / candidate).resolve()
        except OSError:
            continue
        if found != base and not found.is_relative_to(base):
            continue
        if found.is_file():
            return found
    return None


def classify(url_path: str) -> Access:
    base = root()
    if base is None:
        return Access.DENIED
    segments = [s for s in url_path.split("/") if s]
    if not segments:
        return Access.PUBLIC     # the root is a redirect with no body of its own
    if _page(segments[0]) not in PUBLIC and _page(segments[0]) not in GATED:
        return Access.DENIED     # keeps every API path off the disk entirely
    found = resolve(url_path)
    if found is None:
        return Access.DENIED
    page = _page(found.relative_to(base).parts[0])
    if page in GATED:
        return Access.GATED
    return Access.PUBLIC if page in PUBLIC else Access.DENIED


def is_public(url_path: str) -> bool:
    return classify(url_path) is Access.PUBLIC


def page_of(found: Path) -> str:
    """Which route a resolved file belongs to."""
    base = root()
    return _page(found.relative_to(base).parts[0]) if base else ""


_STRIP = str.maketrans("", "", "\t\n\r")


def safe_next(value: str | None) -> str:
    """Where the gate may send someone afterwards. The browser's own parser strips raw TAB, LF and CR
    before it parses and folds a backslash into a slash, so `/\\evil.com` reaches a foreign host while
    looking like a local path; reproducing those two moves is what makes containment real. Refusing
    every control character is stricter than the frontend and also makes a split header impossible."""
    if not value or not value.startswith("/"):
        return HOME
    cleaned = value.translate(_STRIP)
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in cleaned):
        return HOME
    cleaned = cleaned.replace("\\", "/")
    if not cleaned.startswith("/") or cleaned.startswith("//"):
        return HOME
    cleaned = cleaned.split("#", 1)[0]
    path, sep, query = cleaned.partition("?")
    return path + (sep + query if sep else "")
