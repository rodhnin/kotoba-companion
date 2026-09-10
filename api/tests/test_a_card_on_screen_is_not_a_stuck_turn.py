"""What is wrong with a hung turn is never that it took long. It is that nothing happened.

A total ceiling of 45 seconds cancelled turns whose approval card was still on screen — the card's
own window is 180 — so somebody answered "Approve" out loud to a turn that had already been thrown
away. Silence is what gets timed now, and a card being read counts as something happening.
"""
from __future__ import annotations

import asyncio

import pytest

from kotoba.discord import cards as cards_mod
from kotoba.discord import client as client_mod


def watcher() -> client_mod.KotobaClient:
    made = client_mod.KotobaClient.__new__(client_mod.KotobaClient)
    made._progress = {}
    return made


def test_a_quiet_turn_is_cancelled_and_says_so(monkeypatch):
    monkeypatch.setattr(client_mod, "STALL_SECONDS", 0.05)

    async def scenario():
        me = watcher()
        turn = asyncio.create_task(asyncio.sleep(30))
        with pytest.raises(asyncio.TimeoutError):
            await me._until_stalled(turn, 99)
        assert turn.cancelled()

    asyncio.run(scenario())


def test_an_open_card_holds_the_turn_open(monkeypatch):
    monkeypatch.setattr(client_mod, "STALL_SECONDS", 0.05)
    cards_mod._OPEN[99] = 1
    try:
        async def scenario():
            me = watcher()

            async def answered():
                await asyncio.sleep(0.2)
                return "done"

            return await me._until_stalled(asyncio.create_task(answered()), 99)

        assert asyncio.run(scenario()) == "done"
    finally:
        cards_mod._OPEN.pop(99, None)


def test_a_running_tool_counts_as_a_sign_of_life(monkeypatch):
    monkeypatch.setattr(client_mod, "STALL_SECONDS", 0.05)

    async def scenario():
        me = watcher()
        note = me._noted(99)

        async def busy():
            for _ in range(6):
                await asyncio.sleep(0.03)
                note("step", {})
            return "done"

        return await me._until_stalled(asyncio.create_task(busy()), 99)

    assert asyncio.run(scenario()) == "done"
