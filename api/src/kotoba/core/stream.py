"""SSE formatting helpers for both channels.

Channel A (voice text) uses the OpenAI chat.completion.chunk shape, consumed by ElevenLabs; channel B
sends named `event: emotion` frames. The regexes below implement the harmony emission grammar.

Two objects travel the text queue that are not text: DONE_SENTINEL ends the turn, FLUSH_SENTINEL marks
a tool-call boundary. Every consumer must recognise BOTH — a sentinel is truthy, so one that knows only
the first feeds an `object` to a filter and dies on it. Compare them through the MODULE, never a name
imported once: they are identities, and a test that reloads this module mints new ones while a
`from … import` elsewhere holds the old, so producer and consumer disagree in silence."""
from __future__ import annotations

import json
import re
from collections.abc import Callable

from kotoba.core import text_security
from kotoba.core.emotions import VALID_EMOTIONS

# Must match the prompt's curated list — anything else (a hallucinated [yay], a translated [emocionada]) is stripped before TTS.
ALLOWED_AUDIO_TAGS = frozenset({
    "happily", "excited", "curious", "warmly", "sad", "nervous", "tired", "sarcastic", "awe",
    "mischievously", "laughs", "laughs softly", "giggles", "sighs", "gasps",
    "whispers", "pause", "rushed", "drawn out",
})

TAG_TO_EMOTION = {  # voice tag → Live2D face, from ONE source, so the spoken emotion and the face always agree
    "happily": "happy", "excited": "excited", "curious": "confused", "warmly": "affectionate",
    "sad": "sad", "nervous": "scared", "tired": "sleepy", "sarcastic": "neutral", "awe": "surprised",
    "mischievously": "happy", "laughs": "happy", "laughs softly": "happy", "giggles": "happy",
    "sighs": "sad", "gasps": "surprised", "whispers": "neutral", "pause": "neutral",
    "rushed": "neutral", "drawn out": "neutral",
}

TAG_TO_FACE = {**TAG_TO_EMOTION, **{e: e for e in VALID_EMOTIONS}}


def _attached(prev: str) -> bool:
    """True when the character just printed makes the bracket an index, not a tag: `arr[0]`, `m[i][j]`."""
    return prev.isalnum() or prev in "_])"


def _tag_words(name: str) -> list[str]:
    """The tag words a bracket's content resolves to, or [] when any part of it is prose. `[sad warmly]`
    is two tags the model glued into one bracket (seen live); `[laughs softly]` is one tag that
    happens to contain a space — known tags match longest-first. Connectors stay prose on purpose:
    `[sad and warmly]` prints, because classifying near-matches to the vocabulary is the mistake that
    once rewrote `((url))`."""
    words = [w for w in re.split(r"[,\s]+", name) if w]
    out: list[str] = []
    i = 0
    while i < len(words):
        two = " ".join(words[i:i + 2])
        if two in TAG_TO_FACE:
            out.append(two)
            i += 2
        elif words[i] in TAG_TO_FACE:
            out.append(words[i])
            i += 1
        else:
            return []
    return out


def expressive_mode() -> bool:
    """True when the expressive-voice (eleven_v3 audio-tag) mode is on — the SAME gate as the prompt;
    both delegate to the single source so a Settings change moves them together
    (they once read env separately and desynced: the prompt asked for tags while this filter stripped them).
    NOTE: tags are only PERFORMED by eleven_v3; on flash/turbo they'd be read aloud literally, which is why
    the source couples this to the TTS engine."""
    from kotoba.core import app_settings

    return app_settings.audio_tags_enabled()


class AudioTagFilter:
    """Streaming filter that drops invalid [audio tags] even when a tag is split across tokens (the
    model streams '[', 'play', 'ful', ']' separately). Buffers only while a '[' is open.

    `text_surface` answers a DIFFERENT question. A voice must not read a bracket it cannot perform, so it
    drops every one; a terminal shows markdown, where most brackets are prose — `[Forbes](url)` is a link,
    `arr[0]` an index. The rule is contextual: a bracket is a tag when every word it holds is one, nothing
    is glued to its left, and no `(` follows. Keeping all printed `[thinking]`; dropping all broke code.

    `on_tag` fires the moment the tag closes, BEFORE any of the reply is emitted, so the face it
    chooses is painted with her first frame instead of repainted after her last."""

    def __init__(self, keep_valid: bool | None = None, text_surface: bool = False,
                 on_tag: Callable[[str], None] | None = None) -> None:
        self._buf = ""
        self._keep_valid = expressive_mode() if keep_valid is None else keep_valid
        self._text = text_surface
        self._on_tag = on_tag
        self._prev = ""
        self._pending = ""

    def feed(self, chunk: str) -> str:
        """Return the safe text to emit now; hold back any partial open tag."""
        out: list[str] = []
        for ch in chunk:
            if self._pending:
                self._release(out, self._settle(ch))
            if self._buf:
                self._buf += ch
                if ch == "]":
                    self._close(out)
                elif ch == "[" or len(self._buf) > 41:
                    self._release(out, self._buf[:-1])
                    self._buf = "[" if ch == "[" else ""
                    if ch != "[":
                        self._release(out, ch)
            elif ch == "[":
                self._buf = "["
            else:
                self._release(out, ch)
        return "".join(out)

    def flush(self) -> str:
        """Emit anything still buffered at end of stream (an unclosed '[' = literal text). A tag held for
        its lookahead is a tag: nothing followed it, so nothing can have made it a link."""
        held, self._pending = self._pending, ""
        if held:
            self._told(held)
        rest, self._buf = self._buf, ""
        return rest

    def _release(self, out: list[str], text: str) -> None:
        if text:
            out.append(text)
            self._prev = text[-1]

    def _close(self, out: list[str]) -> None:
        token, self._buf = self._buf, ""
        name = token[1:-1].strip().lower()
        if not self._text:
            self._release(out, token if (name in ALLOWED_AUDIO_TAGS and self._keep_valid) else "")
            return
        if not _tag_words(name) or _attached(self._prev or " "):
            self._release(out, token)
            return
        self._pending = token

    def _settle(self, ch: str) -> str:
        token, self._pending = self._pending, ""
        if ch == "(":
            return token
        self._told(token)
        return ""

    def _told(self, token: str) -> None:
        if self._on_tag is None:
            return
        for word in _tag_words(token[1:-1].strip().lower()):
            self._on_tag(word)


