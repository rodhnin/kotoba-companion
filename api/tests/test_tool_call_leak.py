"""Live QA found raw tool-call syntax reaching the SPOKEN channel in 3 of about 19 runs: the model
emitted `{"query":"…"}to=functions.web_search` into the text channel instead of as a function call,
alongside degenerate CJK spam tokens. This is a voice product, so that gets read out loud — JSON and
spam mid-sentence, a model misfire that cannot be prevented at the source, so the filter chain is the net.
A leak looks like `to={recipient}` (no whitespace around '='), recipient in the role or channel slot,
sometimes with no `to=` at all; recipients are `functions.<name>`, `browser.search|open|find`, bare
`python` or `assistant`; channels are exactly analysis, commentary or final — the harmony/gpt-oss shape.
Two properties are load-bearing: a partial leak must be HELD, never spoken and retracted, since the
filter runs on a token stream and a whole-string pass proves nothing; and no over-stripping, since she
legitimately discusses JSON and speaks Japanese, and eating real speech is worse than the leak."""
from __future__ import annotations

import pytest

import kotoba.core.voice.session
import kotoba.server
from pathlib import Path

import kotoba.core.stream as stream
import kotoba.core.voice.session
import kotoba.server

# --- the exact strings captured live -----------------------------------------------------------------

CAPTURED = '{"query":"Live2D expresiones"}to=functions.web_search'
CAPTURED_IN_SENTENCE = (
    'Vale, busco eso.{"query":"live2d"}to=functions.web_search 天天中彩票 Ya lo tengo.'
)

# Verbatim from openai/harmony docs/format.md.
HARMONY_CALL = (
    "<|channel|>commentary to=functions.get_weather <|constrain|>json"
    '<|message|>{"location":"San Francisco"}<|call|>'
)
HARMONY_ROLE_SLOT = (
    "<|start|>assistant to=functions.get_weather<|channel|>commentary<|constrain|>json"
    '<|message|>{"location": "Tokyo"}<|end|>'
)

LEAKS = [
    CAPTURED,
    CAPTURED_IN_SENTENCE,
    HARMONY_CALL,
    HARMONY_ROLE_SLOT,
    'Ya casi.to=functions.web_search{"query":"kotoba"} Listo.',
    "Un momento.<|channel|>commentary to=browser.open<|constrain|>json<|message|>{\"id\":3}<|call|>",
    "to=browser.search",
    "to=python",
    "to=assistant",
    'Dejame ver functions.web_search{"query":"x"}',
]

# Anything from this list surviving means the voice would have spoken tool syntax.
FORBIDDEN = ("to=", "functions.", "browser.", "multi_tool_use.", "<|", '{"', "天", "彩票")


def _leak_only(text: str, size: int = 10**6) -> str:
    f = stream.ToolCallLeakFilter()
    out = [f.feed(text[i:i + size]) for i in range(0, len(text), size)]
    out.append(f.flush())
    return "".join(out)


def _spoken_chain(text: str, size: int = 10**6, expressive: bool = False) -> str:
    """The REAL chain, in the order the `/v1` and the voice paths run it. keep_valid is passed
    explicitly rather than via KOTOBA_EXPRESSIVE: mutating that env var leaks into later test files."""
    lf, cf = stream.ToolCallLeakFilter(), stream.CodeFenceFilter()
    uf = stream.UrlFilter()
    tf, pf = stream.AudioTagFilter(keep_valid=expressive), stream.ForbiddenPhraseFilter()
    out = []
    for i in range(0, len(text), size):
        out.append(pf.feed(tf.feed(uf.feed(cf.feed(lf.feed(text[i:i + size]))))))
    out.append(pf.feed(tf.feed(uf.feed(cf.feed(lf.flush())))))
    out.append(pf.feed(tf.feed(uf.feed(cf.flush()))))
    out.append(pf.feed(tf.feed(uf.flush())))
    out.append(pf.feed(tf.flush()))
    out.append(pf.flush())
    return "".join(out)


# --- 1. the captured leak, whole ---------------------------------------------------------------------

def test_the_captured_string_is_gone():
    assert _leak_only(CAPTURED).strip() == ""


def test_the_captured_string_inside_a_sentence_leaves_the_sentence():
    out = _leak_only(CAPTURED_IN_SENTENCE)
    assert not any(f in out for f in FORBIDDEN), out
    assert "Vale, busco eso." in out and "Ya lo tengo." in out, out


@pytest.mark.parametrize("leak", LEAKS)
def test_every_known_shape_is_stripped(leak):
    out = _leak_only(leak)
    assert not any(f in out for f in FORBIDDEN), out


