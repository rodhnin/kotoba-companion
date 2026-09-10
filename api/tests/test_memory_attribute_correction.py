"""Live QA found durable memory holding six mutually exclusive favourite colours at once: a
correction never retired the old value because a colour isn't recognized as a value token, and the
phrasings were too dissimilar to compare (Jaccard 0.33-0.40, below threshold). Fixed by keying
retirement on same attribute + same subject + different value, refusing to store a non-English or
compound fact the detector could never match again.

Over-retiring is worse than clutter, so a block is reported rather than swallowed whenever the
subject, attribute or language does not line up. It came back anyway when the subject check read
only words before the attribute phrase — canonical phrasing puts the owner after the head noun, so
an unrelated fact was silently deleted as a stale value; the boundary is now read at the copula."""
from __future__ import annotations

import asyncio
import importlib

import pytest

# The seven frames exactly as live QA found them in ~/.kotoba/memory/topics/preferences.md.
LIVE_FACTS = [
    "Favorite color is purple.",
    "User's favorite color is jade green.",
    "The user's favorite color is teal green and they have a cat named Miso.",
    "Prefers jasmine tea and the color indigo.",
    "Su color favorito es el índigo",
    "Has color favorito is cobalt blue.",
    "They no longer like moss green as their favorite color.",
]


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "memory"))
    import kotoba.core.user_memory as um

    importlib.reload(um)
    return um


def _write(fact: str, topic: str = "preferences") -> str:
    from kotoba.tools.builtin import memory_write

    return asyncio.run(memory_write.execute({"fact": fact, "topic": topic}, None))


def test_the_six_reworded_frames_converge_to_one_favorite_color(mem):
    """The defect itself, at store level: five different framings of one attribute, written in the order
    live QA produced them, must leave ONE colour. The two compound frames are the tool's job (they carry
    a second fact), so this is the atomic-English store they should have arrived as."""
    for f in ["Favorite color is purple.", "User's favorite color is jade green.",
              "The user's favorite color is teal green.", "Favorite color is indigo.",
              "Has color favorito is cobalt blue."]:
        assert mem.append_fact(f, "preferences") is True
    assert mem.existing_facts() == ["Has color favorito is cobalt blue."]


def test_a_correction_reports_what_it_replaced(mem):
    mem.append_fact("Favorite color is jade green.", "preferences")
    res = mem.write_fact("Favorite color is cobalt blue.", "preferences")
    assert res["retired"] == ["Favorite color is jade green."]
    assert res["conflicts"] == []


def test_an_english_correction_retires_a_legacy_spanish_fact(mem):
    """"color favorito" and "favorite color" are the same attribute key, which is how the Spanish and
    Spanglish facts already sitting in the live store finally get superseded instead of piling up."""
    assert mem.append_fact("Su color favorito es el índigo", "preferences") is True
    assert mem.append_fact("Favorite color is cobalt blue.", "preferences") is True
    assert mem.existing_facts() == ["Favorite color is cobalt blue."]


def test_a_different_person_is_never_retired(mem):
    """The dog/cat lesson in this rule's own terms: same attribute, different SUBJECT."""
    assert mem.append_fact("Ana's favorite color is red", "preferences") is True
    assert mem.append_fact("Jordan's favorite color is blue", "preferences") is True
    assert sorted(mem.existing_facts()) == ["Ana's favorite color is red", "Jordan's favorite color is blue"]


def test_the_bystander_age_case_still_does_not_over_retire(mem):
    """Re-pinned here because this rule is the one that could resurrect it: no attribute marker, so the
    attribute path must not even look at the pair, and the frame rule keeps the cat's age."""
    p = mem.topic_path("pets")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# pets\n\n- the cat is 7 years old\n- the dog is 3 years old\n", encoding="utf-8")
    assert mem.append_fact("the dog is 4 years old", "pets") is True
    assert mem.existing_facts() == ["the cat is 7 years old", "the dog is 4 years old"]
    assert mem._attribute_match("the dog is 4 years old", "the cat is 7 years old") is None


def test_a_plural_attribute_is_multi_valued_and_retires_nothing(mem):
    """Someone may keep several favourite colours; exactly one birthday. A plural head noun states a set,
    so the rule abstains — a favourite-colourS fact neither retires nor is retired."""
    assert mem.append_fact("Favorite colors are blue and red", "preferences") is True
    assert mem.append_fact("Favorite colors are green and yellow", "preferences") is True
    assert len(mem.existing_facts()) == 2
    assert mem._attribute("Favorite colors are blue and red") is None


