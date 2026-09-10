"""cronjob — schedule reminders and proactive nudges (create, list, remove, skip in one tool).

One tool so it does not inflate the schema; the scheduler fires due jobs. A REPEAT IS THE USER'S WORD,
NEVER THE MODEL'S REFLEX: live QA found "remind me in two minutes" stored hourly, so `once` is the
first enum value and the parameter says it is the answer. A message asking for two reminders is judged
PER REMINDER, the tool abstains when it cannot tell, and `_cadence_note` SAYS what it decided.

A cadence the worker cannot fire is a promise nobody keeps — `monthly` rang ONCE while the tool said
it repeated — so anything outside the three real cadences maps to one-time. `skip` moves `due_at` one
period on, but ONLY within half a period, or acknowledging a fire five minutes late eats tomorrow's."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

SCHEMA = {
    "type": "function",
    "name": "cronjob",
    "description": (
        "Schedule, list, or cancel reminders for the user. To create: give `message` plus either "
        "`in_minutes` (e.g. 90) or `due_at` (ISO-8601 UTC you compute from the current time). "
        "PREFER `in_minutes` for any RELATIVE time ('in 20 min', 'in 2 hours') — don't hand-compute a UTC "
        "`due_at` for those. Use `due_at` only for an ABSOLUTE clock time ('at 8am'), converting from the "
        "user's LOCAL time (given in the prompt) to UTC — the clock number is LOCAL, not UTC. "
        "A reminder rings ONCE. `recurring` is where you would say otherwise, and its answer is 'once' "
        "unless you are quoting the user — read that parameter before you fill it. "
        "Create each reminder exactly ONCE per request: after a successful create, you are DONE — do NOT "
        "list/remove/re-create it to 'fix' it, and do NOT call this tool again for the same reminder. "
        "action='list' shows scheduled ones; action='remove' cancels by `id`. "
        "action='skip' marks ONE occurrence as already done — use it when the user says they already did "
        "the thing BEFORE its reminder fires ('ya la llamé', 'done for today'): a recurring reminder "
        "moves to its NEXT slot instead of firing this period; a one-time one is cancelled. If it already "
        "fired this period there is nothing to skip — do NOT call it then. Give `id` from the list, or "
        "`message` naming the reminder."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "list", "remove", "skip"], "description": "Default 'create'."},
            "message": {"type": "string", "description": "What to remind the user about (create), or which reminder is done (skip)."},
            "in_minutes": {"type": "integer", "description": "Minutes from now until it fires (create)."},
            "due_at": {"type": "string", "description": "ISO-8601 UTC time it should fire (create)."},
            "recurring": {
                "type": "string",
                "enum": ["once", "hourly", "daily", "weekly"],
                "description": (
                    "How often it rings. 'once' — the answer for nearly every reminder, and the one to "
                    "send whenever you are not quoting the user. Only their own words can put another "
                    "value here: a time of day is not a repeat, a habit of theirs is not a repeat, and "
                    "your guess is not a repeat."
                ),
            },
            "id": {"type": "string", "description": "Cronjob id to cancel (remove) or acknowledge (skip)."},
        },
        "required": [],
        "additionalProperties": False,
    },
}
BUILT_IN = False
TOOLSET = "cron"
RISK = "write"

ANNOUNCE = "Let me set that reminder for you..."
HEARTBEAT = ["Putting it on the calendar..."]
COMPLETE = "Done — I'll remind you!"
FAIL = "I couldn't set that reminder — when did you want it?"
EXPRESSIONS = {"focus": "determined", "done": "happy", "fail": "confused"}


# ~10-year horizon: a huge `in_minutes` would OverflowError on timedelta.
_MAX_MINUTES = 10 * 365 * 24 * 60


def _parse_due(args: dict) -> str | None:
    # `in_minutes` counts ONLY when POSITIVE: the model fills `in_minutes: 0` as a schema default even when
    # it means `due_at` — treating 0 as "now" fired the reminder immediately AND shadowed a good `due_at`.
    im = args.get("in_minutes")
    if im is not None:
        try:
            mins = int(im)
        except (TypeError, ValueError):
            mins = None
        if mins is not None and mins > 0:
            if mins > _MAX_MINUTES:
                return None  # absurdly far → graceful "when did you want it?" FAIL
            try:
                return (datetime.now(timezone.utc) + timedelta(minutes=mins)).strftime("%Y-%m-%d %H:%M:%S")
            except (OverflowError, OSError, ValueError):
                return None
    raw = (args.get("due_at") or "").strip()
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OverflowError, OSError):
            return None
    return None


_DUP_WINDOW_SECONDS = 120

import re as _re


def _norm(s: str) -> set[str]:
    """Word set of a message, lowercased + punctuation-stripped — for fuzzy 'same reminder' matching."""
    return {w for w in _re.findall(r"\w+", (s or "").lower()) if w}


def _similar(a: str, b: str) -> bool:
    """Same reminder intent? Exact (normalized) match, one contained in the other, or ≥50% token overlap.
    Catches the model's wording drift across a turn-storm ('respire profundo' / 'Respira profundo, solo
    una vez') while keeping clearly-different reminders ('tomar agua' vs 'llamar a mamá') distinct."""
    wa, wb = _norm(a), _norm(b)
    if not wa or not wb:
        return (a or "").strip().lower() == (b or "").strip().lower()
    if wa == wb or wa <= wb or wb <= wa:
        return True
    return len(wa & wb) / len(wa | wb) >= 0.5


