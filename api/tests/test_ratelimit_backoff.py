"""A token-heavy work turn 429'd on OpenAI's TPM ceiling; without backoff the loop failed the whole work,
the companion turn re-issued start_work, and it cascaded into a runaway loop that kept the TPM pegged.
The retry that pauses for the server's retry-after and resumes IN PLACE now wraps the whole streaming
call, and this file measures it there.

A second, older backoff helper existed with no production caller, its own hardcoded 5-second pace cap,
and no mode awareness; it was deleted rather than adopted, since a live retry must wrap the whole stream
and may only re-run while nothing has been spoken, which a function that just hands back a stream
cannot know. Its useful parts (429 classification, retry-after parsing) are tested here. Stale browser
snapshots are also elided from history so re-sending them every iteration doesn't blow the ceiling."""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.loop as loop


def test_is_rate_limit_detects_tpm_message():
    assert loop._is_rate_limit(Exception("Rate limit reached for gpt-5.4-mini ... tokens per min (TPM)"))
    assert loop._is_rate_limit(Exception("HTTP 429 Too Many Requests"))
    assert not loop._is_rate_limit(ValueError("totally unrelated"))


def test_retry_after_parsing():
    assert loop._retry_after_seconds(Exception("Please try again in 2.869s.")) == pytest.approx(2.869)
    assert loop._retry_after_seconds(Exception("Please try again in 960ms.")) == pytest.approx(0.96)
    assert loop._retry_after_seconds(Exception("no hint here")) is None


class _Event:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class _Stream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for event in self._evs:
                yield event
        return gen()


class _DB:
    async def list_approved_commands(self):
        return []

    async def insert_audit_log(self, **kw):
        return None


def test_a_capped_pace_never_spends_the_call_it_just_proved_unaffordable(monkeypatch, tmp_path):
    """Measured on the real loop. The budget says ~40s to the window reset; the companion cap is
    5s. The loop slept its 5s, learned nothing changed, and sent the request anyway — one round trip
    it already knew it could not pay for, and then up to _MAX_RL_RETRIES more.

    The assertion is on WHEN the request left, not on the sleep total: the request must not go out
    until the budget it needs actually exists."""
    import kotoba.core.ratelimit as rl
    from kotoba.core import workspace

    rl._reset_for_tests()
    real_sleep = asyncio.sleep
    slept = {"s": 0.0}

    async def fake_sleep(s):
        slept["s"] += s
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(workspace, "resolve_workdir", lambda _sid: tmp_path)

    sent_after = []

    class _Responses:
        async def create(self, **kw):
            sent_after.append(slept["s"])
            return _Stream([_Event("response.output_text.delta", delta="ya voy")])

    class _Client:
        responses = _Responses()

    monkeypatch.setattr(loop, "get_client", lambda: _Client())
    rl.note_headers({"x-ratelimit-remaining-tokens": "500", "x-ratelimit-reset-tokens": "40s"})

    async def go():
        return await loop.agentic_loop([{"role": "user", "content": "hola"}], "rl-seq", _DB(),
                                       asyncio.Queue(), {}, max_iterations=1, mode="companion",
                                       channel="text")

    try:
        asyncio.run(go())
    finally:
        rl._reset_for_tests()

    assert len(sent_after) == 1, sent_after
    assert sent_after[0] >= 35.0, (
        f"request left after only {sent_after[0]:.1f}s of a ~40s window — a call the pacer had just "
        "proved unaffordable"
    )


def test_elide_browser_history_keeps_only_latest():
    items = [
        {"role": "user", "content": "go"},
        {"type": "function_call_output", "call_id": "s1", "output": "SNAP-1 (huge)"},
        {"type": "function_call_output", "call_id": "s2", "output": "SNAP-2 (huge)"},
        {"type": "function_call_output", "call_id": "s3", "output": "SNAP-3 (latest)"},
    ]
    loop._elide_browser_history(items, ["s1", "s2", "s3"])
    assert items[1]["output"] == loop._ELIDED_VIEW
    assert items[2]["output"] == loop._ELIDED_VIEW
    assert items[3]["output"] == "SNAP-3 (latest)"  # latest kept intact
    assert items[0]["content"] == "go"              # non-view items untouched


def test_is_browser_view():
    assert loop._is_browser_view("browser__browser_snapshot")
    assert loop._is_browser_view("browser__browser_take_screenshot")
    # run_code/evaluate page inspections are heavy + soon-stale → also elidable (TPM saver).
    assert loop._is_browser_view("browser__browser_run_code_unsafe")
    assert loop._is_browser_view("browser__browser_evaluate")
    assert not loop._is_browser_view("browser__browser_navigate")  # small text, keep it
    assert not loop._is_browser_view("web_extract")