def emotion_from_text(text: str) -> str | None:
    """The face the FIRST tag in the reply asks for, or None when she wrote none.

    Read by the SAME filter the terminal reads tags with, so the browser and the CLI cannot disagree
    about which bracket was a tag or which face it picks. Matching `[...]` against the nineteen AUDIO
    tags alone left `[thinking]`, `[determined]` and `[confused]` — the ones she writes most, and the
    ones the text register names for her — falling through to a second LLM call in the browser while the
    terminal had already resolved them off `TAG_TO_FACE`."""
    seen: list[str] = []
    tags = AudioTagFilter(keep_valid=False, text_surface=True, on_tag=seen.append)
    tags.feed(text or "")
    tags.flush()
    return TAG_TO_FACE.get(seen[0]) if seen else None


class CodeFenceFilter:
    """Safety net: strip fenced ``` code blocks ``` and inline `backticks` from the SPOKEN stream so the
    voice never dictates code ("backtick backtick python import..."). The real fix is the prompt + work
    mode (she writes code to a file via write_file); this guarantees nothing slips through if the model
    still pastes code. Stateful across stream chunks; while inside a fence, content is suppressed.

    In BOTH states a trailing run of ≤2 backticks is held back: a fence marker arrives char-by-char,
    and wiping the whole buffer would eat the partial closer (or opener) — the fence never closes."""

    def __init__(self) -> None:
        self._buf = ""
        self._in_fence = False

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        return self._drain(final=False)

    def flush(self) -> str:
        return self._drain(final=True)

    def _drain(self, final: bool) -> str:
        out: list[str] = []
        while True:
            idx = self._buf.find("```")
            if idx == -1:
                break
            if not self._in_fence:
                out.append(self._buf[:idx])
                self._buf = self._buf[idx + 3:]
                self._in_fence = True
            else:
                self._buf = self._buf[idx + 3:]
                self._in_fence = False
        if self._in_fence:
            text = self._buf
            hold = 0
            while hold < 2 and hold < len(text) and text[len(text) - 1 - hold] == "`":
                hold += 1
            self._buf = text[len(text) - hold:] if (hold and not final) else ""
            if final:
                self._in_fence = False
            return _strip_inline_ticks("".join(out))
        text = self._buf
        hold = 0
        while hold < 2 and hold < len(text) and text[len(text) - 1 - hold] == "`":
            hold += 1
        if not final and hold:
            self._buf = text[len(text) - hold:]
            text = text[: len(text) - hold]
        else:
            self._buf = ""
        out.append(text)
        return _strip_inline_ticks("".join(out))


def _strip_inline_ticks(s: str) -> str:
    """Drop stray single/double backticks (inline `code`) — keep the word, lose the backtick noise."""
    return s.replace("`", "")


_HARMONY_TOKEN = r"<\|[A-Za-z0-9_]{1,40}\|>"
_HARMONY_WORD = r"(?:analysis|commentary|final|json|code|text|assistant|user|system|developer)"
_HARMONY_SEG = rf"{_HARMONY_TOKEN}(?:\s*{_HARMONY_WORD}\b)?"
_JSON_ARGS = r"\{(?:[^{}]|\{[^{}]*\}){0,600}\}"
_CJK_CHAR = r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾝ]"
_CJK_RUN = _CJK_CHAR + r"{1,40}"

# `functions.x` is also ordinary text ("edita functions.php"), so the no-`to=` form is kept strict.
_NOT_A_FILE_EXT = (
    r"(?!(?:php|jsx?|tsx?|py|rb|go|rs|sh|json|md|txt|html?|css|xml|ya?ml|c|cpp|java|lua|kt|swift)\b)"
)
_NAMESPACE = r"(?:functions|browser|multi_tool_use)"
_TO_RECIPIENT = rf"\bto=(?:{_NAMESPACE}\.[A-Za-z0-9_.-]{{1,64}}|python\b|assistant\b)"
_BARE_RECIPIENT = rf"{_NAMESPACE}\.{_NOT_A_FILE_EXT}[a-z_][a-z0-9_]{{0,62}}"
_LEAK_TAIL = rf"(?:\s*{_HARMONY_SEG})*(?:\s*{_JSON_ARGS})?(?:\s*{_HARMONY_SEG})*(?:\s*{_CJK_RUN})?"

_TOOL_CALL_LEAK_RE = re.compile(
    rf"(?:{_JSON_ARGS}\s*)?(?:{_HARMONY_SEG}\s*)*{_TO_RECIPIENT}{_LEAK_TAIL}"
    rf"|(?:{_JSON_ARGS}\s*)(?:{_HARMONY_SEG}\s*)*{_BARE_RECIPIENT}{_LEAK_TAIL}"
    rf"|(?:{_HARMONY_SEG}\s*)+{_BARE_RECIPIENT}{_LEAK_TAIL}"
    rf"|{_BARE_RECIPIENT}(?:\s*{_HARMONY_SEG})*\s*(?:{_JSON_ARGS}|{_HARMONY_SEG}){_LEAK_TAIL}"
)
_HARMONY_SEG_RE = re.compile(_HARMONY_SEG)