def test_a_compound_stored_fact_is_kept_and_reported_not_retired(mem):
    """Retiring "favorite color is teal green and they have a cat named Miso" would take Miso with it.
    It survives — and the caller is TOLD it survived, so she cannot claim the old value is gone."""
    mem.append_fact("The user's favorite color is teal green and they have a cat named Miso.", "preferences")
    res = mem.write_fact("Favorite color is cobalt blue.", "preferences")
    assert res["retired"] == []
    assert res["conflicts"] == ["The user's favorite color is teal green and they have a cat named Miso."]
    assert "Miso" in " ".join(mem.existing_facts())


def test_a_stored_fact_naming_someone_else_blocks_the_retirement(mem):
    """A proper noun only the stored side carries may be a different subject — Spanish puts the owner
    AFTER the attribute ("el color favorito de Ana"), where the subject-prefix guard cannot see it."""
    mem.append_fact("El color favorito de Ana es el rojo", "preferences")
    res = mem.write_fact("Favorite color is cobalt blue.", "preferences")
    assert res["retired"] == []
    assert res["conflicts"] == ["El color favorito de Ana es el rojo"]


def test_a_negative_retraction_neither_retires_nor_is_retired(mem):
    """A fact and its opposite are never the same fact. The retraction is stored
    alongside — visible clutter, never a silent removal of something the user may still mean."""
    mem.append_fact("Favorite color is moss green.", "preferences")
    assert mem.append_fact("They no longer like moss green as their favorite color.", "preferences") is True
    assert mem.append_fact("Favorite color is cobalt blue.", "preferences") is True
    facts = mem.existing_facts()
    assert "Favorite color is moss green." not in facts
    assert "They no longer like moss green as their favorite color." in facts
    assert "Favorite color is cobalt blue." in facts


def test_the_same_value_reworded_is_still_a_duplicate(mem):
    """The attribute rule must not turn every rewording into a correction: the de-dup contract still
    holds when only the FRAME changes."""
    assert mem.append_fact("Favorite game is Elden Ring", "games") is True
    assert mem.append_fact("User's favorite game is Elden Ring.", "games") is False
    assert mem._attribute_match("User's favorite game is Elden Ring.", "Favorite game is Elden Ring") is None


def test_a_vaguer_restatement_never_deletes_the_detailed_fact(mem):
    """The over-retire trap this rule introduced and had to close: "Favorite color is blue" states the
    SAME value as "…is deep cobalt blue, chosen for the studio walls" with less detail. Treated as a new
    value it deleted the fact that held everything. Poorer → kept out; richer → replaces, as always."""
    mem.append_fact("Favorite color is deep cobalt blue, chosen for the studio walls", "preferences")
    res = mem.write_fact("Favorite color is blue", "preferences")
    assert res["written"] is False and res["retired"] == []
    assert mem.existing_facts() == ["Favorite color is deep cobalt blue, chosen for the studio walls"]


def test_a_reworded_restatement_is_not_appended_but_still_clears_the_stale_values(mem):
    """The live store held "Has color favorito is cobalt blue" already, so saving the same value in
    proper English corrected four stale colours and was written as a fifth bullet repeating the newest.
    It must count as known — and still retire what it contradicts, or being told twice changes nothing.
    Seeded on disk because that is how the live store got there: the facts predate the rule."""
    p = mem.topic_path("preferences")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# preferences\n\n- Favorite color is purple.\n"
                 "- Has color favorito is cobalt blue.\n", encoding="utf-8")
    res = mem.write_fact("Favorite color is cobalt blue.", "preferences")
    assert res["written"] is False and res["reason"] == "restated"
    assert res["duplicate_of"] == "Has color favorito is cobalt blue."
    assert res["retired"] == ["Favorite color is purple."]
    assert mem.existing_facts() == ["Has color favorito is cobalt blue."]
    msg = _write("Favorite color is cobalt blue.")
    assert msg.startswith("Already remembered in other words")
    assert "reworded" not in msg


def test_a_different_attribute_is_untouched(mem):
    assert mem.append_fact("Favorite color is blue", "preferences") is True
    assert mem.append_fact("Favorite food is sushi", "preferences") is True
    assert len(mem.existing_facts()) == 2


def test_english_ordinals_past_tenth_are_value_tokens(mem):
    """The Spanish number-word lexicon gap had an English twin nobody hit while facts could be saved
    in Spanish: "May twelfth" → "May fifteenth" was a lost correction because the ordinals stopped at
    'tenth'. Enforcing English makes that phrasing the common case for dates."""
    assert mem.append_fact("Jordan's birthday is May twelfth", "dates") is True
    assert mem.append_fact("Jordan's birthday is May fifteenth", "dates") is True
    assert mem.existing_facts() == ["Jordan's birthday is May fifteenth"]