_CADENCES = ("hourly", "daily", "weekly")
_NOT_A_REPEAT = frozenset({"once", "one-time", "one time", "onetime", "single", "none",
                           "no", "never", "null", "false", "0"})


def _cadence(raw) -> tuple[str | None, bool]:
    """(the cadence the worker can fire, whether the model named one it cannot).

    'once' is in the enum so the model has a way to SAY one-time. Anything unknown — 'monthly',
    'yearly', 'every 2 days' — used to be stored verbatim, and `core.cron._RECUR` has no entry for it,
    so the job rang once while this tool announced "it repeats monthly". It becomes one-time and the
    answer says which case it was, rather than promising a repeat that will never come."""
    v = " ".join(str(raw or "").split()).lower()
    if not v or v in _NOT_A_REPEAT:
        return None, False
    if v in _CADENCES:
        return v, False
    return None, True


# Overrides the model's reflexive 'daily'/'hourly' tagging when the user's own words said "once".
_ONE_TIME = ("solo una vez", "sólo una vez", "una sola vez", "una única vez", "just once", "only once",
             "once", "one time", "one-time", "single time", "not recurring")

_TIME_NOUN = (
    r"(?:d[íi]as?|semanas?|mes(?:es)?|ma[ñn]anas?|noches?|tardes?|horas?|minutos?|"
    r"lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bados?|domingos?|"
    r"days?|weeks?|months?|mornings?|nights?|evenings?|afternoons?|hours?|minutes?|"
    r"mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|sundays?)"
)
_WEEKDAY_ES = r"(?:lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bados|domingos)"
_WEEKDAY_EN = r"(?:mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|sundays)"
# A repetition the USER asked for, in either language — word-anchored and tied to a time noun.
_RECUR_RE = _re.compile(
    r"\b(?:daily|weekly|hourly|monthly|nightly|recurring|recurrent)\b"
    r"|\b(?:diariamente|semanalmente|mensualmente|quincenalmente|recurrente|peri[óo]dicamente)\b"
    r"|\b(?:diari[oa]s?|semanal(?:es)?|mensual(?:es)?)\b|\ba diario\b"
    rf"|\bcada\s+(?:\d+\s+|dos\s+|tres\s+|otr[oa]s?\s+)?{_TIME_NOUN}\b"
    rf"|\btod[oa]s\s+l[oa]s\s+{_TIME_NOUN}\b|\bl[oa]s\s+{_WEEKDAY_ES}\b"
    rf"|\bevery\s+(?:other\s+)?(?:\d+\s+)?{_TIME_NOUN}\b|\beach\s+{_TIME_NOUN}\b"
    rf"|\bon\s+{_WEEKDAY_EN}\b|\b{_WEEKDAY_EN}\s+(?:and|y)\s+{_WEEKDAY_EN}\b"
    rf"|\b(?:una|dos|tres|\d+)\s+(?:vez|veces)\s+(?:al|a la|por)\s+{_TIME_NOUN}\b"
    rf"|\b(?:once|twice|\d+\s+times)\s+(?:an?|per)\s+{_TIME_NOUN}\b"
    r"|\bque se repit\w*\b"
    r"|\b(?:recu[ée]rda\w*|av[íi]sa\w*|recordarme|avisarme)\s+siempre\b|\balways\s+remind\b",
    _re.IGNORECASE,
)
# A repeat word inside a REFUSAL of one. Checked first, so "no repitas" is one-time, not hourly.
_NO_REPEAT_RE = _re.compile(
    r"\b(?:no|not|sin|nunca|never|don'?t|do not|doesn'?t)\s+"
    r"(?:me\s+|lo\s+|la\s+|it\s+|se\s+|que\s+se\s+)?(?:repit\w*|repet\w*|repeat\w*|recurring)\b",
    _re.IGNORECASE,
)
_ASKS_FOR_ONE_RE = _re.compile(
    r"\b(?:recu[ée]rda\w*|record[áa]\w*|recordatorio|av[íi]sa\w*|alarma|alerta|"
    r"remind\w*|reminder|alert|ping me|wake me)\b",
    _re.IGNORECASE,
)
_SEGMENT_SPLIT_RE = _re.compile(
    r"[,;\n]+|\by\s+(?:aparte|tambi[ée]n|luego|adem[áa]s)\b|\band\s+(?:also|then)\b", _re.IGNORECASE)
