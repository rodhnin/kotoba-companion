"""Two heuristics that degenerated on ordinary input.

1. `_as_field_label` split on the first period, so any abbreviation ("Dr.", "e.g.", "No.") became the
   whole card title and the real question hid behind "See description".
2. `_has_escape_flag` only inspected `head[:2]`, so an escape flag anywhere but first in a short bundle
   slipped through: `tail -qf` follows forever exactly like `tail -f`, burning the tool budget instead
   of asking. Reads must stay frictionless — a gate that asks about everything trains blind approval.
"""
from __future__ import annotations

import pytest

from kotoba.core.approval import _has_escape_flag
from kotoba.tools.builtin.ask_user import _TITLE_MIN, _as_field_label


# --- labels ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", [
    "Dr. Smith email address",
    "e.g. your OpenAI key",
    "No. 5 street address",
    "Apt. number and street",
])
def test_an_abbreviation_is_not_a_sentence_end(prompt):
    title, detail = _as_field_label(prompt, None)
    assert title == prompt, "the whole question is the label"
    assert detail is None
    assert len(title) >= _TITLE_MIN


def test_a_real_second_sentence_still_moves_to_the_detail():
    assert _as_field_label("Paste the link here. It must be the public one.", None) == (
        "Paste the link here.", "It must be the public one.")


def test_an_abbreviation_before_a_real_sentence_cuts_at_the_later_boundary():
    title, detail = _as_field_label("Dr. Smith's email. I'll only use it to send the report.", None)
    assert title == "Dr. Smith's email."
    assert detail == "I'll only use it to send the report."


def test_a_short_label_is_untouched_and_empty_input_is_safe():
    assert _as_field_label("Your postal code", None) == ("Your postal code", None)
    assert _as_field_label("", None) == ("", None)
    assert _as_field_label("   \n ", "ctx") == ("", "ctx")


# --- escape flags ----------------------------------------------------------------------------------

def _asks(cmd: str) -> bool:
    parts = cmd.split()
    return _has_escape_flag(parts[0], parts)


@pytest.mark.parametrize("cmd", [
    "tail -f log", "tail -qf x", "tail -nf x", "tail --follow x",
    "grep -Z pat .", "grep -rZ pat .",
    "rg --pre x pat", "rg --pre=x pat",
    "sort -o out.txt in.txt", "sort -uo out.txt in.txt", "sort --compress-program=x f",
    "find . -exec rm {} ;", "find . -delete", "find . -fprintf out.txt %p",
])
def test_an_escape_flag_anywhere_still_asks(cmd):
    assert _asks(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "ls -la", "cat f", "wc -l f",
    "grep -rn pat .", "grep -ri pat .", "grep -rin pat .",
    "rg -n pat", "rg -il pat", "rg --json pat",
    "tail -n 20 log", "tail -c 100 log",
    "head -c 100 f", "head -n 5 f",
    "sort -u f", "sort -rn f", "sort -k2 f",
    'find . -name "*.py"', "find . -type f -maxdepth 2",
])
def test_reading_never_costs_a_prompt(cmd):
    assert not _asks(cmd), f"{cmd} must not ask — it only reads"
