"""The spoken prompt is FROZEN. `build_system_prompt` gained a `register` axis so a terminal gets markdown,
fences, URLs and digits, and the promise is that the voice register does not move by one byte — this
test IS that promise. A failure means either the voice prompt changed on purpose (re-record it and say
so) or text work leaked into voice. `register` is NOT `channel`: a typed web turn can carry
`register="voice"` and still be spoken aloud. Everything the builder reads that isn't an argument is
pinned too — the settings file, the flags behind audio_tags_enabled, and the wall clock. The effort is
pinned rather than left unset because it now defaults to 'low', and this snapshot is the NON-reasoning
build; the reasoning one is pinned separately against this minus its scaffold spans."""
from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from kotoba.core import app_settings
from kotoba.soul import prompt

SNAPSHOTS = Path(__file__).parent / "snapshots"
FROZEN_NOW = ("2026-01-02 03:04 UTC", "2026-01-02 04:04 (CET UTC+01:00)")

SOUL = {"name": "Kotoba", "language": "auto",
        "personality": "Warm, playful, a little catlike.",
        "address_style": "Call the user by name once you know it.",
        "emotional_rules": "Delight on good news. Soften when they are hurting.",
        "quirks": "A soft 'hmph' when teased."}
KW = dict(session_id="snapshot-session", skills=["research — offer-first web research"],
          connected_mcp=[{"name": "notion", "tools": ["notion__search"]}],
          pending_mcp=[{"name": "linear", "kind": "oauth"}])

CASES = [("tags_on", "expressive"), ("tags_off", "fast")]


@pytest.fixture(autouse=True)
def _pinned(monkeypatch, tmp_path):
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    for var in ("KOTOBA_EXPRESSIVE", "KOTOBA_VOICE_MODE", "KOTOBA_TTS_ENGINE", "KOTOBA_TZ",
                "KOTOBA_REASONING_EFFORT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(prompt, "_now_strings", lambda: FROZEN_NOW)


def _build(engine: str, **kwargs) -> str:
    app_settings.set_runtime("reasoning_effort", "")
    app_settings.set_runtime("expressive", True)
    app_settings.set_runtime("voice_mode", "local")
    app_settings.set_runtime("tts_engine", engine)
    return prompt.build_system_prompt(SOUL, "- Name: Jordan", ["likes matcha"], **KW, **kwargs)


@pytest.mark.parametrize("label,engine", CASES)
def test_the_default_call_is_byte_identical_to_the_recorded_voice_prompt(label, engine):
    """No `register` argument at all — what every existing caller does."""
    expected = (SNAPSHOTS / f"voice_{label}.txt").read_text(encoding="utf-8")
    actual = _build(engine)
    if actual != expected:
        diff = "\n".join(difflib.unified_diff(expected.splitlines(), actual.splitlines(),
                                              "snapshot", "built", lineterm=""))
        pytest.fail(f"the spoken prompt ({label}) changed:\n{diff}")


@pytest.mark.parametrize("label,engine", CASES)
def test_asking_for_voice_explicitly_equals_asking_for_nothing(label, engine):
    assert _build(engine, register="voice") == _build(engine)


def test_the_written_register_actually_differs():
    """Guards the opposite mistake: a parameter that is accepted and then ignored would let every other
    test here pass while the terminal still got the spoken register."""
    spoken, written = _build("expressive"), _build("expressive", register="text")
    assert written != spoken
    assert "NEVER SPEAK CODE OR FILE CONTENTS OUT LOUD" not in written
    assert "NEVER READ A FILE NAME OR PATH OUT LOUD" not in written
    assert "You are WRITING, not speaking" in written


def test_the_written_register_still_asks_for_tags():
    """They are never printed — they are how her face is chosen. Drop them and every terminal turn pays
    for a second LLM call to infer the emotion instead."""
    written = _build("expressive", register="text")
    assert "[audio tags]" in written or "audio tag" in written.lower()


def test_the_written_register_without_tags_never_orders_them_kept():
    """With the tag channel off, the closing note says "Do not write emotion tags or brackets" — and
    _TEXT_RULES still said "Keep the [audio tags]" in the same prompt: two orders, mutually exclusive,
    in every fast-engine CLI session. The tags clause now rides the expressive gate like every other
    tag instruction, and the face falls to the emotion-extraction pass the plain note describes."""
    written = _flat(_build("fast", register="text"))
    assert "Keep the [audio tags]" not in written
    assert "Do not write emotion tags or brackets" in written
    assert "Everything else is yours to shape" in written


def test_the_written_register_bans_emojis():
    """_SPEECH_ONLY_RULES bans them twice; _TEXT_RULES banned nothing, and the model's own prose was the
    one place that could still put an arbitrary emoji on the glass. The ban is written-only: the voice
    prompt already carries its own, and its bytes are frozen above."""
    written = _build("expressive", register="text")
    assert "NEVER use emojis" in written


def test_the_written_register_says_a_finished_job_is_over():
    """The __work_done__ sentinel turn announced the START of a job that had just ENDED: the prompt
    orders the start-announcement twice against one closing line. The written register carries the
    counterweight; the voice bytes are frozen above and must not carry it."""
    written = _build("expressive", register="text")
    spoken = _build("expressive")
    assert "[BACKGROUND WORK just finished/failed]" in written
    assert "already OVER" in written
    assert "[BACKGROUND WORK just finished/failed]" not in spoken


def _flat(text: str) -> str:
    """One line, so an assertion is about the words and not about where they happen to wrap."""
    return " ".join(text.split())


@pytest.mark.parametrize("kwargs", [{}, {"register": "text"}], ids=["voice", "text"])
def test_the_prompt_never_orders_her_to_narrate_while_a_tool_runs(kwargs):
    """The clause "narrate naturally while a tool runs, never go silent" is inherited from the original
    design and survived into General rules, and it asks for something the transport cannot do in either
    register: the model
    emits text BEFORE a function call and AFTER the result, never during. The same prompt says so ~600
    lines earlier, so what the clause actually bought was pressure to over-announce — the failure the
    accurate rule spells out a ban on ("ONE line: not a plan, not a list of steps"). Deleted; the
    duplicate-call half of the same sentence is real and stays."""
    built = _flat(_build("expressive", **kwargs))
    assert "narrate naturally while a tool runs" not in built
    assert "never go silent" not in built
    assert "NEVER call the same tool twice with the same input" in built


@pytest.mark.parametrize("label,engine", CASES)
def test_the_rule_that_is_true_is_the_one_that_survives(label, engine):
    """The counterweight: both spoken variants must still carry the accurate version, or deleting the
    contradiction would have left her with no instruction to speak before a tool at all."""
    spoken = _flat(_build(engine))
    assert "While the tool runs they hear nothing else" in spoken
    assert "so that line has to come BEFORE the call, never after it" in spoken


def test_the_snapshots_are_not_accidentally_empty():
    for label, _engine in CASES:
        text = (SNAPSHOTS / f"voice_{label}.txt").read_text(encoding="utf-8")
        assert len(text) > 20_000, f"{label} looks truncated ({len(text)} chars)"
        assert text.startswith("# Who you are")