_TIME_WORD_RE = _re.compile(
    rf"^(?:{_TIME_NOUN}|cada|todos|todas|siempre|always|every|each|daily|weekly|hourly|monthly)$",
    _re.IGNORECASE,
)
_LOOKBACK = 2


def _content(text: str | None) -> set[str]:
    """The words that NAME a reminder: long enough to carry meaning, minus the ones EVERY reminder has
    (the asking verb, the cadence, the clock). What's left is the errand itself — "copias seguridad"
    against "llamar banco" — which is the only thing that can tell two reminders apart."""
    return {w for w in _re.findall(r"\w+", (text or "").lower())
            if len(w) >= 4 and not _TIME_WORD_RE.match(w) and not _ASKS_FOR_ONE_RE.fullmatch(w)}


def _reminder_chunks(text: str | None) -> list[str]:
    """One message cut into the reminders it actually asks for — usually one, sometimes two.

    A new chunk starts only where a segment BOTH asks for a reminder and names an errand of its own, so
    "recuérdame cada lunes, a las nueve, revisar las copias" stays one reminder (its middle segments name
    nothing) and "recuérdame llamar al banco, recuérdamelo cada día" stays one too (the second segment
    restates the first). Anything else is glued to the chunk before it: over-splitting would hand
    _attribute two halves of one errand and it would refuse to choose."""
    chunks: list[str] = []
    for seg in _SEGMENT_SPLIT_RE.split(text or ""):
        seg = (seg or "").strip()
        if not seg:
            continue
        if chunks and not (_ASKS_FOR_ONE_RE.search(seg) and _content(seg)):
            chunks[-1] += " " + seg
        else:
            chunks.append(seg)
    return chunks


def _attribute(message: str, chunks: list[str]) -> str | None:
    """Which of several reminders in one message is THIS one? None when the words cannot say.

    Matched on the errand words, NOT on position and not on a substring: the `message` argument is the
    model's own paraphrase ("Llamar al banco" for "llamar al banco", but just as easily "Call the bank"),
    so a span match would be brittle in exactly the direction that costs most. A tie or a blank names
    nobody, and None is not a failure here — it is the abstain the caller wants."""
    words = _content(message)
    if not words or not chunks:
        return None
    scored = sorted(((len(words & _content(c)), c) for c in chunks), key=lambda s: s[0], reverse=True)
    if scored[0][0] == 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return scored[0][1]


