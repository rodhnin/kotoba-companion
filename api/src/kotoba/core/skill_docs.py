"""Skills as documents — installable knowledge/instructions Kotoba reads on demand.

A "skill" here is a markdown file under soul/skills/ (e.g. research.md): not code, but guidance the
model pulls up when relevant ("search your frontend skills and build me a page"). The system prompt lists
the available skill titles; she calls skill_view to load a body when she needs it.

Jailed to the skills dir (KOTOBA_SKILLS_DIR override) — a skill name can never escape it.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from kotoba.paths import DATA_DIR, REPO_ROOT


def skills_dir() -> Path:
    """Override > the clone's soul/skills (or ~/.kotoba/soul/skills on a wheel, if the user made one) >
    the copy packaged inside the wheel."""
    env = os.getenv("KOTOBA_SKILLS_DIR")
    if env:
        return Path(env).expanduser()
    d = REPO_ROOT / "soul" / "skills"
    return d if d.is_dir() else DATA_DIR / "soul" / "skills"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split optional YAML frontmatter (--- ... ---) from the markdown body. Returns (meta, body). No
    frontmatter → ({}, text). YAML errors degrade gracefully to ({}, original text)."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    fm_raw = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    try:
        import yaml

        meta = yaml.safe_load(fm_raw) or {}
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    return meta, body


def _title_and_desc(text: str) -> tuple[str, str]:
    title, desc = "", ""
    for line in text.splitlines():
        s = line.strip()
        if not title and s.startswith("# "):
            title = s[2:].strip()
            continue
        if title and s and not s.startswith("#"):
            desc = s
            break
    return title, desc


def _requires_toolsets(meta: dict) -> list[str]:
    """Normalize requires_toolsets from frontmatter: a list or a single string. Empty list = no
    requirement, and the skill is always eligible."""
    req = meta.get("requires_toolsets")
    if req is None:
        return []
    if isinstance(req, str):
        return [req]
    if isinstance(req, list):
        return [str(x) for x in req]
    return []


def list_skills() -> list[dict]:
    """[{name, title, description, requires_toolsets}] for every skill doc available. Supports YAML
    frontmatter (name/description/requires_toolsets); falls back to the first-`#`-title /
    first-paragraph convention when there's no frontmatter (backward compatible)."""
    d = skills_dir()
    out: list[dict] = []
    if d.exists():
        for f in sorted(d.glob("*.md")):
            meta, body = _parse_frontmatter(f.read_text(errors="replace", encoding="utf-8"))
            title_fb, desc_fb = _title_and_desc(body)
            name = str(meta.get("name") or f.stem)
            title = str(meta.get("name") or title_fb or f.stem)
            desc = str(meta.get("description") or desc_fb or "")
            out.append({
                "name": name,
                "title": title,
                "description": desc,
                "requires_toolsets": _requires_toolsets(meta),
            })
    return out


def skills_for_toolsets(active_toolsets: set[str]) -> list[dict]:
    """Skills eligible given the currently-active toolsets: a skill with `requires_toolsets` is included
    only when ALL its required toolsets are active; a skill with no requirement is always included, so a
    browser skill surfaces only once the browser is connected."""
    active = set(active_toolsets or set())
    return [s for s in list_skills() if all(t in active for t in s.get("requires_toolsets", []))]


def skill_titles() -> list[str]:
    """Compact 'name — description' lines for the system prompt."""
    return [f"{s['name']} — {s['description']}" if s["description"] else s["name"] for s in list_skills()]


def view_skill(name: str) -> str | None:
    """Return a skill's full markdown body, or None if it doesn't exist. Name is slugified + jailed."""
    slug = _slug(name)
    if not slug:
        return None
    p = (skills_dir() / f"{slug}.md").resolve()
    root = skills_dir().resolve()
    if p != root and root not in p.parents:  # jail
        return None
    return p.read_text(errors="replace", encoding="utf-8") if p.exists() else None
