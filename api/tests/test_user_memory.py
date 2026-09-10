"""Structured user memory: a small index (USER.md) over per-topic files.

The store has to stay scalable (the index cannot grow with the number of facts), jailed (a topic
name is untrusted input and must never escape `topics/`), and searchable in both languages the user
writes in. Recall is a starting point, not a wall: a topic hit still runs the cross-topic search."""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def mem(tmp_path, monkeypatch):
    """Point the memory store at a temp dir and reload the module so paths re-read the env."""
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "memory"))
    import kotoba.core.user_memory as um

    importlib.reload(um)
    return um


def test_fact_goes_to_topic_file_and_index(mem):
    """One fact lands in two places: its topic file, and the index — which both points at that file
    under ## Topics and carries the fact itself under ## Recent."""
    assert mem.append_fact("Works as a security engineer", "work") is True
    tp = mem.topic_path("work")
    assert tp.exists() and "Works as a security engineer" in tp.read_text(encoding="utf-8")
    idx = mem.read_user_md()
    assert "## Topics" in idx and "topics/work.md" in idx
    assert "Works as a security engineer" in idx


def test_free_form_topics_create_separate_files(mem):
    mem.append_fact("Building Kotoba, an AI VTuber", "kotoba-project")
    mem.append_fact("Has a dog named Luna", "pets")
    slugs = {s for s, _ in mem.list_topics()}
    assert slugs == {"kotoba-project", "pets"}


def test_index_stays_small_as_topics_grow(mem):
    """200 facts across many topics, and USER.md stays tiny — the detail lives in the topic files.

    Every fact is given all-unique tokens so the fuzzy cross-topic de-dup correctly keeps every one:
    this is a test about index size, not about de-dup."""
    for i in range(200):
        mem.append_fact(f"alpha{i} beta{i} gamma{i} delta{i}", f"topic{i % 8}")
    idx_lines = len(mem.read_user_md().splitlines())
    assert idx_lines < 60, f"index ballooned to {idx_lines} lines"
    assert len(mem.existing_facts()) == 200


def test_recall_returns_facts_and_path(mem):
    mem.append_fact("Prefers dark mode", "preferences")
    r = mem.recall("preferences")
    assert r["path"] == "topics/preferences.md"
    assert "Prefers dark mode" in r["facts"]


def test_recall_missing_topic_lists_available(mem):
    mem.append_fact("x fact about y", "work")
    r = mem.recall("nonexistent")
    assert r["facts"] == [] and "work" in r["available_topics"]


def test_dedup_within_topic(mem):
    assert mem.append_fact("Favorite game is Elden Ring", "games") is True
    assert mem.append_fact("User's favorite game is Elden Ring.", "games") is False
    assert len(mem.topic_facts("games")) == 1


def test_slug_cannot_escape_jail(mem):
    """A topic name is untrusted input: a traversal one is neutralised into a safe slug inside
    `topics/`, and nothing is written outside the jail."""
    mem.append_fact("sneaky", "../../etc/evil")
    for slug, _ in mem.list_topics():
        p = mem.topic_path(slug)
        assert str(p.resolve()).startswith(str(mem.topics_dir().resolve()))
    assert not (mem.memory_dir().parent / "etc").exists()


def test_recent_capped_in_index(mem):
    """Recent is capped at RECENT_CAP, plus at most one topics-summary line.

    The pointer line has to say the list is a WINDOW and how to reach the rest — the assertion is on
    that contract rather than on the wording, because without a total the line read as "this is
    everything", which it is not. The fuzzy cross-topic de-dup collapses most of these generated
    variants, which is its job, so the count asserted is what actually landed and not what was
    offered."""
    import itertools

    words = ["apple", "bridge", "cobalt", "dolphin", "ember", "falcon", "garnet", "harbor", "ivory", "jasmine"]
    facts = [f"Enjoys {a} with {b}" for a, b in itertools.combinations(words, 2)][:40]
    for f in facts:
        mem.append_fact(f, "misc")
    recent = mem.facts_for_prompt()
    assert len(recent) <= mem.RECENT_CAP + 1
    stored = len(mem.existing_facts())
    pointer = next(r for r in recent if "memory_recall" in r)
    assert f"of {stored} facts" in pointer, pointer
    assert len(pointer) < 600, "the pointer line must stay bounded, not grow with the topic count"


def test_migrate_old_flat_user_md(mem):
    """An older flat USER.md — facts in one list, no ## Topics — migrates into the structured store.

    The three facts are deliberately distinct, with no shared boilerplate, so the fuzzy de-dup keeps
    all of them: a migration is not allowed to lose one."""
    mem.memory_dir().mkdir(parents=True, exist_ok=True)
    mem.user_md_path().write_text(
        "# USER.md\n\n## Facts\n- Lives in Madrid\n- Works as a wildlife photographer\n"
    )
    mem.migrate_from_db_facts(["Owns a cat named Mochi"])
    allf = mem.existing_facts()
    assert "Lives in Madrid" in allf and "Works as a wildlife photographer" in allf and "Owns a cat named Mochi" in allf
    assert "## Topics" in mem.read_user_md()


def test_migrate_noop_when_already_structured(mem):
    mem.append_fact("structured already", "work")
    mem.migrate_from_db_facts(["should not appear"])
    assert "should not appear" not in mem.existing_facts()


def test_no_tmp_files_left(mem):
    mem.append_fact("a", "t1")
    mem.append_fact("b", "t2")
    assert list(mem.memory_dir().rglob("*.tmp")) == []