def _wants_one_time(user_text: str | None) -> bool:
    t = (user_text or "").lower()
    if not t:
        return False
    if _NO_REPEAT_RE.search(t):
        return True
    if _RECUR_RE.search(t):
        return False  # "una vez al día / once a day" is RECURRING despite containing "una vez"/"once"
    return any(p in t for p in _ONE_TIME)


def _asked_to_repeat(user_text: str | None) -> bool:
    """Did the user's own words ask for a REPEAT? Only then may `recurring` survive.

    The schema already says one-time-by-default in capitals and the model sets 'hourly' anyway: live QA
    measured a plain "remind me in two minutes" stored as hourly, next to a "call my sister" repeating
    every hour since July. `_wants_one_time` was the guard, but opt-out — it needs the user to say "once",
    and nobody says "once" when they mean once. So the burden moves: no repetition word in their message,
    no repetition. A missed weekly is one sentence to fix; an unasked-for hourly outlives the conversation."""
    t = (user_text or "").lower()
    if not t or _NO_REPEAT_RE.search(t):
        return False
    return bool(_RECUR_RE.search(t))


def _said(ctx) -> str:
    """The user's latest utterance that HAS words, which is not always the latest one.

    A turn whose newest user message carries no text — a photo or a file sent with no caption — leaves
    `ctx.user_text` empty while the messages before it are still there in `ctx.user_texts`. The guard
    ran under `if ctx.user_text`, so that turn skipped it whole and the model's tag went to the
    database announcing itself as a daily repeat (measured live)."""
    latest = getattr(ctx, "user_text", None)
    if latest and latest.strip():
        return latest
    for t in (getattr(ctx, "user_texts", None) or []):
        if t and t.strip():
            return t
    return ""


def _repeat_was_asked(ctx) -> bool:
    """The same question over the last few user messages, because scheduling is a CONVERSATION.

    "remind me every morning to drink water" → "what time?" → "eight" puts the cadence one turn behind
    the create, and reading only ctx.user_text dropped it. The lookback stops the moment the latest
    message asks for a reminder of its own, which is what keeps an old "cada día" from making every
    fresh one-off repeat forever.

    No utterance at all answers NO. That branch used to trust the model instead, guarding a cron-driven
    create that does not exist — this tool is the only writer of the table, and all four transports fill
    `user_texts` from the turn's own messages."""
    latest = _said(ctx)
    if _asked_to_repeat(latest):
        return True
    if not latest or _ASKS_FOR_ONE_RE.search(latest):
        return False
    earlier = [t for t in (getattr(ctx, "user_texts", None) or []) if t and t != latest]
    return any(_asked_to_repeat(t) for t in earlier[:_LOOKBACK])


def _repeat_survives(ctx, message: str) -> tuple[bool, str]:
    """May THIS reminder keep the `recurring` the model tagged it with — and if not, why not.

    The guard used to read the whole message, which is wrong the moment one asks for two reminders:
    "CADA LUNES revisa las copias, y aparte EN UNA HORA llama al banco" gave the bank call a repeat in
    either creation order. So a multi-reminder message is judged per reminder, and when the repeat cannot
    be tied to THIS one the answer is no: an unasked-for repeat outlives the conversation.

    A REFUSAL of a repeat is deliberately NOT scoped this way, the one asymmetry here: telling "no
    repitas el aviso del informe" from "cada día a las ocho" needs a split that would cut the second
    and strip a repeat the user really asked for. So a refusal keeps the whole message and errs one-time."""
    said = _said(ctx)
    chunks = _reminder_chunks(said)
    if len(chunks) > 1:
        mine = _attribute(message, chunks)
        if mine is None:
            return False, "ambiguous"
        if _wants_one_time(mine):
            return False, "said_once"
        return _asked_to_repeat(mine), "not_asked"
    if _wants_one_time(said):
        return False, "said_once"
    return _repeat_was_asked(ctx), "not_asked"


