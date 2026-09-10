"""An end-to-end finding: durable memory piled up near-duplicate facts.

De-dup was per-topic only, and the model invents synonymous topics (proyecto/proyectos,
search/search-activity/social/…), so the same fact landed under each one and never collapsed. Accents
broke keyword matching too — 'Jesús' and 'Jesus' were different words. De-dup is now cross-topic,
accent-normalised and fuzzy (keyword overlap), so reworded and cross-topic repeats are rejected while
genuinely distinct facts persist."""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "memory"))
    import kotoba.core.user_memory as um
    importlib.reload(um)
    return um


def test_cross_topic_verbatim_duplicate_rejected(mem):
    """The same fact under a DIFFERENT but synonymous topic must not be stored a second time."""
    assert mem.append_fact("Tiene un proyecto relacionado con Notion", "proyecto") is True
    assert mem.append_fact("Tiene un proyecto relacionado con Notion", "proyectos") is False
    assert mem.existing_facts().count("Tiene un proyecto relacionado con Notion") == 1


def test_reworded_and_accented_duplicate_rejected(mem):
    """Reworded, accented and filed under another topic, and still caught: accents are normalised away
    before the fuzzy keyword overlap runs."""
    assert mem.append_fact("Looking for Fulano Perez on Facebook", "search") is True
    assert mem.append_fact("User is searching for Fulano Pérez on Facebook", "search-activity") is False
    assert mem.append_fact("Is trying to find Fulano Pérez on Facebook", "social") is False
    assert len([f for f in mem.existing_facts() if "ulano" in f]) == 1


def test_distinct_facts_still_persist(mem):
    assert mem.append_fact("Uses the Brave browser", "preferences") is True
    assert mem.append_fact("Building Kotoba, an AI VTuber companion", "projects") is True
    assert mem.append_fact("User uses Facebook email someone@example.com for sign-in", "preferences") is True
    facts = mem.existing_facts()
    assert "Uses the Brave browser" in facts
    assert "Building Kotoba, an AI VTuber companion" in facts
    assert len(facts) == 3


def test_empty_or_blank_fact_not_written(mem):
    assert mem.append_fact("   ", "task") is False
    assert mem.existing_facts() == []


def test_ephemeral_task_actions_not_saved(mem):
    """The AUTOMATIC extractor screens task narration out — that is what filter_ephemeral is for.
    An explicit memory_write does not filter: the model already decided the fact is durable, and a
    dropped fact there is reported as "already remembered", which nothing retries after."""
    kw = {"filter_ephemeral": True}
    assert mem.append_fact("Creating a folder named test", "file-management", **kw) is False
    assert mem.append_fact("Running npm version command", "development", **kw) is False
    assert mem.append_fact("Adding a line that says 'el pepe' to saludo2.txt", "files", **kw) is False
    assert mem.append_fact("Creating a Notion page titled 'Prueba Kotoba3'", "notion", **kw) is False
    assert mem.append_fact("Uses the Brave browser", "preferences", **kw) is True
    assert mem.append_fact("Building Kotoba, an AI VTuber companion", "projects", **kw) is True
    assert len(mem.existing_facts()) == 2


def test_search_finds_facts_across_topics(mem):
    """Search reaches every topic at once and matches accent-insensitively, without dragging in facts
    that have nothing to do with the query."""
    mem.append_fact("User has a Facebook account", "social-media")
    mem.append_fact("Looking for Fulano Pérez on Facebook", "search")
    mem.append_fact("Building Kotoba, an AI VTuber companion", "projects")
    hits = mem.search_facts("facebook")
    facts = [h["fact"] for h in hits]
    assert any("Facebook account" in f for f in facts)
    assert any("Fulano Pérez" in f for f in facts)
    assert all("Kotoba" not in f for f in facts)


def test_recall_falls_back_to_search_on_topic_miss(mem):
    """The model guesses the topic 'facebook', which exists as no slug at all, so recall falls back to
    searching and finds the fact anyway."""
    mem.append_fact("User has a Facebook account", "social-media")
    res = mem.recall("facebook")
    assert res.get("searched") is True
    assert any("Facebook account" in f for f in res["facts"])


def test_dedupe_store_collapses_and_consolidates(mem):
    """The retroactive cleanup, against the mess as it was really found: one fact under synonym topics,
    plus reworded repeats.

    The seeds are written straight to disk to bypass the live de-dup — the duplicates being collapsed
    here are the ones that predate it. Afterwards the Notion fact survives exactly once, the synonym
    topic that held only a duplicate is gone, and one Fulano Pérez variant is left across all topics."""
    mem.append_fact("alpha unique fact", "projects")
    mem.append_fact("Tiene un proyecto relacionado con Notion", "proyecto")
    mem._atomic_write(mem.topic_path("proyectos"),
                      "# proyectos\n\nx\n\n- Tiene un proyecto relacionado con Notion\n")
    mem._atomic_write(mem.topic_path("search"),
                      "# search\n\nx\n\n- Looking for Fulano Perez on Facebook\n")
    mem._atomic_write(mem.topic_path("social"),
                      "# social\n\nx\n\n- Is trying to find Fulano Pérez on Facebook\n")
    res = mem.dedupe_store()
    assert res["removed"] >= 2
    facts = mem.existing_facts()
    assert facts.count("Tiene un proyecto relacionado con Notion") == 1
    assert "proyectos" not in {s for s, _ in mem.list_topics()}
    assert len([f for f in facts if "ulano" in f]) == 1
