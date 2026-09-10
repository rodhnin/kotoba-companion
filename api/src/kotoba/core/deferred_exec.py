"""Run an approval-gated action WITHOUT holding the live voice turn open — ElevenLabs agent transport ONLY.

A voice turn that blocks goes silent, EL times it out and re-fires it, and the re-fire supersedes the
waiter — the approval arrives orphaned and nothing runs. So the tool shows the card, ends the turn,
and this DETACHED task waits outside it, runs the action and announces through work_state. `busy` is
sampled AFTER the wait, or a background job that started meanwhile is marked finished by this action.

ONE REQUEST, ONE RUN. Nothing durable records that a tool ran, and the announce turn re-derives the
same request, so the model re-emits the same call respelled (`1,101` → `1, 101`). `schedule()` keys
on (user request + normalized action) and refuses. Every other transport blocks on its card inline."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Awaitable, Callable

from kotoba.core import work_state
from kotoba.core.interaction import (
    DECLINED, DISMISSED, UNANSWERED, UNREACHABLE,
    approver_for, refusal_note, refusal_row, request_approval, verdict_of,
)
from kotoba.core.sandbox.base import exit_outcome, record_exits

log = logging.getLogger("kotoba.deferred_exec")

# Held so detached tasks aren't GC'd mid-flight; keyed by session so cancel_work can reach them.
_tasks: dict[str, set[asyncio.Task]] = {}

_awaiting: dict[str, dict[str, str]] = {}   # session -> {action key: label} while its card is on screen
_settled: dict[str, dict[str, str]] = {}    # session -> {action key: outcome} once it can't run again
_MAX_KEYS = 64
_MAX_SESSIONS = 200

# Detached from the EL voice turn, so this wait can be generous where an inline one cannot.
_DEFERRED_APPROVAL_TIMEOUT = 180.0

# What she SAYS when the card ended without a yes — one sentence per ending, English on purpose (they go
# to the model to relay, and `language: auto` renders them in the user's language). She reported all three
# as "I never got the go-ahead", so a No pressed at 64 seconds came back as "the approval never arrived".
_UNRUN = {
    DECLINED: "you said no, so I left it alone.",
    UNANSWERED: "the approval card timed out on screen before anyone answered it.",
    UNREACHABLE: "I couldn't get the approval card onto your screen, so nobody was ever asked.",
}


class NothingRan(Exception):
    """A runner that ended before anything could execute — no process, no exit code, no trace on the
    host. The message is the sentence she says about it.

    It exists because a runner reports through its RETURN VALUE, and prose is not a channel: a runner
    that answered "I couldn't get the environment ready" was read as a summary of a successful run and
    filed as `executed`. The audit trail is the only place that answers "did this reach my machine",
    so it is the one place a guess is not allowed."""


def _norm_action(action: str) -> str:
    """One action under the respelling the model does between turns — see `_action_key`."""
    return re.sub(r"[ \t\r\n]+", "", str(action or "")).lower()


def _action_key(ctx, action: str) -> str | None:
    """Identity of a deferred action WITHIN one user request. The request anchors it (so "run it again" is a
    new ask), and whitespace is stripped from the action because the model respells the same snippet between
    turns — `print(sum(range(1,101)))` and `print(sum(range(1, 101)))` are one intent, not two.

    None when there is no user request to anchor to (core.loop always sets user_text on a companion turn;
    a sessionless/synthetic caller may not). Then the guard is OFF rather than session-global — a sticky
    session-wide block would refuse to ever run that command again for the rest of the call."""
    request = " ".join(str(getattr(ctx, "user_text", "") or "").split()).lower()
    if not request:
        return None
    return f"{request}\x00{_norm_action(action)}"


def _remember(store: dict[str, dict[str, str]], sid: str, key: str, value: str) -> None:
    bucket = store.setdefault(sid, {})
    bucket.pop(key, None)
    bucket[key] = value
    while len(bucket) > _MAX_KEYS:
        bucket.pop(next(iter(bucket)))
    while len(store) > _MAX_SESSIONS:
        store.pop(next(iter(store)))


def forget_session(session_id: str | None) -> None:
    """Drop this session's deferred-action memory (an explicit /leave ends the conversation)."""
    _awaiting.pop(session_id or "", None)
    _settled.pop(session_id or "", None)