def _stored_fact(recurring: str | None, downgraded: str) -> str:
    """What the ROW says — the half that corrects her before she speaks.

    The result used to read "Reminder set for … UTC: Stretch" whatever was stored, so a downgraded tag
    was invisible: she confirmed "done — every day at eight" over a one-shot row and the user found out
    on day two, when nothing rang."""
    if recurring:
        return f" It repeats {recurring}."
    if downgraded == "ambiguous":
        return (" SAVED AS A ONE-TIME REMINDER: that message asked for more than one reminder and the "
                "repeat in it could not be tied to THIS one, so this one rings once.")
    if downgraded == "said_once":
        return " SAVED AS A ONE-TIME REMINDER: their own words said not to repeat it, so it rings once."
    if downgraded == "unsupported":
        return (" SAVED AS A ONE-TIME REMINDER: that cadence is not one I can schedule — only hourly, "
                "daily and weekly — so it rings once.")
    if downgraded:
        return (" SAVED AS A ONE-TIME REMINDER: it rings once and does NOT repeat, because they never "
                "asked for a repeat.")
    return " It rings once and does not repeat."


def _speech_note(recurring: str | None, downgraded: str, asked: bool) -> str:
    """What she is ALLOWED to SAY about it — a different job, on a different reader, kept apart.

    The row is already written when this is composed; nothing here can change it, and everything here
    steers one sentence out of her mouth. Merged into the fact above it, it read as one long correction
    and carried the conversation's densest run of cadence vocabulary into the context the NEXT create
    is generated from — the very parameter this file is trying to keep empty.

    Every branch answers a DISCREPANCY. When there is none the last branch returns nothing: the model
    asked for a one-off and got one, so "it rings once" is not news, it is the user's own sentence read
    back. The row still SAYS it rings once; that half informs her, this half only ever corrects her."""
    if recurring:
        return " Tell them how often it repeats."
    if downgraded == "ambiguous":
        return " Say it rings once, and ask whether they want THIS one repeated."
    if downgraded == "said_once":
        return (" Say that plainly — and if that 'no' was about a DIFFERENT reminder they will tell you, "
                "and you can set the repeat then.")
    if downgraded == "unsupported":
        return " Say it rings once, and tell them which repeats you can do if they want one."
    if downgraded:
        return (" Say it's set for that one time. Do NOT describe it as repeating. If they want it to "
                "repeat they only have to say so.")
    if asked:
        return (" They sounded like they wanted this one to repeat — it is NOT set to, so ask them "
                "before you promise it.")
    return ""


def _cadence_note(recurring: str | None, downgraded: str, asked: bool = False) -> str:
    """The two halves, joined for the one string a tool gets to answer with. Re-reads the cadence so a
    row written before `_cadence` existed is described by what the worker will really do with it."""
    cad, unsupported = _cadence(recurring)
    if unsupported:
        downgraded = "unsupported"
    return _stored_fact(cad, downgraded) + _speech_note(cad, downgraded, asked)


def _where_it_rings() -> str:
    """Cron delivers into the event queues of whichever process ticks, and the bot starts with none,
    so a reminder asked for in a channel rings on a screen the asker may not have open — or waits
    forever on a Discord-only install. Saying "Reminder set" alone was a promise to nobody."""
    from kotoba.discord import state

    if not state.runtime_live():
        return ""
    return (" It will not ring in this Discord channel: reminders ring where your screen is, the"
            " browser or the terminal, and wait if neither is open.")


async def _find_duplicate(ctx, message: str, due_at: str) -> dict | None:
    """An active, not-yet-fired reminder for THIS session that's the same intent the model just re-created on
    a redundant ElevenLabs turn. Match = overlapping due time (within _DUP_WINDOW_SECONDS) AND a similar
    message — `_similar` is the part that absorbs the model's wording/language drift during a turn-storm.
    Two genuinely-different reminders, typed apart, stay distinct.

    There is no second, time-of-creation arm: this docstring described one ("or it was created moments ago,
    within _RAPID_FIRE_SECONDS") that no longer exists anywhere in the tree, so a reader looking for the
    knob found nothing and a reader trusting the text expected a match the code cannot make."""
    try:
        new_dt = datetime.strptime(due_at, "%Y-%m-%d %H:%M:%S")
        jobs = await ctx.db.list_cronjobs()
    except Exception:
        return None
    for j in jobs:
        if j.get("session_id") != ctx.session_id or j.get("fired_at"):
            continue
        try:
            if abs((datetime.strptime(j["due_at"], "%Y-%m-%d %H:%M:%S") - new_dt).total_seconds()) > _DUP_WINDOW_SECONDS:
                continue  # different time slot → a real, separate reminder
        except (ValueError, TypeError):
            continue
        if _similar(message, j.get("message") or ""):
            return j
    return None


