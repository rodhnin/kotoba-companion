"""The repeat guard must not eat content that legitimately repeats.

An earlier fix for stuttering collapsed any two sentences that recurred in order anywhere in a reply.
Structured content does that constantly: a weekly plan kept "Lunes" and its two lines, then printed the
other weekdays empty, reading as if she forgot to fill them in rather than that something deleted them.
What tells a real stutter apart is CONTIGUITY plus PROSE: a stutter replays the turn's tail with nothing
new between, and only prose, so copies separated by a heading, or inside a table or code fence, now
survive. Accepted cost: a reply that is nothing but a repeated list is left alone even if doubled, since
losing a day of a plan is worse than reading a list twice. Speech scrubs list/heading markers before the
guard sees a line, so a bulleted list is not prose to one chain and structure to the other."""
from __future__ import annotations

from kotoba.core import stream as sse

PLAN = (
    "Lunes\n- Correr treinta minutos\n- Estirar la espalda\n"
    "Martes\n- Correr treinta minutos\n- Estirar la espalda\n"
    "Miercoles\n- Correr treinta minutos\n- Estirar la espalda\n"
)

PLAN_NO_BULLETS = (
    "Lunes\nCorrer treinta minutos\nEstirar la espalda\n"
    "Martes\nCorrer treinta minutos\nEstirar la espalda\n"
    "Miercoles\nCorrer treinta minutos\nEstirar la espalda\n"
)

TABLE = (
    "Aqui tienes el resumen de ventas por region:\n\n"
    "| Region | Producto | Unidades |\n| --- | --- | --- |\n"
    "| Norte | Teclado mecanico | 120 |\n| Norte | Teclado mecanico | 120 |\n"
    "| Sur | Raton inalambrico | 90 |\n"
)

FENCE = (
    "Te dejo el bucle tal cual lo quieres:\n\n"
    "```py\ntotal = total + 1\ntotal = total + 1\nprint(total)\n```\n\n"
    "Suma dos veces a proposito.\n"
)

PROJECTS = (
    "Para el proyecto A hazlo asi. Primero revisas el backlog pendiente. "
    "Luego cierras las tareas viejas que ya nadie mira. "
    "Para el proyecto B hazlo asi. Primero revisas el backlog pendiente. "
    "Luego cierras las tareas viejas que ya nadie mira."
)


def spoken(text: str) -> str:
    f = sse.ForbiddenPhraseFilter()
    return f.feed(text) + f.flush()


def test_the_weekly_plan_keeps_every_day():
    assert sse.collapse_repeats(PLAN) == PLAN
    said = spoken(PLAN)
    assert said.count("Correr treinta minutos") == 3, said
    assert said.count("Estirar la espalda") == 3, said


def test_the_same_plan_without_bullets_keeps_every_day():
    """The heading is the only thing that says these are three days and not a stutter — and in speech
    it is the ONLY thing left, because the markers are scrubbed before the guard sees the line."""
    assert sse.collapse_repeats(PLAN_NO_BULLETS) == PLAN_NO_BULLETS
    assert spoken(PLAN_NO_BULLETS).count("Correr treinta minutos") == 3


def test_a_table_keeps_both_identical_rows():
    assert sse.collapse_repeats(TABLE) == TABLE
    assert spoken(TABLE).count("| Norte | Teclado mecanico | 120 |") == 2


def test_a_code_fence_keeps_a_line_written_twice_on_purpose():
    assert sse.collapse_repeats(FENCE) == FENCE
    assert spoken(FENCE).count("total = total + 1") == 2


def test_the_second_project_keeps_its_steps():
    """The shape the audit found: identical prose steps under two different headings. The heading breaks the
    replay, and everything held while it was being weighed comes back in order."""
    assert sse.collapse_repeats(PROJECTS) == PROJECTS
    assert spoken(PROJECTS) == PROJECTS


def test_short_acks_still_repeat_freely():
    text = "Si. Claro. Si. Vale. Si."
    assert sse.collapse_repeats(text) == text


def test_a_doubled_reply_that_contains_a_list_still_collapses():
    """The boundary: the block carries a prose sentence, so the whole doubling goes — list and all."""
    reply = "Te dejo la compra de la semana.\n- Cafe\n- Pan\n- Fruta\nY con eso llegamos al finde.\n"
    # rstrip: the blank line that TRAILED the dropped copy is not part of any sentence and survives it.
    assert sse.collapse_repeats(reply + reply).rstrip("\n") == reply.rstrip("\n")
    assert spoken(reply + reply).count("Te dejo la compra de la semana") == 1


def test_a_list_repeated_with_no_prose_between_is_left_alone():
    """The accepted cost, pinned so it is a decision and not a surprise."""
    listing = "- Correr treinta minutos\n- Estirar la espalda\n"
    assert sse.collapse_repeats(listing + listing) == listing + listing
    assert spoken(listing + listing).count("Correr treinta minutos") == 2
