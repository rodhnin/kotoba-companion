"""Proactive TPM pacing (the smart layer OpenAI recommends): read the
rate-limit headers OpenAI returns on every response and WAIT before a request we can't afford, so we avoid
the 429 instead of just reacting to it. Plus exponential backoff with jitter as the safety net."""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.ratelimit as rl


def setup_function():
    rl._reset_for_tests()


def test_parse_duration_formats():
    assert rl._parse_duration("1.5s") == pytest.approx(1.5)
    assert rl._parse_duration("13ms") == pytest.approx(0.013)
    assert rl._parse_duration("6m0s") == pytest.approx(360.0)
    assert rl._parse_duration("2m") == pytest.approx(120.0)
    assert rl._parse_duration("1h0m0s") == pytest.approx(3600.0)
    assert rl._parse_duration("") == 0.0


class _Headers(dict):
    def get(self, k, default=None):  # case-insensitive-ish for our fixed keys
        return super().get(k, default)


def test_note_headers_then_throttle_waits(monkeypatch):
    slept = {"s": 0.0}

    async def fake_sleep(s):
        slept["s"] += s

    monkeypatch.setattr(rl.asyncio, "sleep", fake_sleep)
    # Almost no budget left, resets in ~2s → throttle must wait ~2s.
    rl.note_headers(_Headers({"x-ratelimit-remaining-tokens": "1000", "x-ratelimit-reset-tokens": "2s"}))
    asyncio.run(rl.throttle(floor=35000))
    assert 1.9 <= slept["s"] <= 3.0          # waited roughly the reset window (+ jitter)
    assert rl._state["remaining"] is None    # cleared → next response re-learns


def test_throttle_noop_when_budget_healthy(monkeypatch):
    slept = {"s": 0.0}

    async def fake_sleep(s):
        slept["s"] += s

    monkeypatch.setattr(rl.asyncio, "sleep", fake_sleep)
    rl.note_headers(_Headers({"x-ratelimit-remaining-tokens": "150000", "x-ratelimit-reset-tokens": "5s"}))
    asyncio.run(rl.throttle(floor=35000))
    assert slept["s"] == 0.0  # plenty of budget → don't wait


def test_throttle_caps_wait_for_live_voice_turn(monkeypatch):
    # a companion (voice) turn must NOT stall on a long pacing sleep — max_wait caps it (no dead air).
    slept = {"s": 0.0}

    async def fake_sleep(s):
        slept["s"] += s

    monkeypatch.setattr(rl.asyncio, "sleep", fake_sleep)
    rl.note_headers(_Headers({"x-ratelimit-remaining-tokens": "500", "x-ratelimit-reset-tokens": "40s"}))
    asyncio.run(rl.throttle(floor=35000, max_wait=5.0))
    assert slept["s"] <= 5.0                  # capped, not the full 40s window
    assert rl._state["remaining"] == 500      # capped → budget NOT cleared (real window hasn't reset)


def test_throttle_noop_when_unknown(monkeypatch):
    slept = {"s": 0.0}

    async def fake_sleep(s):
        slept["s"] += s

    monkeypatch.setattr(rl.asyncio, "sleep", fake_sleep)
    asyncio.run(rl.throttle())  # no headers seen yet
    assert slept["s"] == 0.0


def test_backoff_prefers_retry_after_and_is_bounded():
    # Retry-After respected (+ small jitter), capped.
    w = rl.backoff_seconds(1, retry_after=3.0)
    assert 3.0 <= w <= 3.6
    # Exponential growth with jitter when no Retry-After, capped at 30.
    w1 = rl.backoff_seconds(1, None, base=1.0)
    w5 = rl.backoff_seconds(5, None, base=1.0)
    assert w1 <= 1.2 and w5 <= 30.0 and w5 > w1


def test_a_long_retry_after_is_not_truncated_to_the_guess_cap():
    """The server said 55s. `cap` bounded OUR exponential guess and was applied to the server's
    answer too, so we came back at 30s — inside a window the server had just told us is closed. The
    second 429 costs a round trip AND a slot out of _MAX_RL_RETRIES, so truncating makes the total
    wait longer — the same shape as the throttle that never reports the seconds it could not wait."""
    w = rl.backoff_seconds(1, retry_after=55.0)
    assert w >= 55.0, f"came back {w:.1f}s after being told 55s — guaranteed to 429 again"


def test_an_absurd_retry_after_is_clamped_at_a_documented_ceiling():
    """The other half: a daily/monthly quota answers with hours. Honouring that verbatim would park a
    live turn until tomorrow, so pacing stops where a per-minute bucket stops and the ladder is left to
    fail the turn honestly."""
    ceiling = rl.max_retry_after()
    assert ceiling > 30.0, "the ceiling has to clear a plausible TPM Retry-After"
    assert rl.backoff_seconds(1, retry_after=ceiling * 100) == pytest.approx(ceiling)


def test_throttle_reports_the_seconds_it_could_not_wait(monkeypatch):
    """Half one. The capped branch is right to leave `remaining` stale-low — it just never told the
    caller, so the caller sent the request anyway. The owed seconds are the caller's cue not to."""
    async def fake_sleep(s):
        return None

    monkeypatch.setattr(rl.asyncio, "sleep", fake_sleep)
    rl.note_headers(_Headers({"x-ratelimit-remaining-tokens": "500", "x-ratelimit-reset-tokens": "40s"}))
    owed = asyncio.run(rl.throttle(floor=35000, max_wait=5.0))
    assert owed >= 30.0, f"capped at 5s of a ~40s window and reported {owed!r} still owed"


def test_throttle_reports_clear_when_it_waited_the_window_out(monkeypatch):
    async def fake_sleep(s):
        return None

    monkeypatch.setattr(rl.asyncio, "sleep", fake_sleep)
    rl.note_headers(_Headers({"x-ratelimit-remaining-tokens": "500", "x-ratelimit-reset-tokens": "2s"}))
    assert asyncio.run(rl.throttle(floor=35000, max_wait=5.0)) == 0.0
    assert asyncio.run(rl.throttle(floor=35000)) == 0.0  # healthy/unknown budget → nothing owed


def test_a_reset_far_outside_a_per_minute_bucket_is_not_paced_on(monkeypatch):
    """A header reading '6m0s' is not describing the TPM window we pace against. Waiting it out would
    stall a turn for minutes on a value we have no reason to trust, so pacing steps aside and the
    reactive backoff carries it."""
    slept = {"s": 0.0}

    async def fake_sleep(s):
        slept["s"] += s

    monkeypatch.setattr(rl.asyncio, "sleep", fake_sleep)
    rl.note_headers(_Headers({"x-ratelimit-remaining-tokens": "500", "x-ratelimit-reset-tokens": "6m0s"}))
    assert asyncio.run(rl.throttle(floor=35000, max_wait=5.0)) == 0.0
    assert slept["s"] == 0.0
    assert rl._state["remaining"] is None
