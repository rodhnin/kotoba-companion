"""Where the gate is allowed to send somebody once they are in.

`?next=` is attacker-controlled and lands in a `Location` header. The obvious guard — starts with one
slash — does not hold it: the browser's parser strips raw TAB, LF and CR before parsing and folds a
backslash into a slash, so `/\\evil.com` looks local and resolves to a foreign host. These vectors and
their expected answers were taken from the frontend implementation, not recomputed here.
"""
from __future__ import annotations

import pytest

from kotoba.core.frontend import HOME, safe_next

#: Refused by the frontend, and refused here. This equality is the property that matters; the two
#: sides may differ on how they normalise something they both accept, never on what they let through.
REFUSED = [
    "", None, "//evil.com", "//evil.com/path", "/\\evil.com", "/\\\\evil.com", "/\\/evil.com",
    "/\t/evil.com", "/\n/evil.com", "/\r/evil.com", "\t/app", "  /app",
    "http://evil.com", "https://evil.com/x", "javascript:alert(1)", "app", "../app",
]

#: The stripped character has to be followed by a SLASH to make a second one; on its own it leaves an
#: ordinary local path, which both sides accept and neither should refuse.
ALLOWED = [
    ("/app", "/app"),
    ("/app?panel=files", "/app?panel=files"),
    ("/app#top", "/app"),
    ("/setup?step=2", "/setup?step=2"),
    ("/app/x?a=1&b=2", "/app/x?a=1&b=2"),
    ("/", "/"),
    ("/login?next=%2Fapp", "/login?next=%2Fapp"),
    ("/\tevil.com", "/evil.com"),
    ("/\nevil.com", "/evil.com"),
]


@pytest.mark.parametrize("value", REFUSED)
def test_nothing_that_could_leave_is_accepted(value):
    assert safe_next(value) == HOME


@pytest.mark.parametrize("value,expected", ALLOWED)
def test_an_ordinary_destination_survives_intact(value, expected):
    assert safe_next(value) == expected


@pytest.mark.parametrize("value", REFUSED + [v for v, _ in ALLOWED] + [
    "/app\x00x", "/app\x7fx", "/a b", "/app/../../x", "/%2e%2e/%2e%2e/etc", "/app\\x",
])
def test_no_answer_can_ever_address_another_host_or_split_a_header(value):
    """Holds for every input, accepted or refused: one leading slash, no backslash, no CR or LF."""
    out = safe_next(value)
    assert out.startswith("/") and not out.startswith("//")
    assert "\\" not in out and "\r" not in out and "\n" not in out
