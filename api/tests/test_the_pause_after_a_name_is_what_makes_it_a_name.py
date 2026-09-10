"""Her name reaches the transcriber spelled however it sounded, often two edits away — "Toma",
"Boba", "Tova", "Goto va", "Au tora". Matched strictly she ignored request after request in one
room, and the people talking concluded she was deaf.

Being less strict is only safe where the punctuation says the word ADDRESSED somebody: calling a
person by name puts a pause after it, which a transcriber writes as a comma, a dash, or the end of
the sentence. Without the mark the same letters open an ordinary sentence.

These spellings are transcriptions, not inventions — which is the whole reason they are the values
they are. Replacing them with made-up ones removes the evidence the tolerance was tuned against.
"""
from __future__ import annotations

import pytest

from kotoba.discord.voice import said_her_name

NAMES = ["Kotoba"]


# The name is matched on a folded form and stripped from the RAW one, so what she is handed keeps its
# accents and capitals — an audit found questions reaching the model as "que hora es en japon".
@pytest.mark.parametrize("heard, expected", [
    ("Toma, salte del chat.", "salte del chat"),
    ("Tova, tienes en tu memoria cómo se llama Ricardo.", "tienes en tu memoria cómo se llama Ricardo"),
    ("Boba, o deje de hablar con nosotros, por favor.", "o deje de hablar con nosotros por favor"),
    ("Otho va, hola.", "hola"),
    ("Goto, va a investigar sobre la siguiente reunión.", "a investigar sobre la siguiente reunión"),
    ("Pato, va, te estoy pidiendo que busques algo.", "te estoy pidiendo que busques algo"),
    ("¿Cómo va? ¿Será que me puedes ayudar?", "Será que me puedes ayudar"),
    ("Au tora. Est-ce que tu peux parler français ?", "Est ce que tu peux parler français"),
])
def test_a_name_the_transcriber_mangled_still_wakes_her(heard, expected):
    assert said_her_name(heard, NAMES) == expected


@pytest.mark.parametrize("heard", [
    "toma esto",
    "coloca eso ahí",
    "cotorra bonita",
    "con toda la razón hermano",
    "¿Con toda a ti te gusta ese equipo?",
    "otra vez, eso es diferente",
])
def test_without_the_pause_it_is_just_a_word(heard):
    assert said_her_name(heard, NAMES) is None


def test_the_extra_room_is_only_at_the_head():
    """Mid-sentence the same tolerance reaches ordinary words, and there she is being discussed."""
    assert said_her_name("no sé, dile que lo toma, y ya veremos qué pasa luego", NAMES) is None
