"""A correction was discarded as a duplicate, and the wrong value became permanent: swapping only a
value shares nearly every keyword with the stale fact it fixes (Jaccard 0.60-1.00), so it scored as
a repeat and the model was told "Already remembered" — self-sealing, since she then believes the new
value while the old one is what got stored.

The discriminator is a same-class value substitution inside an identical frame: once every value
token either side holds is stripped, the rest must match word for word, or a same-class swap could
retire an unrelated fact's value instead of the one it means to fix. A plain reword still counts as
duplicate, a cross-class substitution never retires anything, and a still-duplicate reply must quote
the fact it matched rather than let her claim a value she just failed to save."""
from __future__ import annotations

import asyncio
import importlib

import pytest


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "memory"))
    import kotoba.core.user_memory as um

    importlib.reload(um)
    return um


def test_date_correction_replaces_the_stale_fact(mem):
    assert mem.append_fact("el cumpleaños de Jordan es el doce de mayo", "dates") is True
    assert mem.append_fact("el cumpleaños de Jordan es el quince de mayo", "dates") is True
    assert mem.existing_facts() == ["el cumpleaños de Jordan es el quince de mayo"]


def test_city_correction_replaces_the_stale_fact(mem):
    assert mem.append_fact("va a la oficina de Madrid los lunes", "work") is True
    assert mem.append_fact("va a la oficina de Barcelona los lunes", "work") is True
    assert mem.existing_facts() == ["va a la oficina de Barcelona los lunes"]


def test_name_correction_replaces_the_stale_fact(mem):
    assert mem.append_fact("la novia de Jordan se llama Ana", "relationships") is True
    assert mem.append_fact("la novia de Jordan se llama Lucia", "relationships") is True
    assert mem.existing_facts() == ["la novia de Jordan se llama Lucia"]


def test_single_digit_correction_replaces_despite_jaccard_one(mem):
    """Single digits fall through the keyword length filter, so '3 → 4' scored Jaccard 1.00 — the
    correction was invisible to similarity entirely. Value tokens come from the RAW fact."""
    assert mem.append_fact("Jordan's dog is 3 years old", "pets") is True
    assert mem.append_fact("Jordan's dog is 4 years old", "pets") is True
    assert mem.existing_facts() == ["Jordan's dog is 4 years old"]


def test_recent_index_follows_the_correction(mem):
    mem.append_fact("la novia de Jordan se llama Ana", "relationships")
    mem.append_fact("la novia de Jordan se llama Lucia", "relationships")
    recent = mem.recent_facts()
    assert "la novia de Jordan se llama Lucia" in recent
    assert "la novia de Jordan se llama Ana" not in recent


def test_plain_rewording_is_still_a_duplicate(mem):
    """A verb swap is not a value swap, so the reworded-repeat contract still holds."""
    assert mem.append_fact("Looking for Fulano Perez on Facebook", "search") is True
    assert mem.append_fact("User is searching for Fulano Pérez on Facebook", "search") is False
    assert mem.append_fact("Favorite game is Elden Ring", "games") is True
    assert mem.append_fact("User's favorite game is Elden Ring.", "games") is False


def test_cross_class_substitution_never_retires(mem):
    """'Madrid' → 'Tuesday' swaps a place for a weekday: two different facts the overlap score wrongly
    merges. The class guard keeps the discriminator from retiring the stored one, and the subject rule
    now stops the pair being refused as a repeat either — both are kept, which is what they are."""
    assert mem.append_fact("team meeting is in Madrid", "work") is True
    assert mem.append_fact("team meeting is on Tuesday", "work") is True
    assert mem.existing_facts() == ["team meeting is in Madrid", "team meeting is on Tuesday"]


def test_below_threshold_contradiction_is_stored_alongside(mem):
    """Below the closeness threshold nothing changes: the contradiction is visible clutter, never a
    silent loss — and never a silent retirement either (the pair may be two real facts)."""
    assert mem.append_fact("his birthday is in May", "dates") is True
    assert mem.append_fact("his birthday is in June", "dates") is True
    assert sorted(mem.existing_facts()) == ["his birthday is in June", "his birthday is in May"]


def test_memory_write_saves_a_correction(mem):
    """In ENGLISH, because the tool refuses a non-English fact outright, so this contract is pinned in
    the store's canonical language. The store itself still accepts any language — every other case here
    goes through append_fact."""
    from kotoba.tools.builtin import memory_write

    mem.append_fact("Jordan's birthday is May twelfth", "dates")
    msg = asyncio.run(memory_write.execute(
        {"fact": "Jordan's birthday is May fifteenth", "topic": "dates"}, None))
    assert msg.startswith("Saved under")
    assert mem.existing_facts() == ["Jordan's birthday is May fifteenth"]