_TRIGGER_RE = re.compile(rf"\{{\s*\"|<\||\bto=|\b{_NAMESPACE}\.[a-z_]")
_TRIGGER_LITERALS = ('{"', '{ "', "<|", "to=", "functions.", "browser.", "multi_tool_use.")
_MAX_TRIGGER_LEN = max(len(t) for t in _TRIGGER_LITERALS)
_MAX_HOLD = 300


def strip_tool_call_leaks(s: str) -> str:
    """Remove leaked tool-call syntax from ASSEMBLED text. Anchored on the recipient — a JSON object or a
    CJK run is only eaten when it is glued to one, so "un JSON con la clave query" survives untouched.
    `to=functions.x` is a leak on its own; BARE `functions.x` is ordinary prose ("usas functions.map")
    and counts only when glued to the JSON args or a harmony segment a real emission always carries —
    without that split the filter ate the sentence. This also runs on what gets persisted."""
    if not s:
        return s
    cleaned = _TOOL_CALL_LEAK_RE.sub(" ", s)
    cleaned = _HARMONY_SEG_RE.sub(" ", cleaned)
    if cleaned == s:
        return s
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return re.sub(r"\s+([.!?,;:])", r"\1", cleaned)


def _partial_tail_len(buf: str) -> int:
    """Length of a trailing run that could still grow into a trigger ('t' → 'to=')."""
    for k in range(min(len(buf), _MAX_TRIGGER_LEN), 0, -1):
        if any(lit.startswith(buf[-k:]) for lit in _TRIGGER_LITERALS):
            return k
    return 0


_LEAK_MAY_GROW_RE = re.compile(rf"^\s*(?:[{{<]|{_CJK_CHAR}|$)")
_HARMONY_WORDS = (
    "analysis", "commentary", "final", "json", "code", "text", "assistant", "user", "system", "developer",
)


def _leak_may_grow(rest: str) -> bool:
    """True when what sits after an already-complete leak could still attach to it. Every tail element is
    optional (trailing args, `<|call|>`, the bare word after `<|constrain|>`, a CJK run), so a match that
    looks finished mid-stream is not: releasing on it is what let `{"location":"Tokyo"}`, `json` and the
    CJK spam through one character at a time while the same string was caught whole."""
    if _LEAK_MAY_GROW_RE.match(rest):
        return True
    word = rest.lstrip()
    return bool(word) and word.isalpha() and any(w.startswith(word) for w in _HARMONY_WORDS)


def _pending_leak_start(buf: str) -> int | None:
    """Index from which text must be withheld because a leak may still be arriving, or None. Only
    _TRIGGER_RE's shapes can begin a leak, so only those are ever held back — and each trigger demands
    the shape that follows it, or an English sentence ending in "functions." would stall the voice. A
    trigger that has outgrown _MAX_HOLD without becoming a leak is stepped past — never stall the
    voice on it."""
    pos = 0
    while True:
        m = _TRIGGER_RE.search(buf, pos)
        if m is None:
            return None
        start = m.start()
        if len(buf) - start > _MAX_HOLD:
            pos = start + 1
            continue
        leak = _TOOL_CALL_LEAK_RE.search(buf, start)
        if leak is not None and not _leak_may_grow(buf[leak.end():]):
            pos = leak.end()
            continue
        return start