async def execute(args: dict, ctx) -> str:
    args = args or {}
    action = (args.get("action") or "create").strip()

    if action == "list":
        jobs = await ctx.db.list_cronjobs()
        if not jobs:
            return "You don't have any reminders set."
        # Only a cadence the worker can fire is worth naming — reading back "monthly" off an old row is
        # the same promise nobody keeps, arriving by the other door.
        lines = []
        for j in jobs:
            cad = _cadence(j.get("recurring"))[0]
            lines.append(f"- [{j['id'][:6]}] {j['message']} (at {j['due_at']} UTC"
                         + (f", {cad}" if cad else "") + ")")
        return "\n".join(lines)

    if action == "remove":
        from kotoba.core import pending_reminder

        jid = (args.get("id") or "").strip()
        if not jid:
            return "Which reminder should I cancel? Tell me its id (from the list)."
        for j in await ctx.db.list_cronjobs():
            if j["id"].startswith(jid):
                await ctx.db.deactivate_cronjob(j["id"])
                pending_reminder.discard(j["message"])
                return f"Cancelled: {j['message']}."
        return "I couldn't find a reminder with that id."

    if action == "skip":
        from kotoba.core import pending_reminder
        from kotoba.core.cron import _RECUR, _next_due

        jid = (args.get("id") or "").strip()
        msg = (args.get("message") or "").strip()
        jobs = await ctx.db.list_cronjobs()
        if jid:
            job = next((j for j in jobs if j["id"].startswith(jid)), None)
        elif msg:
            cand = [j for j in jobs if _similar(msg, j.get("message") or "")]
            job = cand[0] if len(cand) == 1 else None
        else:
            job = None
        if job is None:
            return "Which reminder is done? Tell me its id (from the list)."
        pending_reminder.discard(job["message"])
        recurring = job.get("recurring")
        if recurring not in _RECUR:
            await ctx.db.deactivate_cronjob(job["id"])
            return f"Done — that was a one-time reminder, so it's off: {job['message']}."
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            due = datetime.strptime(job["due_at"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            due = now
        if due - now > _RECUR[recurring] / 2:
            return f"I'll keep the schedule as it is — the next one is {job['due_at']} UTC: {job['message']}."
        nxt = _next_due(job["due_at"], _RECUR[recurring], now)
        if not await ctx.db.reschedule_cronjob(job["id"], nxt, job["due_at"]):
            pending_reminder.discard(job["message"])
            return f"Marked done for now: {job['message']}."
        return f"Done for now — I'll remind you again at {nxt} UTC: {job['message']}."

    message = (args.get("message") or "").strip()
    due_at = _parse_due(args)
    if not message or not due_at:
        return None
    recurring, unsupported = _cadence(args.get("recurring"))
    downgraded = ""
    asked_anyway = False
    if recurring or unsupported:
        keep, why = _repeat_survives(ctx, message)
        if not keep:
            recurring, downgraded = None, why
        elif unsupported:
            recurring, downgraded = None, "unsupported"
    else:
        # The other direction, and it never writes: a repeat asked for and not tagged stays one-time,
        # because a cadence read out of prose is the bug this file's regex was written against.
        asked_anyway = _repeat_survives(ctx, message)[0]
    dup = await _find_duplicate(ctx, message, due_at)
    if dup:
        return (f"Reminder set for {dup['due_at']} UTC: {dup['message']}."
                + _cadence_note(dup.get("recurring"),
                                "" if dup.get("recurring") else downgraded, asked_anyway)
                + _where_it_rings())
    await ctx.db.insert_cronjob(
        message=message, due_at=due_at, session_id=ctx.session_id, recurring=recurring
    )
    return (f"Reminder set for {due_at} UTC: {message}."
            + _cadence_note(recurring, downgraded, asked_anyway)
            + _where_it_rings())