def test_memory_write_quotes_what_is_actually_stored_on_duplicate(mem):
    """When the verdict is 'duplicate' of a DIFFERENTLY-worded fact, the model must see the stored
    wording — answering 'Already remembered: <the new text>' claimed knowledge of a value that was
    just thrown away."""
    from kotoba.tools.builtin import memory_write

    mem.append_fact("Prefers jasmine tea", "preferences")
    msg = asyncio.run(memory_write.execute({"fact": "Likes jasmine tea", "topic": "drinks"}, None))
    assert 'Already remembered as: "Prefers jasmine tea"' in msg
    assert mem.existing_facts() == ["Prefers jasmine tea"]


def test_bystander_fact_is_not_retired_by_a_same_class_swap(mem):
    """Collateral found while verifying the fix: 'the dog is 4 years old' retired the CAT's age — two real
    facts that differ only in same-class values, and the older one vanished. Cat and dog are different
    frames, so the pair is neither a correction nor a repeat: the cat keeps its age and the dog gets
    one, and the reply says Saved rather than reporting a deletion nobody asked for."""
    from kotoba.tools.builtin import memory_write

    assert mem.append_fact("the cat is 3 years old", "pets") is True
    msg = asyncio.run(memory_write.execute({"fact": "the dog is 4 years old", "topic": "pets"}, None))
    assert mem.existing_facts() == ["the cat is 3 years old", "the dog is 4 years old"]
    assert msg.startswith("Saved under") and "REPLACED" not in msg


def test_correction_retires_its_own_fact_and_spares_the_bystander(mem):
    """With cat-3 AND dog-3 both stored, dog-4 must retire ONLY dog-3, and the duplicate verdict must
    not short-circuit on the cat near-dup it meets first — a correction target anywhere in the store
    outranks a bystander match, or store order alone would resurrect the original self-sealing defect.
    The pair is seeded on disk because the overlap threshold never lets both in through appends."""
    p = mem.topic_path("pets")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# pets\n\n- the cat is 3 years old\n- the dog is 3 years old\n", encoding="utf-8")
    assert mem.append_fact("the dog is 4 years old", "pets") is True
    assert mem.existing_facts() == ["the cat is 3 years old", "the dog is 4 years old"]


def test_identical_frame_proper_noun_subject_swap_keeps_both(mem):
    """The case that used to be conceded: two genuinely distinct facts, word-for-word identical around
    their swapped values ('the Madrid office has 3 floors' → the Barcelona one), and the older one was
    retired. It was never a correction — the swapped word is what each fact is ABOUT. Both are kept
    now, and 'recency wins' is no longer an answer this store gives to that shape."""
    mem.append_fact("the Madrid office has 3 floors", "work")
    assert mem.append_fact("the Barcelona office has 3 floors", "work") is True
    assert mem.existing_facts() == ["the Madrid office has 3 floors", "the Barcelona office has 3 floors"]


def test_sentence_initial_value_is_lost_but_the_net_catches_it(mem):
    """A Spanish fact whose value sits at position 0 — "Madrid es donde vive …" corrected to
    "Barcelona es donde vive …", the same sentence with the city moved to the front — stays a lost
    correction: position 0 cannot join the name class because the store's canonical facts are
    verb-initial English ('Prefers jasmine tea'), and counting position 0 turned every verb-swap
    rewording into a 'correction'. Accepted loss, and the net still holds: nothing is deleted, and the
    English-only gate is what answers, so the caller is told plainly why it did not land."""
    from kotoba.tools.builtin import memory_write

    mem.append_fact("Madrid es donde vive Jordan", "home")
    msg = asyncio.run(memory_write.execute({"fact": "Barcelona es donde vive Jordan", "topic": "home"}, None))
    assert mem.existing_facts() == ["Madrid es donde vive Jordan"]
    assert msg.startswith("NOT SAVED") and "ENGLISH" in msg


