"""The prompt may only claim a capability the session actually offers.

`build_system_prompt` asserted a working terminal and web access unconditionally, while the tool registry
was already dropping those tools — a toolset switched off in settings, or a sandbox that isn't up. In the
same breath it forbade the true sentence ("I can't access that" was on the forbidden list), so the model
was left with two moves and both banned: say what is true, or invent an outcome — this project's most-
tracked failure, saying something untrue instead of "I can't". The capability blocks are now written from
the tools the turn will really be offered: the assertion disappears when the tool does, and the honest
sentence becomes explicitly allowed. An unset toolset assumes the full one and keeps the frozen voice
snapshot byte-identical, so the path production actually takes cannot drift from the recorded one."""
from __future__ import annotations

from pathlib import Path

import pytest

from kotoba.core import app_settings
from kotoba.soul import prompt
from kotoba.tools import registry

SOUL = {"name": "Kotoba", "language": "auto",
        "personality": "Warm, playful, a little catlike.",
        "address_style": "Call the user by name once you know it.",
        "emotional_rules": "Delight on good news. Soften when they are hurting.",
        "quirks": "A soft 'hmph' when teased."}

TERMINAL_CLAIM = "You DO have a terminal in this call"
NO_TERMINAL_BAN = 'never say "I don\'t have a terminal" (you do)'
WEB_CLAIM = "You have working web access"
WEB_BAN = "Those phrases are forbidden"
MISSING_HEAD = "What you genuinely CANNOT do right now"


@pytest.fixture(autouse=True)
def _pinned(monkeypatch, tmp_path):
    """The effort decides which of two prompts is built, so every test here names the one it means.

    Left to the environment these read the stock `low`, took the lean path, and asserted the presence
    of spans only the scaffolded one carries — three failures that said nothing about the subject."""
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    for var in ("KOTOBA_REASONING_EFFORT", "KOTOBA_MODEL"):
        monkeypatch.delenv(var, raising=False)


def _build(available, *, reasoning: bool = False, **kw):
    app_settings.set_runtime("reasoning_effort", "low" if reasoning else "")
    return prompt.build_system_prompt(SOUL, "- Name: Jordan", ["likes matcha"], session_id="s",
                                      available_tools=available, **kw)


def _companion_names():
    registry.discover()
    return {t.get("name") or t.get("type") for t in registry.schemas_for("companion")}


# --- the assertion follows the tool ----------------------------------------------------------------

@pytest.mark.parametrize("reasoning", [False, True])
def test_the_terminal_is_asserted_only_when_shell_is_offered(reasoning):
    full = _companion_names()
    assert {"shell", "execute_code"} <= full, "the fixture assumes both are normally offered"
    assert TERMINAL_CLAIM in _build(full, reasoning=reasoning)

    without = full - {"shell", "execute_code"}
    text = _build(without, reasoning=reasoning)
    assert TERMINAL_CLAIM not in text
    assert NO_TERMINAL_BAN not in text
    assert "**shell** and **execute_code** are both" in text


@pytest.mark.parametrize("reasoning", [False, True])
def test_web_access_is_asserted_only_when_web_search_is_offered(reasoning):
    full = _companion_names()
    assert WEB_CLAIM in _build(full, reasoning=reasoning)

    text = _build(full - {"web_search", "web_extract"}, reasoning=reasoning)
    assert WEB_CLAIM not in text
    assert WEB_BAN not in text, "the ban on saying 'I can't access that' cannot outlive the access"
    assert "web search is switched off" in text


def test_the_bans_ride_with_the_scaffolding_and_the_claim_does_not():
    """Both bans live inside _SCAFFOLD spans, so a reasoning model is told it HAS the capability and
    never told which sentences are forbidden. That is the design — it is hand-holding, and the honest
    sentence is not one it needs banning from — but it means the ban cannot be used as a proxy for the
    claim, which is how the two tests above came to assert a path they were not building."""
    full = _companion_names()
    assert NO_TERMINAL_BAN in _build(full) and WEB_BAN in _build(full)
    lean = _build(full, reasoning=True)
    assert NO_TERMINAL_BAN not in lean and WEB_BAN not in lean
    assert TERMINAL_CLAIM in lean and WEB_CLAIM in lean