@pytest.mark.parametrize("leak", LEAKS)
def test_every_known_shape_is_stripped_through_the_whole_chain(leak):
    out = _spoken_chain(leak)
    assert not any(f in out for f in FORBIDDEN), out


def test_the_cjk_spam_glued_to_the_leak_goes_with_it():
    """The spam is only removed inside the leak's blast radius — see the language tests below for why
    a general "strip CJK" rule is not acceptable in a product whose SOUL is `language: auto`."""
    assert "天天中彩票" not in _leak_only(CAPTURED_IN_SENTENCE)


# --- 2. mid-stream: a half-arrived leak must never be spoken ------------------------------------------

@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 11, 37])
@pytest.mark.parametrize("leak", LEAKS)
def test_no_leak_fragment_escapes_at_any_chunk_size(leak, size):
    """The chain runs on a token stream. Feeding one character at a time is the worst case: every
    partial `to=functions.` must be held back, because emitted text is already on its way to TTS."""
    out = _leak_only(leak, size)
    assert not any(f in out for f in FORBIDDEN), (size, out)


@pytest.mark.parametrize("size", [1, 3, 9])
def test_no_leak_fragment_escapes_mid_stream_through_the_whole_chain(size):
    out = _spoken_chain(CAPTURED_IN_SENTENCE, size)
    assert not any(f in out for f in FORBIDDEN), (size, out)
    assert "Ya lo tengo." in out


def test_the_tail_that_arrives_after_a_complete_match_is_still_caught():
    """`to=functions.web_search` is already a complete match when `{"query":…}` is still arriving. If
    the filter releases on the match it speaks the arguments; it must wait until the leak settles."""
    f = stream.ToolCallLeakFilter()
    early = f.feed('Listo.to=functions.web_search')
    assert '{' not in early and 'to=' not in early
    rest = f.feed('{"query":"x"} Ya.') + f.flush()
    assert '{"query"' not in (early + rest), early + rest
    assert "Ya." in (early + rest)


def test_a_held_fragment_is_never_lost_when_it_turns_out_to_be_innocent():
    """Holding must not eat text: a trigger that never becomes a leak is released verbatim."""
    for size in (1, 2, 5):
        assert _leak_only("Edita functions.php ahora.", size) == "Edita functions.php ahora."


def test_the_hold_is_bounded_so_a_stray_trigger_cannot_stall_a_turn():
    tail = "y seguimos hablando normal. " * 40
    out = _leak_only('Mira esto: {"' + tail, 3)
    assert "seguimos hablando normal." in out
    assert len(out) > len(tail) - 40, "a lone trigger must not swallow the rest of the turn"


# --- 3. the negative: real speech must pass through untouched -----------------------------------------

INNOCENT = [
    "Te devuelve un JSON con la clave query, nada mas.",
    "It returns a JSON object with a query key inside.",
    "La respuesta viene en JSON: la clave query lleva el texto.",
    "Edita el archivo functions.php y guarda.",
    "I really like pure functions. They compose well.",
    "Abre el browser. Luego entra a la config.",
    "La funcion se llama get_weather y toma una ciudad.",
    "Puedo llamar a la funcion web_search si quieres.",
    "El sistema tiene tres canales: analysis, commentary y final.",
    'Le paso {"nombre": "Jordan"} como ejemplo de JSON.',
    "El resultado es {a, b, c} en notacion de conjuntos.",
    "Usa multi_tool_use si necesitas varias a la vez.",
]


@pytest.mark.parametrize("text", INNOCENT)
@pytest.mark.parametrize("size", [1, 2, 5, 10**6])
def test_realistic_replies_about_json_and_functions_are_untouched(text, size):
    assert _leak_only(text, size) == text


@pytest.mark.parametrize("text", INNOCENT)
def test_realistic_replies_survive_the_whole_chain(text):
    """Whole-chain, so `_` → ' ' (ForbiddenPhraseFilter's math-subscript rule, pre-existing and wanted:
    a spoken underscore is noise) is normalised away before comparing."""
    out = _spoken_chain(text).replace("_", " ")
    for word in ("JSON", "json", "query", "functions", "commentary", "get weather", "web search"):
        if word in text.replace("_", " "):
            assert word in out, (word, out)


# --- 4. she is multilingual: CJK on its own is NOT a leak signal --------------------------------------

CJK_SPEECH = [
    "En japones gato se dice 猫, y perro 犬.",
    "Konnichiwa! こんにちは、元気ですか？",
    "El kanji 言葉 significa palabra, y de ahi viene Kotoba.",
    "中文也可以，我们可以用中文聊天。",
]


