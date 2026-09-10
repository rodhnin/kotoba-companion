"""She joined a voice channel on request, and then answered a request to leave in words and stayed.

The enum always carried `leave`. Every instruction in the description was about arriving — including
the emphatic one — and the only example phrases were English, in a product whose language is `auto`.
A model reads the description to decide, so that is where a missing half of a tool lives.
"""
from __future__ import annotations

import re

from kotoba.tools.action import discord_voice

SPEC = discord_voice.SPEC if hasattr(discord_voice, "SPEC") else None


def _schema() -> dict:
    for name in dir(discord_voice):
        value = getattr(discord_voice, name)
        if isinstance(value, dict) and value.get("name") == "discord_voice":
            return value
    raise AssertionError("the tool no longer declares a schema this test can read")


def _sentences(text: str) -> list[str]:
    return [s.strip().lower() for s in re.split(r"(?<=[.:])\s+", text) if s.strip()]


def test_both_directions_are_in_the_enum():
    action = _schema()["parameters"]["properties"]["action"]
    assert set(action["enum"]) == {"join", "leave"}


def test_the_emphatic_instruction_covers_leaving_too():
    """`Call it EVERY time…` used to name only going in, which is the half she obeyed."""
    said = [s for s in _sentences(_schema()["description"]) if "every time" in s]
    assert said, "nothing tells her to call it every time"
    assert any(("out" in s or "leave" in s) for s in said), (
        "the instruction she follows names only going in:\n  " + "\n  ".join(said))


def test_leaving_is_named_as_an_instruction_not_only_in_passing():
    description = _schema()["description"].lower()
    telling = [s for s in _sentences(description) if "leave" in s or " out " in s]
    assert len(telling) >= 2, (
        "leaving is mentioned once, in passing, and instructed nowhere:\n  " + "\n  ".join(telling))


def test_the_trigger_does_not_depend_on_one_language():
    """The private build carried Spanish examples and worked; this one carried English ones and did
    not. Naming languages does not scale — telling her the language is irrelevant does."""
    description = _schema()["description"].lower()
    assert "whatever language" in description or "any language" in description
