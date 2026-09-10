"""A reminder repeats only if the user's own words asked it to.

A plainly one-off spoken request was stored with `recurring='hourly'` — the schema already demanded
one-time-by-default in capitals, but the model set the tag anyway, so the check has to be in the
code, not the prompt.

Measured again later: two more one-off requests came back `daily` and then `hourly`, a different
invention each time. The schema had never given the model a way to SAY one-time — its three enum
values were all yes — so the guard is what holds this, not the prompt."""
from __future__ import annotations

import asyncio

import pytest

from kotoba.db.database import Database
from kotoba.tools import ToolContext
from kotoba.tools.action import cronjob


def _stored_recurring(tmp_path, said, name, **args):
    async def go():
        db = Database("sqlite:///" + str(tmp_path / f"{name}.db"))
        await db.connect()
        try:
            ctx = ToolContext(db=db, session_id="s", mode="work")
            ctx.user_text = said
            await cronjob.execute({"action": "create", "in_minutes": 2, **args}, ctx)
            jobs = await db.list_cronjobs()
            return jobs[0]["recurring"] if jobs else "NO JOB"
        finally:
            await db.close()

    return asyncio.run(go())


_PARAM = cronjob.SCHEMA["parameters"]["properties"]["recurring"]


def test_the_parameter_offers_a_way_to_say_one_time():
    """The invitation, and the direct analogue of `shell`'s "(sh -c)": an enum of hourly|daily|weekly is
    three ways to answer yes and none to answer no, so a model that emits the field at all cannot say
    what almost every reminder is. 'once' goes first because a guess lands on the first value."""
    assert _PARAM["enum"][0] == "once"
    assert set(_PARAM["enum"]) == {"once", "hourly", "daily", "weekly"}
    assert "once" in _PARAM["description"]


def test_the_description_stops_supplying_the_cadences():
    """The other half. `recurring` used to be surrounded by its own answers — 'every day', 'cada semana',
    'todas las mañanas', 'every day/week/hour' — in the tool description and again in the downgrade note.
    Whether that primes is generation and cannot be tested here; removing it costs nothing, since the
    model knows what "cada semana" means without being handed it next to the parameter."""
    text = cronjob.SCHEMA["description"] + " " + _PARAM["description"]
    for word in ("every day", "cada semana", "todas las mañanas", "every week", "cada día"):
        assert word not in text.lower(), f"the schema still hands the model {word!r}"


@pytest.mark.parametrize("value", ["once", "one-time", "none", "no", "never", ""])
def test_a_model_saying_one_time_out_loud_stores_one_time(tmp_path, value):
    """Every way a model might try to say "no repeat" — including leaving the field blank — is read as
    one-time, so the answer is never lost to a spelling."""
    got = _stored_recurring(tmp_path, "recuérdame en dos minutos sacar la basura",
                            f"once{value or 'blank'}", message="Sacar la basura", recurring=value)
    assert got is None


@pytest.mark.parametrize("i,value", list(enumerate(["monthly", "yearly", "every 2 days", "biweekly"])))
def test_a_cadence_the_worker_cannot_fire_is_never_promised(tmp_path, i, value):
    """`core.cron._RECUR` knows three cadences. Anything else was stored verbatim, so the worker rang it
    ONCE and marked it fired while this tool had already answered "It repeats monthly — you can tell
    them that". She promised a repeat that could not happen; the user finds out by it not happening."""
    from kotoba.core.cron import _RECUR

    assert value not in _RECUR
    got = _stored_recurring(tmp_path, "recuérdame cada mes revisar el alquiler", f"unsup{i}",
                            message="Revisar el alquiler", recurring=value)
    assert got is None


def test_an_unschedulable_cadence_is_explained_not_hidden(tmp_path):
    """Refusing the cadence is not enough — the return has to TELL her it became one-time, in words she
    can pass on, and without repeating the cadence name back, which is how the false promise is made."""
    async def go():
        db = Database("sqlite:///" + str(tmp_path / "unsup-note.db"))
        await db.connect()
        try:
            ctx = ToolContext(db=db, session_id="s", mode="companion")
            ctx.user_text = "recuérdame cada mes pagar el alquiler"
            return await cronjob.execute(
                {"action": "create", "message": "Pagar el alquiler", "in_minutes": 2,
                 "recurring": "monthly"}, ctx)
        finally:
            await db.close()

    out = asyncio.run(go())
    assert "ONE-TIME" in out and "not one I can schedule" in out
    assert "monthly" not in out, "naming it back is how she repeats the promise"


def test_a_repeat_asked_for_and_not_tagged_is_flagged_not_invented(tmp_path):
    """The other direction, and it must never write. A cadence read out of prose is the bug the
    word-anchored regex was built against; the tool says the words sounded like a repeat and leaves the
    row one-shot, so SHE asks instead of the code guessing."""
    async def go():
        db = Database("sqlite:///" + str(tmp_path / "asked.db"))
        await db.connect()
        try:
            ctx = ToolContext(db=db, session_id="s", mode="companion")
            ctx.user_text = "recuérdame todos los días a las nueve tomar agua"
            out = await cronjob.execute(
                {"action": "create", "message": "Tomar agua", "in_minutes": 2}, ctx)
            jobs = await db.list_cronjobs()
            return jobs[0]["recurring"], out
        finally:
            await db.close()

    rec, out = asyncio.run(go())
    assert rec is None, "the tool must not invent the cadence the model dropped"
    assert "ask them" in out