def test_a_dead_sandbox_removes_the_terminal_claim_and_leaves_the_web_one():
    """The two capabilities are gated independently: KOTOBA_SANDBOX=docker with no daemon takes shell
    and execute_code away through check(), and touches nothing about web access."""
    full = _companion_names()
    text = _build(full - {"shell", "execute_code"})
    assert TERMINAL_CLAIM not in text
    assert WEB_CLAIM in text


# --- and the true sentence becomes sayable ---------------------------------------------------------

@pytest.mark.parametrize("gone,phrase", [
    (("shell", "execute_code"), "I can't run commands right now"),
    (("web_search", "web_extract"), "my web access is off"),
])
def test_the_honest_sentence_is_explicitly_permitted(gone, phrase):
    text = _build(_companion_names() - set(gone))
    assert phrase in text, "a prompt that removes the claim but still bans 'I can't' is still a trap"


def test_missing_capabilities_are_named_and_inventing_one_is_banned():
    text = _build(_companion_names() - {"shell", "execute_code", "cronjob"})
    assert MISSING_HEAD in text
    for line in ("- run a terminal command", "- run a Python snippet", "- set a reminder"):
        assert line in text
    assert "NEVER invent the result you would have got" in text
    assert "OVERRIDES" in text, "it must beat the general bans above it, not sit beside them"


def test_nothing_missing_says_nothing():
    assert MISSING_HEAD not in _build(_companion_names())


# --- the default and the real call both stay on the recorded prompt --------------------------------

def test_the_full_toolset_prompt_is_byte_identical_to_the_frozen_voice_snapshot(monkeypatch):
    """What the snapshot records must equal what a caller on that path really sends — else the frozen
    test would be guarding a path nobody takes. The path it records is the SCAFFOLDED one, which is
    every non-reasoning model, so the effort is named here rather than inherited from the default."""
    monkeypatch.setattr(prompt, "_now_strings",
                        lambda: ("2026-01-02 03:04 UTC", "2026-01-02 04:04 (CET UTC+01:00)"))
    app_settings.set_runtime("expressive", True)
    app_settings.set_runtime("voice_mode", "local")
    app_settings.set_runtime("tts_engine", "expressive")
    app_settings.set_runtime("reasoning_effort", "")
    snapshot = (Path(__file__).parent / "snapshots" / "voice_tags_on.txt").read_text(encoding="utf-8")
    built = prompt.build_system_prompt(
        SOUL, "- Name: Jordan", ["likes matcha"], session_id="snapshot-session",
        skills=["research — offer-first web research"],
        connected_mcp=[{"name": "notion", "tools": ["notion__search"]}],
        pending_mcp=[{"name": "linear", "kind": "oauth"}],
        available_tools=_companion_names(),
    )
    assert built == snapshot


def test_omitting_the_argument_assumes_everything():
    assert _build(None) == _build(_companion_names())


# --- the wiring: the set comes from the same gate the loop reads ------------------------------------

def test_load_context_passes_the_offered_tools(monkeypatch, tmp_path):
    """A prompt that consults the toolset only if someone remembers to pass it is not a fix."""
    import asyncio
    import types

    from kotoba.core import context
    from kotoba.models.schemas import ChatRequest

    seen = {}

    def fake_build(*a, **kw):
        seen.update(kw)
        return "PROMPT"

    monkeypatch.setattr(context, "build_system_prompt", fake_build)
    registry.discover()
    registry.set_toolset_enabled("terminal", False)
    try:
        db = types.SimpleNamespace(
            fetch_soul_config=lambda: _async({"name": "K"}),
            fetch_user_profile_as_markdown=lambda: _async("- Name: Jordan"),
            fetch_recent_turns=lambda *a, **k: _async([]),
        )
        req = ChatRequest(messages=[{"role": "user", "content": "hola"}], session_id="s")
        asyncio.run(context.load_context(req, db, "s"))
    finally:
        registry.set_toolset_enabled("terminal", True)

    available = seen.get("available_tools")
    assert available is not None, "load_context must tell the prompt what this turn really offers"
    assert "shell" not in available
    assert "clarify" in available


async def _async(value):
    return value
