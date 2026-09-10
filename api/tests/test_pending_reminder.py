"""A due cron reminder must be VOICED by Kotoba naturally (not shown as a robotic card). The cron worker
stashes it in pending_reminder; load_context's inject_work_note injects it into the turn the frontend's
__reminder__ trigger fires, so the model brings it up in its own words. Consumed once.

The sentinel never reaches inject_work_note — `_history_from_request` has already dropped it, and
load_context says `unprompted=True` instead. The turns below are shaped the way the builder really
shapes them; where the reminder LANDS on such a turn is not this file's question."""
from __future__ import annotations

from kotoba.core import pending_reminder
from kotoba.core.context import inject_work_note, is_trigger_sentinel

_TURN = [{"role": "developer", "content": "SYS"}, {"role": "user", "content": "Hola."},
         {"role": "assistant", "content": "[warmly] Hola, Jordan."}]


def test_reminder_trigger_is_a_filtered_sentinel():
    assert is_trigger_sentinel("__reminder__") is True


def test_due_reminder_is_injected_and_survives_until_explicitly_cleared():
    # load PEEKS the reminder (does NOT consume) — so a superseded/errored turn doesn't lose it.
    # it is cleared only after the turn actually spoke.
    pending_reminder.clear("sr")
    pending_reminder.add("sr", "tomar agua")
    note = inject_work_note(_TURN, "sr", unprompted=True)[-1]
    assert "tomar agua" in note["content"] and "DUE REMINDER" in note["content"]
    assert "own words" in note["content"]  # told to phrase it naturally, not as a system notification
    # NOT consumed by the peek — if this turn were superseded, it must still be injected next time
    assert pending_reminder.has_pending("sr")
    out_retry = inject_work_note(_TURN, "sr", unprompted=True)
    assert "tomar agua" in out_retry[-1]["content"]  # survives the retry
    # the turn spoke → the transport's finally clears it; now it's gone
    pending_reminder.clear("sr")
    out2 = inject_work_note([{"role": "user", "content": "hola"}], "sr")
    assert not (out2 and out2[-1]["role"] == "developer" and "DUE REMINDER" in out2[-1].get("content", ""))


def test_multiple_due_reminders_batched():
    pending_reminder.clear("sr2")
    pending_reminder.add("sr2", "comer")
    pending_reminder.add("sr2", "estirar")
    note = inject_work_note(_TURN, "sr2", unprompted=True)[-1]["content"]
    assert "comer" in note and "estirar" in note


def test_no_reminder_no_injection():
    pending_reminder.clear("sr3")
    items = [{"role": "user", "content": "hola"}]
    assert inject_work_note(items, "sr3") == items  # unchanged when nothing pending
