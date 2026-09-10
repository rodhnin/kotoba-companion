"""Everything she is handed is written in English, whoever installed her.

The prompt was authored bilingually and the Spanish leaked into the instructions themselves: example
phrasings she was told to recognize, and sentences she was told to say back. To a reader who speaks no
Spanish that is noise at best, and a nudge to answer in a language nobody asked for at worst.

A byte-for-byte recording cannot carry the rule, because re-recording one blesses whatever the code now
builds, Spanish included. So this asserts the property directly, across every gate that changes the
text — both registers, both engines, the reasoning and the scaffolded build, a guest toolset and a full
one — and across the skill bodies, which reach her the same way.
"""
from __future__ import annotations

import re

import pytest

from kotoba.core import app_settings, skill_docs
from kotoba.discord import authority
from kotoba.soul import loader, prompt
from kotoba.tools import registry

# Spanish spelling that no English sentence produces.
_LETTERS = re.compile(r"[¿¡ñÑáéíóúÁÉÍÓÚ]|(?<![A-Za-z])ü")

# Every word English also uses is left out — no, con, sin, todo, a — since the text being scanned is
# English prose and a finder that cried wolf on it would be switched off within the week.
_LEXICON = frozenset("""
la los las una unos unas del que por para pero muy es un en de se su sus lo al el y ni ya esto eso
este esta esa ese estos estas esos esas cada uno cuando donde porque hay ser estar tiene tienen hace
hacer igual mismo misma fondo profundidad panorama completo completa corriente grados horas cosa
cosas vez veces otro otra otros otras cual cuales siempre nunca nada algo alguien desde hasta entre
sobre mientras aunque entonces ahora asi hola como hoy gracias favor puedo puedes quiero dime dame
hazme estoy eres soy vamos tambien segun alli aqui
""".split())

_WORD = re.compile(r"[A-Za-zÀ-ÿ]+")

_SOUL = {"name": "Kotoba", "language": "auto",
         "personality": "Warm, playful, a little catlike.",
         "address_style": "Call the user by name once you know it.",
         "emotional_rules": "Delight on good news.",
         "quirks": "A soft 'hmph' when teased."}


def spanish_in(text: str) -> list[str]:
    """The offending lines, so a failure names what to rewrite instead of only that something is wrong.

    A line is Spanish when it is spelled that way, or when three of these words share it — three in a
    row, or three different ones anywhere on it. One is a name or a loanword; three is grammar."""
    out = []
    for line in text.splitlines():
        if _LETTERS.search(line):
            out.append(line)
            continue
        words = [w.lower() for w in _WORD.findall(line)]
        run = longest = 0
        for w in words:
            run = run + 1 if w in _LEXICON else 0
            longest = max(longest, run)
        if longest >= 3 or len({w for w in words if w in _LEXICON}) >= 3:
            out.append(line)
    return out


@pytest.fixture(autouse=True)
def _pinned(monkeypatch, tmp_path):
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    for var in ("KOTOBA_EXPRESSIVE", "KOTOBA_VOICE_MODE", "KOTOBA_TTS_ENGINE", "KOTOBA_TZ",
                "KOTOBA_REASONING_EFFORT"):
        monkeypatch.delenv(var, raising=False)


def _full():
    registry.discover()
    return {t.get("name") or t.get("type") for t in registry.schemas_for("companion")}


def _guest():
    return _full() - set(authority.excluded_tools(None))


def _build(monkeypatch, *, engine, register, tools, reasons):
    monkeypatch.setattr(prompt, "_reasons", lambda: reasons)
    app_settings.set_runtime("expressive", True)
    app_settings.set_runtime("voice_mode", "local")
    app_settings.set_runtime("tts_engine", engine)
    return prompt.build_system_prompt(
        _SOUL, "- Name: Jordan", ["likes matcha"], session_id="s",
        skills=skill_docs.skill_titles(),
        connected_mcp=[{"name": "notion", "tools": ["notion__search"]}],
        pending_mcp=[{"name": "linear", "kind": "oauth"}],
        register=register, available_tools=tools)


@pytest.mark.parametrize("engine", ["expressive", "fast"])
@pytest.mark.parametrize("register", ["voice", "text"])
@pytest.mark.parametrize("who", ["full", "guest"])
@pytest.mark.parametrize("reasons", [True, False], ids=["reasoning", "scaffolded"])
def test_the_assembled_prompt_is_english(monkeypatch, engine, register, who, reasons):
    tools = _full() if who == "full" else _guest()
    built = _build(monkeypatch, engine=engine, register=register, tools=tools, reasons=reasons)
    assert spanish_in(built) == []


def test_the_shipped_personality_is_english():
    """Her sections are pasted into the prompt verbatim, so the file is as much prompt as the code is."""
    soul = loader.load_soul_from_file(loader.DEFAULT_SOUL_PATH)
    for field, value in soul.items():
        assert spanish_in(str(value or "")) == [], field


@pytest.mark.parametrize("name", [s["name"] for s in skill_docs.list_skills()])
def test_every_skill_body_is_english(name):
    body = skill_docs.view_skill(name)
    assert body, f"{name} could not be read back"
    assert spanish_in(body) == []


def test_there_are_skills_to_check():
    assert len(skill_docs.list_skills()) >= 2


@pytest.mark.parametrize("line", [
    'say "veintiún años", never "treinta y uno grados"',
    '("compárame A, B y C", "investiga cada uno de estos")',
    "la corriente de colector es igual a beta",
    "la corriente de colector es igual a beta multiplicado por la corriente de base",
    'explicitly in depth on a broad topic ("a fondo", "en profundidad", "el panorama completo de X")',
    "Hola, como estas hoy",
])
def test_the_detector_can_actually_see_spanish(line):
    """An always-empty finder would let every assertion above pass while the prompt filled up again.
    Each line here is one the prompt really carried."""
    assert spanish_in(line) == [line]


@pytest.mark.parametrize("line", [
    "",
    "The collector current equals beta multiplied by the base current.",
    "- **todo** — YOUR OWN working notebook, so you don't lose the thread of a job.",
    'They get read out loud literally ("el-oh-el") and sound broken.',
])
def test_the_detector_leaves_english_alone(line):
    """The counterweight: a finder that flagged prose would be silenced the first time it cried wolf."""
    assert spanish_in(line) == []
