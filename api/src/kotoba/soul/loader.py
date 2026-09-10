"""Parse a SOUL.md file into soul_config columns, and seed the DB from it on startup.

Layout: frontmatter `key: value` lines, a `---` separator, then `## Section` blocks. Frontmatter
name/language/voice_id/avatar_model map onto columns; the sections map as
    ## Personality             -> personality
    ## How to address the user -> address_style
    ## Emotional rules         -> emotional_rules
    ## Tool voice patterns     -> tool_patterns   (raw markdown, parsed later by voice_patterns)
    ## Quirks                  -> quirks
"""
from __future__ import annotations

import logging

from pathlib import Path
from kotoba.paths import API_DIR, DATA_DIR, REPO_ROOT, home_dir

log = logging.getLogger("kotoba.soul")

_SECTION_TO_COLUMN = {
    "personality": "personality",
    "how to address the user": "address_style",
    "emotional rules": "emotional_rules",
    "tool voice patterns": "tool_patterns",
    "quirks": "quirks",
}

_FRONTMATTER_KEYS = {"name", "language", "voice_id", "avatar_model"}

_DEFAULTS = {
    "name": None,
    "language": "auto",
    "voice_id": None,
    "avatar_model": "mao_pro/runtime/mao_pro.model3.json",
    "personality": "",
    "address_style": "name",
    "emotional_rules": "",
    "tool_patterns": "",
    "quirks": "",
}


DEFAULT_SOUL_PATH = "./soul/default.md"


def resolve_soul_path(soul_path: str) -> Path:
    """Find the SOUL file `soul_path` names.

    The distinction that matters is the SHIPPED DEFAULT versus a path the user chose. The default must
    never follow the current directory — a CLI started inside someone's project would otherwise load
    that project's `soul/default.md` as her personality. Anything the user set is honoured, including
    relative to where they ran the command, because that is the only way to point at their own file.

    The packaged copy closes the list: a wheel install has no repo `soul/` at all, and from a clone it
    is byte-identical, so it can never shadow an edit. Ahead of it sits the home directory, the only
    writable place a wheel install has. Duplicates drop out: on a wheel two candidates collapse."""
    api_dir, repo_root = API_DIR, REPO_ROOT
    given = Path(soul_path).expanduser()
    if given.is_absolute():
        candidates = [given]
    elif soul_path == DEFAULT_SOUL_PATH:
        candidates = [api_dir / soul_path, repo_root / "soul" / "default.md",
                      home_dir() / "soul" / "default.md",
                      DATA_DIR / "soul" / "default.md"]
    else:
        rel = soul_path[2:] if soul_path.startswith("./") else soul_path
        candidates = [
            Path.cwd() / soul_path,          # the user asked for this one
            api_dir / soul_path,
            repo_root / rel,
            repo_root / "soul" / "default.md",   # last resort so she still boots
            DATA_DIR / "soul" / "default.md",
        ]
    candidates = list(dict.fromkeys(candidates))
    for c in candidates:
        if c.is_file():
            return c.resolve()
    raise FileNotFoundError(
        f"SOUL file not found. SOUL_PATH={soul_path!r}; tried: "
        + ", ".join(str(c) for c in candidates)
    )


def _place(source: Path, target: Path) -> None:
    """Whole or not at all. A write interrupted halfway leaves a file that `is_file()` accepts for
    ever after, and on a wheel that stub is what she boots from.

    Through `publish` because Windows REFUSES the rename while anyone holds the target open, and this
    one runs at boot — the likeliest moment for something else to be reading her personality."""
    import os
    import tempfile

    from kotoba.core.atomic_file import publish

    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(source.read_bytes())
            fh.flush()
            os.fsync(fh.fileno())       # the rename is atomic; the CONTENT reaching disk is not
        publish(tmp, str(target))
    except BaseException:
        Path(tmp).unlink(missing_ok=True)   # a fixed `.part` name outlived its failure and was adopted
        raise


def seed_home_copies() -> Path | None:
    """Put a personality file and the templates somewhere the person can actually open.

    An install from a wheel has no `soul/` on disk: her personality lives inside site-packages, which
    a reinstall overwrites and nobody should be editing anyway. So the invitation to make her yours
    had no file to address. Copies land in the home once and are never overwritten afterwards — an
    edit is the whole point, and a second run must not undo one. Returns the file to edit, or None if
    the home cannot be written, which is not worth failing a setup over."""
    home = home_dir() / "soul"
    try:
        home.mkdir(parents=True, exist_ok=True)
        default = home / "default.md"
        if not default.is_file():
            _place(DATA_DIR / "soul" / "default.md", default)
        packed = DATA_DIR / "soul" / "templates"
        if packed.is_dir():
            (home / "templates").mkdir(exist_ok=True)
            for one in packed.glob("*.md"):
                target = home / "templates" / one.name
                if not target.is_file():
                    _place(one, target)
        return default
    except OSError:
        log.debug("could not seed the personality files into the home", exc_info=True)
        return None


def load_soul_from_file(soul_path: str) -> dict:
    path = resolve_soul_path(soul_path)
    text = path.read_text(encoding="utf-8")
    return parse_soul_markdown(text)


def parse_soul_markdown(text: str) -> dict:
    soul = dict(_DEFAULTS)

    lines = text.splitlines()
    sep_idx = next((i for i, ln in enumerate(lines) if ln.strip() == "---"), None)
    frontmatter = lines[:sep_idx] if sep_idx is not None else []
    body = lines[sep_idx + 1:] if sep_idx is not None else lines

    for ln in frontmatter:
        s = ln.strip()
        if not s or s.startswith("#") or ":" not in s:
            continue
        key, _, value = s.partition(":")
        key = key.strip().lower()
        value = value.split("#", 1)[0].strip()
        if key in _FRONTMATTER_KEYS:
            soul[key] = value or None

    current_col: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if current_col is not None:
            soul[current_col] = "\n".join(buf).strip()

    for ln in body:
        if ln.startswith("## "):
            flush()
            heading = ln[3:].strip().lower()
            current_col = _SECTION_TO_COLUMN.get(heading)
            buf = []
        elif current_col is not None:
            buf.append(ln)
    flush()

    return soul


async def sync_from_file(db, soul_path: str) -> dict:
    """Make soul/default.md the source of truth for personality on every startup.

    First run: insert the whole config. Later runs: refresh the developer-authored personality
    fields (personality, tool_patterns, quirks, ...) from the file, but PRESERVE runtime fields
    (name, language) that onboarding sets. This is what lets edits to default.md take effect even
    though the DB now persists across restarts.
    """
    existing = await db.fetch_soul_config()
    soul = load_soul_from_file(soul_path)
    if not existing:
        await db.seed_soul_config(soul)
    else:
        await db.sync_soul_config_fields(soul)
    return await db.fetch_soul_config()

