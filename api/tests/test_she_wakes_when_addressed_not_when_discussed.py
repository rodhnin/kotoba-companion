"""A voice channel is a room full of people who are mostly not talking to her.

The whole design rests on one line: she answers when somebody says her name and stays quiet
otherwise. Get it too loose and she interrupts a conversation about her; too tight and people repeat
themselves until they give up.

Addressing somebody puts their name at the front, or just after a "hey". "Ayer hablé con Kotoba de
eso" is people discussing her — a first draft with a four-word window woke her on exactly that.

The names come from the gateway, so this works for whatever the installation renamed her to.
"""
from __future__ import annotations

import pytest

from kotoba.discord.voice import normalise, said_her_name

NAMES = ["Kotoba", "Mizuki"]


# What she is HANDED is her own words, punctuation aside: the comparison is folded, the handover is
# not. Fold it here too and a question reaches the model as "que hora es en japon".
@pytest.mark.parametrize("heard, expected", [
    # front — the shape most languages use to open
    ("Kotoba, qué hora es", "qué hora es"),
    ("kotoba que hora es", "que hora es"),
    ("¡Kotoba! ayúdame", "ayúdame"),
    ("oye Kotoba, ven", "oye ven"),
    ("hey kotoba", "hey"),
    ("Mizuki, hola", "hola"),
    # trailing — Spanish does this as readily as it does the other, and so do many others
    ("¿qué hora es, Kotoba?", "qué hora es"),
    ("what time is it, Kotoba", "what time is it"),
    ("ちょっと待って、Kotoba", "ちょっと待って"),
    # short line — wherever the name sits, the whole thing was said to somebody
    ("dime la hora Kotoba porfa", "dime la hora porfa"),
])
def test_being_addressed_wakes_her_and_hands_over_the_rest(heard, expected):
    assert said_her_name(heard, NAMES) == expected


@pytest.mark.parametrize("heard", [
    "ayer hablé con Kotoba de eso y no me acuerdo bien",
    "no sé, pregúntale luego a Kotoba supongo, o mañana mejor",
    "el otro día lo que dijo Kotoba sobre eso estuvo bastante bien",
    "buenas a todos",
    "",
    "   ",
])
def test_being_discussed_leaves_her_alone(heard):
    assert said_her_name(heard, NAMES) is None


def test_a_name_the_installation_never_heard_of_does_not_wake_her():
    assert said_her_name("Kotoba, hola", ["Sora"]) is None


def test_accents_and_case_do_not_decide_it():
    assert normalise("¿KOTOBA?") == "¿kotoba?"
    assert said_her_name("KOTOBA ven", NAMES) == "ven"


def test_a_vocative_at_either_end_works_the_same():
    """A rule written for one word order silently ignores half the people who would use it."""
    assert said_her_name("Kotoba ven", NAMES) == "ven"
    assert said_her_name("ven Kotoba", NAMES) == "ven"


def test_her_name_alone_is_still_being_addressed():
    """"Kotoba?" is somebody checking she is there, and deserves an answer."""
    assert said_her_name("Kotoba", NAMES) == ""
    assert said_her_name("¿Kotoba?", NAMES) == ""


def test_no_names_at_all_means_she_never_wakes():
    """Before the gateway has told her what she is called, silence beats guessing."""
    assert said_her_name("Kotoba, hola", []) is None


@pytest.mark.parametrize("heard", [
    "Cotoba, hola",          # the c/k a transcriber picks by ear
    "kotova ven",            # b heard as v, which Spanish does not distinguish
    "kotoua que hora es",
    "Kotobá, hola",          # an accent it invented
])
def test_the_name_as_a_transcriber_actually_writes_it(heard):
    """A name has no dictionary behind it, so transcription spells it how it sounded. Matched
    exactly, she misses half the times she was called and the person concludes she is broken."""
    assert said_her_name(heard, NAMES) is not None


@pytest.mark.parametrize("heard", [
    "coloca eso ahí",
    "cotorra bonita",
    "toma esto",
    "código nuevo",
])
def test_an_ordinary_word_is_not_close_enough_to_be_her(heard):
    assert said_her_name(heard, NAMES) is None


def test_a_short_name_gets_a_tighter_tolerance():
    """“Sora” must not be reachable from “ahora”; a long name can afford two edits, a short one
    cannot without swallowing real words."""
    from kotoba.discord.voice import sounds_like

    assert sounds_like("kotova", "kotoba")
    assert not sounds_like("ahora", "sora")
    assert not sounds_like("cosa", "sora")


def test_her_name_in_another_script_is_still_her_name():
    """Measured live: a Spanish “Kotoba, hola” came back as コトバオラ. Her name is a real Japanese
    word, so a transcriber left to guess picks that script and nothing matches anything."""
    from kotoba.discord.voice import romanise

    assert romanise("コトバ") == "kotoba"
    assert romanise("ことば") == "kotoba"
    assert said_her_name("コトバ、ちょっと待って", NAMES) == "ちょっと待って"


def test_romanising_leaves_other_scripts_alone():
    from kotoba.discord.voice import romanise

    assert romanise("hola qué tal") == "hola qué tal"
    assert romanise("привет") == "привет"


def test_the_name_a_partial_heard_is_not_thrown_away():
    """Measured live, three times in one minute: the transcriber wrote the name while it was still
    listening and rewrote it away when it committed.

        partial '¿Cotoba?'  ->  final '¿Qué hubo?'
        partial 'Toba.'     ->  final '¿Qué haces?'

    Reading only the committed text discards the clearest evidence she was called, and from outside
    that is exactly the inconsistency people describe: "sometimes it just doesn't hear me"."""
    import inspect

    from kotoba.discord import voice

    src = inspect.getsource(voice.VoiceRoom._listen)
    assert "name_in_partial = True" in src
    settle = inspect.getsource(voice.VoiceRoom._committed)
    assert "speaker.name_in_partial" in settle
    assert "name_in_partial = False" in settle, "the flag has to clear, or she wakes forever after"
