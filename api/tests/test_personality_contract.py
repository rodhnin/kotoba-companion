"""Audit gate — the per-tool personality contract holds across EVERY registered tool.

Contract: every tool Kotoba can call must (a) speak in character
for all four outcomes (ANNOUNCE/HEARTBEAT/COMPLETE/FAIL, resolved via core.loop._voice_for), with a FAIL
line that is graceful and never technical (never trips the ForbiddenPhraseFilter, never leaks a raw
`server__tool` name or a stack trace), and (b) expose a valid face profile (ToolSpec.expressions) whose
states map to real emotions — now actually consumed by the work loop via _voice_for/_expr_for.
"""
from __future__ import annotations

import kotoba.core.stream as stream
from kotoba.core.emotions import VALID_EMOTIONS
from kotoba.core.loop import _GENERIC_VOICE, _expr_for, _voice_for
from kotoba.tools.registry import registry

# Tools whose RETURN is itself the spoken or guidance output, so before/after are intentionally empty:
# clarify asks the question itself; start_work/cancel_work return guidance the model speaks in its own
# words; open_link shows a card and the model narrates the offer. They still need a graceful FAIL.
_QUESTION_TOOLS = {"clarify", "ask_user", "open_link", "start_work", "cancel_work", "request_credential",
                   "ask_secret", "get_credential"}

# Tokens that must NEVER reach the user inside a spoken FAIL line: plumbing leaking into character.
_TECHNICAL_TOKENS = ("server__", "Traceback", "Exception", "errno", "stderr", "None", "null")


def _function_tool_names() -> list[str]:
    """Every registered tool the model can call as a function (excludes OpenAI built-ins like
    web_search, which OpenAI narrates itself and has no module voice)."""
    return [name for name, spec in registry().items() if not spec.built_in]


def test_every_tool_resolves_four_state_voice():
    """_voice_for must return a complete 4-key voice for every tool — its own or the generic default,
    never a half-empty dict that would leave her silent mid-task.

    The empty `soul_patterns` argument is deliberate: it forces the module/generic fallback path."""
    for name in _function_tool_names():
        v = _voice_for(name, {})
        assert set(v) >= {"before", "heartbeat", "after", "fail"}, f"{name}: missing voice keys"
        assert isinstance(v["heartbeat"], list), f"{name}: heartbeat must be a list"


def test_every_tool_has_a_graceful_fail_line():
    """A FAIL is mandatory for all tools (the one outcome that must never be a bare error/silence)."""
    for name in _function_tool_names():
        fail = _voice_for(name, {}).get("fail", "")
        assert fail and fail.strip(), f"{name}: empty FAIL line"


def test_fail_lines_are_in_character_not_technical():
    """No authored FAIL line may trip the ForbiddenPhraseFilter ("I can't access…") or leak a
    technical token.

    The filter is the same one the server puts on the spoken stream, and it REWRITES forbidden
    web-access claims — so an authored FAIL must pass through it unchanged."""
    for name in _function_tool_names():
        fail = _voice_for(name, {}).get("fail", "")
        f = stream.ForbiddenPhraseFilter()
        out = (f.feed(fail) + f.flush()).strip()
        assert out == fail.strip(), f"{name}: FAIL altered by ForbiddenPhraseFilter → {fail!r} -> {out!r}"
        assert not stream._FORBIDDEN_RE.search(fail), f"{name}: FAIL trips forbidden-phrase regex: {fail!r}"
        for tok in _TECHNICAL_TOKENS:
            assert tok not in fail, f"{name}: FAIL leaks technical token {tok!r}: {fail!r}"


def test_before_after_present_except_question_tools():
    """Every tool announces before it runs and confirms after, except the ones whose return value IS
    the utterance (`_QUESTION_TOOLS` above)."""
    for name in _function_tool_names():
        v = _voice_for(name, {})
        if name in _QUESTION_TOOLS:
            continue
        assert v["before"].strip(), f"{name}: empty ANNOUNCE (before) line"
        assert v["after"].strip(), f"{name}: empty COMPLETE (after) line"


