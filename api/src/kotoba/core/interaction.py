"""Human-in-the-loop interaction over the live call.

She emits a `need_input` event on the session's SSE channel and awaits a per-REQUEST Future until
POST /api/session/{id}/input answers it, so one session can hold several cards at once. This is also
the ApprovalGate's asker: sensitive actions ASK in character rather than defaulting to deny.

An approval has FIVE endings and an (approved, always) tuple can name only one: APPROVED, DECLINED,
UNANSWERED, UNREACHABLE, DISMISSED. Every sentence about an ending derives from that word —
`refusal_note`, `refusal_row`, `approver_for`, `verdict_of` — never re-derived from (False, False).
Human wait is charged to a run SCOPE, never the session, or unrelated cards extend a job's ceiling."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from contextvars import ContextVar

from kotoba.core import events as _events
from kotoba.core.events import emit_emotion, emit_task

log = logging.getLogger("kotoba")

# session_id -> {request_id: Future}, in card-open order. Two requests on ONE session can be outstanding
# at once (a work-mode helper SHARES its parent's session_id) — never collapse this to a single slot.
_pending: dict[str, dict[str, asyncio.Future]] = {}

_seen_sessions: set[str] = set()


def note_turn(session_id: str | None) -> None:
    """Called at the start of each turn. Logs a warning if a NEW session_id appears while another session
    still has an unanswered interaction card — the orphaned-pending-state limitation (accepted, not fixed).

    Swallows everything on purpose. Its callers invoke it unguarded before the turn is under way, so a
    raise here ends the turn before it emits `turn_end` and the client waits forever — a diagnostic must
    never be able to do that."""
    if not session_id:
        return
    try:
        if session_id not in _seen_sessions:
            orphans = [
                s for s, reqs in _pending.items()
                if s != session_id and any(not f.done() for f in reqs.values())
            ]
            if orphans:
                log.warning(
                    "new session_id %s began a turn while %d other session(s) still have an UNANSWERED "
                    "interaction card (%s) — those cards are now orphaned (in-memory state is per-session_id). "
                    "This is a known limitation: those cards will never resolve, and the sessions holding "
                    "them have to be restarted.",
                    session_id, len(orphans), ", ".join(orphans[:3]),
                )
            if len(_seen_sessions) > 2000:  # bound the diagnostic set; sids are short-lived
                _seen_sessions.clear()
            _seen_sessions.add(session_id)
    except Exception:
        log.exception("note_turn diagnostic failed for %s — ignoring", session_id)

# Seconds BLOCKED on the human, per SCOPE (see the module docstring) — the work-runner subtracts its
# own from its COMPUTE budget. A wait made outside every scope is charged to nothing.
_interactive_acc: dict[str, float] = {}
_wait_scope: ContextVar[str | None] = ContextVar("kotoba_interactive_scope", default=None)


def interactive_seconds(scope: str | None) -> float:
    """Time this scope has been blocked on a human (ask_user/ask_secret/approval) since it opened."""
    return _interactive_acc.get(scope or "", 0.0)


def reset_interactive(scope: str | None) -> None:
    _interactive_acc.pop(scope or "", None)


@contextlib.contextmanager
def interactive_scope(scope: str):
    """Charge the human waits made inside this block — and inside the tasks it spawns — to `scope`.

    Only the code the scope encloses is blocked on those humans, which is the whole claim a refund
    rests on. Everything else on the same session goes on paying its own way."""
    reset_interactive(scope)
    token = _wait_scope.set(scope)
    try:
        yield
    finally:
        _wait_scope.reset(token)
        reset_interactive(scope)

DEFAULT_TIMEOUT = 180.0
VOICE_APPROVAL_TIMEOUT = 25.0
LOCAL_VOICE_APPROVAL_TIMEOUT = 60.0
TEXT_APPROVAL_TIMEOUT = DEFAULT_TIMEOUT

APPROVED = "approved"
DECLINED = "declined"
UNANSWERED = "unanswered"
UNREACHABLE = "unreachable"
DISMISSED = "dismissed"

# What `dismiss` resolves a wait with — unforgeable on purpose: a secret box must never "type" a word of ours.
_DISMISSAL = object()


def el_agent_turn(ctx) -> bool | None:
    """Whether an ElevenLabs agent is holding THIS turn's clock open — the only axis that may widen a
    voice window, and the one thing a shared module cannot work out for itself.

    The fact is `ToolContext.el_call_bound`, put there by core.transport at construction: True inside
    `/v1`'s producer, which is the only code that knows an agent is on the other end, and False on our
    own voice WebSocket and in the CLI. None means nobody said — a bare test double — and buys nothing.
    `voice_mode` is NOT this axis: it is an intention stored in settings.yaml while `/v1` is gated on a
    bearer and nothing else, so with the tunnel up an EL turn would take the relaxed branch and have its
    call cut, which the transport marks. One stored field, read by one name."""
    return getattr(ctx, "el_call_bound", None)


def approval_timeout(channel: str | None, *, el_agent: bool | None = None) -> float:
    """How long an approval card may stay open on this channel ('voice' | 'text'), for this transport.

    On an ElevenLabs voice turn it must stay just under the loop's TOOL_TIMEOUT (30s), so a no-answer
    resolves to a clean in-character deny rather than a compute-cancel that abandons the card. A typed
    channel has no such clock and gets DEFAULT_TIMEOUT.

    The 25 s was never a property of speech: it binds a turn through /v1, where EL times a silent turn out
    and re-fires it. On our own socket nothing re-fires, and "25 s to read an `rm -rf`" is hostile, so a
    voice turn known not to be an agent's gets a readable window. Both unknowns fall to the tight side:
    that way costs an early deny, the other an abandoned card or 155 seconds taken off a typed one."""
    if channel == "text":
        return TEXT_APPROVAL_TIMEOUT
    return LOCAL_VOICE_APPROVAL_TIMEOUT if el_agent is False else VOICE_APPROVAL_TIMEOUT


def verdict_of(card: dict | None, approved: bool) -> str:
    """Which of the five endings a finished `request_approval` had — the ONE reading of that out-param.

    A card that came back without one is a test double or a caller predating the word, and at these call
    sites (False, False) has always meant the user declined; reading it any other way would turn every
    double in the tree into a fabricated timeout."""
    return str((card or {}).get("verdict") or (APPROVED if approved else DECLINED))


def approver_for(verdict: str) -> str:
    """WHOSE AUTHORITY the audit row should name for this ending — the trail's answer to "who let this
    run on my machine", which is the one job it exists for.

    A yes and a no are both the user deciding. A card that expired is nobody deciding, and writing that
    down as `approver='user', approved=0` is the trail asserting they saw it and refused it — measured on
    a typed `df -h` that expired unread at 181 s. A card waved away by a barge-in is the
    same trap: the gesture is the user's but the decision never happened, so it gets its own word rather
    than theirs. A card that could not be drawn is the channel failing, which already had a word: 'error'.
    Anything outside the vocabulary is the user, the same convention `verdict_of` keeps, so an asker
    cannot invent an authority by handing back a string."""
    if verdict == UNANSWERED:
        return "expired"
    if verdict == UNREACHABLE:
        return "error"
    if verdict == DISMISSED:
        return "dismissed"
    return "user"


def refusal_row(verdict: str) -> str:
    """The terminal row's sentence for an action that ended at a card, second person, no leading mark.

    One mark (nothing ran) and four sentences, because only one of the four is a decision the user
    made. The row used to read "cancelled by user" over a card that had timed out unread."""
    return {
        DECLINED: "you said no — it never ran",
        UNANSWERED: "no answer on the card — it never ran",
        UNREACHABLE: "there was no way to ask you — it never ran",
        DISMISSED: "the card went away when you spoke — it never ran",
    }.get(verdict, "")


def _reachable(session_id: str, what: str) -> bool:
    """Refuse to open a card nobody can answer.

    With no queue registered the emit is a silent no-op, so the wait runs its whole window and ends in
    a timeout the user reads as a refusal they were never asked for. Guarding in the primitive means a
    new entry point cannot forget. A BLOCKING card needs only this question: every surface that
    registers a queue also wires something that answers one, in its own idiom. The fire-and-forget
    cards are the ones that need a painter, and they ask `events.draws_cards` for themselves."""
    if _events.has_listener(session_id):
        return True
    log.warning("no interaction channel for session %s — refusing to open a card for %s", session_id, what)
    return False


def has_pending(session_id: str | None) -> bool:
    reqs = _pending.get(session_id) if session_id else None
    return bool(reqs) and any(not f.done() for f in reqs.values())


def pending_labels(session_id: str | None) -> list[str]:
    """What the cards still waiting on this session actually ASK, in the order they opened.

    Her voice no longer takes a card away (core.voice.session yields the microphone instead), so a card
    can now be on screen while she answers something else — and a companion who cannot see it would talk
    straight past it. core.context turns this into the one developer note that tells her it is there."""
    reqs = _pending.get(session_id) if session_id else None
    return [getattr(f, "card_label", "") for f in (reqs or {}).values() if not f.done()]


def pending_request_ids(session_id: str | None) -> frozenset[str]:
    """The request_ids of the cards still waiting on this session — identity, not `has_pending`'s truth.

    core.voice.session's microphone yield needs WHICH cards that voice stepped over: the yield covers
    that snapshot and dies with it, so a card that opens later still shuts the mic even while an
    interrupted card stands. That is the caller; `_pending` is private and nobody outside reads it."""
    reqs = _pending.get(session_id) if session_id else None
    return frozenset(rid for rid, fut in (reqs or {}).items() if not fut.done())


def resolve(session_id: str, value, request_id: str | None = None) -> bool:
    """Called by the /input endpoint when the user submits. Returns True if something was waiting.

    Without a request_id (the web UI renders ONE card at a time and doesn't send one) the answer belongs
    to the card currently on screen — the most recently opened request, so match newest-first."""
    reqs = _pending.get(session_id) or {}
    if request_id is not None:
        fut = reqs.get(request_id)
        candidates = [fut] if fut is not None else []
    else:
        candidates = list(reversed(list(reqs.values())))
    for fut in candidates:
        if not fut.done():
            fut.set_result(value)
            return True
    return False


def dismiss(session_id: str | None) -> int:
    """Take back every card this session still has waiting on a human. Returns how many.

    Resolves the FUTURE with the dismissal sentinel and never touches the task behind it. A detached
    approval task has two lives — waiting on the card, then running what was approved — so cancelling it
    could stop a command the user had just said yes to. A resolved Future can only end the wait, and it
    ends it as an ANSWER, so a run under way never notices. Cancelling instead raised a CancelledError
    indistinguishable from the waiter's own, and an inline work card unwound its whole job in silence.

    NOTHING IN PRODUCTION CALLS THIS. It stays because it is the one safe shape for a wave-away gesture,
    and the obvious rewrite is the defect above. Delete it only together with the DISMISSED ending."""
    reqs = _pending.get(session_id or "") or {}
    dropped = 0
    for fut in list(reqs.values()):
        if not fut.done():
            fut.set_result(_DISMISSAL)
            dropped += 1
    return dropped


def _open_request(session_id: str, label: str = "") -> tuple[str, asyncio.Future]:
    """Register a new outstanding request and return its own (request_id, Future).

    The label rides ON the Future rather than in a second dict: it is what `pending_labels` reads to tell
    the model what is waiting on screen, and a parallel structure could outlive the card it describes."""
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    fut.card_label = label
    request_id = uuid.uuid4().hex[:8]
    _pending.setdefault(session_id, {})[request_id] = fut
    return request_id, fut


def _abandon_request(session_id: str, request_id: str) -> None:
    """Unregister a request whose card was never drawn (the need_input emit raised before the wait began).

    Without this the Future stays in `_pending` with no waiter behind it, and everything keyed on
    `has_pending` is wrong for the rest of the session — above all the voice mic hold, which would keep
    the microphone shut with no clock left to ever release it. No production emit raises today; this is
    the belt that keeps that failure impossible rather than merely unlikely."""
    reqs = _pending.get(session_id)
    if reqs is not None:
        reqs.pop(request_id, None)
        if not reqs:
            _pending.pop(session_id, None)


async def _await_response(session_id: str, request_id: str, fut: asyncio.Future, timeout: float):
    """The wait every card shares, and the ONE place a card is cleared for the two endings the caller
    never sees a value for. A cancel pops the Future with nothing emitted, leaving a dead card on the
    screen — and a `kind="key"` box would still save a typed credential — so the `clear` goes out
    here, best-effort because the task is already unwinding; a dismissal clears the same way. A
    timeout returns None and leaves the clear to the caller, which names its own card. The seconds
    blocked are charged to the scope on the way out, whatever the ending."""
    t0 = time.monotonic()
    try:
        resp = await asyncio.wait_for(fut, timeout=timeout)
        if resp is _DISMISSAL:
            with contextlib.suppress(Exception):
                await emit_task(session_id, "need_input", mode="clear", request_id=request_id)
        return resp
    except asyncio.TimeoutError:
        return None
    except asyncio.CancelledError:
        try:
            await emit_task(session_id, "need_input", mode="clear", request_id=request_id)
        except BaseException:
            pass
        raise
    finally:
        scope = _wait_scope.get()
        if scope:
            _interactive_acc[scope] = _interactive_acc.get(scope, 0.0) + (time.monotonic() - t0)
        reqs = _pending.get(session_id)
        if reqs is not None:
            reqs.pop(request_id, None)
            if not reqs:
                _pending.pop(session_id, None)


async def open_input_card(
    session_id: str | None, label: str, kind: str = "text", detail: str | None = None,
    name: str | None = None,
) -> bool:
    """Show the typed-input card and RETURN immediately — does NOT block the turn.

    Used by ask_user over voice: blocking the voice turn while the user types kills the ElevenLabs
    WebSocket. Instead we just open the card; the typed value comes back as a normal user turn (the
    frontend sends it via sendUserMessage), and a 'key' is stored backend-only via POST /input.
    `name` is the storage id for kind='key' cards; the frontend echoes it in the POST /input body.

    Returns whether the card is PAINTED, not whether the frame lands: a surface can consume frames and
    draw no box, and the caller must not then tell the model "I opened a box on screen". Emitting anyway
    (and reporting) rather than refusing keeps this a pure fire-and-forget primitive."""
    if not session_id:
        return False
    await emit_emotion(session_id, "surprised")
    extra: dict = {}
    if detail:
        extra["detail"] = detail
    if name:
        extra["name"] = name
    drawn = _events.draws_cards(session_id)
    await emit_task(
        session_id, "need_input", mode="input", input_kind=kind, label=label, wait=False,
        request_id=uuid.uuid4().hex[:8], **extra,
    )
    return drawn


async def open_link_card(session_id: str | None, url: str, why: str = "") -> bool:
    """Offer to open a link in a NEW TAB — show a card the user accepts or declines, and RETURN at once.

    Used by `open_link` over voice (e.g. the research agent offering its principal source). Non-blocking:
    a long wait inside the ElevenLabs turn kills the WebSocket, so we just show the card; the frontend
    opens the tab locally on accept (window.open) — nothing comes back into the conversation. `why` is the
    one-line explanation shown to the user (what the link is / why it's worth opening).

    Returns whether the card is PAINTED, exactly like open_input_card: a listener is not a surface, and
    the caller must not tell the model "I put a card on screen" for a card nobody drew."""
    if not session_id:
        return False
    await emit_emotion(session_id, "happy")
    drawn = _events.draws_cards(session_id)
    await emit_task(
        session_id, "need_input", mode="open_link", input_kind="link", label=why, url=url, wait=False
    )
    return drawn


async def request_input(
    session_id: str | None, label: str, kind: str = "text", timeout: float = DEFAULT_TIMEOUT,
    detail: str | None = None, name: str | None = None, card: dict | None = None,
) -> str | None:
    """Ask the user to type something and WAIT (blocking) — for non-voice callers only; ask_user over
    voice uses open_input_card instead. (kind: 'text' | 'key' | 'link'). Returns the string or None.

    `wait=True` on the frame tells the frontend to deliver the value through POST /input, which
    resolves the Future, and NOT as a chat message. A timeout clears MY card by its request_id and
    never whatever else is on screen; a dismissal comes back with no value and its card already
    cleared by `_await_response`.

    `card["verdict"]` names WHICH ending produced a None, the way request_approval does: four endings
    that a bare None cannot tell apart, and only one of them is a decision the user made."""
    if not session_id or not _reachable(session_id, f"input {kind!r}"):
        if card is not None:
            card["verdict"] = UNREACHABLE
        return None
    await emit_emotion(session_id, "surprised")
    request_id, fut = _open_request(session_id, label)
    extra: dict = {}
    if detail:
        extra["detail"] = detail
    if name:
        extra["name"] = name
    try:
        await emit_task(
            session_id, "need_input", mode="input", input_kind=kind, label=label, wait=True,
            request_id=request_id, **extra,
        )
    except BaseException:
        _abandon_request(session_id, request_id)
        raise
    resp = await _await_response(session_id, request_id, fut, timeout)
    if resp is None:
        await emit_task(session_id, "need_input", mode="clear", request_id=request_id)
    if resp is _DISMISSAL:
        if card is not None:
            card["verdict"] = DISMISSED
        return None
    if resp is None:
        if card is not None:
            card["verdict"] = UNANSWERED
        return None
    value = resp.get("value") if isinstance(resp, dict) else (resp if isinstance(resp, str) else None)
    if card is not None:
        # Three endings, not two. A value is the answer and an EMPTY box is a choice to give nothing —
        # but no value at all is nobody having answered, and reporting that as their decision is the
        # trail asserting they saw the card and refused it.
        card["verdict"] = APPROVED if value else (DECLINED if value == "" else UNANSWERED)
    return value


async def request_approval(
    session_id: str | None, action: str, timeout: float | None = None, channel: str | None = None,
    family: str | None = None, card: dict | None = None, notice: dict | None = None,
    el_agent: bool | None = None,
) -> tuple[bool, bool]:
    """Ask the user to approve a sensitive action (✓/× card + voice). Returns (approved, always): `approved`
    only on an explicit yes, `always` when they chose "always allow". WHICH no it was goes into
    `card["verdict"]`, for callers that must not report a decision the user never made; it rides that
    out-param because the tuple is what every call site is built on.

    `family` is the unit an "always allow" would grant, and the card names it — "always allow" over
    `npm` and over ALL Python are wildly different promises. Whatever the gate would refuse to persist
    gets `can_always=False`, so no card offers a button the backend will not honour; `can_always_exact`
    is the narrow grant beside it and `always_note` says why the broad key is missing. The `request_id`
    is written into `card` as soon as it exists, so a clear names ONE card and never the whole screen."""
    from kotoba.core.approval import always_note, command_family, persistable, persistable_exact

    if not session_id or not _reachable(session_id, f"approval for {action!r}"):
        if card is not None:
            card["verdict"] = UNREACHABLE
        return (False, False)
    if timeout is None:
        timeout = approval_timeout(channel, el_agent=el_agent)
    await emit_emotion(session_id, "confused")
    request_id, fut = _open_request(session_id, action)
    if card is not None:
        card["request_id"] = request_id
    fam = family if family is not None else command_family(action)
    try:
        await emit_task(
            session_id, "need_input", mode="approval", label=action, request_id=request_id,
            family=fam, can_always=persistable(action, fam),
            can_always_exact=persistable_exact(action, fam), always_note=always_note(action, fam),
            **({"notice": notice} if notice else {}),
        )
    except BaseException:
        _abandon_request(session_id, request_id)
        raise
    resp = await _await_response(session_id, request_id, fut, timeout)
    if resp is None:
        await emit_task(session_id, "need_input", mode="clear", request_id=request_id)
    always_exact = False
    if isinstance(resp, dict):
        approved, always = bool(resp.get("approved")), bool(resp.get("always"))
        always_exact = bool(resp.get("always_exact"))
    elif isinstance(resp, bool):
        approved, always = resp, False
    else:
        approved, always = False, False
    if card is not None:
        if resp is _DISMISSAL:
            card["verdict"] = DISMISSED
        else:
            card["verdict"] = APPROVED if approved else (UNANSWERED if resp is None else DECLINED)
        card["always_exact"] = always_exact and approved
    return (approved, always)


def note_no_run(ctx, verdict: str) -> None:
    """Witness that the tool call running right now ended at a card and executed NOTHING.

    core.deferred_exec keeps the same record for the approvals that outlive a turn, and core.loop reads
    both before it draws a row: an action that never ran must not be marked ✓, and the audit trail must
    not gain an "executed" line for it. Keyed by ctx.call_id, the id of the Responses call in flight."""
    call_id = str(getattr(ctx, "call_id", "") or "")
    if not call_id:
        return
    try:
        ctx._approval_verdicts[call_id] = verdict
    except AttributeError:
        ctx._approval_verdicts = {call_id: verdict}


def no_run_verdict(ctx, call_id: str) -> str:
    """The verdict that ended `call_id` at a card, or '' when this call was never stopped by one."""
    return (getattr(ctx, "_approval_verdicts", None) or {}).get(call_id or "", "")


def refusal_note(verdict: str, what: str) -> str:
    """What the MODEL is told when an approval ended without a yes — four sentences, because only one of
    these endings is a decision the user made, and she must never report the others as one.

    English on purpose: it goes to the model to relay, and `language: auto` renders it in the user's
    language. `what` names the action in the -ing form ("installing 'x'")."""
    if verdict == DECLINED:
        return (f"The user said NO to {what}, so it did not happen. Tell them you left it alone, and do "
                "not ask again for this request.")
    if verdict == UNANSWERED:
        return (f"The approval card for {what} expired with no answer, so it did not happen. Nobody "
                "decided anything — assume they never even saw it. Tell them it timed out on screen and "
                "offer to ask again. This was a timeout, not their choice: report it as one.")
    if verdict == DISMISSED:
        return (f"The approval card for {what} was dismissed unanswered when the user started speaking, "
                "so it did not happen. Nobody decided anything — this was NOT a refusal. Do not reopen "
                "the card on your own; carry on without it if you can, and mention that it still needs "
                "their go-ahead so they can ask for it again.")
    return (f"There was no way to put the approval card for {what} on their screen, so it did not "
            "happen. Tell them plainly that you could not ask.")


def no_run_result(ctx, what: str, fallback: str) -> str:
    """What a tool hands the MODEL after `ctx.approval.confirm` came back False: the sentence for the
    ending the asker witnessed, and `fallback` only when no ending was recorded — a gate wired without
    an asker (a test double, a bare `ApprovalGate()`).

    `shell` and `execute_code` used to return one canned line for every ending, "I didn't get the
    go-ahead", while the row beside it read "you said no — it never ran". Told only that no go-ahead
    had come, the model reported that NO as a permission still pending and invited the user to
    "authorise it again" — one they had never given once. A refusal, an expired card, a card nobody
    could draw and a card waved away are four different facts; the row tells them apart, so does this."""
    verdict = no_run_verdict(ctx, str(getattr(ctx, "call_id", "") or ""))
    return refusal_note(verdict, what) if verdict else fallback


async def ask_approval(ctx, action: str, *, family: str | None = None,
                       notice: dict | None = None) -> str:
    """Approve an action for the tool call in flight, and return which of the five endings happened.

    The door tools use instead of calling request_approval themselves. It reads the clock off the ctx —
    the channel this turn reaches the user by, and the transport that decides whether anything is timing
    the turn out — so a tool cannot forget it and hand a typed reader the 25-second voice window; and on
    anything other than a yes it records the witness core.loop's terminal row and audit trail read, which
    is the other half of a refusal that no longer paints ✓. `notice` rides through to the card
    unchanged (see request_approval)."""
    card: dict = {}
    approved, _always = await request_approval(
        getattr(ctx, "session_id", None), action, channel=getattr(ctx, "channel", None),
        family=family, card=card, notice=notice, el_agent=el_agent_turn(ctx),
    )
    verdict = verdict_of(card, approved)
    if verdict != APPROVED:
        note_no_run(ctx, verdict)
    return verdict
