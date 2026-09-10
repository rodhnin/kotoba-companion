"""The spoken chain must not carry what a voice cannot perform, and a caption must not draw.

Two reproduced leaks: a bidi override/isolate quoted off a web page reordered the caption (a surface we
do not own), a ZWSP crossed as an invisible break, and a C0 cursor move rode along to every consumer —
now caught by one `scrub` pass, newlines kept for the sentence splitter. The URL pattern was anchored on
`https?://` alone, so a scheme-less `www.` link was read out loud in full; it now counts as a URL, while
prose merely mentioning "www." (dot then space) survives.

Bare domains and un-fenced code stay spoken on purpose — naming a site is her job, pasted code is the
prompt's fix. Every invisible codepoint below is written as an escape, never itself."""
from __future__ import annotations

from kotoba.core import stream as sse

RLO, PDF = "\u202e", "\u202c"
LRI, PDI = "\u2066", "\u2069"
ZWSP, SHY = "\u200b", "\xad"


def chain():
    return (sse.ToolCallLeakFilter(), sse.CodeFenceFilter(), sse.UrlFilter(),
            sse.AudioTagFilter(keep_valid=False), sse.ForbiddenPhraseFilter())


def speak(chunks: list[str]) -> str:
    leak, code, url, tag, phrase = chain()
    out = []
    for c in chunks:
        out.append(phrase.feed(tag.feed(url.feed(code.feed(leak.feed(c))))))
    out.append(sse.flush_spoken(leak, code, url, tag, phrase, final=True))
    return "".join(out)


def test_bidi_overrides_and_isolates_never_reach_the_voice():
    got = speak([f"The file is {RLO}gnp.evil{PDF} ok. Name {LRI}hidden{PDI} here."])
    assert RLO not in got and PDF not in got
    assert LRI not in got and PDI not in got
    assert "gnp.evil" in got and "hidden" in got  # readable bytes still show


def test_invisible_breaks_are_removed_not_spaced():
    got = speak([f"One{ZWSP}word and a{SHY}join."])
    assert ZWSP not in got and SHY not in got
    assert "Oneword" in got  # removal, never a space: a space is the boundary an attacker wanted


def test_cursor_moves_become_a_space_and_the_payload_stays_readable():
    got = speak(["Danger \x1b[2K\rhidden text."])
    assert "\x1b" not in got and "\r" not in got
    assert "[2K" in got and "hidden text." in got  # neutralised, not silently shortened


def test_a_www_url_is_not_read_aloud():
    got = speak(["Visit www.evil-site.com/track?id=7 now."])
    assert "www." not in got and "evil-site" not in got
    assert got.strip().startswith("Visit")


def test_a_www_url_after_a_copula_gets_the_standin():
    got = speak(["La direccion es www.ejemplo.com y listo."])
    assert "www." not in got
    assert "su web oficial" in got


def test_a_parenthetical_www_aside_drops_whole():
    got = speak(["Lo saque de (Reuters: www.reuters.com) ayer."])
    assert "www." not in got and "Reuters" not in got
    assert "Lo saque de" in got and "ayer" in got


def test_prose_about_www_survives():
    got = speak(["The www. prefix means web."])
    assert "www." in got


def test_a_www_url_split_across_chunks_is_still_caught():
    got = speak(["Mira www.ev", "il-site.com/track ahora mismo."])
    assert "www." not in got and "il-site" not in got
    assert "ahora mismo" in got


def test_the_scrub_reaches_the_flush_tail_too():
    leak, code, url, tag, phrase = chain()
    phrase.feed(tag.feed(url.feed(code.feed(leak.feed(f"ok {RLO}evil")))))
    tail = sse.flush_spoken(leak, code, url, tag, phrase, final=True)
    assert RLO not in tail