def test_capitalization_drift_does_not_block_the_correction(mem):
    """The frame is computed by removing every value token EITHER side holds. Comparing each side minus
    only its own values would leave 'max' in the new fact's frame (written lowercase there, so never a
    name) while the stored fact classifies 'Max' as a value — same statement, verdict flipped by casing
    drift between sessions."""
    mem.append_fact("Jordan's dog Max is 3 years old", "pets")
    assert mem.append_fact("Jordan's dog max is 4 years old", "pets") is True
    assert mem.existing_facts() == ["Jordan's dog max is 4 years old"]


def test_es_number_word_lexicon_gaps_are_covered(mem):
    """'veintidós → veintitrés' and ordinals past 'quinto' were lost corrections — the number-word
    lexicon simply stopped at veintiuno/quinto."""
    mem.append_fact("Jordan tiene veintidós años", "identity")
    assert mem.append_fact("Jordan tiene veintitrés años", "identity") is True
    assert mem.existing_facts() == ["Jordan tiene veintitrés años"]
    mem.append_fact("vive en el quinto piso", "home")
    assert mem.append_fact("vive en el sexto piso", "home") is True
    assert "vive en el quinto piso" not in mem.existing_facts()


def test_lowercase_correction_is_lost_but_the_net_catches_it(mem):
    """An all-lowercase fact carries no proper-noun signal, so a Spanish "… vive en madrid" corrected
    to barcelona is invisible to the discriminator — an accepted loss, since the canonical store is
    model-written English, which capitalizes. The net is the contract: the stored fact is untouched and
    the caller is told why nothing landed, never told the new value was already known."""
    from kotoba.tools.builtin import memory_write

    mem.append_fact("andrea vive en madrid", "home")
    msg = asyncio.run(memory_write.execute({"fact": "andrea vive en barcelona", "topic": "home"}, None))
    assert mem.existing_facts() == ["andrea vive en madrid"]
    assert msg.startswith("NOT SAVED") and "ENGLISH" in msg


def test_reworded_correction_is_lost_but_the_net_catches_it(mem):
    """The stricter rule's price, paid on purpose: a correction that ALSO rewords its frame ('ahora'
    added) no longer takes the correction path the loose rule granted it. It must land in the net —
    the stored fact untouched and the caller told why — not in silence."""
    from kotoba.tools.builtin import memory_write

    mem.append_fact("Jordan trabaja en la oficina de Madrid", "work")
    msg = asyncio.run(memory_write.execute(
        {"fact": "Jordan ahora trabaja en la oficina de Barcelona", "topic": "work"}, None))
    assert mem.existing_facts() == ["Jordan trabaja en la oficina de Madrid"]
    assert msg.startswith("NOT SAVED") and "ENGLISH" in msg


def test_verbatim_repeat_never_rides_a_correction_out(mem):
    """A fact already stored verbatim stays a duplicate even when a stale sibling in the store would
    count as its correction target — otherwise the correction-outranks-duplicate ordering would append
    the same bullet twice. Both siblings are seeded on disk directly: appends alone cannot produce this
    store, which is exactly why the corner needs pinning (a legacy or hand-edited store can)."""
    p = mem.topic_path("pets")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# pets\n\n- the dog is 3 years old\n- the dog is 4 years old\n", encoding="utf-8")
    assert mem.append_fact("the dog is 4 years old", "pets") is False
    assert mem.existing_facts().count("the dog is 4 years old") == 1


# --- the single-valued verb family: where the user lives -------------------------------------------

def test_moving_city_retires_the_old_residence(mem):
    """Spoken live as «vivo en Barcelona» and later «me mudé, ahora vivo en Madrid» ("I live in
    Barcelona" / "I moved, I live in Madrid now"). The canonical facts the extractor writes are two
    keywords each, so the pair sits at Jaccard 1/3 forever and the closeness-gated discriminator can
    never see it: both cities stayed live and Madrid only won recall by prompt order. Residence is
    declared single-valued (_SV_VERBS), the way 'favorite <noun>' already is."""
    out = mem.write_fact("Lives in Barcelona", "location")
    assert out["written"] is True
    out = mem.write_fact("Lives in Madrid", "location")
    assert out["written"] is True
    assert out["retired"] == ["Lives in Barcelona"], "the caller must be able to say what happened"
    assert mem.existing_facts() == ["Lives in Madrid"]
    assert mem.recent_facts() == ["Lives in Madrid"]


def test_residence_correction_works_in_the_legacy_spanish_store_too(mem):
    assert mem.append_fact("vive en Barcelona", "home") is True
    assert mem.append_fact("vive en Madrid", "home") is True
    assert mem.existing_facts() == ["vive en Madrid"]


