"""Speaking over an open approval card takes the MICROPHONE back, not the card.

The gesture used to dismiss every open card, on the reading that unheard words "can only mean take
that away" — but a card that vanishes the instant the user speaks is lossy too: the client drops the
sentence that broke the hold, so the user loses the card AND the words. A card now ends only the two
ways a person can mean it: answered, or timed out.

Nothing pinned the old behaviour before this — the suite stayed green through the change because it was
only ever verified live.
"""
from __future__ import annotations

import asyncio

import pytest

from kotoba.core import context as ctx_mod
from kotoba.core import interaction
from kotoba.core.voice import session as vs


class _WS:
    async def send_text(self, *a, **k):
        pass


def _session(sid: str) -> vs.VoiceSession:
    return vs.VoiceSession(_WS(), sid, db=None, soul_patterns={})


async def _open_card(sid: str, action: str) -> asyncio.Task:
    """A real pending approval, opened the way a tool opens one."""
    task = asyncio.create_task(
        interaction.request_approval(sid, action, timeout=30.0, channel="text")
    )
    for _ in range(50):
        if interaction.has_pending(sid):
            return task
        await asyncio.sleep(0.01)
    raise AssertionError("the card never opened")


@pytest.fixture(autouse=True)
def _listener(monkeypatch):
    """`_reachable` refuses to open a card with nothing listening — give it a channel."""
    monkeypatch.setattr(interaction, "_reachable", lambda sid, what: True)
    monkeypatch.setattr(interaction, "emit_emotion", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(interaction, "emit_task", lambda *a, **k: asyncio.sleep(0))


def test_his_voice_leaves_the_card_exactly_where_it_was():
    async def main():
        sid = "keep-card"
        task = await _open_card(sid, "rm -rf /tmp/x")
        _session(sid)._yield_mic_to_user()
        await asyncio.sleep(0.05)
        assert interaction.has_pending(sid), "his voice took the card away"
        assert not task.done(), "the waiter behind the card was ended"
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(main())


def test_the_hold_steps_aside_so_he_is_heard_at_all():
    """Without the yield flag the watcher re-shuts the mic within one poll — straight back behind the
    bracket the user just broke."""

    async def main():
        sid = "yield-mic"
        task = await _open_card(sid, "df -h")
        s = _session(sid)
        decisions: list[str] = []

        async def held(_turn):
            decisions.append("hold")

        async def released(_turn):
            decisions.append("release")

        s._hold_mic, s._release_mic = held, released

        watcher = asyncio.create_task(s._watch_cards())
        await asyncio.sleep(0.05)
        assert decisions and decisions[0] == "hold", "a pending card must shut the mic"

        s._yield_mic_to_user()
        s._bracket_closed.set()
        await asyncio.sleep(0.05)
        assert decisions[-1] == "release", f"the hold never stepped aside: {decisions}"

        watcher.cancel()
        task.cancel()
        await asyncio.gather(watcher, task, return_exceptions=True)

    asyncio.run(main())


def test_the_next_card_shuts_the_mic_again():
    """The yield covers the cards the user interrupted and nothing more — the ambient-noise protection is
    suspended, never retired."""

    async def main():
        sid = "yield-clears"
        first = await _open_card(sid, "ls")
        s = _session(sid)
        s._yield_mic_to_user()
        decisions: list[str] = []

        async def held(_turn):
            decisions.append("hold")

        async def released(_turn):
            decisions.append("release")

        s._hold_mic, s._release_mic = held, released
        watcher = asyncio.create_task(s._watch_cards())
        await asyncio.sleep(0.05)
        assert decisions[-1] == "release"

        first.cancel()                      # the card the user interrupted goes away
        await asyncio.gather(first, return_exceptions=True)
        s._bracket_closed.set()
        await asyncio.sleep(0.05)
        assert not s._mic_yielded, "the yield outlived the card that caused it"

        second = await _open_card(sid, "curl example.org")
        s._bracket_closed.set()
        await asyncio.sleep(0.05)
        assert decisions[-1] == "hold", f"a NEW card did not shut the mic: {decisions}"

        watcher.cancel()
        second.cancel()
        await asyncio.gather(watcher, second, return_exceptions=True)

    asyncio.run(main())


def test_she_is_told_what_is_waiting_and_that_it_is_not_hers_to_answer():
    async def main():
        sid = "tell-her"
        task = await _open_card(sid, "rm -rf /tmp/x")
        assert interaction.pending_labels(sid) == ["rm -rf /tmp/x"]

        note = ctx_mod._pending_card_note(sid)
        assert "rm -rf /tmp/x" in note
        assert "cannot approve or decline it for them" in note
        assert "does not make it go away" in note

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(main())


def test_no_note_at_all_when_nothing_is_waiting():
    """The note is per-turn and rides behind the cached prefix, but a note on every quiet turn would
    still be a lie she has to reconcile."""
    assert ctx_mod._pending_card_note("quiet") == ""
    assert ctx_mod._pending_card_note(None) == ""
    assert interaction.pending_labels("quiet") == []