class ToolCallLeakFilter:
    """First stage of the spoken chain: strips raw tool-call syntax the model emits into the TEXT channel
    by mistake. Captured live in 3 of ~19 runs as `{"query":"…"}to=functions.web_search` — read aloud,
    JSON and all. It cannot be prevented at the source, so the filter is the net.

    Streaming-safe in the UrlFilter sense: a fragment that could still grow into a leak is held back
    rather than spoken, because a half-arrived `to=functions.` must never reach TTS. Text with no trigger
    in it streams through with zero added latency, and the hold is bounded so nothing can stall a turn."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        start = _pending_leak_start(self._buf)
        cut = start if start is not None else len(self._buf) - _partial_tail_len(self._buf)
        if cut <= 0:
            return ""
        release, self._buf = self._buf[:cut], self._buf[cut:]
        return strip_tool_call_leaks(release)

    def flush(self, *, partial: bool = False) -> str:
        """`partial=True` is the tool-call seam: a buffer that still holds a POSSIBLE leak keeps it.

        The leak regex is anchored on the recipient, so a half-arrived emission — the JSON args with
        `to=functions.web_search` not yet written — matches nothing and would be spoken as itself
        ('Claro, query dos puntos tokio'). Holding costs no speech: what is held is a leak, and a
        leak is never anything she was going to say."""
        if partial and _pending_leak_start(self._buf) is not None:
            return ""
        rest, self._buf = self._buf, ""
        return strip_tool_call_leaks(rest)


_COPULA_ES = frozenset({"es", "era", "sería", "seria", "está", "esta", "estaba", "sea", "son"})
_COPULA_EN = frozenset({"is", "was", "are", "were", "be", "here's", "it's"})
_PREP_ES = frozenset({"en", "a", "al", "de", "del", "desde", "hacia", "hasta", "por", "para",
                      "sobre", "con", "visita", "abre", "entra", "usa"})
_PREP_EN = frozenset({"at", "on", "to", "from", "into", "via", "through", "in", "under", "visit", "open"})

_URL_STANDIN = {("es", "cop"): "su web oficial", ("es", "prep"): "su web oficial",
                ("en", "cop"): "its official site", ("en", "prep"): "its official site"}
_FILE_STANDIN = {("es", "cop"): "uno de tus archivos", ("es", "prep"): "tus archivos",
                 ("en", "cop"): "one of your files", ("en", "prep"): "your files"}

_LAST_WORD_RE = re.compile(r"([A-Za-zÀ-ÿ']+)$")


def _dangling_connector(ctx: str) -> tuple[str, str] | None:
    """(lang, kind) when `ctx` ends in a copula/preposition/colon that the stripped span was completing.

    Those words NEED a complement: deleting a URL/path after one beheads the sentence ("la dirección
    es" — silence), so a stand-in is spoken. Any other context reads fine as a plain drop — which is
    why the word sets deliberately carry no "check/see"."""
    ctx = ctx.rstrip()
    if ctx.endswith(":"):
        return ("es" if _ES_TEXT_RE.search(ctx) else "en", "prep")
    m = _LAST_WORD_RE.search(ctx)
    if not m:
        return None
    w = m.group(1).lower()
    if w in _COPULA_ES:
        return ("es", "cop")
    if w in _PREP_ES:
        return ("es", "prep")
    if w in _COPULA_EN:
        return ("en", "cop")
    if w in _PREP_EN:
        return ("en", "prep")
    return None


def _sub_unspeakable(s: str, prev: str, pattern: re.Pattern, standin: dict) -> str:
    def repl(m: re.Match) -> str:
        text, punct = m.group(0), ""
        while text and text[-1] in ".,;:!?…":  # a URL match swallows the sentence period — keep it spoken
            punct = text[-1] + punct
            text = text[:-1]
        conn = _dangling_connector(prev + m.string[: m.start()])
        return (standin[conn] if conn else "") + punct

    return pattern.sub(repl, s)


def _strip_urls(s: str, prev: str = "") -> str:
    """Strip everything a VOICE must never read — web_search citation markers, URLs, and file names/paths —
    then tidy the brackets left behind (incl. the classic orphan "(" when a link inside parens was removed).
    Done in the UrlFilter layer (whole-token buffered) so the sentence-splitter never cuts a link/path at
    its dots. `prev` is the already-released text before this window — the connector context.

    The text_security scrub runs here too, for the same reason it runs on every drawn surface: a bidi
    override or a C0/C1 cursor move quoted off a web page is nothing a voice can perform, and the /v1
    stream is a caption surface we do not own. One policy, imported from its one home — newlines kept,
    because the sentence splitter downstream feeds on them."""
    s = text_security.scrub(s, newlines=True)
    s = strip_citation_markers(s)
    s = _PAREN_URL_RE.sub("", s)
    s = _sub_unspeakable(s, prev, _URL_RE, _URL_STANDIN)
    s = _sub_unspeakable(s, prev, _FILEPATH_RE, _FILE_STANDIN)
    s = re.sub(r"\(\s*[.,;:]*\s*\)|\[\s*\]", "", s)
    s = re.sub(r"\(\s*(?=[.!?])", "", s)
    return re.sub(r"\s+([.!?,;:])", r"\1", s)


class UrlFilter:
    """Streaming URL remover. A URL has no spaces, so we release text only up to the last whitespace and
    hold the trailing partial token — that way a URL is always seen WHOLE before it's emitted (the
    sentence-splitting ForbiddenPhraseFilter would otherwise cut it at its dots, e.g. 'chatforest.' |
    'com/...', and miss it). A voice can't speak a link; the prompt tells her to name it instead — and
    when the model types one anyway right after a copula/preposition, a stand-in phrase replaces it so
    the spoken sentence never ends beheaded ("…es" — silence). The connector usually left the filter in
    an earlier release, hence the `_tail` of already-emitted text."""

    def __init__(self) -> None:
        self._buf = ""
        self._tail = ""

    def _emit(self, text: str) -> str:
        out = _strip_urls(text, self._tail)
        self._tail = (self._tail + out)[-160:]
        return out

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        i = max(self._buf.rfind(" "), self._buf.rfind("\n"), self._buf.rfind("\t"))
        if i == -1:
            return ""
        release = self._buf[: i + 1]
        j = release.rfind("(")
        if j != -1 and ")" not in release[j:] and len(self._buf) - j <= 200:
            release = release[:j]  # hold an open parenthetical whole so "(label: url)" drops as ONE aside
        if not release:
            return ""
        self._buf = self._buf[len(release):]
        return self._emit(release)

    def flush(self, *, partial: bool = False) -> str:
        """`partial=True` is the tool-call seam: an aside whose '(' is still open stays held.

        Releasing it there speaks the orphan the whole-parenthetical hold exists to prevent —
        measured, "Lo saqué de (Reuters: su web oficial" — and costs nothing to keep, because an
        unclosed aside around a link is text this filter was going to delete either way."""
        if partial:
            j = self._buf.rfind("(")
            if j != -1 and ")" not in self._buf[j:]:
                rest, self._buf = self._buf[:j], self._buf[j:]
                return self._emit(rest)
        rest, self._buf = self._buf, ""
        return self._emit(rest)


_FORBIDDEN_RE = re.compile(
    r"""(
        \bi\s*(?:'m|\sam)?\s*                                # I / I'm / I am
        (?:                                                  # an inability verb …
            ca(?:n'?t|nnot|n\s*not)
          | could?n'?t | could\s*not
          | (?:un)?able\s+to
          | not\s+able\s+to
        )
        \s*(?:to\s+)?
        (?:access|open|browse|reach|view|get\s+to|pull\s+up|load|connect|go\s+online)  # a reach verb,
        [^.!?]*[.!?]?                                        # REQUIRED — so an ordinary tool failure
    )                                                        # ("I couldn't save/write/find/set …") is
    | (\bi\s*(?:'m|\sam)?\s*do(?:n'?t|\s*not)\s+have\s+access\s+to[^.!?]*[.!?]?)  # NOT mistaken for a web
    | (?P<other>\bno\s+puedo\s+(?:acceder|abrir|leer|ver|cargar|conectar)[^.!?]*[.!?]?)  # claim and rewritten.
    """,
    re.IGNORECASE | re.VERBOSE,
)


_WEB_OBJECT_RE = re.compile(
    r"""https?:// | \bwww\.
      | \b(?:internet|webs?|websites?|webpages?|sites?|pages?|links?|urls?|online)\b
      | \b(?:p[áa]ginas?|sitios?|enlaces?)\b | \ben\s+l[íi]nea\b
      | \b[a-z0-9-]{2,}\.(?:com|net|org|io|ai|dev|app|edu|gov)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


_REACTIONS = (
    "[mischievously] Hmph, that page is playing hard to get — let me grab it another way! ",
    "[nervous] Ah, that link's being shy and won't open... no worries, I'll find it elsewhere! ",
    "[laughs] Oops, that page just slammed the door on me! Let me dig it up another way. ",
    "[curious] Hmm, that one won't budge for me — let me look it up differently! ",
)

_SENTENCE_BREAK = re.compile(r"[.!?]\s*")


def _drop_claim_sentence(text: str, m: re.Match) -> str:
    """Take the whole sentence the false claim sat in, not just the claim.

    Cutting the claim alone left what led into it — "Lo siento, ", "[sad] " — and she spoke the
    fragment. The lead-in and the audio tag belong to the sentence that carried the lie."""
    starts = [b.end() for b in _SENTENCE_BREAK.finditer(text[:m.start()])]
    return (text[:starts[-1] if starts else 0] + text[m.end():]).strip()


def _strip_leading_tag(line: str) -> str:
    """Drop a leading `[tag] ` from one of our own reaction lines."""
    return re.sub(r"^\s*\[[^\]]{1,40}\]\s*", "", line)

_FILTERED_FALLBACK = "One sec — let me put that a better way for you. "
_ES_TEXT_RE = re.compile(
    r"[áéíóúüñ¿¡]"
    r"|\b(?:el|la|los|las|un|una|de|que|para|con|porque|pero|voy|puedo|puedes|quiero"
    r"|necesito|tengo|como|cuando|donde|esta|estas|eso|esto|otra|vez|ahora|luego|hoy"
    r"|aqui|mi|si|tu|te|ya|hola|gracias|vale|bueno|vamos"
    r"|oye|mira|dime|dame|espera|sigue|repite|busca|pon|ponme|abre|cierra|borra|guarda"
    r"|manda|apunta|escribe|baja|sube|quita|hazlo|hazme|detente"
    r"|cancela|cancelalo|cancelala|cancelar|salir|vete|venga|ayuda|ayudame|escuchame|hablame"
    r"|cantame|cuentame|explicame|avisame|recuerdame|muestrame|ensename|llamame|mandame"
    r"|empieza|termina|terminalo|apaga|apagalo|enciende|silencio|despacio|pausa|listo"
    r"|traducelo|resumeme|dejalo|olvidalo|borralo|guardalo|abrelo|cierralo|leelo"
    r"|perdon|perdona|noches|tardes)\b"
    r"|\bno\s+(?:me|te|se|lo|la|le|es|hay|va|puedo|puedes|quiero|funciona|sirve|entiendo)\b",
    re.IGNORECASE,
)

def filtered_reply_fallback() -> str:
    """A safe in-character line for when the filters emptied a reply the model really produced.

    English, like every other canned line. A per-language copy only ever covers the languages
    somebody remembered to write, so it reads as her own voice for two of them and as nothing at all
    for the rest — and each new one has to be translated, kept in step, and guessed at by a detector."""
    return _FILTERED_FALLBACK


_CRASH_APOLOGY = "Sorry, something tripped up on my end — let's try that again."


def crash_apology() -> str:
    """The spoken line for a turn the loop crashed out of."""
    return _CRASH_APOLOGY


_SLANG_RE = re.compile(  # standalone words only (\b) — laughter (haha/jaja) deliberately kept, TTS voices it fine
    r"\b(lol|lmao|lmfao|rofl|omg|omfg|btw|idk|imo|imho|fyi|tbh|ngl|smh|jk|wtf|xd|uwu)\b",
    re.IGNORECASE,
)

# `www.` counts as a URL only with something after the dot, so prose about "www." itself survives.
_URL_RE = re.compile(r"\(*\b(?:https?://|www\.(?=\S))[^\s)]+\)*", re.IGNORECASE)
# Strip the WHOLE "(… https://… )" parenthetical — removing just the URL leaves the orphan "(Reuters:".
_PAREN_URL_RE = re.compile(r"\(\s*[^()]*?(?:https?://|www\.(?=\S))[^()]*?\)", re.IGNORECASE)

_CITATION_MARKER_RE = re.compile(chr(0xE200) + r"[\s\S]*?" + chr(0xE201))
_STRAY_PUA_RE = re.compile("[" + chr(0xE200) + "-" + chr(0xE20F) + "]")


def strip_citation_markers(text: str) -> str:
    """Remove OpenAI web_search inline citation marker runs (and stray PUA markers) from any text — used for
    both spoken output and content written to files, so `citeturn0search0` never leaks to the user.

    OpenAI injects the markers as Private-Use-Area runs (U+E200..U+E201) — metadata, not words; TTS
    would read "cite turn zero search zero". The real URLs come from url_citation annotations."""
    if not text:
        return text
    text = _CITATION_MARKER_RE.sub("", text)
    return _STRAY_PUA_RE.sub("", text)


class CitationFilter:
    """The same citation runs, taken out of a STREAM rather than a finished string.

    Buffered because the run arrives split across chunks: a per-chunk strip deletes the two delimiters
    and leaves `citeturn0search0` behind as prose, which is worse than the boxes. Past the limit the
    hold is given back with the PUA characters removed — an opening mark with no close is a run this
    build cannot place, and swallowing the rest of her reply over it loses what she said.
    """

    _LIMIT = 256
    _OPEN, _CLOSE = chr(0xE200), chr(0xE201)

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        out: list[str] = []
        while self._buf:
            at = self._buf.find(self._OPEN)
            if at < 0:
                out.append(self._buf)
                self._buf = ""
                break
            out.append(self._buf[:at])
            end = self._buf.find(self._CLOSE, at + 1)
            if end < 0:
                held = self._buf[at:]
                self._buf = "" if len(held) > self._LIMIT else held
                if not self._buf:
                    out.append(held)
                break
            self._buf = self._buf[end + 1:]
        return _STRAY_PUA_RE.sub("", "".join(out))

    def flush(self) -> str:
        held, self._buf = self._buf, ""
        return _STRAY_PUA_RE.sub("", held)


class HeadTagFilter:
    """The bracket her reply OPENS with, when the tag vocabulary cannot read it.

    The model coins near-vocabulary tags, and a text surface rightly refuses to guess at brackets
    mid-prose. Position is the evidence the vocabulary cannot give: a bracket before any other
    character, holding only lowercase letters and not opening a markdown link, sits exactly where she
    was told to put her tag. One printable character ends the watch for the whole turn, which is what
    keeps `[1]`, `list[int]` and `[Forbes](url)` out of it.
    """

    _LIMIT = 43     # a "tag" longer than AudioTagFilter would even buffer is prose; let it through

    def __init__(self, on_tag: Callable[[str], None] | None = None) -> None:
        self._head = True
        self._buf = ""
        self._closed = ""
        self._on_tag = on_tag

    def feed(self, chunk: str) -> str:
        if not self._head:
            return chunk
        out: list[str] = []
        for i, ch in enumerate(chunk):
            if self._closed:
                self._settle(out, ch)
                if not self._head:
                    out.append(chunk[i:])
                    break
                if ch == "[":
                    self._buf = "["
                elif ch.isspace():
                    out.append(ch)
                else:
                    out.append(chunk[i:])
                    self._head = False
                    break
                continue
            if self._buf:
                self._buf += ch
                if ch == "]":
                    self._closed, self._buf = self._buf, ""
                elif len(self._buf) > self._LIMIT:
                    out.append(self._buf)
                    self._buf, self._head = "", False
                    out.append(chunk[i + 1:])
                    break
            elif ch == "[":
                self._buf = "["
            elif ch.isspace():
                out.append(ch)
            else:
                out.append(chunk[i:])
                self._head = False
                break
        return "".join(out)

    def flush(self) -> str:
        out: list[str] = []
        if self._closed:
            self._settle(out, "")
        rest, self._buf = self._buf, ""
        self._head = False
        return "".join(out) + rest

    def _settle(self, out: list[str], after: str) -> None:
        """Judged on the first character that follows: `(` or `:` makes it markdown and it prints;
        letter-only lowercase words make it her tag and it goes, its known words to the face. The `:`
        is the reference-link definition, which opens a reply as often as a link does."""
        token, self._closed = self._closed, ""
        words = re.split(r"[,\s]+", token[1:-1].strip())
        if after in ("(", ":") or not all(w.isalpha() and w.islower() for w in words):
            out.append(token)
            self._head = False
            return
        if self._on_tag is not None:
            for word in words:
                if word in TAG_TO_FACE:
                    self._on_tag(word)


# Anchored on a file extension; JS/CSS-ish exts deliberately excluded so "Node.js" is never eaten.
_FILEPATH_RE = re.compile(
    r"\S*\.(?:md|markdown|html?|pdf|txt|csv|json|py|ipynb|docx?|xlsx?|pptx?|png|jpe?g|gif|svg)\b",
    re.IGNORECASE,
)

# Emoji blocks only, in order: pictographs (+supplemental/extended-A), misc symbols + dingbats, misc
# technical, arrows/stars, flags, variation selectors, ZWJ (binds emoji sequences) — deliberately
# spares Spanish accents, ¿ ¡, the em-dash (U+2014) and ellipsis (U+2026).
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002300-\U000023FF"
    "\U00002B00-\U00002BFF"
    "\U0001F1E6-\U0001F1FF"
    "\U0000FE00-\U0000FE0F"
    "\U0000200D]"
)

_STT_TOKEN_RE = re.compile(r"<\|[^>]*\|>")

_LATEX_WRAP_RE = re.compile(r"\\[\[\]()]")
_LATEX_TEXTCMD_RE = re.compile(r"\\(?:text|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}")
_LATEX_OP_RE = re.compile(
    r"\\(?:cdot|times|div|frac|left|right|displaystyle|quad|"
    r"rightarrow|leftarrow|Rightarrow|Leftarrow|leftrightarrow|to|mapsto|implies|approx|neq|leq|geq|pm)\b"
    r"|\\[,;!:>]"
)
_LATEX_CMD_RE = re.compile(r"\\([a-zA-Z]+)")
_MATH_SUBSUP_BRACE_RE = re.compile(r"[_^]\{([^{}]*)\}")
_MATH_SUBSUP_RE = re.compile(r"(?<=[A-Za-z0-9])[_^](?=[A-Za-z0-9])")


_SENTENCE_END = re.compile(r"[.!?\n]")

_WORDLESS_SOUND = re.compile(r"[mh]+")


def _speech_key(sentence: str) -> str:
    """Comparison form of a spoken sentence: collapsed whitespace, lowercased, outer punctuation gone."""
    return re.sub(r"\s+", " ", sentence).strip().lower().strip(".!?¿¡…\"' ")


def is_wordless_sound(text: str) -> bool:
    """The ONE test for "this is a hum, not a thing she said". A hum, in any language, is only m's and
    h's, and no word in one is spelled from those letters alone — which is all _WORDLESS_SOUND matches.
    Shared with the expressive TTS engine, which must not colour a six-character body with an emotion
    tag."""
    return bool(_WORDLESS_SOUND.fullmatch(_speech_key(text)))


# What a line IS, not what it says: a bullet, a step, a heading, a quote, a table row, a fence.
_STRUCTURE_RE = re.compile(r"^\s*(?:[-*+•]\s|\d+[.)]\s|#{1,6}\s|>|\||`{3}|~{3})")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")


class RepeatCollapse:
    """The ONE definition of "she already said that", shared by the spoken chain and the persisted turn.

    A stutter is a CONTIGUOUS replay: what arrives now reproduces, verbatim and in order, the run of
    sentences immediately preceding it, to the end of the turn. Asking only for two sentences recurring
    ANYWHERE ate structured content, so EVERY non-blank sentence is compared, short ones included — and a
    block must also hold one real PROSE sentence, or a twice-written table row would collapse.

    A sentence opening a possible replay is HELD, not dropped: the copy goes only when the replay reaches
    the end of what was said, and the first mismatch releases everything held, in order. Blank lines and
    wordless hums are TRANSPARENT — one heartbeat hum between announcement and result broke contiguity."""

    def __init__(self) -> None:
        self._said: list[tuple[str, bool]] = []
        self._pending: list[tuple[str, str, bool]] = []
        self._start = 0
        self._matched = 0
        self._fence = False

    def take(self, sentence: str, source: str | None = None) -> str:
        line = sentence if source is None else source
        norm = _speech_key(sentence)
        prose = self._prose(line, norm)
        if not norm or _WORDLESS_SOUND.fullmatch(norm):
            if self._pending:
                self._pending.append((sentence, "", False))
                return ""
            return sentence
        held = ""
        if self._pending:
            if self._said[self._start + self._matched][0] == norm:
                self._pending.append((sentence, norm, prose))
                self._matched += 1
                if self._start + self._matched == len(self._said):
                    self._pending.clear()  # the whole tail came back verbatim — this copy goes
                return ""
            held = self.flush()
        for i in range(len(self._said) - 1, -1, -1):
            if self._said[i][0] != norm or not any(p for _, p in self._said[i:]):
                continue
            if i == len(self._said) - 1:
                return held  # a one-sentence tail: nothing left to confirm
            self._pending = [(sentence, norm, prose)]
            self._start, self._matched = i, 1
            return held
        return held + self._keep(sentence, norm, prose)

    def flush(self) -> str:
        """Release what is still being weighed. Every caller ends here, or a reply whose LAST sentence
        is the one on hold (one that never got a closing '.') would be held forever, i.e. lost."""
        out = [self._keep(raw, norm, prose) if norm else raw for raw, norm, prose in self._pending]
        self._pending = []
        return "".join(out)

    def _prose(self, line: str, norm: str) -> bool:
        """Called once per sentence, in order — it carries the fence toggle."""
        inside = self._fence
        if _FENCE_RE.match(line):
            self._fence = not self._fence
            return False
        return not inside and len(norm) > 12 and " " in norm and not _STRUCTURE_RE.match(line)

    def _keep(self, sentence: str, norm: str, prose: bool) -> str:
        self._said.append((norm, prose))
        return sentence


def collapse_repeats(text: str) -> str:
    """Drop the verbatim block repeat from a FINISHED reply, for the turn we persist.

    The spoken chain already collapses it on the way out, but the persisted turn is the raw text the
    loop returned, and every subsequent request re-sends it as history: a doubled turn pays its own
    length again on every turn until it scrolls out of the recent-turns window, and shows the model a
    worked example of stuttering. Same RepeatCollapse the voice uses, so what she is heard to say and
    what is written down cannot disagree about what a repeat is."""
    if not text:
        return text
    collapse = RepeatCollapse()
    out, buf = [], text
    while True:
        m = _SENTENCE_END.search(buf)
        if not m:
            break
        sentence, buf = buf[:m.end()], buf[m.end():]
        out.append(collapse.take(sentence))
    out.append(collapse.take(buf))
    out.append(collapse.flush())
    return "".join(out)


class ForbiddenPhraseFilter:
    """Sentence-buffered filter that keeps her spoken text clean: the FIRST time the model wrongly claims
    it cannot reach a page, the claim becomes a playful in-character reaction (later ones are dropped),
    and typed-chat shorthand ("lol", "btw") that TTS would spell out is stripped.

    A claim is rewritten only when its sentence shows it is about the WEB, because the same inability
    verbs open refusals that are TRUE and prompt-mandated — rewriting "I can't connect Notion, press Sign
    in in Settings" deleted the instruction and promised a search the prompt forbids.

    Runs LAST in the chain so a reaction's opening [tag] is never screened, and it keeps that tag only
    where tags are actually performed — otherwise the word is READ ALOUD."""

    def __init__(self) -> None:
        self._buf = ""
        self._reacted = False
        self._repeats = RepeatCollapse()
        import random

        i = random.randrange(len(_REACTIONS))
        keep_tags = expressive_mode()
        self._reaction = _REACTIONS[i] if keep_tags else _strip_leading_tag(_REACTIONS[i])

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        out = []
        while True:
            m = _SENTENCE_END.search(self._buf)
            if not m:
                break
            end = m.end()
            sentence, self._buf = self._buf[:end], self._buf[end:]
            out.append(self._repeats.take(self._scrub(sentence), sentence))
        return "".join(out)

    def flush(self) -> str:
        rest, self._buf = self._buf, ""
        return self._repeats.take(self._scrub(rest), rest) + self._repeats.flush()

    def _scrub(self, text: str) -> str:
        m = _FORBIDDEN_RE.search(text)
        if m and _WEB_OBJECT_RE.search(text):
            # The reaction is English, and she claimed it in whatever language she was speaking.
            # Dropping a false claim is the property that matters; putting two languages in one
            # sentence to keep the quip is a worse answer than losing the quip.
            english = m.group("other") is None
            if self._reacted or not english:
                text = _drop_claim_sentence(text, m)
            else:
                text = _FORBIDDEN_RE.sub(self._reaction, text)
            self._reacted = True
        text = _SLANG_RE.sub("", text)
        text = _STT_TOKEN_RE.sub("", text)
        text = _LATEX_TEXTCMD_RE.sub(r"\1", text)
        text = _LATEX_OP_RE.sub(" ", text)
        text = _LATEX_WRAP_RE.sub("", text)
        text = _LATEX_CMD_RE.sub(r"\1", text)
        text = _MATH_SUBSUP_BRACE_RE.sub(r" \1", text)
        text = _MATH_SUBSUP_RE.sub(" ", text)
        text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
        text = _EMOJI_RE.sub("", text)
        text = text.replace("~", "")
        text = text.replace("**", "").replace("__", "")
        text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
        text = re.sub(r"(?m)^\s*\d+[.)]\s+", "", text)
        text = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)
        text = re.sub(r"\s+([.!?,;:])", r"\1", text)
        return re.sub(r"[ \t]{2,}", " ", text)


