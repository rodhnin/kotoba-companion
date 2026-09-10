"""The repeat guard reads the words people actually say — and says which way it went.

The first version was a substring list — `cada`, `todos los`, `every`, `al día` — written from
phrasings its author thought of and never tried on the bare adverbs. Measured live: `daily`, `weekly`
and `hourly` (the very enum values the parameter filters), `semanalmente`, `mensualmente`, `los lunes`
and `recurrente` all lost their repeat, while "revisa TODOS LOS detalles y recuérdame en diez minutos"
and "remind me in AN HOUR" kept one nobody asked for. It also read only the latest message, so a
cadence given a turn earlier was gone by the time the tool ran, and the spoken confirmation was
identical either way — so a recurring cadence got confirmed over a one-shot row. The phrasings here
are verbatim in both languages under test, since the words themselves are what is under test."""
from __future__ import annotations

import asyncio
import re

import pytest

from kotoba.core.loop import _recent_user_texts
from kotoba.db.database import Database
from kotoba.tools import ToolContext
from kotoba.tools.action import cronjob


_CADENCE_WORDS = re.compile(
    r"\b(?:cada|todos los|every|each|daily|weekly|hourly|monthly|diario|semanal)\b", re.IGNORECASE)


def _create(tmp_path, name, latest, earlier=(), **args):
    async def go():
        db = Database("sqlite:///" + str(tmp_path / f"{name}.db"))
        await db.connect()
        try:
            ctx = ToolContext(db=db, session_id="s", mode="work")
            ctx.user_text = latest
            ctx.user_texts = [latest, *earlier]
            out = await cronjob.execute({"action": "create", "in_minutes": 30, **args}, ctx)
            jobs = await db.list_cronjobs()
            return out, (jobs[0]["recurring"] if jobs else "NO JOB")
        finally:
            await db.close()

    return asyncio.run(go())


# --- the adverbs, which is what people say -----------------------------------------------------------

@pytest.mark.parametrize("said", [
    "remind me daily at 8 to stretch",
    "remind me weekly to water the plants",
    "remind me hourly to drink water",
    "remind me monthly to pay the rent",
    "recuérdame semanalmente revisar las copias",
    "recuérdame mensualmente pagar el alquiler",
    "recuérdame diariamente estirarme",
    "recuérdame los lunes llamar al gestor",
    "recuérdame los sábados regar las plantas",
    "remind me on mondays to send the invoice",
    "hazlo recurrente: avisarme de la reunión",
    "quiero un recordatorio semanal del backup",
    "recuérdame siempre cerrar el portátil",
    "always remind me to lock the door",
    "recuérdame cada dos días revisar el correo",
    "remind me every other day to check the logs",
])
def test_a_bare_adverb_is_a_repeat(said):
    assert cronjob._asked_to_repeat(said) is True, f"{said!r} lost its repeat"


@pytest.mark.parametrize("said", [
    "revisa todos los detalles y recuérdame en diez minutos",
    "remind me in an hour to call the bank",
    "remind me in two minutes to breathe",
    "recuérdame en dos minutos respirar",
    "remind me at 8 to stretch",
    "recuérdame el lunes que viene llamar al gestor",
    "mira cada detalle del informe y recuérdame luego",
])
def test_an_incidental_word_is_not_a_cadence(said):
    """The false-positive half: `cada`/`todos los`/`an hour` as ordinary prose, and one-off phrasings."""
    assert cronjob._asked_to_repeat(said) is False, f"{said!r} kept a repeat nobody asked for"


@pytest.mark.parametrize("said", ["no repitas esto", "sin repetir", "don't repeat it", "not recurring"])
def test_a_refused_repeat_is_not_read_as_one(said):
    """The word is in the sentence and the sentence says no. Checked before the cadence, or "no repitas"
    would arrive as a repetition request."""
    assert cronjob._asked_to_repeat(said) is False
    assert cronjob._wants_one_time(said) is True


