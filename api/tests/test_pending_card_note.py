"""The pending-card note: what the model is told while a card waits on the human.

Still-pending Futures become one developer message per turn. Four properties it keeps: ONE
CARD ONE NOTE, since a deferred approval owns a separate prompt_note that must not give
opposite orders about the same card. BOUNDED, since an approval label is the full command by
design — one heredoc put an 8,400-character document into the note before labels were
clipped to their first line. NO LEAKED WAITER, since a need_input emit raising after the
Future is registered would orphan the request and hold the voice mic shut for the call,
guarded by `_abandon_request`. NO SECRET, since a key/secret box's label is the prompt, not
the typed value, and vanishes from pending_labels once its Future resolves."""
from __future__ import annotations

import asyncio
import types

import pytest

from kotoba.core import context as ctx_mod
from kotoba.core import deferred_exec, events, interaction


def _ctx(sid: str, call_id: str, user_text: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(session_id=sid, call_id=call_id, user_text=user_text,
                                 run_id="", approval=None)


async def _wait(pred, limit: float = 5.0) -> bool:
    end = asyncio.get_running_loop().time() + limit
    while asyncio.get_running_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.005)
    return False


async def _cleanup(sid: str, queue: asyncio.Queue, answers: int) -> None:
    for _ in range(answers):
        interaction.resolve(sid, {"approved": False})
    await asyncio.sleep(0.05)
    deferred_exec.cancel(sid)
    deferred_exec.forget_session(sid)
    events.unregister(sid, queue)


def test_a_deferred_card_is_narrated_once_not_twice():
    """The reproduced contradiction: with a deferred approval waiting, prompt_note said "say nothing
    further about it" while the card note said "say once that it is still waiting" — both about the
    same card, in the same turn. The deferred note owns its cards; the card note stands down."""

    async def main():
        sid = "note-once"
        q = events.register(sid)

        async def runner() -> str:
            return "ran"

        deferred_exec.schedule(_ctx(sid, "c1", "instala requests"), "pip install requests",
                               runner, label="pip install requests", step_kind="shell")
        assert await _wait(lambda: interaction.has_pending(sid)), "the deferred card never opened"

        card_note = ctx_mod._pending_card_note(sid)
        defer_note = deferred_exec.prompt_note(sid)
        await _cleanup(sid, q, answers=1)
        return card_note, defer_note

    card_note, defer_note = asyncio.run(main())
    assert defer_note, "the deferred note must describe its own card"
    assert card_note == "", f"the card note described a deferred card too: {card_note!r}"
    assert "only THEY can answer it" in defer_note, (
        "handing the card over must not lose the boundary — the deferred note has to state it"
    )


def test_the_handover_matches_execute_codes_respelled_label():
    """execute_code schedules with label='your code' but its CARD label is the full snippet — the
    handover must match on the ACTION under deferred_exec's normalisation, not on display text."""

    async def main():
        sid = "note-code"
        q = events.register(sid)

        async def runner() -> str:
            return "ran"

        action = "run Python:\nprint(sum(range(1, 101)))"
        deferred_exec.schedule(_ctx(sid, "c1", "suma 1 a 100"), action, runner,
                               label="your code", step_kind="code", family="execute_code")
        assert await _wait(lambda: interaction.has_pending(sid)), "the deferred card never opened"

        note = ctx_mod._pending_card_note(sid)
        await _cleanup(sid, q, answers=1)
        return note

    assert asyncio.run(main()) == ""


def test_a_plain_card_beside_a_deferred_one_is_still_described_and_clipped():
    """Handing over the deferred card must not silence the note for everything else — and the label of
    what remains is clipped to one line and a budget, because an approval label is the full command."""

    async def main():
        sid = "note-mixed"
        q = events.register(sid)

        async def runner() -> str:
            return "ran"

        deferred_exec.schedule(_ctx(sid, "c1", "instala requests"), "pip install requests",
                               runner, label="pip install requests", step_kind="shell")
        assert await _wait(lambda: interaction.has_pending(sid))

        heredoc = "sh -c 'cat <<DOC\n" + ("x" * 200 + "\n") * 40 + "DOC'"
        asyncio.ensure_future(
            interaction.request_approval(sid, heredoc, timeout=5.0, channel="text")
        )
        assert await _wait(lambda: len(interaction.pending_labels(sid)) == 2)

        note = ctx_mod._pending_card_note(sid)
        await _cleanup(sid, q, answers=2)
        return note

    note = asyncio.run(main())
    assert note, "the plain card lost its note in the handover"
    assert "pip install requests" not in note, "the deferred card leaked back into the note"
    assert "sh -c 'cat <<DOC…" in note, f"the clipped head must still say what the card asks: {note!r}"
    assert "x" * 200 not in note, "the whole heredoc reached the model"
    assert "\n" not in note and len(note) < 600, f"unbounded note: {len(note)} chars"