DONE_SENTINEL = object()
FLUSH_SENTINEL = object()


def flush_spoken(leak_f, code_f, url_f, tag_f, phrase_f, *, final: bool) -> str:
    """Everything the spoken chain is still holding, each stage's remainder pushed through the stages
    that follow it. The ONE definition of that order, so the three consumers cannot drift.

    `final=False` is the TOOL-CALL seam, and it exists because two stages hold text only later text can
    release: UrlFilter releases up to the last whitespace, and ForbiddenPhraseFilter then has no
    terminator and holds the whole sentence. Measured: 10.09s of silence on a 10s tool.

    At that seam three stages keep a hold on purpose. The fence filter is not flushed at all — its flush
    clears `_in_fence`, so an open code block reopens as prose and the code is read aloud. A non-final
    flush closes with a SPACE, or what follows lands against a full stop, as in the measured "sale.Mmm"."""
    out = phrase_f.feed(tag_f.feed(url_f.feed(code_f.feed(leak_f.flush(partial=not final)))))
    if final:
        out += phrase_f.feed(tag_f.feed(url_f.feed(code_f.flush())))
    out += phrase_f.feed(tag_f.feed(url_f.flush(partial=not final)))
    out += phrase_f.feed(tag_f.flush())
    out += phrase_f.flush()
    if not final and out and not out[-1].isspace():
        out += " "
    return out