def test_the_stored_row_follows_the_adverb(tmp_path):
    _, rec = _create(tmp_path, "daily", "remind me daily at 8 to stretch",
                     message="Stretch", recurring="daily")
    assert rec == "daily"


# --- scheduling is a conversation --------------------------------------------------------------------

def test_the_loop_hands_the_tool_more_than_the_latest_message():
    items = [
        {"role": "developer", "content": "soul"},
        {"role": "user", "content": "recuérdame cada mañana tomar agua"},
        {"role": "assistant", "content": "¿a qué hora?"},
        {"role": "user", "content": [{"text": "a las ocho"}]},
    ]
    assert _recent_user_texts(items) == ["a las ocho", "recuérdame cada mañana tomar agua"]
    assert _recent_user_texts([]) == []


def test_a_cadence_given_one_turn_earlier_survives_the_follow_up(tmp_path):
    out, rec = _create(tmp_path, "twostep", "a las ocho",
                       earlier=["¿a qué hora?", "recuérdame cada mañana tomar agua"],
                       message="Tomar agua", recurring="daily")
    assert rec == "daily", "the repeat was one turn back and the guard never looked"
    assert "repeats daily" in out


def test_a_fresh_one_off_does_not_inherit_an_old_cadence(tmp_path):
    """The lookback stops at a message that asks for a reminder of its own — otherwise a "cada día"
    from earlier in the call would make every later one-off repeat forever."""
    out, rec = _create(tmp_path, "fresh", "recuérdame en diez minutos llamar a Ana",
                       earlier=["recuérdame cada día tomar agua"],
                       message="Llamar a Ana", recurring="daily")
    assert rec is None
    assert "ONE-TIME" in out


# --- the answer can no longer hide the downgrade ------------------------------------------------------

def test_a_downgrade_is_reported_in_the_tool_result(tmp_path):
    """The load-bearing half. The row is one-shot; if the answer doesn't say so she confirms the repeat
    the model invented, and the user learns otherwise on day two."""
    out, rec = _create(tmp_path, "down", "recuérdame en dos minutos revisar el informe",
                       message="Revisar el informe", recurring="hourly")
    assert rec is None
    assert "ONE-TIME" in out and "does NOT repeat" in out
    assert "they only have to say so" in out, "she must still be able to offer the fix"
    assert not _CADENCE_WORDS.search(out), (
        "the note used to spell out 'every day/week/hour' and 'cada día'. It is the densest run of "
        "cadence vocabulary in the conversation and it lands in the context the NEXT create is "
        "generated from — the parameter this guard exists to keep empty. She does not need an example "
        "to offer a repeat in the user's own language"
    )


def test_a_repeat_that_survived_is_stated_too(tmp_path):
    out, rec = _create(tmp_path, "kept", "recuérdame todos los días a las nueve",
                       message="Tomar agua", recurring="daily")
    assert rec == "daily" and "repeats daily" in out


def test_a_plain_one_time_reminder_says_it_rings_once(tmp_path):
    out, rec = _create(tmp_path, "plain", "recuérdame en diez minutos llamar a Ana",
                       message="Llamar a Ana")
    assert rec is None and "rings once" in out


# --- one message, two reminders ----------------------------------------------------------------------

_TWO = ("Dos recordatorios: recuérdame cada lunes revisar las copias de seguridad, "
        "y aparte recuérdame en una hora llamar al banco.")


def test_one_message_two_reminders_does_not_leak_the_cadence(tmp_path):
    """Live QA: this exact message gave the bank call `daily`. The guard was scoped to the MESSAGE, so
    the hint from the first reminder reached every create in the turn — and this is the dangerous
    direction: a guard written to strip an invented repeat was preserving one."""
    out, rec = _create(tmp_path, "two-a", _TWO, message="Llamar al banco", recurring="daily")
    assert rec is None, "'en una hora' inherited 'cada lunes'"
    assert "ONE-TIME" in out

    out, rec = _create(tmp_path, "two-b", _TWO, message="Revisar las copias de seguridad",
                       recurring="weekly")
    assert rec == "weekly", "the reminder that really asked for a repeat lost it"
    assert "repeats weekly" in out