def test_a_compound_fact_is_refused_and_nothing_is_stored(mem):
    msg = _write("The user's favorite color is teal green and they have a cat named Miso.")
    assert msg.startswith("NOT SAVED")
    assert "once per fact, dropping none of it" in msg
    assert mem.existing_facts() == []


def test_a_spanish_fact_is_refused_and_nothing_is_stored(mem):
    msg = _write("Su color favorito es el índigo")
    assert msg.startswith("NOT SAVED")
    assert "ENGLISH" in msg
    assert mem.existing_facts() == []


def test_a_spanglish_fact_is_refused(mem):
    msg = _write("Has color favorito is cobalt blue.")
    assert msg.startswith("NOT SAVED")
    assert mem.existing_facts() == []


def test_a_refusal_never_fires_for_something_already_stored(mem):
    """A fact the store already holds is reported as known, not scolded: nagging a legacy Spanish entry
    into being rewritten in English would create a second, differently-worded copy of it."""
    mem.append_fact("Le gusta el té de jazmín", "preferences")
    msg = _write("Le gusta el té de jazmín")
    assert msg.startswith("Already remembered")
    assert mem.existing_facts() == ["Le gusta el té de jazmín"]


def test_ordinary_english_facts_are_never_refused(mem):
    """The refusals must be cheap to satisfy and hard to trigger. A coordinated noun phrase is one fact;
    a Spanish place name or loanword is not a Spanish fact; and a quoted TITLE is neither — its
    coordinators and its language belong to the title, not to the statement carrying it."""
    for fact in ["Likes salt and pepper", "Likes fish and chips", "Jordan and Ana are married",
                 "Lives in El Paso", "Uses ES modules for every new project",
                 "Their favorite dish is arroz con pollo", "Has a son named Leo",
                 'Watched the film "Todo sobre mi madre y el amor que se le tiene"',
                 "The user’s favorite drink is a cortado",
                 "Works as a security engineer at Acme Corp in Madrid"]:
        assert _write(fact, "misc").startswith("Saved under"), fact


def test_the_saved_message_says_what_happened_to_the_old_value(mem):
    """She said "lo de antes no lo volví a usar" while it was still stored and still being recalled. The
    tool now states both halves — what is gone, and what was kept on purpose."""
    mem.append_fact("Favorite color is jade green.", "preferences")
    mem.append_fact("The user's favorite color is teal green and they have a cat named Miso.", "preferences")
    msg = _write("Favorite color is cobalt blue.")
    assert "REPLACED and deleted from memory" in msg and "jade green" in msg
    assert "STILL STORED and contradicting this" in msg and "Miso" in msg


def test_the_live_pile_up_converges_through_the_tool(mem):
    """End to end on the real seven: the tool refuses four of them, the model complies with atomic
    English (what the schema asked for all along), and the store lands on ONE favourite colour with both
    bundled facts — the cat and the tea — intact rather than retired."""
    refused = [f for f in LIVE_FACTS if _write(f).startswith("NOT SAVED")]
    assert refused == LIVE_FACTS[2:6]
    for rewritten in ["The user's favorite color is teal green.", "Has a cat named Miso.",
                      "Prefers jasmine tea.", "Favorite color is indigo.",
                      "Favorite color is cobalt blue."]:
        _write(rewritten)
    assert sorted(mem.existing_facts()) == [
        "Favorite color is cobalt blue.",
        "Has a cat named Miso.",
        "Prefers jasmine tea.",
        "They no longer like moss green as their favorite color.",
    ]


def test_the_conflict_report_is_read_only(mem):
    """The maintenance path for a store that already contains the pile-up: it NAMES the groups and
    changes nothing. Which of six colours is true is the user's answer, not a de-dup verdict."""
    p = mem.topic_path("preferences")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# preferences\n\n" + "".join(f"- {f}\n" for f in LIVE_FACTS), encoding="utf-8")
    before = mem.existing_facts()
    report = mem.attribute_conflict_report()
    assert mem.existing_facts() == before
    assert len(report) == 1 and report[0]["attribute"] == "favorite color"
    assert "Has color favorito is cobalt blue." in report[0]["facts"]


