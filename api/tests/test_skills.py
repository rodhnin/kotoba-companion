"""Skills as documents: listing and viewing them, jailed to the skills directory."""
from __future__ import annotations

import importlib

import pytest


def test_a_real_shipped_skill_is_available():
    from kotoba.core import skill_docs

    importlib.reload(skill_docs)  # use the real soul/skills dir
    names = [s["name"] for s in skill_docs.list_skills()]
    assert "research" in names
    body = skill_docs.view_skill("research")
    assert body and "research" in body and "sources" in body.lower()


def test_skill_titles_compact():
    from kotoba.core import skill_docs

    importlib.reload(skill_docs)
    titles = skill_docs.skill_titles()
    assert any(t.startswith("research") for t in titles)


@pytest.fixture
def skilldir(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_SKILLS_DIR", str(tmp_path / "skills"))
    from kotoba.core import skill_docs

    importlib.reload(skill_docs)
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "cooking.md").write_text("# cooking\n\nHow to plan simple meals.\n")
    return skill_docs


def test_list_and_view_custom(skilldir):
    names = [s["name"] for s in skilldir.list_skills()]
    assert names == ["cooking"]
    assert "simple meals" in skilldir.view_skill("cooking")


def test_view_unknown_returns_none(skilldir):
    assert skilldir.view_skill("nonexistent") is None


def test_view_cannot_escape_jail(skilldir):
    assert skilldir.view_skill("../../../etc/passwd") is None
    assert skilldir.view_skill("../secrets") is None