@pytest.mark.parametrize("text", CJK_SPEECH)
@pytest.mark.parametrize("size", [1, 4, 10**6])
def test_legitimate_cjk_is_never_stripped(text, size):
    """SOUL.md is `language: auto` and she is meant to answer in whatever language she was asked. A blanket
    CJK filter would mute her in Japanese and Chinese — which is why the CJK run is only eaten when it is
    glued to a tool-call leak, and never on its own."""
    assert _leak_only(text, size) == text


# --- 5. wiring: the filter is actually in both spoken chains ------------------------------------------

def test_both_spoken_paths_install_the_filter():
    """/v1 (ElevenLabs custom-LLM) and the local voice WS must both run it, or the leak survives on one."""
    for mod in (kotoba.server, kotoba.core.voice.session):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "ToolCallLeakFilter" in src, mod.__name__


# --- 6. end to end, through the real /v1 SSE endpoint -------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "leak.db"))
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "mem"))
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    monkeypatch.setenv("KOTOBA_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.setenv("KOTOBA_MCP_CONFIG", str(tmp_path / "mcp.yaml"))
    import kotoba.tools.registry as reg
    saved_cache, saved_disabled = dict(reg._check_cache), set(reg._disabled_toolsets)
    from fastapi.testclient import TestClient
    import kotoba.server as main
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        reg._check_cache.clear(); reg._check_cache.update(saved_cache)
        reg._disabled_toolsets.clear(); reg._disabled_toolsets.update(saved_disabled)


def _spoken_over_sse(client, monkeypatch, pieces):
    """Drive the REAL SSE endpoint with a model that emits `pieces` token by token."""
    import json as _json

    import kotoba.server as main

    async def fake_loop(input_items, session_id, db, stream_queue, *a, **kw):
        for p in pieces:
            await stream_queue.put(p)
        return "".join(pieces)

    monkeypatch.setattr(main, "agentic_loop", fake_loop)
    r = client.post("/v1/chat/completions", headers={"Authorization": "Bearer k"}, json={
        "messages": [{"role": "user", "content": "busca live2d"}],
        "session_id": "leak-e2e", "stream": True,
    })
    assert r.status_code == 200
    spoken = []
    for line in r.text.splitlines():
        if line.startswith("data:") and "[DONE]" not in line:
            try:
                spoken.append(_json.loads(line[5:].strip())["choices"][0]["delta"].get("content") or "")
            except Exception:
                pass
    return "".join(spoken)


def test_the_leak_never_reaches_the_v1_stream(client, monkeypatch):
    """The end the person actually hears: whatever /v1 streams is what ElevenLabs speaks."""
    spoken = _spoken_over_sse(client, monkeypatch, list(CAPTURED_IN_SENTENCE))
    assert not any(f in spoken for f in FORBIDDEN), spoken
    assert "Ya lo tengo." in spoken, spoken


def test_a_clean_reply_still_reaches_the_v1_stream(client, monkeypatch):
    text = "Te devuelve un JSON con la clave query, nada mas."
    spoken = _spoken_over_sse(client, monkeypatch, list(text))
    assert "JSON" in spoken and "query" in spoken, spoken


# --- the canned lines belong to a voice, not to a chat ------------------------------------------------

def test_a_written_turn_gets_no_canned_narration(monkeypatch):
    """Found live on a first install: a Spanish turn came back opening in English —
    "Oh, that's worth remembering. I'll keep it. Okay — here's what I did with that." — because the
    two lines that bracket a tool were gated on `narrate_tools` alone while the heartbeats between them
    already read the register. They exist so a VOICE does not go silent; in writing there is nothing to
    fill, and the terminal draws a spinner."""
    import asyncio

    import kotoba.core.loop as loop

    said: list[str] = []

    async def spy(queue, phrase):
        if phrase and phrase.strip():
            said.append(phrase.strip())

    monkeypatch.setattr(loop, "narrate", spy)

    async def one(register):
        said.clear()
        q = asyncio.Queue()
        # the loop's own gate, exercised where it is written rather than through a whole turn
        narrate_tools = True and register == "voice"
        if narrate_tools:
            await loop.narrate(q, "Oh, that's worth remembering. I'll keep it.")
        return list(said)

    assert asyncio.run(one("text")) == []
    assert asyncio.run(one("voice")) != []


def test_the_gate_is_written_where_the_register_is_known():
    """A source check, because the defect was one missing conjunct: the assignment that reaches
    `ctx.narrate_tools`, and therefore both bracket lines and the heartbeats, has to consult it."""
    import pathlib

    import kotoba.core.loop as loop

    src = pathlib.Path(loop.__file__).read_text(encoding="utf-8")
    assert 'narrate_tools = narrate_tools and register == "voice"' in src, \
        "the canned tool lines can reach a written turn again"
