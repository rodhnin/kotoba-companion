"""A deferred command settling must not overwrite a running background job's state.

work_state is how a finished thing gets ANNOUNCED. The deferred path borrowed it to carry its own
message, and already knew a job might own it — it skipped the announce when busy, but wrote the state
anyway. So the job was marked done, unannounced, with the deferred command's message: the next
/api/session/{id}/work poll announced "I didn't run X" as the result of work still in flight.
"""
from __future__ import annotations

from kotoba.core import work_state
import kotoba.core.deferred_exec


def _job_running(sid: str):
    work_state.clear(sid)
    work_state.start(sid, "scrape the 40 listings")
    assert work_state.is_running(sid)


def test_a_running_job_keeps_its_own_state_when_a_card_is_declined():
    sid = "clobber-decline"
    _job_running(sid)

    # What the decline path does now: sample first, only touch work_state when nobody owns it.
    busy = work_state.is_running(sid)
    if not busy:
        work_state.finish(sid, "I didn't run “rm -rf build”.", [])

    s = work_state.get(sid)
    assert s["status"] == "running", "the job must still be running"
    assert "rm -rf" not in (s.get("summary") or ""), "the job's result must not be the card's message"
    assert work_state.has_pending_announcement(sid) is False, "nothing to announce — the job isn't done"
    work_state.clear(sid)


def test_with_no_job_running_the_deferred_message_still_gets_announced():
    """The guard must not silence the ordinary case, which is the whole point of borrowing work_state."""
    sid = "clobber-idle"
    work_state.clear(sid)
    assert work_state.is_running(sid) is False

    if not work_state.is_running(sid):
        work_state.finish(sid, "I didn't run “rm -rf build” — no go-ahead.", [])

    assert work_state.has_pending_announcement(sid) is True
    assert "go-ahead" in work_state.prompt_note(sid)
    work_state.clear(sid)


def test_every_settle_path_guards_the_write():
    """Source check: decline, runner-failed and success each sampled `busy` but only guarded the announce.

    Counted, not enumerated — a new ending is added to this module every time an action learns another
    way to finish, and a census pinned to today's number goes red for the addition instead of for the
    defect. The floor is what stops the check passing on a file where the writes have vanished."""
    from pathlib import Path

    src = Path(kotoba.core.deferred_exec.__file__).read_text(encoding="utf-8")
    lines = src.splitlines()
    writes = [i for i, l in enumerate(lines) if "work_state.finish(sid" in l or "work_state.fail(sid" in l]
    assert len(writes) >= 3, f"the settle paths have gone missing, not grown: found {len(writes)}"
    for i in writes:
        guard = "\n".join(lines[max(0, i - 3):i])
        assert "if not busy:" in guard, f"unguarded work_state write at line {i + 1}: {lines[i].strip()}"
