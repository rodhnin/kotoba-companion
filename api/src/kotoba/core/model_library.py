"""Live2D models installed on this machine, read-only — nothing here writes, unpacks or downloads.

They live OUTSIDE the repo (~/.kotoba/models, override KOTOBA_MODELS_DIR) because `public/` is not
writable at runtime: a wheel install has no repo, and a container freezes its image.

A model is a DIRECTORY holding a `.model3.json`, and the whole directory has to be reachable: Cubism
resolves its textures, motions and expressions relative to that entry file, so a model whose siblings
404 loads and then shows nothing at all. Two refusals decide what leaves — a path that escapes, and a
file type a browser would run, since this is somebody's unpacked download served on the app's own
origin."""
from __future__ import annotations

import os
from pathlib import Path

from kotoba.core.path_security import PathSecurityError, validate_within_dir
from kotoba.paths import HOME_DIR

_ENTRY_SUFFIX = ".model3.json"
_MAX_DEPTH = 3   # Live2D's own sample keeps its entry at runtime/<name>.model3.json

_SERVABLE = {
    ".json": "application/json",
    ".moc3": "application/octet-stream",
    ".moc": "application/octet-stream",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def models_dir() -> Path:
    return Path(os.getenv("KOTOBA_MODELS_DIR", str(HOME_DIR / "models"))).expanduser().resolve()


def _entry_of(d: Path) -> str | None:
    """The model's entry file relative to its own folder, shallowest first. Depth-bounded and never
    through a symlinked directory: a loop in somebody's unpacked download would otherwise hang the
    request that lists what is installed."""
    frontier = [(d, 0)]
    while frontier:
        here, depth = frontier.pop(0)
        try:
            # By NAME, so the entry file a model is worn through is the same one on either platform:
            # sorting Path objects compares them case-folded under Windows.
            children = sorted(here.iterdir(), key=lambda c: c.name)
        except OSError:
            continue
        for c in children:
            if c.name.endswith(_ENTRY_SUFFIX) and c.is_file():
                return c.relative_to(d).as_posix()
            if depth + 1 < _MAX_DEPTH and c.is_dir() and not c.is_symlink():
                frontier.append((c, depth + 1))
    return None


def installed() -> list[dict]:
    """Every model on disk as {dir, entry} — the folder somebody dropped in, and where its entry file
    sits inside it. The scan is the truth about the entry: people unpack the same model flat or under
    `runtime/`, and a stored path is a guess. A dot-named folder is an install still being staged —
    `model_install` stages beside its destination so the last step is a rename, and a model that is
    half-written must never be offered as one somebody can wear."""
    try:
        folders = sorted((p for p in models_dir().iterdir()
                          if p.is_dir() and not p.name.startswith(".")), key=lambda p: p.name)
    except OSError:
        return []
    found = []
    for d in folders:
        entry = _entry_of(d)
        if entry is not None:
            found.append({"dir": d.name, "entry": entry})
    return found


def measure(name: str) -> dict:
    """How many files a model is and how much disk it takes, walked now rather than remembered.

    The first-run card states this, and it may only state it for the model actually on disk: the
    installer's own count is the truth for one install and says nothing on the next visit. Symlinked
    directories are not followed and unreadable entries are skipped, for the same reason `_entry_of`
    bounds its walk — somebody else's unpacked folder is not ours to trust."""
    root = models_dir() / (name or "").strip().lstrip("/").split("/", 1)[0]
    files = 0
    total = 0
    frontier = [(root, 0)]
    while frontier:
        here, depth = frontier.pop()
        try:
            children = list(here.iterdir())
        except OSError:
            continue
        for c in children:
            try:
                if c.is_dir():
                    if depth + 1 < _MAX_DEPTH and not c.is_symlink():
                        frontier.append((c, depth + 1))
                elif c.is_file():
                    files += 1
                    total += c.stat().st_size
            except OSError:
                continue
    return {"files": files, "bytes": total}


def selection(configured: str, models: list[dict]) -> dict | None:
    """Which installed model actually answers, for a `soul_config.avatar_model` of `<dir>/<entry>`.

    A configured model nobody installed is the FRESH-INSTALL path rather than an edge case, so the
    first one on disk stands in: a face somebody did not ask for beats no face at all, and there is
    nothing else the value could have meant."""
    if not models:
        return None
    want = (configured or "").strip().lstrip("/")
    folder = want.split("/", 1)[0]
    for m in models:
        if f"{m['dir']}/{m['entry']}" == want or m["dir"] == folder:
            return m
    return models[0]


def resolve(rel_path: str) -> Path | None:
    """A servable file inside the models directory, or None. The type check runs on the RESOLVED path,
    so it judges the file that would actually be opened rather than the spelling asked for."""
    rel = (rel_path or "").strip().lstrip("/")
    if not rel:
        return None
    try:
        p = validate_within_dir(rel, models_dir())
    except PathSecurityError:
        return None
    if p.suffix.lower() not in _SERVABLE:
        return None
    return p if p.is_file() else None


def media_type_for(path: Path) -> str:
    """Stated, never guessed: mimetypes has no answer for `.moc3` and the fallback used elsewhere is
    text, which is a document."""
    return _SERVABLE.get(path.suffix.lower(), "application/octet-stream")