def test_expressions_profiles_map_to_real_emotions():
    """Every declared EXPRESSIONS value must be one of the 14 canonical emotions (so the controller
    can actually apply it). Catches typos that would silently no-op the face.

    The state names are the declarable vocabulary, not the emitted set: only `focus` and `fail` reach the
    avatar. `done` is declared and unread on purpose — the face after a finished
    turn follows the audio tag she speaks."""
    for name, spec in registry().items():
        profile = spec.expressions or {}
        for state, emotion in profile.items():
            assert state in {"focus", "done", "fail", "glance-back", "needs-you"}, (
                f"{name}: unknown expression state {state!r}"
            )
            assert emotion in VALID_EMOTIONS, f"{name}: EXPRESSIONS[{state!r}]={emotion!r} not a real emotion"


def test_expr_for_consumes_profile_and_defaults_sanely():
    """The work loop's face selector returns the tool's declared face when present, and a valid
    fallback emotion when not (so tools with no profile still react, never break).

    `shell` is a tool WITH a declared profile; the `web_search` built-in has none, so it exercises the
    default, which must still be a real emotion."""
    assert _expr_for("shell", "fail", "sad") == "embarrassed"
    assert _expr_for("shell", "focus", "thinking") == "determined"
    fallback = _expr_for("web_search", "fail", "sad")
    assert fallback in VALID_EMOTIONS


def test_generic_voice_is_in_character():
    """Runtime MCP/plugin tools with no module voice fall back to _GENERIC_VOICE — it must be
    complete and in character (never a raw name, never a forbidden claim)."""
    assert set(_GENERIC_VOICE) >= {"before", "heartbeat", "after", "fail"}
    assert not stream._FORBIDDEN_RE.search(_GENERIC_VOICE["fail"])
    for tok in _TECHNICAL_TOKENS:
        assert tok not in _GENERIC_VOICE["fail"]


def test_mcp_find_complete_line_is_outcome_neutral():
    """mcp_find is INTERACTIVE, so the loop takes EVERY non-empty return as ok=True and narrates
    COMPLETE over it — including the honest non-connections ("couldn't find a server", "I held off",
    "needed X and didn't get it"). The line must therefore never claim a connection happened; a
    "Got it connected" here was a false promise on those turns.

    An audit found this latent on the default reasoning model, which suppresses canned narration —
    it only became audible after a switch to a model that speaks the canned lines."""
    from kotoba.tools.action import mcp_find

    low = mcp_find.COMPLETE.lower()
    for claim in ("connected", "conectad", "mine now", "all set", "got it working"):
        assert claim not in low, f"mcp_find COMPLETE claims an outcome it can't know: {mcp_find.COMPLETE!r}"
    assert mcp_find.COMPLETE.strip(), "COMPLETE must stay non-empty (four-state voice contract)"


# --- ForbiddenPhraseFilter precision ---------------------------------------------------------------

_GENUINE_WEB_CLAIMS = [
    "I can't access that page.",
    "I'm unable to browse the internet.",
    "I cannot open that link.",
    "I couldn't reach that website.",
    "I don't have access to the internet.",
    "No puedo acceder a esa página.",
    "No puedo abrir el enlace.",
]
_ORDINARY_FAILURES = [
    "I couldn't save that just now.",
    "I couldn't write that file.",
    "I couldn't find anything matching that.",
    "I couldn't set that reminder.",
    "I couldn't get that one connected.",
    "Hmm, that file wouldn't open for me.",
    "I couldn't put the report together.",
]


def test_filter_still_catches_genuine_web_inability_claims():
    """The half the filter exists for: the model wrongly claiming it cannot reach the web, in either
    of the two languages she speaks."""
    for claim in _GENUINE_WEB_CLAIMS:
        assert stream._FORBIDDEN_RE.search(claim), f"filter no longer catches web claim: {claim!r}"


def test_filter_does_not_mangle_ordinary_tool_failures():
    """The other half, and the regression this pins: the reach-verb used to be optional, so any
    "I couldn't <verb>" was rewritten into a random web quip — a failed memory save came out as
    something like "that page slammed the door".

    The point of the filter is that she keeps talking IN CHARACTER when a tool fails, so each of
    these lines must survive the streaming filter end to end, byte for byte."""
    for line in _ORDINARY_FAILURES:
        assert not stream._FORBIDDEN_RE.search(line), f"filter false-positives on tool failure: {line!r}"
        f = stream.ForbiddenPhraseFilter()
        out = f.feed(line) + f.flush()
        assert out.strip() == line.strip(), f"streaming filter altered an in-character line: {line!r} -> {out!r}"