def test_he_asks_in_the_first_person_and_still_finds_the_fact(mem):
    """The store writes about the user in the third person ("Wants…", "Lives in…") and the user asks
    in the first ("what do I want"), so before stemming the two shared no token at all and recall came
    back empty. Measured on a live store: 97 of 168 facts opened with `wants`."""
    mem.append_fact("Wants the CLI to look like the demo", "work")
    mem.append_fact("Lives in Madrid", "general")

    assert [h["fact"] for h in mem.search_facts("what do I want")] == [
        "Wants the CLI to look like the demo"]
    assert [h["fact"] for h in mem.search_facts("where do I live")] == ["Lives in Madrid"]


def test_stemming_unifies_the_two_languages_he_speaks(mem):
    """A trailing-vowel strip is what carries Spanish conjugation, and it unifies cognates for free —
    a real store holds both `favorite` and `favorito`. The fact fed here is Spanish ("their favourite
    colour is moss green") and the query is English."""
    mem.append_fact("Su color favorito es el verde musgo", "preferences")

    assert mem.search_facts("favorite color"), "an English query missed the Spanish fact"


def test_stemming_never_reached_the_write_side(mem):
    """`_keywords` feeds the duplicate and RETIREMENT machinery, and retiring is a permanent delete —
    a widened notion of sameness there would drop facts nobody contradicted. Search may widen; writing
    may not."""
    assert mem._keywords("Wants a report") == frozenset({"wants", "report"})
    assert mem._search_keys("Wants a report") == frozenset({"want", "report"})

    mem.append_fact("Wants a report on Live2D", "work")
    written = mem.write_fact("Want a report on Live2D", "work")
    assert written["retired"] == [], "search stemming leaked into what gets deleted"


def test_a_topic_hit_is_a_starting_point_not_a_wall(mem):
    """recall() used to short-circuit on a topic hit, so the topic NAME was the whole answer. Measured
    on the live store, 57 of 79 topics held facts a search on the same word finds under a DIFFERENT
    slug: `personal` returned a stale Barcelona and `location` returned Madrid, and neither branch
    could see the other — the answer to "where do I live" was decided by which name she guessed.

    `facts` and `path` still mean the topic FILE; the cross-topic hits arrive as `related`, and each
    one says which topic it is filed under."""
    mem.append_fact("The user lives in Barcelona", "personal")
    mem.append_fact("Wants a reminder to pay rent", "reminders")
    mem.append_fact("Wants to be reminded of a phrase", "reminder")

    r = mem.recall("reminder")
    assert r["path"] == "topics/reminder.md"
    assert r["facts"] == ["Wants to be reminded of a phrase"]
    assert "Wants a reminder to pay rent" in [h["fact"] for h in r["related"]]
    assert [h["topic"] for h in r["related"]] == ["reminders"]


def test_related_never_repeats_the_topics_own_facts_and_stays_capped(mem):
    for i in range(12):
        mem.append_fact(f"Wants a widget for reason number {i} alpha{i}", "widgets")
    for i in range(12):
        mem.append_fact(f"Wants a widget spare for reason {i} beta{i}", "spares")

    r = mem.recall("widgets")
    facts = [h["fact"] for h in r["related"]]
    assert len(facts) <= mem._RELATED_CAP
    assert not set(facts) & set(r["facts"]), "a topic's own facts came back a second time as `related`"


def test_an_empty_topic_still_falls_through_to_the_pure_search(mem):
    mem.append_fact("Prefers dark mode", "preferences")
    r = mem.recall("nonexistent-topic")
    assert r["facts"] == [] and "preferences" in r["available_topics"]
    r = mem.recall("dark")
    assert r.get("searched") and r["facts"] == ["Prefers dark mode"]


def test_recent_facts_carry_the_topic_they_are_filed_under(mem):
    """The prompt showed a list of facts and, underneath, a list of topic names, with nothing saying
    which fact came from which drawer — so a topic name was a word she had to guess the contents of.

    `recent_facts()` stays UNTAGGED, though: it is the extractor's de-dup context, a tag is not part
    of the fact, and feeding one back would teach the extractor to write tags of its own."""
    mem.append_fact("Has a cat named Michi", "pets")
    mem.append_fact("Works as a security engineer", "work")

    lines = mem.facts_for_prompt()
    assert "Has a cat named Michi [pets]" in lines
    assert "Works as a security engineer [work]" in lines
    assert mem.recent_facts() == ["Has a cat named Michi", "Works as a security engineer"]


def test_topic_summary_is_capped_and_says_what_it_left_out(mem):
    """The summary is capped and names its own overflow, and the names-only form — what the extractor
    is handed — is the same list minus the counts it has no use for."""
    for i in range(20):
        mem.append_fact(f"alpha{i} beta{i} gamma{i}", f"topic{i:02d}")
    with_counts = mem.topic_summary()
    assert "(1)" in with_counts and "+8 more topics" in with_counts
    names_only = mem.topic_summary(12, False)
    assert "(1)" not in names_only and "+8 more topics" in names_only
    assert len(names_only) < len(with_counts)


def test_the_tool_labels_where_each_related_fact_is_filed(mem):
    """`facts` are printed under the topic file's own path, so the cross-topic hits must be a separate,
    labelled section — printing them under "From topics/x.md:" would say they live in a file they do
    not, and this tool's whole job is telling the user what is stored WHERE."""
    import asyncio

    from kotoba.tools.builtin import memory_recall

    mem.append_fact("Wants a reminder to pay rent", "reminders")
    mem.append_fact("Wants to be reminded of a phrase", "reminder")
    out = asyncio.run(memory_recall.execute({"topic": "reminder"}, ctx=None))
    assert "From topics/reminder.md:" in out
    assert "- Wants to be reminded of a phrase" in out
    assert "Filed elsewhere but related to 'reminder':" in out
    assert "- Wants a reminder to pay rent (reminders)" in out