_RESPONSE_ID = "chatcmpl-kotoba"
_MODEL = "kotoba"


def _now() -> int:
    import time

    return int(time.time())


def _chunk(delta: dict, finish: str | None = None) -> str:
    payload = {
        "id": _RESPONSE_ID,
        "object": "chat.completion.chunk",
        "created": _now(),
        "model": _MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish, "logprobs": None}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def text_role() -> str:
    """Priming chunk — OpenAI streams the assistant role first; some strict parsers require it."""
    return _chunk({"role": "assistant", "content": ""})


def text_chunk(content: str) -> str:
    return _chunk({"content": content})


def text_final() -> str:
    return _chunk({}, finish="stop")


def text_tool_call(name: str, arguments: str = "{}", call_id: str | None = None) -> str:
    """An OpenAI-streaming tool-call delta. Used to invoke an ElevenLabs system tool (e.g. skip_turn)
    from our custom LLM — the sanctioned way to make the agent stay silent without an empty completion."""
    return _chunk({
        "tool_calls": [{
            "index": 0, "id": call_id or f"call_{name}", "type": "function",
            "function": {"name": name, "arguments": arguments},
        }]
    })


def text_final_tool() -> str:
    """Closing chunk for a tool-call turn (finish_reason 'tool_calls', not 'stop')."""
    return _chunk({}, finish="tool_calls")


def text_done() -> str:
    return "data: [DONE]\n\n"


def emotion_event(emotion: str) -> str:
    return f"event: emotion\ndata: {json.dumps({'emotion': emotion})}\n\n"


def task_event(payload: dict) -> str:
    """SSE frame for a work-mode UI event ({kind, ...}). Drives the terminal/files panels + chip."""
    return f"event: task\ndata: {json.dumps(payload)}\n\n"


async def narrate(queue, phrase: str) -> None:
    """Put a complete narration phrase (tool before/after/heartbeat) on the queue with a trailing
    space, so consecutive phrases don't run together into the next chunk ('keep it.Got it.')."""
    if phrase and phrase.strip():
        await queue.put(phrase.strip() + " ")