def is_pending(ctx, call_id: str) -> bool:
    """True while `call_id`'s action is deferred (card shown, not run yet) — the loop marks its terminal row
    `pending` instead of ✓ done, and we complete that same row from _finish_step()."""
    return bool(call_id) and call_id in getattr(ctx, "_deferred_steps", ())


def _mark_no_execution(ctx, call_id: str) -> None:
    if not call_id:
        return
    try:
        ctx._deferred_noop.add(call_id)
    except AttributeError:
        ctx._deferred_noop = {call_id}


def ran_nothing(ctx, call_id: str) -> bool:
    """True when this tool call executed NOTHING: it either deferred (card on screen, awaiting a human) or
    was refused as a re-emission of one already carded/run. Either way an audit row saying it ran would be
    a lie — the caller writes no row and lets the deferred path record the real event."""
    return bool(call_id) and (
        call_id in getattr(ctx, "_deferred_steps", ()) or call_id in getattr(ctx, "_deferred_noop", ())
    )


def narrated_actions(session_id: str | None) -> set[str]:
    """The actions this module is already telling the model about, as `_norm_action` forms.

    core.context's pending-card note reads this to hand these cards over: a deferred card is a pending
    interaction Future too, and describing it twice gave the model both notes' orders at once — this
    one says say nothing further about it, that one said say once that it is still waiting."""
    return {key.split("\x00", 1)[1] for key in (_awaiting.get(session_id or "") or {})}


def prompt_note(session_id: str | None) -> str:
    """A developer note for the next turn while a card is still unanswered, so she doesn't keep announcing
    the same request ("just approve it" three times). Empty when nothing is waiting."""
    waiting = [lbl for lbl in (_awaiting.get(session_id or "") or {}).values() if lbl]
    if not waiting:
        return ""
    items = "; ".join(f"“{w[:120]}”" for w in waiting[:3])
    return (
        f"[WAITING ON THE USER] You have already asked them, on screen, to approve: {items}. The card is "
        "up and unanswered, and only THEY can answer it — you cannot approve or decline it for them. Do "
        "NOT call that tool again and do NOT repeat the request — say nothing further about it; you will "
        "be told the moment it runs."
    )


def schedule(
    ctx,
    action: str,
    runner: Callable[[], Awaitable[str]],
    *,
    label: str | None = None,
    step_kind: str = "tool",
    family: str | None = None,
) -> str | None:
    """Fire-and-forget: ask for approval out-of-band, then run `runner()` (an async () -> summary) if granted.

    Returns None when the action was scheduled. When this same action was ALREADY handled for the current
    user request, nothing is scheduled and the return is a line for the MODEL (never a second card).

    `family` is the key an "always allow" is persisted under, and MUST be the one the calling tool gates
    on. execute_code's action reads "run Python: …", whose first token is the junk pattern "run" — saved
    under that, an "always allow" could never match the tool's `execute_code` family and the button had
    no effect at all."""
    sid = getattr(ctx, "session_id", None) or ""
    call_id = str(getattr(ctx, "call_id", "") or "")
    key = _action_key(ctx, action)
    if key is not None:
        done = (_settled.get(sid) or {}).get(key)
        if done is not None:
            _mark_no_execution(ctx, call_id)
            return done
        if key in (_awaiting.get(sid) or {}):
            _mark_no_execution(ctx, call_id)
            return (
                "You have ALREADY asked the user to approve this exact action for this request and the card "
                "is still on screen, unanswered. Do NOT ask again and do NOT call this tool again — wait; "
                "you will be told the result the moment they approve it."
            )
        _remember(_awaiting, sid, key, label or action)

    # Snapshot the row NOW: the turn's end clears ctx._open_steps, but the card outlives the turn.
    step =(getattr(ctx, "_open_steps", None) or {}).get(call_id) or (label or action)
    row = {"id": call_id, "step_kind": step_kind, "action": step, "run_id": getattr(ctx, "run_id", "")}
    if call_id:
        try:
            ctx._deferred_steps.add(call_id)
        except AttributeError:
            ctx._deferred_steps = {call_id}

    task = asyncio.create_task(_run_on_approval(ctx, action, runner, label or action, row, key, family))
    _tasks.setdefault(sid, set()).add(task)

    def _done(t: asyncio.Task) -> None:
        s = _tasks.get(sid)
        if s is not None:
            s.discard(t)
            if not s:
                _tasks.pop(sid, None)

    task.add_done_callback(_done)