def test_a_failed_card_emit_leaves_no_pending_waiter():
    """The synthetic leak: an emit that raises between _open_request and _await_response used to strand
    the Future — has_pending stuck True, the voice mic hold shut forever. The request is abandoned on
    the way out of BOTH askers, and the raise still reaches the caller unchanged."""

    async def main():
        sid = "note-leak"
        q = events.register(sid)
        orig = interaction.emit_task

        async def boom(*a, **k):
            if k.get("mode") in ("approval", "input") and k.get("wait", True):
                raise RuntimeError("emit failed")
            return await orig(*a, **k)

        interaction.emit_task = boom
        try:
            with pytest.raises(RuntimeError):
                await interaction.request_approval(sid, "df -h", timeout=1.0, channel="text")
            with pytest.raises(RuntimeError):
                await interaction.request_input(sid, "type it", "text", timeout=1.0)
            return interaction.has_pending(sid), interaction.pending_labels(sid)
        finally:
            interaction.emit_task = orig
            events.unregister(sid, q)

    pending, labels = asyncio.run(main())
    assert pending is False and labels == [], "a card that was never drawn left a waiter behind"


def test_the_note_speaks_for_every_surface_not_just_the_web():
    """Live QA: the note reaches CLI turns too, and it said "on screen … with the buttons in front of
    them" — a terminal answers its cards with y/N keys, so relaying that would have her
    describing controls that do not exist, the untrue-about-her-own-reach failure the note itself guards
    against. It names the card and the boundary; the widgetry belongs to whichever surface drew it."""

    async def main():
        sid = "note-surface"
        q = events.register(sid)
        waiter = asyncio.ensure_future(
            interaction.request_approval(sid, "npm run build", timeout=5.0, channel="text")
        )
        assert await _wait(lambda: interaction.has_pending(sid)), "the card never opened"
        note = ctx_mod._pending_card_note(sid)
        interaction.resolve(sid, {"approved": False})
        await asyncio.wait_for(waiter, 5.0)
        events.unregister(sid, q)
        return note

    note = asyncio.run(main())
    assert "npm run build" in note, "the note must still say what the card asks"
    assert "cannot approve or decline it for them" in note, "and keep the boundary"
    assert "button" not in note.lower(), f"web widgetry leaked into a surface-neutral note: {note!r}"
    assert "screen" not in note.lower(), f"a terminal prompt is not 'on screen': {note!r}"


def test_a_typed_secret_never_reaches_a_label_or_the_note():
    """A key box's label is the PROMPT, and the typed value arrives as the Future's result — so while
    the box waits the note carries only the question, and the moment it is answered the label leaves
    pending_labels entirely. The value still reaches the one waiter it belongs to."""

    async def main():
        sid = "note-secret"
        q = events.register(sid)
        waiter = asyncio.ensure_future(
            interaction.request_input(sid, "paste your OpenAI key", "key", timeout=5.0)
        )
        assert await _wait(lambda: interaction.has_pending(sid))
        before = (list(interaction.pending_labels(sid)), ctx_mod._pending_card_note(sid))

        interaction.resolve(sid, {"kind": "key", "value": "sk-SECRET-VALUE"})
        after_labels = interaction.pending_labels(sid)
        got = await asyncio.wait_for(waiter, 5.0)
        after_note = ctx_mod._pending_card_note(sid)
        events.unregister(sid, q)
        return before, after_labels, got, after_note

    (labels, note), after_labels, got, after_note = asyncio.run(main())
    assert labels == ["paste your OpenAI key"] and "paste your OpenAI key" in note
    assert "sk-SECRET-VALUE" not in note
    assert after_labels == [] and after_note == "", "the resolved box outlived its answer"
    assert got == "sk-SECRET-VALUE", "the value must still reach the one waiter it belongs to"
