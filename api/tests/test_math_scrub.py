"""Safety-net scrub — even if the model slips and emits LaTeX/symbols, TTS must never read "backslash
beta" / "I underscore C". The real fix is the prompt (speak math in words); this guarantees no raw
notation reaches the voice/transcript. Also strips STT artifact tokens like <|es|>.

Note: the scrub is LaTeX-specific and must NOT damage normal Spanish/English speech.
"""
from __future__ import annotations

from kotoba.core.stream import ForbiddenPhraseFilter


def scrub(text: str) -> str:
    f = ForbiddenPhraseFilter()
    return (f.feed(text) + f.flush()).strip()


def test_strips_stt_language_token():
    assert "<|es|>" not in scrub("Hola, Negin. Hola.<|es|>")
    assert "Hola, Negin. Hola." in scrub("Hola, Negin. Hola.<|es|>")


def test_backslash_command_keeps_word_drops_slash():
    out = scrub("El factor beta. La corriente es \\beta por algo.")
    assert "\\beta" not in out and "backslash" not in out.lower()
    assert "beta" in out


def test_subscript_not_read_as_underscore():
    out = scrub("La corriente I_C es grande.")
    assert "I_C" not in out
    assert "underscore" not in out.lower() and "_" not in out


def test_equation_block_delimiters_removed():
    out = scrub("Entonces \\[ I_C = 100 \\cdot 0.0003 \\] amperios.")
    assert "\\[" not in out and "\\]" not in out and "\\cdot" not in out
    assert "amperios" in out


def test_latex_spacing_commands_removed():
    # \, \; \! are LaTeX thin/neg spaces — must not survive as a literal backslash.
    out = scrub("Son 0.3 \\, mA y 30 \\, mA en total.")
    assert "\\" not in out
    assert "mA" in out


def test_arrow_command_not_read_as_word():
    # \rightarrow must NOT leak as the spoken word "rightarrow".
    out = scrub("El metano reacciona \\rightarrow produce dióxido de carbono.")
    assert "rightarrow" not in out.lower() and "\\" not in out
    assert "dióxido" in out


def test_text_command_unwrapped():
    out = scrub("Resultado: 30 \\text{miliamperios}.")
    assert "\\text" not in out and "{" not in out
    assert "miliamperios" in out


def test_normal_spanish_untouched():
    # No LaTeX here — accents, ¿¡, em-dash must survive; nothing stripped.
    s = "¿Cómo estás? Me alegra verte — todo bien, ¡genial!"
    assert scrub(s) == s


def test_normal_english_untouched():
    s = "That's great. Let me help you with that."
    assert scrub(s) == s
