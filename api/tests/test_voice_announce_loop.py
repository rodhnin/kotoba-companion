"""The announce-loop guard must be wired on the LOCAL VOICE chain too.

Two turn chains handle the `__work_done__` sentinel; a guard on only one leaves the loop
alive on whichever mode is in use. The other chain is covered elsewhere; this file pins the
voice chain's wiring.

It reads the source rather than driving a socket: the turn only reaches `agentic_loop` once a
pile of session preconditions line up, and a socket read with no deadline turns any regression
into a hung suite instead of a failing test.
"""
from __future__ import annotations

import inspect
import re

import kotoba.core.voice.session as vs


def _run_turn_source() -> str:
    return inspect.getsource(vs.VoiceSession._run_turn)


def test_the_voice_chain_passes_exclude_tools_to_the_loop():
    src = _run_turn_source()
    call = re.search(r"agentic_loop\((.*?)\n\s*\)", src, re.S)
    assert call, "agentic_loop call not found — this test needs updating with the refactor"
    assert "exclude_tools" in call.group(1), (
        "the local voice chain must pass exclude_tools; without it a __work_done__ turn can "
        "re-launch the work whose completion triggered it"
    )


def test_the_exclusion_is_conditional_on_the_sentinel():
    src = _run_turn_source()
    m = re.search(r"exclude_tools\s*=\s*(.+)", src)
    assert m, "exclude_tools is not assigned in the call"
    expr = m.group(1)
    assert "is_trigger" in expr, "the exclusion must apply to sentinel turns only"
    for tool in ("start_work", "delegate"):
        assert tool in expr, f"{tool} must be withheld from an announce turn"
    assert "None" in expr, "a real user turn must keep every tool"


def test_the_sentinel_is_still_detected_here():
    """The guard is worthless if the chain stops recognising the sentinel."""
    assert vs.is_trigger_sentinel("__work_done__") is True
    assert vs.is_trigger_sentinel("investiga el protocolo OSC 52") is False