@pytest.mark.parametrize("i,said", list(enumerate([
    "recuérdame en dos minutos que tengo que revisar el informe de QA",
    "recuérdame llamar a mi hermana",
    "remind me in two minutes to check the report",
    "avísame luego de esto",
])))
def test_a_plain_request_never_becomes_recurring(tmp_path, i, said):
    """Four plainly one-off requests, in both languages, each handed the tool an invented `hourly`.
    None of them may keep it."""
    got = _stored_recurring(tmp_path, said, f"plain{i}", message="Revisar el informe", recurring="hourly")
    assert got is None, f"{said!r} kept recurring={got!r}"


@pytest.mark.parametrize("i,said,tag", [
    (0, "recuérdame cada mañana que tome agua", "daily"),
    (1, "recuérdame todos los días a las nueve", "daily"),
    (2, "remind me every hour to stretch", "hourly"),
    (3, "recuérdalo una vez al día", "daily"),
    (4, "remind me once a week to back up", "weekly"),
])
def test_a_request_that_asks_to_repeat_keeps_it(tmp_path, i, said, tag):
    """The guard is not a blanket refusal: when the user's own words ask for a repeat, the cadence the
    model tagged survives untouched."""
    got = _stored_recurring(tmp_path, said, f"rec{i}", message="Tomar agua", recurring=tag)
    assert got == tag, f"{said!r} lost its repeat"


def test_an_explicit_once_kills_a_tag_the_model_invented(tmp_path):
    """An explicit "solo una vez" ("just once") in the request beats any cadence the model attached."""
    got = _stored_recurring(tmp_path, "recuérdame esto solo una vez", "once", message="X", recurring="daily")
    assert got is None


def test_a_repeat_word_still_outranks_a_bare_once(tmp_path):
    """`_RECUR_RE` beating `_ONE_TIME` is deliberate and predates this change: "una vez al día" contains
    "una vez" and is plainly recurring. Pinned so the new gate is not read as licence to invert it."""
    got = _stored_recurring(tmp_path, "recuérdamelo una vez al día", "hint", message="X", recurring="daily")
    assert got == "daily"


def test_with_no_user_text_the_tag_is_not_trusted(tmp_path):
    """This branch used to trust the model, and it was the guard's only bypass.

    It defended a cron-driven or replayed create, and neither exists: `insert_cronjob` has exactly one
    caller, this tool, and every transport that reaches it (`server`, `voice.session`, `cli.session`,
    `work_runner`) goes through `agentic_loop`, which fills `user_texts` from the turn's own messages.
    What the branch really covered was a context assembled by hand — `ToolContext.child`, which copies
    no utterance at all. Nobody asked for anything there, so no repeat survives: no evidence is not
    permission, and the model was measured wrong on both live requests in the sample."""
    got = _stored_recurring(tmp_path, None, "notext", message="X", recurring="daily")
    assert got is None


def test_a_captionless_photo_does_not_skip_the_guard(tmp_path):
    """The reachable half of the same bypass, measured live. A turn whose newest user message is
    an image or a file with no caption leaves `ctx.user_text` empty — `core.loop._recent_user_texts`
    yields '' for it — and the guard ran under `if ctx.user_text`, so it was skipped whole and the
    model's `daily` was stored AND announced. The words are still there, one message back."""
    async def go():
        db = Database("sqlite:///" + str(tmp_path / "captionless.db"))
        await db.connect()
        try:
            ctx = ToolContext(db=db, session_id="s", mode="companion")
            ctx.user_text = ""
            ctx.user_texts = ["", "ponme un recordatorio en dos minutos para sacar la basura"]
            out = await cronjob.execute(
                {"action": "create", "message": "Sacar la basura", "in_minutes": 2,
                 "recurring": "daily"}, ctx)
            jobs = await db.list_cronjobs()
            return jobs[0]["recurring"], out
        finally:
            await db.close()

    rec, out = asyncio.run(go())
    assert rec is None
    assert "ONE-TIME" in out


def test_a_one_time_reminder_is_still_one_time(tmp_path):
    """The base case: no cadence asked for, none supplied, none stored."""
    got = _stored_recurring(tmp_path, "recuérdame en dos minutos", "plain_none", message="X")
    assert got is None


@pytest.mark.parametrize("i,said,tag,message", [
    (0, "Ponme un recordatorio para dentro de dos minutos que diga: sacar la basura",
     "daily", "Sacar la basura"),
    (1, "Ponme un recordatorio para dentro de tres minutos que diga: revisar el correo",
     "hourly", "revisar el correo"),
])
def test_the_two_calls_measured_live(tmp_path, i, said, tag, message):
    """Verbatim from a live QA session, arguments included. Two one-off requests, two different
    inventions — the sample where the model was wrong every time."""
    assert _stored_recurring(tmp_path, said, f"live{i}", message=message, recurring=tag) is None


def test_the_tool_is_the_only_writer_the_guard_has_to_cover():
    """The guard is worth exactly its coverage, so the coverage is pinned rather than asserted in prose.
    One writer means one place the repeat can be decided; a second caller would be the real finding."""
    import pathlib

    import kotoba

    root = pathlib.Path(kotoba.__file__).parent
    callers = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.py")
                     if "insert_cronjob(" in p.read_text(encoding="utf-8", errors="ignore"))
    assert callers == ["db/database.py", "tools/action/cronjob.py"], callers
