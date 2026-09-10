"""Told to RUN a command she searched for it instead, then claimed she could not run it. Both are prompt.

`web_search` was described as the default for ANY question — a claim over everything — while "actually
call the tool" sat 3,925 characters later with nothing ordering the two, and `calculator` was never
named at all. A clean typed input reproduced it, so bad transcription is not the cause. The over-broad
claim is narrowed where it is written (a search's subject is the WORLD, never this machine) and the
boundary is stated once, near the END. Banning the false SUCCESS had left the false FAILURE as the only
legal closing move, so the prompt now also supplies a true negative sentence — and a blunt block
ordering her to run what the toolset gate already removed would manufacture that failure: it is pinned.
"""
from __future__ import annotations

import pytest

from kotoba.soul import prompt
from kotoba.tools import registry

SOUL = {"name": "Kotoba", "language": "auto", "personality": "Warm.", "address_style": "By name.",
        "emotional_rules": "Delight on good news.", "quirks": "A soft 'hmph'."}

RUN_HEAD = "# ⛔ AN INSTRUCTION IS NOT A QUESTION — WHEN THEY ASK YOU TO RUN SOMETHING, RUN IT"
CALCULATOR = "**execute_code** is your calculator"


def _companion_names():
    registry.discover()
    return {t.get("name") or t.get("type") for t in registry.schemas_for("companion")}


def _build(available=None, **kw):
    return prompt.build_system_prompt(SOUL, "- Name: Jordan", [], session_id="s",
                                      available_tools=available, **kw)


def _flat(text: str) -> str:
    return " ".join(text.split())


# --- the over-broad claim is gone, not counterweighted ---------------------------------------------

def test_web_search_is_no_longer_the_default_for_any_question():
    """The sentence that outranked everything. If it comes back, so does the defect."""
    built = _flat(_build())
    assert "your default for ANY question" not in built
    assert "your default for any QUESTION you would otherwise answer out of your own memory" in built


def test_the_bullet_itself_says_a_search_cannot_see_this_machine():
    """The scope fix has to live where the tool is introduced — that is the sentence the model reads
    when it is deciding, some three thousand characters before anything mentions running a command."""
    built = _flat(_build())
    assert "Its subject is the WORLD" in built
    assert "never the way to answer something you could just DO" in built


@pytest.mark.parametrize("kwargs", [{}, {"register": "text"}], ids=["voice", "text"])
def test_the_blunt_block_is_present_in_both_registers(kwargs):
    """A terminal turn runs the same tools and hit the same wall; nothing about this is about speech."""
    built = _build(**kwargs)
    assert RUN_HEAD in built
    assert CALCULATOR in built


def test_it_states_the_boundary_without_naming_a_query_she_must_not_run():
    """No ⛔ counter-example may quote a search string. Two reasons, both learned the hard way in one
    day. It writes the failing text into the prompt beside the tool it warns about — the instance
    measured after `NOT web_search for "calculator: 16"` shipped was `calculator: 1+1`, the literal
    example expression in OpenAI's own `web` tool spec, where `calculator` is a sibling command of
    `search_query`; she was reproducing a memorised tool name, not disobeying. And it forbids by
    anecdote a lookup that may be entirely reasonable another day: she is allowed to search for how
    something works. The boundary survives as a principle and as the positive statement of which tool
    owns what."""
    built = _flat(_build())
    assert "NOT web_search for" not in built, "no ⛔ may name a query she must not run"
    assert "calculator:" not in built, "the failing token sequence must never be written into the prompt"
    assert "**execute_code** is your calculator" in built, "the positive instruction still stands"
    # The example itself, pinned by what it ROUTES rather than by how it greets: the sentence is prose
    # and has already been reworded once (it used to open in Spanish), which broke this line while the
    # teaching it guards never moved.
    example = next((ln for ln in built.splitlines() if "df -k" in ln and "✅" in ln), "")
    assert example, "the positive example that routes a run request to the terminal is gone"
    assert "**shell**" in example and "`df -k`" in example


