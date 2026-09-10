"""Skills get YAML frontmatter so a skill can declare WHEN it applies
and which toolset it needs — e.g. an 'operating-websites' skill bound to the browser toolset, surfaced only
when the browser is available. Must stay backward-compatible with the current first-`#`-title / first-
paragraph format, for a skill written without frontmatter.
"""
from __future__ import annotations

import kotoba.core.skill_docs as sd


_WITH_FM = """---
name: operating-websites
description: Drive a website end-to-end (login, search, navigate) via the browser.
when_to_use: any multi-step task in a real website
requires_toolsets: [browser]
---

# Operating websites

Body text here.
"""

_NO_FM = """# plain-heading-skill
Build distinctive web interfaces.

More body.
"""


def test_parse_frontmatter_fields(tmp_path, monkeypatch):
    d = tmp_path / "skills"
    d.mkdir()
    (d / "operating-websites.md").write_text(_WITH_FM)
    monkeypatch.setenv("KOTOBA_SKILLS_DIR", str(d))

    skills = sd.list_skills()
    s = next(x for x in skills if x["name"] == "operating-websites")
    assert s["description"].startswith("Drive a website")
    assert s["requires_toolsets"] == ["browser"]


def test_backward_compatible_without_frontmatter(tmp_path, monkeypatch):
    d = tmp_path / "skills"
    d.mkdir()
    (d / "plain-heading-skill.md").write_text(_NO_FM)
    monkeypatch.setenv("KOTOBA_SKILLS_DIR", str(d))

    s = sd.list_skills()[0]
    assert s["title"] == "plain-heading-skill"
    assert "distinctive" in s["description"].lower()
    assert s["requires_toolsets"] == []          # no frontmatter → no toolset requirement (always eligible)


def test_skills_for_toolsets_filters_by_requirement(tmp_path, monkeypatch):
    d = tmp_path / "skills"
    d.mkdir()
    (d / "operating-websites.md").write_text(_WITH_FM)
    (d / "plain-heading-skill.md").write_text(_NO_FM)
    monkeypatch.setenv("KOTOBA_SKILLS_DIR", str(d))

    # browser available → both eligible (operating-websites requires it; the plain one requires none)
    names_with = {s["name"] for s in sd.skills_for_toolsets({"browser", "web"})}
    assert names_with == {"operating-websites", "plain-heading-skill"}

    # browser NOT available → operating-websites filtered out; the unrequired one stays
    names_without = {s["name"] for s in sd.skills_for_toolsets({"web"})}
    assert names_without == {"plain-heading-skill"}


def test_skill_titles_still_works(tmp_path, monkeypatch):
    d = tmp_path / "skills"
    d.mkdir()
    (d / "operating-websites.md").write_text(_WITH_FM)
    monkeypatch.setenv("KOTOBA_SKILLS_DIR", str(d))
    titles = sd.skill_titles()
    assert any("operating-websites" in t for t in titles)