def test_somebody_elses_residence_is_not_the_users(mem):
    """One extra frame keyword — a sister, a past tense — and the verb rule abstains: below the
    threshold, deleting a bystander is still worse than visible clutter."""
    assert mem.append_fact("Sister lives in Valencia", "family") is True
    assert mem.append_fact("Lives in Madrid", "location") is True
    assert sorted(mem.existing_facts()) == ["Lives in Madrid", "Sister lives in Valencia"]


def test_a_vaguer_residence_restatement_does_not_delete_the_detail(mem):
    """The vn < vo lesson, verb-form: 'Lives in Madrid' after 'Lives in Madrid Centro' adds nothing
    and must not delete the detail (its place tokens are a subset of the stored ones)."""
    assert mem.append_fact("Lives in Madrid Centro", "location") is True
    mem.write_fact("Lives in Madrid", "location")
    assert "Lives in Madrid Centro" in mem.existing_facts()


def test_other_short_verb_facts_keep_the_below_threshold_contract(mem):
    """The family is a whitelist, not a heuristic: 'Has visited Paris' → Rome is cumulative history,
    and below the threshold it keeps both — exactly the pinned May/June behaviour above."""
    assert mem.append_fact("Has visited Paris", "travel") is True
    assert mem.append_fact("Has visited Rome", "travel") is True
    assert sorted(mem.existing_facts()) == ["Has visited Paris", "Has visited Rome"]


def test_a_broader_place_retires_the_precise_one_the_known_limit():
    """The documented cost of the single-valued verb rule, pinned so it is visible rather than found.

    `_sv_verb_match` cannot rank two proper nouns — no geography lives in this module — so "Spain"
    against "Madrid" reads exactly like "Barcelona" against "Madrid", and retiring is a permanent
    delete. Blocking the unrankable case would block the correction the rule exists for. If someone
    ever teaches it containment, this is the test that should start failing.
    """
    from kotoba.core.user_memory import _sv_verb_match

    assert _sv_verb_match("Lives in Madrid", "Lives in Barcelona") == "correct"
    assert _sv_verb_match("Lives in Spain", "Lives in Madrid") == "correct"   # the trade, not a bug


def test_the_literal_subset_guard_still_holds_both_ways():
    """What the rule CAN rank it must refuse: a restatement that drops a place is not a correction."""
    from kotoba.core.user_memory import _sv_verb_match

    assert _sv_verb_match("Lives in Madrid", "Lives in Madrid and Barcelona") is None
    assert _sv_verb_match("Lives in Madrid and Barcelona", "Lives in Madrid") is None


def test_bystanders_are_never_taken_by_a_move():
    """Someone else's home, a past home, another verb — each one keyword away, each left alone."""
    from kotoba.core.user_memory import _sv_verb_match

    for old in ("Sister lives in Valencia", "Lived in Barcelona for ten years",
                "Works in Barcelona", "Lives in Barcelona and works in Sitges"):
        assert _sv_verb_match("Lives in Madrid", old) is None, old


def test_a_proper_noun_bystander_inside_the_stored_fact_survives_the_move(mem):
    """The frame guard promised that ONE extra keyword makes the rule abstain, and kept that promise
    only for lowercase ones: a proper noun is swept into the value set, so it left no keyword behind
    and the frames matched. Measured before the shape guard — 'Lives in Madrid' retired 'Lives in
    Barcelona with Ana' outright, deleting who he lives with along with the address."""
    assert mem.append_fact("Lives in Barcelona with Ana", "home") is True
    res = mem.write_fact("Lives in Madrid", "home")

    assert res["retired"] == []
    assert sorted(mem.existing_facts()) == ["Lives in Barcelona with Ana", "Lives in Madrid"]


def test_the_same_verb_in_another_sense_is_not_a_residence(mem):
    """The preposition that separates living-IN from living-FOR is a stopword, so the frame could not
    see it either: 'Lives for Formula 1' read as a home and was deleted by a move to Madrid."""
    assert mem.append_fact("Lives for Formula 1", "interests") is True
    res = mem.write_fact("Lives in Madrid", "home")

    assert res["retired"] == []
    assert sorted(mem.existing_facts()) == ["Lives for Formula 1", "Lives in Madrid"]


