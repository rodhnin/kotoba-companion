"""A false "I can't reach the web" claim is taken out of her speech, and taking it out must not put a
second language in.

The filter swaps that claim for a playful in-character line, and the line is English: canned text stays
in one language, because a copy per language only covers the ones somebody remembered to write. But the
claim arrives in whatever language she was speaking, and answering a Spanish sentence with an English
quip is how English leaked into Spanish voice turns once already.

So the regex decides, not a language detector — it already knows which alternative fired. The English
claim is replaced; anything else is removed, which is the property that matters.
"""
from __future__ import annotations

import kotoba.core.stream as stream

_ENGLISH_MARKERS = ("page", "door", "shy", "budge", "hard to get", "let me", "i'll")


def _run(text: str) -> str:
    f = stream.ForbiddenPhraseFilter()
    return (f.feed(text) + f.flush()).strip()


def test_an_english_claim_is_replaced_by_the_reaction():
    out = _run("I can't access that page.")
    assert out and "can't access" not in out
    assert any(m in out.lower() for m in _ENGLISH_MARKERS)


def test_a_claim_in_another_language_is_removed_not_translated():
    for said in ("No puedo acceder a esa página.", "No puedo abrir el enlace."):
        out = _run(said).lower()
        assert not any(m in out for m in _ENGLISH_MARKERS), f"English leaked into: {said!r}"
        assert "no puedo" not in out, f"the false claim survived: {said!r}"


def test_removing_it_keeps_the_sentences_around_it():
    """It is one clause out of a turn, not the turn. What she legitimately said still reaches them."""
    out = _run("Claro, te lo busco. No puedo abrir el enlace. Sigo.")
    assert "Claro, te lo busco." in out and "Sigo." in out
    assert "No puedo" not in out


def test_only_one_reaction_a_turn():
    """The second claim is dropped rather than reacted to twice — one quip is character, two is a tic."""
    out = _run("I can't access that page. I can't open that link either.")
    assert sum(out.lower().count(m) for m in ("hard to get", "shy", "door", "budge")) == 1


def test_removing_it_takes_the_lead_in_with_it():
    """Cutting the claim alone left what introduced it — "Lo siento, " — and she spoke the fragment.
    The sentence carried the lie, so the sentence goes."""
    for said in ("Lo siento, no puedo acceder a esa página.",
                 "Hmm, no puedo abrir el enlace, así que te lo resumo yo."):
        assert _run(said) == "", f"a fragment survived: {_run(said)!r}"


def test_an_audio_tag_does_not_survive_the_sentence_it_introduced():
    """A bare `[sad] ` is non-empty, so nothing downstream replaces it — and the tag is then read
    out as the word "sad"."""
    assert _run("[sad] No puedo acceder a esa página.") == ""


def test_the_sentences_around_it_are_not_collateral():
    out = _run("Claro. No puedo abrir el enlace. Sigo con lo que tengo.")
    assert out == "Claro. Sigo con lo que tengo."