def test_an_owner_named_after_the_head_noun_is_a_different_subject(mem):
    """The shape the store actually writes: the owner sits AFTER "favorite color", where the old
    subject guard could not see it. Saving the user's own favourite colour must not delete their
    daughter's."""
    mem.append_fact("Favorite color of the user's daughter is pink", "family")
    res = mem.write_fact("Favorite color is blue", "preferences")
    assert res["written"] is True and res["retired"] == []
    assert "Favorite color of the user's daughter is pink" in mem.existing_facts()
    assert mem._attribute_match("Favorite color is blue",
                                "Favorite color of the user's daughter is pink") is None


def test_the_owner_after_the_head_noun_is_also_seen_in_spanish(mem):
    """The same fact in the language the legacy store is full of. "su" and "the user's" are both
    stopwords, so before the copula split BOTH sides looked like a bare fact about the user."""
    mem.append_fact("El color favorito de su hija es el rosa", "family")
    res = mem.write_fact("Su color favorito es el azul", "preferences")
    assert res["written"] is True and res["retired"] == []
    assert "El color favorito de su hija es el rosa" in mem.existing_facts()


def test_a_room_is_a_context_not_a_stale_value(mem):
    """Two rooms are two facts. Chained, this is how three unrelated colours became one — each write
    wiping the previous, the daughter's colour retired by a kitchen wall."""
    for fact in ["Favorite color of the user's daughter is pink",
                 "Favorite color for the kitchen walls is white",
                 "Favorite color for the car is red"]:
        assert mem.write_fact(fact, "misc")["retired"] == []
    assert len(mem.existing_facts()) == 3


def test_a_time_of_day_is_a_context_not_a_stale_value(mem):
    """Jasmine in the morning and chamomile after dinner are both true at once."""
    mem.append_fact("Favorite tea in the morning is jasmine", "preferences")
    res = mem.write_fact("Favorite tea after dinner is chamomile", "preferences")
    assert res["written"] is True and res["retired"] == []
    assert sorted(mem.existing_facts()) == ["Favorite tea after dinner is chamomile",
                                            "Favorite tea in the morning is jasmine"]


def test_a_pets_possession_is_not_the_users_own_attribute(mem):
    """An owner that is a common noun — a relative, a room, a dog's collar — had no protection at all:
    the foreign-proper-noun guard only ever fired on a Capitalized name."""
    mem.append_fact("Favorite color of the dog's collar is red", "pets")
    res = mem.write_fact("Favorite color is blue", "preferences")
    assert res["written"] is True and res["retired"] == []
    assert "Favorite color of the dog's collar is red" in mem.existing_facts()


def test_a_qualified_fact_does_not_retire_the_users_own_either(mem):
    """The same defect read backwards, which the audit's table did not cover: the daughter's colour
    arriving second must not delete the user's, and the near-duplicate path must not swallow it."""
    mem.append_fact("Favorite color is blue", "preferences")
    res = mem.write_fact("Favorite color of the user's daughter is pink", "family")
    assert res["written"] is True and res["retired"] == []
    assert len(mem.existing_facts()) == 2


def test_the_same_context_still_corrects(mem):
    """The fix must narrow the rule, not switch it off: within ONE context a new value still replaces
    the stale one, and an owner named in either position is the same owner in both languages."""
    mem.append_fact("Favorite tea in the morning is jasmine", "preferences")
    assert mem.write_fact("Favorite tea in the morning is oolong",
                          "preferences")["retired"] == ["Favorite tea in the morning is jasmine"]
    mem.append_fact("El color favorito de Ana es el rojo", "family")
    assert mem.write_fact("El color favorito de Ana es el verde",
                          "family")["retired"] == ["El color favorito de Ana es el rojo"]


def test_a_frame_with_no_copula_abstains_instead_of_guessing(mem):
    """With no copula the scope and the value cannot be told apart, so the rule stands down. Both are
    kept: two colours on file is clutter somebody can read and correct, and clutter was always the
    price this rule agreed to pay rather than delete the wrong one."""
    mem.append_fact("Favorite color: blue", "preferences")
    res = mem.write_fact("Favorite color: red", "preferences")
    assert res["written"] is True and res["retired"] == []
    assert mem.existing_facts() == ["Favorite color: blue", "Favorite color: red"]
    assert mem._attribute_parts("Favorite color: red") is None


def test_the_store_itself_still_accepts_any_language(mem):
    """The gates are the TOOL's. append_fact stays language-agnostic: the automatic extractor and the
    one-time migration write through it, and a migration that dropped facts would be silent loss."""
    assert mem.append_fact("Le gusta el té de jazmín", "preferences") is True
    assert mem.append_fact("Tiene un gato y le gusta correr", "pets") is True
