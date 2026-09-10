"""Voice de-dup: the "she says it twice" bug.

The model emits the SAME sentence twice in one reply — the start_work line before AND after the call,
for instance: "Ya voy, lo ves en pantalla.Ya voy, lo ves en pantalla." ("On it, you can see it on
screen", glued to itself). The spoken-text filter drops a verbatim consecutive repeat. Verified live.
"""
from __future__ import annotations

from kotoba.core import stream


def _spoken(parts):
    f = stream.ForbiddenPhraseFilter()
    out = ""
    for p in parts:
        out += f.feed(p)
    return out + f.flush()


def test_glued_duplicate_sentence_collapsed():
    """The exact shape reported live: the same sentence twice, glued, inside one chunk."""
    out = _spoken(["Ya voy, y lo puedes ver en la pantalla.Ya voy, y lo puedes ver en la pantalla."])
    assert out.count("Ya voy, y lo puedes ver en la pantalla") == 1


def test_duplicate_split_across_two_feeds_collapsed():
    """The repeat arrives in two model calls, before and after a tool, and is still caught: the filter
    persists for the whole turn."""
    out = _spoken(["Listo, ya quedó armado el informe. ", "Listo, ya quedó armado el informe."])
    assert out.count("Listo, ya quedó armado el informe") == 1


def test_distinct_sentences_kept():
    out = _spoken(["Primero busqué la noticia. Luego te la resumo."])
    assert "Primero busqué la noticia" in out and "Luego te la resumo" in out


def test_short_acks_not_collapsed():
    """Short confirmations may legitimately recur; only long verbatim repeats are dropped."""
    out = _spoken(["Sí. Sí."])
    assert out.count("Sí") == 2


def test_non_consecutive_repeat_kept():
    """A repeat with something distinct between it and its twin is kept: only the back-to-back stutter
    is the bug."""
    out = _spoken(["Abro la página ahora mismo. Te muestro lo que encuentre. Abro la página ahora mismo."])
    assert out.count("Abro la página ahora mismo") == 2
