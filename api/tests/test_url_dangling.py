"""A stripped URL/filepath must never leave a beheaded spoken sentence ("…la dirección es" — silence).
When the link completes a copula/preposition/colon, a stand-in phrase is spoken instead; elsewhere the
plain drop stays. Runs the FULL production chain (CodeFence → Url → AudioTag → ForbiddenPhrase) fed
token-by-token, exactly like the `/v1` and the voice paths."""
from __future__ import annotations

import re

import kotoba.core.stream as stream

_CONNECTORS = stream._COPULA_ES | stream._COPULA_EN | stream._PREP_ES | stream._PREP_EN


def _chain(tokens: list[str]) -> str:
    cf, uf = stream.CodeFenceFilter(), stream.UrlFilter()
    tf, pf = stream.AudioTagFilter(keep_valid=True), stream.ForbiddenPhraseFilter()
    out = []
    for t in tokens:
        out.append(pf.feed(tf.feed(uf.feed(cf.feed(t)))))
    out.append(pf.feed(tf.feed(uf.feed(cf.flush()))))
    out.append(pf.feed(tf.feed(uf.flush())))
    out.append(pf.feed(tf.flush()))
    out.append(pf.flush())
    return "".join(out)


def _spoken(text: str) -> str:
    return _chain(list(text))  # char-by-char: the hardest streaming case


def _last_word(text: str) -> str:
    m = re.search(r"([A-Za-zÀ-ÿ']+)[^A-Za-zÀ-ÿ']*$", text)
    return m.group(1).lower() if m else ""


def test_live_qa_bug_spanish_copula():
    out = _spoken("[warmly] La dirección exacta de la documentación oficial de React es https://react.dev/.")
    assert "http" not in out and "react.dev" not in out
    assert "es su web oficial." in out
    assert _last_word(out) not in _CONNECTORS


def test_live_qa_bug_english_copula():
    out = _spoken("The exact address of the React documentation is https://react.dev/.")
    assert "http" not in out and "react.dev" not in out
    assert "is its official site." in out
    assert _last_word(out) not in _CONNECTORS


def test_spanish_preposition_mid_sentence():
    out = _spoken("Puedes leerla en https://react.dev, tiene guías muy buenas.")
    assert out == "Puedes leerla en su web oficial, tiene guías muy buenas."


def test_english_preposition_mid_sentence():
    out = _spoken("You can read it at https://react.dev whenever you like.")
    assert out == "You can read it at its official site whenever you like."


def test_url_swallowed_period_is_restored():
    out = _spoken("La dirección es https://react.dev. ¿Quieres que la abra?")
    assert "es su web oficial." in out
    assert "¿Quieres que la abra?" in out


def test_colon_before_url_both_languages():
    assert _spoken("Aquí la tienes: https://react.dev") == "Aquí la tienes: su web oficial"
    assert _spoken("Here you go: https://react.dev") == "Here you go: its official site"


def test_non_connector_context_still_plain_drop():
    out = _spoken("Te dejé el enlace https://react.dev aquí mismo.")
    assert "http" not in out and "oficial" not in out
    assert out == "Te dejé el enlace aquí mismo."


def test_parenthetical_url_aside_dropped_whole():
    out = _spoken("La fuente principal (Reuters: https://reuters.com/ai) lo confirma.")
    assert "(" not in out and "http" not in out
    assert "La fuente principal lo confirma." in out


def test_normal_site_mention_untouched():
    es = "La documentación está en la web oficial de React, es muy completa."
    en = "It's all on the official React site, and it reads nicely."
    assert _spoken(es) == es
    assert _spoken(en) == en


def test_filepath_after_preposition_both_languages():
    out = _spoken("Lo guardé en research/informe-caminar.md, ¿quieres verlo?")
    assert ".md" not in out and "informe" not in out
    assert out == "Lo guardé en tus archivos, ¿quieres verlo?"
    out2 = _spoken("I saved it in notes.md for you.")
    assert out2 == "I saved it in your files for you."


def test_filepath_after_copula_both_languages():
    assert _spoken("El archivo es scraper.py.") == "El archivo es uno de tus archivos."
    assert _spoken("The report is summary.pdf.") == "The report is one of your files."


def test_char_stream_equals_single_blob():
    text = "[excited] Mira, la guía está en https://react.dev/learn y es genial."
    assert _spoken(text) == _chain([text])


def test_battery_never_ends_dangling():
    cases = [
        "La página oficial es https://react.dev",
        "Está en https://react.dev/docs.",
        "Entra a https://react.dev/learn.",
        "The docs are at https://react.dev.",
        "It is https://react.dev/reference",
        "Ve al https://react.dev ya.",
    ]
    for text in cases:
        out = _spoken(text).strip()
        assert "http" not in out, text
        assert out, text
        assert _last_word(out) not in _CONNECTORS, f"{text!r} -> {out!r}"
