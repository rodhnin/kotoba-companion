"""Scaffolding a reasoning model does not need, and should not pay for on every turn.

The companion prompt pushed a model into using a tool it already had ("it works, use it", "ACTUALLY
call the tool", a list of forbidden inability phrases) — measurably not what makes a reasoning model
act: asked three times to RUN a command it searched instead, with "It works. Use it" sitting 3,925
characters ahead of "ACTUALLY call the tool". It is emitted only when the answering model does NOT
reason, gated on the same signal used elsewhere so a second one cannot drift from it — the build a
non-reasoning model gets, and what `reasoning_effort=off` asks for. The hand-holding is aimed, not
deleted: text tied to a measured failure or to the real toolset stays in both builds."""
from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from kotoba.core import app_settings
from kotoba.soul import prompt

SNAPSHOTS = Path(__file__).parent / "snapshots"
ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"
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

# The full-toolset spans, in the order they appear in the built prompt.
SCAFFOLDS = [
    ("_WEB_HEAD_ON_SCAFFOLD", prompt._WEB_HEAD_ON_SCAFFOLD),
    ("_WEB_SEARCH_SCAFFOLD", prompt._WEB_SEARCH_SCAFFOLD),
    ("_WEB_EXTRACT_SCAFFOLD_A", prompt._WEB_EXTRACT_SCAFFOLD_A),
    ("_WEB_EXTRACT_SCAFFOLD_B", prompt._WEB_EXTRACT_SCAFFOLD_B),
    ("_TODO_SCAFFOLD", prompt._TODO_SCAFFOLD),
    ("_HAVE_BOTH_SCAFFOLD", prompt._HAVE_BOTH_SCAFFOLD),
]

# What must survive in BOTH builds: the capability facts, the rules that were earned live, and the
# voice register. Each one is a thing a careless split of the constants above would swallow.
EARNED = [
    "You have working web access",                      # the FACT, gated on the real toolset
    "You DO have a terminal in this call",              # the same, for the runner
    "Its subject is the WORLD",                         # the scope fix, measured
    "immediately use **web_search**",                   # the web_extract fallback
    "⛔ HONESTY, AND IT CUTS BOTH WAYS",                 # the truth condition, both directions
    "Deciding not to call a tool is not an obstacle",
    "AN INSTRUCTION IS NOT A QUESTION",                 # the blunt block, with its live examples
    "**execute_code** is your calculator",              # the boundary, stated positively
    "NEVER name a button on that card",                 # the card promise, retired live
    "a step marked done before you did it is a false progress bar",   # todo, no schema twin
    "NEVER read it aloud",
    "SEARCHING FOR THE LATEST",                         # the single-sourced recency rule
]