def test_it_sits_near_the_end_where_the_restrictions_that_stick_live():
    """Every behavioural restriction that stuck in this codebase took the same shape: a rule buried
    mid-prompt was measurably not enough, and the blocks that changed her behaviour are the blunt ⛔ ones
    near the tail. Pinning the ordering stops a later edit from quietly moving this one back into the
    middle."""
    built = _build()
    assert built.index(RUN_HEAD) > built.index("⛔ HONESTY, AND IT CUTS BOTH WAYS")
    assert built.index(RUN_HEAD) > built.index("**web_search** —")
    tail = len(built) - built.index(RUN_HEAD)
    assert tail < 6000, f"the block drifted {tail} characters from the end of the prompt"


def test_a_garbled_request_is_a_question_for_the_user_not_for_a_search_engine():
    """Two of the three live instances followed a mangled transcription. The block has to name that
    route or the fragment goes back to the search box the moment STT slips again."""
    built = _flat(_build())
    assert "ASK them in one short line" in built
    assert "NEVER hand the fragment to a search engine" in built


# --- and it never orders a tool the session does not have ------------------------------------------

def test_no_runner_means_no_block():
    """With shell and execute_code gone there is nothing to prefer, and _HAVE_NEITHER already licenses
    the true sentence. Ordering her to "run it" here is how she is taught to claim she did."""
    text = _build(_companion_names() - {"shell", "execute_code"})
    assert RUN_HEAD not in text
    assert "I can't run commands right now" in text


def test_no_web_search_means_no_block():
    """Nothing to prefer it over — and _WEB_HEAD_OFF is already telling her she cannot look anything up."""
    text = _build(_companion_names() - {"web_search"})
    assert RUN_HEAD not in text


def test_the_examples_follow_the_tools_that_are_really_offered():
    only_shell = _build(_companion_names() - {"execute_code"})
    assert RUN_HEAD in only_shell
    assert "**shell** with `df -k`" in only_shell
    assert CALCULATOR not in only_shell, "it would name a calculator this session does not have"

    only_code = _build(_companion_names() - {"shell"})
    assert RUN_HEAD in only_code
    assert CALCULATOR in only_code
    assert "**shell** with `df -k`" not in only_code


# --- the honesty rule is symmetric, and the true sentence exists -----------------------------------

def test_the_false_failure_is_banned_by_the_rule_that_already_bans_the_false_success():
    """One rule with two halves, not a second rule about lying. The success half is the one that has
    demonstrably worked here; the failure half is written to the same shape and lives in the same
    block, gated on the same tools, so it cannot outlive the capability it talks about."""
    built = _flat(_build())
    assert "NEVER claim you ran something" in built
    assert "NEVER say you couldn't run it, that it failed, that something stopped you" in built
    assert "unless you really called shell/execute_code and it really came back refused" in built


def test_not_calling_a_tool_is_named_as_not_being_an_obstacle():
    """The exact hole the live turn fell through: she chose another path, then reported a wall."""
    assert "Deciding not to call a tool is not an obstacle" in _flat(_build())


def test_she_is_given_the_true_sentence_to_close_with():
    """A ban with no permitted alternative is the trap this project keeps re-discovering. The prompt
    that removed the claim but still banned "I can't" is the documented factory for the invented one,
    and with every tool present _missing_block — where all the honest phrasing lives — is empty."""
    built = _flat(_build())
    assert "What you genuinely CANNOT do right now" not in built, "the fixture must be the healthy config"
    assert "I haven't run it yet — want me to?" in built
    assert "An invented obstacle is worse than the honest sentence" in built


def test_the_symmetric_half_disappears_with_the_terminal():
    """It rides on _HONESTY_RUN, which _doing_block only emits when there is something to have run."""
    text = _build(_companion_names() - {"shell", "execute_code"})
    assert "NEVER say you couldn't run it" not in text
    assert "NEVER say you ran something" in text, "_HAVE_NEITHER keeps its own half of the honesty"
