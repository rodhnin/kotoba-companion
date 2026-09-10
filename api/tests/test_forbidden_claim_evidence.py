"""The forbidden-phrase filter rewrite needs evidence that the claim is about the web.

The filter exists for one lie: the model claiming it can't reach a page when it has a browser and
web search. But its inability verbs also open refusals that are true and prompt-mandated, and the
rewrite did three harms: it deleted an actionable instruction, made her talk about a page not in the
conversation, and promised a search she must not run — in both languages identically.

The discriminator: a claim is rewritten only when its sentence names a web object, one list for both
languages, run through the real streaming filter end-to-end rather than a reimplemented regex."""
from __future__ import annotations

import pytest

import kotoba.core.stream as stream


def _run(text: str) -> str:
    f = stream.ForbiddenPhraseFilter()
    return (f.feed(text) + f.flush()).strip()


_MUST_REWRITE = [
    "I can't access that page.",
    "I cannot open that link.",
    "I couldn't reach that website.",
    "I'm unable to browse the internet.",
    "I don't have access to the internet.",
    "I can't go online right now.",
    "I can't load the site.",
    "I can't pull up that URL.",
]

# The same lie in another language. The correction she would be given is English, so putting it here
# would answer a Spanish sentence in English — these are REMOVED instead. Detected identically;
# only what happens next differs.
_MUST_VANISH = [
    "No puedo acceder a esa página.",
    "No puedo abrir el enlace.",
    "No puedo cargar ese sitio ahora mismo.",
    "No puedo conectarme a internet.",
    "No puedo ver esa página web.",
    "No puedo leer ese enlace.",
]

_MUST_PASS_UNTOUCHED = [
    "I can't connect Notion from here — press Sign in in Settings.",
    "I can't view your screen right now.",
    "I can't open that file, it's outside my folder.",
    "I can't access your microphone.",
    "I can't read your mind, haha.",
    "'notion' needs a browser sign-in I can't do on my own — I've added it to Settings under "
    "'Needs connection' so you can finish it there with the 'Sign in' button.",
    "No puedo conectar Notion desde aquí, dale a Sign in en Ajustes.",
    "No puedo ver tu pantalla.",
    "No puedo abrir ese fichero, está fuera de mi carpeta.",
    "No puedo leer tu mente, jaja.",
    "No puedo ver tu cámara desde esta llamada.",
    "No puedo cargar ese archivo tan grande.",
    "Your Pi can't reach the site because the DNS is wrong.",
    "The CLI can't access the internet from that container.",
    "Their API can't reach the website from inside the VPN.",
    "Siri can't open the page on that phone.",
]


@pytest.mark.parametrize("claim", _MUST_REWRITE)
def test_false_web_claims_are_still_corrected(claim):
    """The filter's real job survives: a claim about a page/link/site/internet gets the playful
    correction."""
    out = _run(claim)
    assert out, f"claim vanished instead of being corrected: {claim!r}"
    assert out != claim, f"false web claim passed through uncorrected: {claim!r}"


@pytest.mark.parametrize("claim", _MUST_VANISH)
def test_the_same_lie_in_another_language_is_removed(claim):
    """The property is that she stops asserting it, and that holds either way. What must never happen
    is the correction arriving in a language she was not speaking."""
    out = _run(claim)
    assert "No puedo" not in out, f"false web claim passed through uncorrected: {claim!r}"
    assert not out, f"nothing English may take its place: {out!r}"


@pytest.mark.parametrize("refusal", _MUST_PASS_UNTOUCHED)
def test_true_refusals_pass_through_untouched(refusal):
    """A refusal that is true and useful — an OAuth sign-in she cannot do, a screen she cannot see, a
    file outside her folder — must come out byte-identical, keeping its actionable instruction."""
    out = _run(refusal)
    assert out == refusal, f"true refusal was rewritten: {refusal!r} -> {out!r}"


def test_the_oauth_sentence_keeps_its_instruction():
    """The pending-OAuth prompt block orders her to say she cannot connect from here AND to point at
    the Settings button; the old rewrite deleted the pointer and promised a forbidden search."""
    out = _run("No puedo conectar Notion desde aquí, dale a Sign in en Ajustes.")
    assert "Sign in" in out and "Ajustes" in out
    assert "busca" not in out.lower() and "otro lado" not in out.lower()


def test_a_true_refusal_does_not_consume_the_one_shot_reaction():
    """The first-reaction slot must stay available for a REAL web claim later in the same reply — a
    preceding true refusal must neither burn it nor be altered."""
    f = stream.ForbiddenPhraseFilter()
    out = (f.feed("I can't view your screen. I can't open that page.") + f.flush()).strip()
    assert out.startswith("I can't view your screen.")
    assert "I can't open that page" not in out
    assert len(out) > len("I can't view your screen."), "the web claim was dropped instead of corrected"


def test_a_true_refusal_survives_the_removal_beside_it():
    """The other language, where the false claim is removed rather than replaced: what was TRUE and
    useful still reaches them, byte for byte."""
    f = stream.ForbiddenPhraseFilter()
    out = (f.feed("No puedo ver tu pantalla. No puedo abrir esa página.") + f.flush()).strip()
    assert out == "No puedo ver tu pantalla."


def test_later_false_claims_are_still_dropped_after_the_first_reaction():
    f = stream.ForbiddenPhraseFilter()
    out = (f.feed("No puedo abrir esa página. No puedo cargar ese sitio web.") + f.flush()).strip()
    assert "No puedo abrir esa página" not in out
    assert "No puedo cargar ese sitio" not in out


def test_url_standins_keep_the_evidence_visible():
    """UrlFilter runs BEFORE this filter and replaces a typed URL with "its official site"/"su web
    oficial" — the stand-in itself carries the web evidence, so a claim wrapped around a real URL is
    still corrected after the link strip."""
    uf, pf = stream.UrlFilter(), stream.ForbiddenPhraseFilter()
    out = (pf.feed(uf.feed("I can't open https://chatforest.com/guides right now, sorry. "))
           + pf.feed(uf.flush()) + pf.flush()).strip()
    assert "chatforest" not in out
    assert "can't open" not in out.lower(), f"claim with a real URL passed uncorrected: {out!r}"
