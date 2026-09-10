"""Proactive tokens-per-minute (TPM) pacing for the Responses API.

The org has a TPM ceiling a token-heavy agentic turn can exhaust. The reactive backoff is the safety
net; this reads the rate-limit headers on every response and waits before sending what we cannot afford.

A wait cut short is not a wait saved, it is a 429 bought. Both halves used to under-wait — `throttle`
capped its sleep and let the caller send anyway, and `backoff_seconds` applied a guess-cap to the
server's own Retry-After — turning one pause into several longer ones. `throttle` now REPORTS the
seconds it could not wait (positive means do not send), and Retry-After is honoured in full. Process-
global on purpose: TPM is an org-wide budget shared across every turn and session."""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time

log = logging.getLogger("kotoba")

# remaining: tokens left in the current TPM window per the last response header (None = unknown).
# reset_at: monotonic time when the token window is expected to refill.
_state: dict[str, float | int | None] = {"remaining": None, "reset_at": 0.0}


def _floor() -> int:
    """If fewer than this many tokens remain in the window, wait for the reset before sending. Default sized
    a bit above a typical heavy agentic request (~30k) so we never start a call we can't afford."""
    try:
        return int(os.getenv("KOTOBA_TPM_FLOOR", "35000"))
    except ValueError:
        return 35000


def _max_pace() -> float:
    """Longest wait we'll take on our OWN header arithmetic. Pacing exists for a per-minute token bucket,
    so a reset further out than this is a header describing something else (a daily cap, a clock skew) and
    is not worth stalling a turn on — we stop pacing and let the reactive backoff carry the request."""
    try:
        return float(os.getenv("KOTOBA_TPM_MAX_PACE", "75"))
    except ValueError:
        return 75.0


def max_retry_after() -> float:
    """Longest server-supplied Retry-After we will actually sleep. Same reasoning from the other side: a
    TPM bucket refills within a minute, so a Retry-After well past that is a daily/monthly quota, and
    parking a live turn on it is worse than letting the bounded retry ladder fail the turn."""
    try:
        return float(os.getenv("KOTOBA_MAX_RETRY_AFTER", "120"))
    except ValueError:
        return 120.0


def _parse_duration(s: str) -> float:
    """OpenAI reset headers look like '6m0s', '1.5s', '13ms', '2m', '880ms'. Return seconds (0.0 if unknown)."""
    s = (s or "").strip().lower()
    if not s:
        return 0.0
    if s.endswith("ms") and s[:-2].replace(".", "", 1).isdigit():
        return float(s[:-2]) / 1000.0
    total = 0.0
    matched = False
    for value, unit in re.findall(r"([\d.]+)\s*(h|m|s)", s):
        matched = True
        total += float(value) * {"h": 3600.0, "m": 60.0, "s": 1.0}[unit]
    if matched:
        return total
    try:
        return float(s)  # a bare number → seconds
    except ValueError:
        return 0.0


def note_headers(headers) -> None:
    """Update the budget from a response's rate-limit headers (best-effort)."""
    if headers is None:
        return
    try:
        get = headers.get
    except AttributeError:
        return
    try:
        rem = get("x-ratelimit-remaining-tokens")
        if rem is not None and str(rem).strip().isdigit():
            _state["remaining"] = int(rem)
        rst = get("x-ratelimit-reset-tokens")
        if rst:
            _state["reset_at"] = time.monotonic() + _parse_duration(str(rst))
    except Exception:
        pass


def note_stream(stream) -> None:
    """Read headers off the AsyncStream's underlying httpx response (it exposes `.response`)."""
    try:
        resp = getattr(stream, "response", None)
        note_headers(getattr(resp, "headers", None) if resp is not None else None)
    except Exception:
        pass


async def throttle(floor: int | None = None, max_wait: float = 65.0) -> float:
    """If the last-seen remaining-token budget is below the floor, sleep until the window resets (plus
    jitter) so the imminent request does not 429. No-op when the budget is unknown or healthy.

    Returns THE SECONDS STILL OWED: 0.0 means clear to send, positive means the sleep was cut short at
    `max_wait` and the budget is still below the floor. That is not advisory. `max_wait` protects a live
    VOICE turn from dead air, but trimming the sleep never made the request affordable — the caller sent
    into a near-certain 429, spent a slot from its retry ladder and backed off anyway, for several times
    the dead air the cap was trimming. So the cap bounds only what THIS function stalls for.

    Pacing only ever covers the per-minute bucket: a reset beyond `_max_pace()` is not paced on."""
    rem = _state["remaining"]
    floor = floor if floor is not None else _floor()
    if rem is None or rem >= floor:
        return 0.0
    due = float(_state["reset_at"]) - time.monotonic()
    if due <= 0:
        _state["remaining"] = None
        return 0.0
    wait = due + random.uniform(0.1, 0.5)
    if wait > _max_pace():
        log.info("TPM pacing: reset is %.0fs out — past a token window; not pacing on it", due)
        _state["remaining"] = None
        return 0.0
    nap = min(wait, max_wait)
    log.info("TPM pacing: %s tokens left (< %d); waiting %.2fs of %.2fs for the window to reset",
             rem, floor, nap, wait)
    await asyncio.sleep(nap)
    if nap < wait:
        return wait - nap  # capped → budget still short, and the caller now knows it
    _state["remaining"] = None  # waited the full window; the next response re-learns the real value
    return 0.0


async def wait_out(owed: float) -> None:
    """Pay the remainder of a wait `throttle` had to cap, for a caller whose alternative is spending a
    request it has just been told it cannot afford."""
    if owed <= 0:
        return
    log.info("TPM pacing: paying the %.2fs the cap left rather than spending a doomed request", owed)
    await asyncio.sleep(min(owed, _max_pace()))
    _state["remaining"] = None


def backoff_seconds(attempt: int, retry_after: float | None, base: float = 1.0, cap: float = 30.0) -> float:
    """Exponential backoff WITH jitter (OpenAI's recommendation), or the server's Retry-After when it gave
    one. attempt is 1-based; the computed branch is base*2^(attempt-1) ± 20% jitter, bounded by `cap`.

    `cap` bounds OUR guess and nothing else. The Retry-After is not a guess: it is the server saying when
    the window reopens. Truncating a 55s answer to 30 sent the retry back inside a window we had just been
    told was closed, so it 429'd again, burned a slot out of the caller's bounded ladder and made the total
    wait longer than honouring the number would have been. It is honoured in full up to `max_retry_after()`
    — past that it is a daily quota rather than a rate window, and failing the turn beats stalling it."""
    if retry_after is not None and retry_after > 0:
        return min(retry_after + random.uniform(0.1, 0.5), max_retry_after())
    raw = base * (2 ** (attempt - 1))
    return min(raw * random.uniform(0.8, 1.2), cap)


def _reset_for_tests() -> None:
    _state["remaining"] = None
    _state["reset_at"] = 0.0
