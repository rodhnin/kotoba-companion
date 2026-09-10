from __future__ import annotations

import asyncio
import importlib
import json

import pytest


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "memory"))
    import kotoba.core.user_memory as um

    importlib.reload(um)
    return um


SUBJECT_SWAPS = [
    ("Friend Marco is 30 years old", "Friend Lucia is 30 years old"),
    ("Friend Marco lives in Madrid", "Friend Lucia lives in Madrid"),
    ("Colleague Ana works as a designer", "Colleague Bea works as a designer"),
    ("the Madrid office has 3 floors", "the Barcelona office has 3 floors"),
]

DISTINCT_FAMILY = [
    ("Mother is named Ana", "Sister is named Ana"),
    ("Son is 5 years old", "Daughter is 3 years old"),
    ("Has a cat named Luna", "Has a dog named Luna"),
    ("Sister lives in Madrid", "Brother lives in Madrid"),
    ("the cat is 3 years old", "the dog is 4 years old"),
    ("Prefers jasmine tea", "Prefers mint tea"),
    ("team meeting is in Madrid", "team meeting is on Tuesday"),
]

REWORDED = [
    ("Prefers jasmine tea", "Likes jasmine tea"),
    ("Looking for Fulano Perez on Facebook", "User is searching for Fulano Pérez on Facebook"),
    ("Looking for Fulano Perez on Facebook", "Is trying to find Fulano Pérez on Facebook"),
    ("Works as a security engineer", "Is a security engineer"),
    ("Favorite game is Elden Ring", "User's favorite game is Elden Ring."),
    ("Likes jasmine tea from Kyoto every morning", "Likes tea"),
]


@pytest.mark.parametrize("stored,new", SUBJECT_SWAPS)
def test_a_proper_noun_subject_swap_is_another_fact_and_retires_nothing(mem, stored, new):
    assert mem.write_fact(stored, "people")["written"] is True
    res = mem.write_fact(new, "people")
    assert res["written"] is True and res["retired"] == []
    assert mem.existing_facts() == [stored, new]


def test_corrects_refuses_a_swap_inside_the_subject_and_keeps_one_inside_the_value(mem):
    assert mem._corrects("Friend Lucia is 30 years old", "Friend Marco is 30 years old") is False
    assert mem._corrects("Friend Marco is 31 years old", "Friend Marco is 30 years old") is True
    assert mem._corrects("Son is 6 years old", "Son is 5 years old") is True


def test_the_extractor_path_keeps_the_bystander(mem, monkeypatch):
    import kotoba.core.llm as llm
    import kotoba.core.memory as memory

    async def _extract(prompt, *, max_output_tokens=512):
        return json.dumps({"facts": [{"fact": "Friend Lucia is 30 years old", "topic": "people"}]})

    monkeypatch.setattr(llm, "utility_extract", _extract)
    mem.append_fact("Friend Marco is 30 years old", "people")
    asyncio.run(memory.extract_and_save_memory("my friend Lucia is 30", db=None))
    assert mem.existing_facts() == ["Friend Marco is 30 years old", "Friend Lucia is 30 years old"]


def test_memory_write_reports_no_deletion_for_a_subject_swap(mem):
    from kotoba.tools.builtin import memory_write

    mem.append_fact("Friend Marco is 30 years old", "people")
    msg = asyncio.run(memory_write.execute({"fact": "Friend Lucia is 30 years old", "topic": "people"}, None))
    assert msg.startswith("Saved under")
    assert "REPLACED" not in msg
    assert "Friend Marco is 30 years old" in mem.existing_facts()


@pytest.mark.parametrize("stored,new", DISTINCT_FAMILY)
def test_a_distinct_fact_with_the_same_frame_is_written_beside_the_stored_one(mem, stored, new):
    assert mem.append_fact(stored, "family") is True
    res = mem.write_fact(new, "family")
    assert res["written"] is True and res["retired"] == [] and res["duplicate_of"] is None
    assert mem.existing_facts() == [stored, new]


@pytest.mark.parametrize("stored,new", REWORDED)
def test_a_reworded_repeat_still_leaves_one_copy(mem, stored, new):
    assert mem.append_fact(stored, "misc") is True
    mem.write_fact(new, "misc")
    assert len(mem.existing_facts()) == 1


def test_no_overlap_threshold_can_separate_a_rewording_from_a_sibling(mem):
    reworded = (mem._keywords("Likes jasmine tea"), mem._keywords("Prefers jasmine tea"))
    sibling = (mem._keywords("Has a dog named Luna"), mem._keywords("Has a cat named Luna"))
    for threshold in (0.34, 0.4, 0.5, 0.6, 0.67, 0.75):
        mem._DUP_JACCARD = threshold
        assert mem._near_duplicate(*reworded) == mem._near_duplicate(*sibling), threshold
    assert mem._distinct("Has a dog named Luna", "Has a cat named Luna") is True
    assert mem._distinct("Likes jasmine tea", "Prefers jasmine tea") is False


@pytest.mark.parametrize("stored,new", [
    ("Lives in Madrid", "Sister lives in Madrid"),
    ("Sister lives in Madrid", "Lives in Madrid"),
    ("Favorite color is blue", "Favorite color of the user's daughter is blue"),
    ("Jordan is 30 years old", "Jordan's dog is 3 years old"),
])
def test_a_fact_about_somebody_else_never_supersedes_the_users_own(mem, stored, new):
    assert mem.append_fact(stored, "misc") is True
    res = mem.write_fact(new, "misc")
    assert res["written"] is True and res["retired"] == []
    assert sorted(mem.existing_facts()) == sorted([stored, new])


def test_a_refinement_of_the_same_subject_still_supersedes(mem):
    assert mem.append_fact("Works as a security engineer", "work") is True
    res = mem.write_fact("Works as a security engineer at Acme Corp in Madrid", "work")
    assert res["retired"] == ["Works as a security engineer"]
    assert mem.append_fact("Sister lives in Madrid", "family") is True
    res = mem.write_fact("Sister lives in Madrid with two cats", "family")
    assert res["retired"] == ["Sister lives in Madrid"]


def test_the_maintenance_sweep_keeps_distinct_family_facts(mem):
    p = mem.topic_path("family")
    p.parent.mkdir(parents=True, exist_ok=True)
    facts = [f for pair in DISTINCT_FAMILY for f in pair]
    p.write_text("# family\n\n" + "".join(f"- {f}\n" for f in facts), encoding="utf-8")
    res = mem.dedupe_store()
    assert res["removed"] == 0
    assert mem.existing_facts() == facts


def test_the_language_gate_still_answers_a_spanish_fact_that_is_no_longer_a_duplicate(mem):
    from kotoba.tools.builtin import memory_write

    mem.append_fact("andrea vive en madrid", "home")
    msg = asyncio.run(memory_write.execute({"fact": "andrea vive en barcelona", "topic": "home"}, None))
    assert msg.startswith("NOT SAVED") and "ENGLISH" in msg
    assert mem.existing_facts() == ["andrea vive en madrid"]