def test_the_shape_guard_keeps_the_move_it_exists_for():
    """It may only ever ABSTAIN more: a real swap, a multi-word place and a quoted one still correct,
    and a compound stored fact is still reported rather than silently kept."""
    from kotoba.core.user_memory import _sv_verb_match

    assert _sv_verb_match("Lives in Madrid", "Lives in Barcelona") == "correct"
    assert _sv_verb_match("Vive en Madrid", "Vive en Barcelona") == "correct"
    assert _sv_verb_match("Lives in Sun Valley", "Lives in Barcelona") == "correct"
    assert _sv_verb_match("Lives in Madrid", 'Lives in "Barcelona"') == "correct"
    assert _sv_verb_match("Lives in Madrid", "Lives in Barcelona; Ana Lopez") == "blocked"


def test_the_verb_rule_never_crosses_a_language(mem):
    """_SV_VERBS holds one entry per language and does NOT normalize them to a single key the way
    _ATTR_MARKERS does, so a legacy Spanish residence fact is left standing by an English correction.
    A miss, not a delete — and there is no residence fact in the live store to widen this on."""
    assert mem.append_fact("Vive en Barcelona", "home") is True
    res = mem.write_fact("Lives in Madrid", "home")

    assert res["retired"] == []
    assert sorted(mem.existing_facts()) == ["Lives in Madrid", "Vive en Barcelona"]


def test_a_capitalized_non_place_still_reads_as_a_home_the_same_known_limit():
    """The other face of "no geography lives here", pinned so it is visible rather than found.

    The rule cannot tell a city from any other capitalized noun, so "Lives in Notion" is a residence
    and a real move deletes it. The shape guard cannot help — both sides ARE the same shape — and only
    a gazetteer would. What it does catch is the same nonsense wearing another preposition, because
    living IN and living FOR do not share a shape.
    """
    from kotoba.core.user_memory import _sv_verb_match

    assert _sv_verb_match("Lives in Madrid", "Lives in Notion") == "correct"   # the trade, not a bug
    assert _sv_verb_match("Lives in Madrid", "Lives for Formula 1") is None


def test_the_extractor_writes_the_same_fact_two_ways_and_the_move_still_wins():
    """Live QA: the user said Barcelona, then Madrid, and the reply was "I have contradictory notes".

    The extractor phrased them differently on the two turns — `The user lives in Barcelona.` and
    `Lives in Madrid` — and `_value_shape` compared the whole token run, so the shapes differed by a
    prefix naming the subject the other sentence already implies. Both were kept.
    """
    from kotoba.core.user_memory import _sv_verb_match

    assert _sv_verb_match("Lives in Madrid", "The user lives in Barcelona.") == "correct"
    assert _sv_verb_match("The user lives in Madrid", "Lives in Barcelona") == "correct"


def test_stripping_the_subject_prefix_never_reaches_somebody_else():
    """The prefix strip is the head only, and a closed set: a fact about another person keeps its
    subject and therefore its shape, so a move of HIS cannot retire hers."""
    from kotoba.core.user_memory import _sv_verb_match

    for old in ("Sister lives in Valencia", "His sister lives in Valencia",
                "Lives in Barcelona with Ana", "Lived in Barcelona for ten years"):
        assert _sv_verb_match("Lives in Madrid", old) is None, old


def test_a_pronoun_subject_is_held_by_the_frame_and_not_by_the_shape():
    """A third party named only by a PRONOUN is the one case `_value_shape` cannot see: `_SELF_REF`
    strips exactly those words, so "She lives in Barcelona" and "Lives in Madrid" reduce to the SAME
    shape. What keeps her home from being deleted is the frame guard — "she" is not a stopword, so it
    survives into the frame and makes it two keywords instead of one.

    That is load-bearing and it is an accident of one set's composition: `his`, `her`, `their` and
    `they` ARE in `_STOP` while `he` and `she` are not. Adding either to `_STOP` as a tidy-up would
    silently turn a partner's address into a stale value of the user's own, and retiring is a permanent
    delete with no archive. Pinned from both ends so the tidy-up fails here instead of in a live store.
    """
    from kotoba.core.user_memory import _STOP, _keywords, _sv_verb_match, _value_shape, _value_tokens

    for old in ("She lives in Barcelona", "He lives in Barcelona"):
        values = set(_value_tokens("Lives in Madrid")) | set(_value_tokens(old))
        assert _value_shape("Lives in Madrid", values) == _value_shape(old, values), old
        assert len(_keywords(old) - values) == 2, old
        assert _sv_verb_match("Lives in Madrid", old) is None, old

    assert not ({"he", "she"} & _STOP), "he/she became stopwords — the third-party guard just died"
