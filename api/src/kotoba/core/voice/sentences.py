"""Streaming sentence segmentation for per-sentence REST TTS (the expressive engine).

Two boundary families. Latin terminals (.!?…) need a following space and obey the abbreviation,
decimal and lowercase-continuation guards. Script-native full stops (CJK 。．！？, Arabic ؟ ۔,
Devanagari ।) are written with NO following space and their scripts have no case, so they split
unconditionally — without them an entire Japanese reply sat in the buffer until end of turn and
went out as one giant request.

A stream that never terminates a sentence (rambling clause, lyrics, unpunctuated CJK) is force-cut
at _MAX_HELD, at the last natural pause: eleven_v3 rejects requests over ~3,000 characters."""
from __future__ import annotations

_TERMINALS = ".!?…"
# Script-native full stops: a boundary with no space after it and no case to check.
_UNSPACED_TERMINALS = "。．！？؟۔।"
_CLOSERS = "\"'”’»)」』】）"
# Forced boundary for terminator-less streams — with request batching on top this stays far
# below the provider's per-request character cap.
_MAX_HELD = 480
# Words a trailing "." does NOT end a sentence after (titles/latinisms, EN+ES). Single letters
# (initials, "p. ej.") are guarded separately.
_ABBREVIATIONS = frozenset({
    "sr", "sra", "srta", "dr", "dra", "prof", "lic", "ing", "ud", "uds", "vd",
    "mr", "mrs", "ms", "st", "jr", "etc", "vs", "aprox", "approx", "ej", "eg", "ie", "vol",
})
_MAX_TAG_LEN = 45


class SentenceSplitter:
    """feed() returns completed sentences (stripped) as they can be confirmed; flush() the remainder.
    Never splits inside an [audio tag], after abbreviations/initials, or inside decimals ("3.14")."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str) -> list[str]:
        self._buf += chunk
        out: list[str] = []
        while True:
            sentence = self._take_one()
            if sentence is None:
                while len(self._buf) > _MAX_HELD:
                    piece = self._carve()
                    if piece:
                        out.append(piece)
                return out
            if sentence:
                out.append(sentence)

    def flush(self) -> str:
        rest, self._buf = self._buf.strip(), ""
        return rest

    def pending_sentence(self) -> str:
        """The held text IF it already looks like a finished sentence (ends in terminals/closers) —
        lets a caller force it out when the stream goes quiet (announce-then-tool-call turns), without
        ever forcing out a mid-sentence fragment."""
        held = self._buf.strip()
        ends = _TERMINALS + _UNSPACED_TERMINALS
        if held and held[-1] in ends + _CLOSERS and any(c in ends for c in held[-4:]):
            self._buf = ""
            return held
        return ""

    def _take_one(self) -> str | None:
        buf = self._buf
        i = 0
        while i < len(buf):
            ch = buf[i]
            if ch == "[":
                close = buf.find("]", i + 1, i + _MAX_TAG_LEN)
                if close != -1:
                    i = close + 1
                    continue
                if len(buf) - i < _MAX_TAG_LEN:
                    return None  # tag possibly still streaming in — hold
                i += 1  # runaway '[' → literal text, scan on
                continue
            if ch not in _TERMINALS and ch not in _UNSPACED_TERMINALS:
                i += 1
                continue
            j = i
            while j + 1 < len(buf) and buf[j + 1] in _TERMINALS + _UNSPACED_TERMINALS:
                j += 1
            k = j
            while k + 1 < len(buf) and buf[k + 1] in _CLOSERS:
                k += 1
            if k + 1 >= len(buf):
                return None  # run may continue ("..." mid-arrival, "3." vs "3.5") — hold
            if any(c in _UNSPACED_TERMINALS for c in buf[i:j + 1]):
                if buf[j] == "．" and buf[j + 1].isdigit():
                    i = j + 1  # fullwidth decimal ("３．１４")
                    continue
                sentence, self._buf = buf[: k + 1].strip(), buf[k + 1:].lstrip()
                return sentence
            if not buf[k + 1].isspace() or self._is_guarded(buf, i, j):
                i = j + 1
                continue
            nxt = self._next_nonspace(buf, k + 1)
            if nxt is None:
                return None
            if nxt.islower():
                i = k + 1  # "Bueno... no sé." — lowercase continuation, same sentence
                continue
            sentence, self._buf = buf[: k + 1].strip(), buf[k + 1:].lstrip()
            return sentence
        return None

    def _carve(self) -> str:
        """One forced piece off the front of a terminator-less buffer, cut at the last natural pause
        (whitespace, or a pause mark not inside a number) before _MAX_HELD — never inside an [audio
        tag]. With no pause at all, the hard cut lands at the cap, skipping past any tag it straddles."""
        buf = self._buf
        cut = 0
        i = 0
        while i < _MAX_HELD:
            ch = buf[i]
            if ch == "[":
                close = buf.find("]", i + 1, i + _MAX_TAG_LEN)
                if close != -1:
                    i = close + 1
                    continue
            elif ch.isspace() or (ch in "、，,;；" and not buf[i + 1].isdigit()):
                cut = i + 1
            i += 1
        piece, self._buf = buf[: cut or i].strip(), buf[cut or i:].lstrip()
        return piece

    def _is_guarded(self, buf: str, i: int, j: int) -> bool:
        if buf[i] != "." or j != i:
            return False
        if i + 1 < len(buf) and buf[i + 1].isdigit():
            return True
        w = i
        while w > 0 and (buf[w - 1].isalpha() or buf[w - 1] == "'"):
            w -= 1
        word = buf[w:i].lower()
        return bool(word) and (len(word) == 1 or word in _ABBREVIATIONS)

    @staticmethod
    def _next_nonspace(buf: str, start: int) -> str | None:
        for ch in buf[start:]:
            if not ch.isspace():
                return ch
        return None
