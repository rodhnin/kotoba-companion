"""Leaked tool-call syntax must not survive in the conversation history.

The spoken filter chain runs on the audio stream, not on what reaches the database, so a leak the user
never heard could still come back through session_search. Only the leak strip is shared: URLs, code
fences and audio tags are voice-only concerns and she needs them in her own history.
"""
from __future__ import annotations

from kotoba.core import stream as sse

_LEAK = '{"query":"kitty keyboard protocol"}to=functions.web_search'


def test_the_strip_removes_a_leak_from_assembled_text():
    text = f"Ya te lo busco. {_LEAK} Aquí está lo que encontré."
    out = sse.strip_tool_call_leaks(text)
    assert "to=functions" not in out
    assert '{"query"' not in out
    assert "Aquí está lo que encontré." in out, "the real sentence must survive"


def test_the_strip_leaves_ordinary_text_alone():
    for text in [
        "Te paso el enlace: https://sw.kovidgoyal.net/kitty/keyboard-protocol/",
        "Puedes mandarme un JSON con la clave query y yo lo leo.",
        "```python\nprint('hola')\n```",
        "[warmly] Claro que sí, Jordan.",
    ]:
        assert sse.strip_tool_call_leaks(text) == text, f"must not touch: {text!r}"


def test_a_url_survives_the_persisted_strip():
    """The persisted text keeps links on purpose — she cites her own earlier sources from history."""
    url = "https://example.test/docs"
    assert url in sse.strip_tool_call_leaks(f"Lo saqué de {url}, por si lo quieres abrir.")


def test_empty_and_none_are_safe():
    assert sse.strip_tool_call_leaks("") == ""


# --- the bare form must not eat prose -------------------------------------------------------------

def test_ordinary_prose_about_code_survives():
    """`to=functions.x` is a leak on its own; a bare `functions.x` is also how people write code.
    The filter runs on what gets PERSISTED, so eating a sentence here outlives the turn."""
    for text in [
        "En JS usas functions.map para eso.",
        "Configura browser.startup y listo.",
        "El fichero functions.php de WordPress.",
        "multi_tool_use.parallel es un concepto raro.",
        "Mira browser.tabs en la documentación.",
    ]:
        assert sse.strip_tool_call_leaks(text) == text, f"must not touch: {text!r}"


def test_the_bare_form_still_goes_when_it_carries_its_args():
    """What a real emission looks like: the recipient glued to its JSON."""
    out = sse.strip_tool_call_leaks('Vale functions.web_search{"query":"sixel"} ya.')
    assert "functions.web_search" not in out
    assert '{"query"' not in out
    assert "Vale" in out and "ya." in out
