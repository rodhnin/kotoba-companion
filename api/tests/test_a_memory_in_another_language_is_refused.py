"""Memory is stored in ENGLISH, because a fact saved in another language can never be matched or
corrected later — the recall side compares English against English.

The filter is evidence-weighted and deliberately hard to trigger, since a false "your fact is bad" is
worse than the silence it closes. But it skipped every capitalized token to spare a proper noun, and
the store's canonical shape is verb-first third person — so the Spanish verb sat at position 0, the
only place the skip applied hardest, and the commonest fact of all sailed through as English.
"""
from __future__ import annotations

import pytest

from kotoba.tools.builtin.memory_write import _looks_spanish

# Written the way the store asks for them: third person, verb first.
_ANOTHER_LANGUAGE = [
    "Vive en Madrid",
    "Tiene un perro",
    "Prefiere el té verde",
    "Es de Caracas",
    "Trabaja como ingeniero de seguridad",
    "Está construyendo una web",
    "Su perro se llama Toby",
    "Estudia por las noches",
]

# The ones the filter must never touch. Proper nouns, a capitalized acronym, and the English verbs
# whose Spanish cousins sit in the word lists.
_ENGLISH = [
    "Lives in Madrid",
    "Lives in El Paso",
    "Works as a security engineer",
    "Uses ES modules",
    "Has a dog named Luna",
    "Prefers green tea",
    "Is allergic to peanuts",
    "Studies at night",
    "Speaks English",
    "Building Kotoba, an AI companion",
    "Visited Los Angeles in the spring",
]


@pytest.mark.parametrize("fact", _ANOTHER_LANGUAGE)
def test_a_fact_written_in_another_language_is_caught(fact):
    assert _looks_spanish(fact), f"would have been stored unfindable: {fact!r}"


@pytest.mark.parametrize("fact", _ENGLISH)
def test_an_english_fact_is_never_refused(fact):
    """Guards the guard: a filter that answered yes to everything would pass the list above."""
    assert not _looks_spanish(fact), f"an English fact was refused: {fact!r}"


def test_the_capital_that_opens_a_sentence_is_not_evidence_of_a_name():
    """Same words, same order, only the sentence-initial capital differs — the verdict must not."""
    assert _looks_spanish("vive en Madrid") == _looks_spanish("Vive en Madrid")
    assert _looks_spanish("lives in Madrid") == _looks_spanish("Lives in Madrid")