def cancel(session_id: str | None) -> int:
    """Cancel this session's pending/running deferred actions (approval wait or the command itself). Called by
    cancel_work / leave so "stop" actually stops a deferred approved shell, not just the work-runner. Returns
    how many were cancelled."""
    tasks = _tasks.pop(session_id or "", set())
    n = 0
    for t in tasks:
        if not t.done():
            t.cancel()
            n += 1
    return n


async def _finish_step(sid: str, row: dict, ok: bool, result: str, outcome: str) -> None:
    """Complete the terminal row this action opened, with what ACTUALLY happened — the same step/done
    shape the loop emits, re-sent with the original id so the store updates that row in place.

    `outcome` is the mark and `ok` is not it: `ok` says the action produced a usable summary, which a
    command that exited 124 does — so every approved command completed its row with a clean run's ✓ and
    every refusal with a real failure's ×. Each caller passes the word its own witness knows: the
    recorded exit code, `refused` for ending without running, `interrupted` for a cancelled process.

    `interrupted` travels twice, as the word and as the older boolean a pre-`outcome` reader
    understands. `result` is the row's phrase, capped at 600 characters; `full` is the whole output."""
    if not row.get("id"):
        return
    try:
        from kotoba.core.events import emit_task
        from kotoba.core.loop import _full_result

        text = (result or "").strip()[:600]
        await emit_task(
            sid, "step", phase="done", id=row["id"], ok=ok, pending=False, outcome=outcome,
            interrupted=(outcome == "interrupted"),
            step_kind=row.get("step_kind"), action=row.get("action"), result=text, text=text,
            full=_full_result(result),
            run_id=row.get("run_id", ""),
        )
    except Exception:
        log.debug("could not complete deferred step row", exc_info=True)


async def _announce(sid: str, has_result: bool, summary: str) -> None:
    """Tell the user NOW that the deferred action finished, instead of ambushing them with it on their next
    message. Deliberately the SAME frame the work-runner emits (`work_done`): the frontend already turns it
    into the __work_done__ sentinel, which makes her voice work_state's pending result once.

    `has_result` rides the wire as the frame's `ok`, and it answers the FRONTEND's question: relay
    `summary` as what happened (True), or report that the work broke with nothing to say (False — the web
    client's announceWork drops the summary and tells the model the work failed). It does NOT answer "did
    the command run"; that word is the step row's `outcome`, completed by _finish_step. So a refusal is
    announced with True and a sentence that itself says nothing ran — False would voice the user's own No
    back to them as a failure, with the honest sentence thrown away."""
    try:
        from kotoba.core.events import emit_task

        await emit_task(sid, "work_done", ok=has_result, summary=(summary or "").strip()[:500])
    except Exception:
        log.debug("could not emit work_done for deferred action", exc_info=True)


def _settle(sid: str, key: str | None, outcome: str) -> None:
    """This action is finished for its user request (ran, declined, failed or cancelled) — stop it being
    approvable again and hand `outcome` to any later re-emission instead of opening a second card."""
    if not key:
        return
    (_awaiting.get(sid) or {}).pop(key, None)
    _remember(_settled, sid, key, outcome)


async def _audit(ctx, action: str, approved: bool, detail: str, verdict: str) -> None:
    """Record on the SAME trail ApprovalGate writes to. Asking the user directly (as this path must)
    used to skip the gate entirely, so the audit log held 387 'work-loop' rows and 12 'user' ones — the
    human approvals that actually authorised a host command were the ones missing.

    Whose decision it was comes from the CARD's own ending, never from a guess made afterwards. Asking
    whether anyone was listening was the right instinct with the wrong witness: it caught the card that
    reached nobody and called the card that expired unread "user" all the same."""
    gate = getattr(ctx, "approval", None)
    if gate is None:
        return
    try:
        await gate.record(action, "exec", approved, approver_for(verdict), detail)
    except Exception:
        log.debug("could not audit deferred action", exc_info=True)