def test_the_leak_is_closed_in_either_order(tmp_path):
    """The model creates them in whichever order it likes; position must not decide."""
    assert _create(tmp_path, "ord-1", _TWO, message="Llamar al banco", recurring="daily")[1] is None
    assert _create(tmp_path, "ord-2", _TWO, message="Revisar las copias", recurring="weekly")[1] == "weekly"


def test_a_message_asking_twice_is_cut_into_two_reminders():
    chunks = cronjob._reminder_chunks(_TWO)
    assert len(chunks) == 2
    assert "copias" in chunks[0] and "banco" in chunks[1]
    assert cronjob._attribute("Llamar al banco", chunks) is chunks[1]
    assert cronjob._attribute("Revisar las copias de seguridad", chunks) is chunks[0]


@pytest.mark.parametrize("said", [
    "recuérdame cada lunes, a las nueve, revisar las copias",
    "recuérdame llamar al banco, recuérdamelo cada día",
    "recuérdame revisar el informe, cada día a las ocho",
    "recuérdame la reunión, cada lunes con el equipo",
])
def test_one_reminder_spread_over_clauses_is_still_one(said):
    """The split has to be conservative in this direction: cutting a single reminder in two would put
    its cadence in the other half and strip a repeat the user really asked for."""
    assert len(cronjob._reminder_chunks(said)) == 1


def test_a_repeat_that_cannot_be_attributed_is_abstained_from(tmp_path):
    """The `message` argument is the model's own paraphrase, not a substring of what the user said — in
    another language it shares no words with either half. Nothing can say whose repeat it was, so the
    tool declines to guess, and says exactly that."""
    chunks = cronjob._reminder_chunks(_TWO)
    assert cronjob._attribute("Call the bank", chunks) is None
    out, rec = _create(tmp_path, "ambig", _TWO, message="Call the bank", recurring="daily")
    assert rec is None
    assert "more than one reminder" in out and "rings once" in out


def test_a_refused_repeat_errs_to_one_time_and_names_the_words(tmp_path):
    """The reverse leak, pinned deliberately. "no repitas el aviso del informe" names a second reminder
    WITHOUT asking for one, so it cannot be split off the first without also splitting "cada día a las
    ocho" — and that split would strip real repeats. Every rescue for this case hands back a repeat the
    user was refusing, so it stays one-time and the answer says which words did it."""
    said = "recuérdame cada día tomar agua, y no repitas el aviso del informe"
    out, rec = _create(tmp_path, "reverse", said, message="Tomar agua", recurring="daily")
    assert rec is None, "if this ever flips, it flipped toward repeating something the user refused"
    assert "said not to repeat it" in out and "DIFFERENT reminder" in out


def test_a_deduped_re_emission_reports_the_same_cadence(tmp_path):
    """The idempotent branch answers about the EXISTING job, so it has to describe that one's cadence —
    silence there is the same lie by another door."""
    async def go():
        db = Database("sqlite:///" + str(tmp_path / "dup.db"))
        await db.connect()
        try:
            ctx = ToolContext(db=db, session_id="s", mode="work")
            ctx.user_text = "recuérdame cada día tomar agua"
            args = {"action": "create", "message": "Tomar agua", "in_minutes": 30, "recurring": "daily"}
            first = await cronjob.execute(dict(args), ctx)
            second = await cronjob.execute(dict(args), ctx)
            return first, second, len(await db.list_cronjobs())
        finally:
            await db.close()

    first, second, n = asyncio.run(go())
    assert n == 1, "the re-emission created a second reminder"
    assert "repeats daily" in first and "repeats daily" in second
