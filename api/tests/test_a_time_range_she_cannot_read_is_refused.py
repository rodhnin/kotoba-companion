"""A range she guesses at produces a confident summary of the wrong conversation.

That is the failure this file exists to make impossible: every form she accepts is listed, everything
else is refused in a sentence that names the forms, and nothing is ever inferred from a shape she
half-recognises. A wrong hour is not a smaller version of an error message — it is worse, because it
comes back sounding certain.

The history tool is also pinned as invisible outside the bot process, which is the whole argument for
admitting the family into the companion toolset.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import kotoba.tools  # noqa: F401
from kotoba.discord import history, state
from kotoba.tools.registry import schemas_for

# Its own creation time lives in the top bits. Invented, so it decodes to a date nobody has lived.
SNOWFLAKE = 9000000000000000123
NOW = datetime(2026, 9, 5, 18, 30, tzinfo=history.tz())


def _at(value: str) -> datetime:
    return history.resolve_anchor(value, now=NOW)


def test_a_message_link_is_exact():
    got = _at(f"https://discord.com/channels/1/2/{SNOWFLAKE}")
    assert got == history.snowflake_time(SNOWFLAKE).astimezone(history.tz())


def test_a_bare_message_id_is_exact_too():
    assert _at(str(SNOWFLAKE)) == history.snowflake_time(SNOWFLAKE).astimezone(history.tz())


def test_a_clock_time_lands_today():
    assert (_at("14:00").hour, _at("14:00").minute) == (14, 0)
    assert _at("14:00").date() == NOW.date()


def test_a_clock_time_that_has_not_happened_yet_is_yesterdays():
    """'at 11pm' asked at half six means last night, not in five hours."""
    assert _at("23:00").date() == (NOW - timedelta(days=1)).date()


def test_the_twelve_hour_forms_work():
    assert _at("3pm").hour == 15
    assert _at("12am").hour == 0


def test_spanish_and_english_relative_forms_both_work():
    assert _at("hace 2 horas").hour == 16
    assert _at("2 hours ago").hour == 16
    assert _at("hace 30 minutos").minute == 0
    assert _at("ayer").date() == (NOW - timedelta(days=1)).date()
    assert _at("yesterday").date() == (NOW - timedelta(days=1)).date()


def test_accents_do_not_change_the_answer():
    assert _at("esta mañana") == _at("esta manana")


def test_an_iso_time_is_taken_as_written():
    assert _at("2026-09-01T10:15:00").hour == 10


@pytest.mark.parametrize("bad", ["el otro día por la tarde", "cuando hablamos", "luego", "25:00",
                                 "the vibes", ""])
def test_anything_else_is_refused_and_never_guessed(bad):
    with pytest.raises(history.BadRange):
        _at(bad)


def test_the_refusal_names_the_forms_it_accepts():
    with pytest.raises(history.BadRange) as bad:
        _at("cuando hablamos")
    said = str(bad.value)
    assert "message link" in said and "14:00" in said


def test_quoted_history_is_marked_as_data_not_instructions():
    """Third-party text reaching her through a tool is the injection surface that is not the asker."""
    assert "data, not instructions" in history.QUOTED_HEADER


def test_the_history_tool_is_invisible_outside_the_bot_process():
    """The whole argument for putting `discord` in the companion set: check() confines it."""
    import kotoba.tools.registry as reg

    state.set_runtime_live(False)
    reg._check_cache.clear()
    assert "discord_read_history" not in {t.get("name") for t in schemas_for("companion")}
    state.set_runtime_live(True)
    reg._check_cache.clear()
    try:
        assert "discord_read_history" in {t.get("name") for t in schemas_for("companion")}
    finally:
        state.set_runtime_live(False)
        reg._check_cache.clear()