async def _run_on_approval(
    ctx, action: str, runner: Callable[[], Awaitable[str]], label: str, row: dict | None = None,
    key: str | None = None, family: str | None = None,
) -> None:
    sid = getattr(ctx, "session_id", None)
    if not sid:
        return
    row = row or {}
    card: dict = {}   # request_approval fills in the id of the card it opened
    try:
        approved, always = await request_approval(
            sid, action, timeout=_DEFERRED_APPROVAL_TIMEOUT, family=family, card=card
        )
    except asyncio.CancelledError:
        _settle(sid, key, f"“{label}” was cancelled before it ran. Do not start it again unless asked.")
        try:
            from kotoba.core.events import emit_task
            # Name the card: an unnamed clear discards every card on screen, not just this dead one.
            await emit_task(sid, "need_input", mode="clear", request_id=card.get("request_id"))
            await _finish_step(sid, row, False, "cancelled before it ran", "refused")
        except Exception:
            pass
        raise
    except Exception:
        log.warning("deferred approval wait failed for %r", label, exc_info=True)
        approved, always = False, False
        card["verdict"] = UNREACHABLE

    verdict = verdict_of(card, approved)
    if verdict == DISMISSED:
        # A barge-in waved the card away — quiet on purpose, like the cancel ending (module docstring).
        _settle(sid, key, f"The approval card for “{label}” was dismissed unanswered when the user "
                          "spoke. It never ran. Do not start it again unless they ask.")
        await _finish_step(sid, row, False, refusal_row(verdict), "refused")
        return
    await _audit(ctx, action, approved, "decision", verdict)

    if not approved:
        _settle(sid, key, refusal_note(verdict, f"running “{label}”"))
        spoken = f"I didn't run “{label}” — {_UNRUN[verdict]}"
        busy = work_state.is_running(sid)
        if not busy:
            work_state.finish(sid, spoken, [])
        await _finish_step(sid, row, False, refusal_row(verdict), "refused")
        if not busy:
            await _announce(sid, has_result=True, summary=spoken)
        return

    gate = getattr(ctx, "approval", None)
    if gate is not None and (always or card.get("always_exact")):
        try:
            if always:
                await gate.persist_always(action, family)
            else:
                await gate.persist_exact(action, family)
        except Exception:
            pass

    # Settled BEFORE the runner starts, so a re-emission arriving mid-run can't card+run it a second time.
    _settle(sid, key, f"“{label}” was already approved and is running/has run for this request. Do NOT run it again.")
    try:
        with record_exits() as codes:
            summary = await runner()
    except asyncio.CancelledError:
        _settle(sid, key, f"“{label}” was STOPPED while it was running, because the user asked to stop. "
                          "Do not start it again unless they ask.")
        await _finish_step(sid, row, False, "stopped mid-run", "interrupted")
        raise
    except NothingRan as nothing:
        # Approved, and then stopped short. The decision row above already recorded the approval; an
        # `executed` row beside it would say a command reached the host when none did. The mark is
        # `failed` and not `refused` — nobody refused it, the machine could not start it.
        stopped = str(nothing).strip() or f"I couldn't start “{label}”."
        _settle(sid, key, f"{stopped} NOTHING ran — no command reached the machine. Say so, and do not "
                          "start it again unless the user asks.")
        busy = work_state.is_running(sid)
        if not busy:
            work_state.fail(sid, stopped)
        await _finish_step(sid, row, False, stopped, "failed")
        if not busy:
            await _announce(sid, has_result=False, summary="")
        return
    except Exception:
        log.warning("deferred runner failed for %r", label, exc_info=True)
        failed = f"I tried to run “{label}” but it failed to execute"
        _settle(sid, key, f"{failed}. Do not retry it automatically — tell the user and ask what they want.")
        await _audit(ctx, action, True, "executed:failed", verdict)
        busy = work_state.is_running(sid)
        if not busy:
            work_state.fail(sid, failed)
        await _finish_step(sid, row, False, failed, "failed")
        if not busy:
            await _announce(sid, has_result=False, summary="")
        return

    # English on purpose: these go to the MODEL to announce, and `language: auto` renders them in the user's language.
    done = summary or f"Done — I ran “{label}”."
    _settle(sid, key, f"You ALREADY ran this for the user's current request. The result was: {done} "
                      "Do NOT run it again — just answer them from that result.")
    await _audit(ctx, action, True, "executed", verdict)
    busy = work_state.is_running(sid)
    if not busy:
        work_state.finish(sid, done, [])
    await _finish_step(sid, row, True, done, exit_outcome(codes))
    if not busy:
        await _announce(sid, has_result=True, summary=done)