@pytest.fixture(autouse=True)
def _pinned(monkeypatch, tmp_path):
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    for var in ("KOTOBA_EXPRESSIVE", "KOTOBA_VOICE_MODE", "KOTOBA_TTS_ENGINE", "KOTOBA_TZ",
                "KOTOBA_REASONING_EFFORT", "KOTOBA_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(prompt, "_now_strings", lambda: FROZEN_NOW)


def _build(engine: str = "expressive", *, reasoning: bool = False, **kwargs) -> str:
    app_settings.set_runtime("expressive", True)
    app_settings.set_runtime("voice_mode", "local")
    app_settings.set_runtime("tts_engine", engine)
    app_settings.set_runtime("reasoning_effort", "low" if reasoning else "")
    return prompt.build_system_prompt(SOUL, "- Name: Jordan", ["likes matcha"], **KW, **kwargs)


# ── the safety property: a fresh clone's prompt does not move ──────────────────────────────────────

@pytest.mark.parametrize("label,engine", CASES)
def test_a_clone_without_reasoning_gets_the_frozen_prompt_byte_for_byte(label, engine):
    """The whole reason this is a gate and not a deletion. Anyone on a non-reasoning model still takes
    this path, and the hand-holding is exactly what carries them."""
    expected = (SNAPSHOTS / f"voice_{label}.txt").read_text(encoding="utf-8")
    actual = _build(engine)
    if actual != expected:
        diff = "\n".join(difflib.unified_diff(expected.splitlines(), actual.splitlines(),
                                              "snapshot", "built", lineterm=""))
        pytest.fail(f"the scaffolded prompt ({label}) changed:\n{diff}")


def test_the_shipped_env_example_agrees_with_the_code_default():
    """A fresh clone takes the LEAN path: the effort defaults to 'low', so the stock reasoning model
    reasons and pays for none of the scaffolding above. `.env.example` is documentation, not the
    source of that default — it stays commented out, and it must not name a different value."""
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    live = [ln for ln in lines if ln.strip().startswith("KOTOBA_REASONING_EFFORT")]
    assert not live, f"KOTOBA_REASONING_EFFORT is no longer commented out: {live}"
    shown = [ln for ln in lines if ln.strip().startswith("# KOTOBA_REASONING_EFFORT")]
    assert shown, "the variable is not documented in .env.example any more"
    default = app_settings._RUNTIME_SPEC["reasoning_effort"][1]
    assert default == app_settings.DEFAULT_REASONING_EFFORT == "low"
    assert shown[0].strip() == f"# KOTOBA_REASONING_EFFORT={default}"
    app_settings.set_runtime("reasoning_effort", default)
    assert prompt._reasons() is True


# ── what the gate removes, and only that ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,span", SCAFFOLDS)
def test_each_scaffold_span_is_in_the_scaffolded_build_and_not_the_reasoning_one(name, span):
    assert span in _build(), f"{name} missing from the scaffolded build"
    assert span not in _build(reasoning=True), f"{name} survived into the reasoning build"


@pytest.mark.parametrize("phrase", EARNED)
@pytest.mark.parametrize("reasoning", [False, True], ids=["scaffolded", "reasoning"])
def test_the_earned_rules_survive_in_both_builds(phrase, reasoning):
    """Flattened, so this is an assertion about the words and not about where they happen to wrap."""
    assert phrase in " ".join(_build(reasoning=reasoning).split())


def test_the_reasoning_build_differs_by_the_scaffold_spans_and_nothing_else():
    """Re-inserting every span, in place, must reproduce the scaffolded build exactly — the only
    proof that no third thing was rewritten while the spans were being carved out."""
    scaffolded, lean = _build(), _build(reasoning=True)
    assert len(scaffolded) - len(lean) == sum(len(s) for _n, s in SCAFFOLDS) - len("  ")
    stripped = scaffolded
    for name, span in SCAFFOLDS:
        assert stripped.count(span) == 1, f"{name} is not a unique span"
        stripped = stripped.replace(span, "  " if name == "_TODO_SCAFFOLD" else "", 1)
    assert stripped == lean


def test_the_shell_only_session_is_gated_on_its_own_branch():
    """_doing_block picks one of four bodies; only two carry a scaffold span, and the shell-only one is
    the branch no full-toolset test would ever reach. The honest absence stays in the lean build, and so
    does the capability fact."""
    names = {"web_search", "shell", "memory_recall"}
    app_settings.set_runtime("reasoning_effort", "")
    scaffolded = prompt.build_system_prompt(SOUL, "", [], available_tools=names)
    app_settings.set_runtime("reasoning_effort", "low")
    lean = prompt.build_system_prompt(SOUL, "", [], available_tools=names)
    assert prompt._HAVE_SHELL_SCAFFOLD in scaffolded
    assert prompt._HAVE_SHELL_SCAFFOLD not in lean
    assert "You have NO Python runner in this session" in lean
    assert "You DO have a terminal in this call" in lean


def test_the_voice_register_is_not_on_this_axis():
    """Every rule that exists because she comes out of a speaker holds whatever the model is."""
    lean = _build(reasoning=True)
    assert prompt._VOICE_RULES_EXPRESSIVE in lean
    assert prompt._SPEECH_ONLY_RULES in lean
    assert prompt._SOUND_RULES_EXPRESSIVE in lean
    assert prompt._EMOTION_NOTE_EXPRESSIVE.format(emotions=prompt.EMOTIONS) in lean


def test_the_written_register_is_gated_the_same_way():
    """The CLI turn is answered by the same companion model, so it is the same question."""
    scaffolded, lean = _build(register="text"), _build(reasoning=True, register="text")
    assert len(scaffolded) > len(lean)
    assert prompt._TEXT_RULES in lean
    assert "You are WRITING, not speaking" in lean


# ── the signal, and only that signal ───────────────────────────────────────────────────────────────

def test_the_gate_follows_the_model_actually_answering_not_the_effort_setting():
    """A non-reasoning MODEL with an effort left behind in settings is still non-reasoning, and still
    needs its scaffolding. This is the distinction the narration gate exists to protect."""
    app_settings.set_runtime("model", "gpt-4o-mini")
    assert prompt._reasons() is False
    assert prompt._WEB_SEARCH_SCAFFOLD in _build(reasoning=True)


def test_the_gate_asks_the_companion_role():
    """This builder writes the companion turn and only that one. Asking any other role would gate the
    companion prompt on a model that never sees it."""
    seen: list = []
    import kotoba.core.llm as llm

    real = llm.is_reasoning_model
    llm.is_reasoning_model = lambda role=None: seen.append(role) or real(role=role)
    try:
        _build()
    finally:
        llm.is_reasoning_model = real
    assert seen and set(seen) == {"companion"}


def test_no_second_signal_was_invented():
    """Rejected in advance: an env var or setting of its own would drift from is_reasoning_model."""
    import inspect

    src = inspect.getsource(prompt)
    assert "is_reasoning_model" in src
    for invented in ("KOTOBA_SCAFFOLD", "KOTOBA_PROMPT_", "scaffold_prompt", "lean_prompt"):
        assert invented not in src


# ── the restatement claim, defended mechanically ───────────────────────────────────────────────────

def test_the_todo_span_is_carried_by_the_tools_own_schema():
    """_TODO_SCAFFOLD is only droppable because the schema says it, on the same turn, to the same
    model. If someone trims the schema description, this fails and the span has to come back."""
    from kotoba.tools.builtin.todo import SCHEMA

    described = SCHEMA["description"]
    detail = SCHEMA["parameters"]["properties"]["steps"]["items"]["anyOf"][1]["properties"]["detail"]
    for claim in ("not something the user asks you for and not a deliverable",
                  "you decide, by your own judgement, when a job is involved enough to be worth "
                  "tracking",
                  "keep it up to date until the work is finished"):
        assert claim in described
    assert "Handed back to you as you work" in detail["description"]


def test_the_dropped_pushes_are_said_again_by_rules_that_stay():
    """_HAVE_BOTH_SCAFFOLD and the web bans are droppable because the prompt says both halves harder,
    later, as truth conditions rather than as pushes: the invented result, the invented obstacle, and
    the true sentence she is handed in place of either."""
    lean = _build(reasoning=True)
    assert "NEVER claim you ran something" in lean
    assert "NEVER say you couldn't run it" in lean
    assert "I haven't run it yet — want me to?" in lean


# ── the blast radius: nobody else's prompt is on this axis ─────────────────────────────────────────

def test_work_mode_and_delegate_carry_none_of_this():
    """They assemble their own system text and never pass through build_system_prompt, which is why
    _reasons() can hardcode the companion role."""
    from kotoba.core.work_runner import _WORK_SUBSYSTEM
    from kotoba.tools.action.delegate import _SUB_SYSTEM

    for other in (_WORK_SUBSYSTEM, _SUB_SYSTEM):
        for _name, span in SCAFFOLDS:
            assert span not in other