# --- typed-shorthand scrub -------------------------------------------------------------------------

def _scrub(text: str) -> str:
    f = stream.ForbiddenPhraseFilter()
    return (f.feed(text) + f.flush()).strip()


def test_typed_shorthand_is_stripped_from_speech():
    """She SPEAKS, and typed shorthand does not survive being read aloud — "lol" comes out of TTS as
    "el-oh-el", which breaks the illusion. It is removed before the text is spoken."""
    assert _scrub("Wait, you got the job? lol that is amazing!") == "Wait, you got the job? that is amazing!"
    assert "omg" not in _scrub("omg that is so cool").lower()
    assert "btw" not in _scrub("btw I missed you").lower()
    assert "idk" not in _scrub("idk honestly").lower()


def test_real_laughter_and_words_survive_the_scrub():
    """Two things the scrub must leave alone: written laughter, which TTS actually vocalizes and so
    is not shorthand at all; and ordinary words that merely CONTAIN the shorthand letters — the
    scrub is word-boundary safe, so "Carol" keeps its "lol" and "idkstreet" its "idk"."""
    assert "haha" in _scrub("haha that tickles")
    assert _scrub("I love Carol and her old dog") == "I love Carol and her old dog"
    assert _scrub("The idkstreet was busy") == "The idkstreet was busy"


def test_emojis_stripped_but_accents_and_punctuation_kept():
    """She SPEAKS, so no emoji may reach TTS — the literals fed below are the real thing. What must
    SURVIVE is Spanish accents and inverted punctuation, plus the em-dash and the ellipsis.

    Found live: a memory_recall reply came back with an emoji still in it."""
    assert _scrub("Your cat is Mochi! 🐾 How is Mochi?") == "Your cat is Mochi! How is Mochi?"
    assert "✨" not in _scrub("Yay ✨ amazing ⭐")
    assert _scrub("I am happy 😸💕") == "I am happy"
    assert _scrub("¿Cómo estás? ¡Qué día, niña!") == "¿Cómo estás? ¡Qué día, niña!"
    assert _scrub("Mmm... pensá — un momento, café y piña") == "Mmm... pensá — un momento, café y piña"


def test_markdown_leaks_stripped_from_speech():
    """Bold markers, bullets and headings the model sometimes emits must not reach TTS, while normal
    dashes, em-dashes and subtraction survive mid-sentence.

    Found live: a spoken skill list came back reading "**Frontend design**"."""
    assert "**" not in _scrub("my list: **Frontend design**: I build sites")
    assert _scrub("- Frontend design") == "Frontend design"
    assert _scrub("## My skills") == "My skills"
    assert _scrub("Well — maybe, but 5 - 3 is two") == "Well — maybe, but 5 - 3 is two"


def test_tone_tilde_is_stripped_from_speech():
    """The "~" tone marker, which some module-default ANNOUNCE lines use, must never reach TTS."""
    assert _scrub("Let me run that real quick~") == "Let me run that real quick"
    assert _scrub("Saved it~ all good") == "Saved it all good"


def test_no_authored_voice_line_contains_typed_shorthand():
    """None of our authored ANNOUNCE/HEARTBEAT/COMPLETE/FAIL lines may contain shorthand to begin with."""
    for name in _function_tool_names():
        v = _voice_for(name, {})
        blob = " ".join([v.get("before", ""), v.get("after", ""), v.get("fail", "")] + list(v.get("heartbeat", [])))
        assert not stream._SLANG_RE.search(blob), f"{name}: authored voice line contains typed shorthand"


def test_urls_stripped_from_speech():
    """A voice cannot read a link, so bare URLs are removed before speech.

    Stripping happens in the UrlFilter stage, ahead of the sentence-splitting ForbiddenPhraseFilter,
    so a url's dots can never be mistaken for sentence ends. Both stages run here, as in production."""
    import kotoba.core.stream as s
    def run(t):
        uf = s.UrlFilter(); pf = s.ForbiddenPhraseFilter()
        return (pf.feed(uf.feed(t)) + pf.feed(uf.flush()) + pf.flush()).strip()
    assert "http" not in run("I found it ((https://chatforest.com/guides/best?utm_source=openai)) here.")
    assert run("Check https://example.com/x for details.").replace("  ", " ") == "Check for details."
    assert run("It is normal (and good), ¿sí?") == "It is normal (and good), ¿sí?"
