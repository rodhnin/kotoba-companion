"""The input/approval flow survives EVERY degenerate user action.

The standing requirement: the flow must never die or go haywire because the person closed the card,
sent nothing, failed, or typed garbage.

Each case asserts request_input/request_approval returns a CLEAN, well-typed value (never raises, never
hangs past the timeout) and that a no-answer dismisses the on-screen card via a need_input{mode:clear}.
"""
from __future__ import annotations

import asyncio


from kotoba.core import events, interaction


def _run(coro):
    return asyncio.run(coro)


def test_cancel_empty_value_returns_empty_not_crash():
    """Cancel button posts {value:"", cancelled:true} → request_input returns "" (falsy → graceful
    FAIL upstream in ask_user), never raises."""
    async def go():
        events.register("e1")
        asyncio.get_event_loop().call_soon(lambda: interaction.resolve("e1", {"value": "", "cancelled": True}))
        v = await interaction.request_input("e1", "paste link", "link", timeout=2)
        events.unregister("e1")
        return v
    assert _run(go()) == ""


def test_garbage_text_is_passed_through_typed():
    """Whatever was typed comes back verbatim — the model deals with it, and nothing crashes."""
    async def go():
        events.register("e2")
        asyncio.get_event_loop().call_soon(lambda: interaction.resolve("e2", {"value": "????   <<>> 🤷 ;rm -rf"}))
        v = await interaction.request_input("e2", "anything", "text", timeout=2)
        events.unregister("e2")
        return v
    assert _run(go()) == "????   <<>> 🤷 ;rm -rf"


def test_malformed_payload_does_not_crash_input():
    """A submit body missing 'value' (or weird shape) → returns None, never raises."""
    async def go():
        events.register("e3")
        asyncio.get_event_loop().call_soon(lambda: interaction.resolve("e3", {"unexpected": 123}))
        v = await interaction.request_input("e3", "x", "text", timeout=2)
        events.unregister("e3")
        return v
    assert _run(go()) is None


def test_approval_garbage_payload_denies_safely():
    """Anything that is not an explicit yes is a denial. Default-deny on anything unclear is the
    security rule here; it never raises."""
    async def go():
        events.register("e4")
        asyncio.get_event_loop().call_soon(lambda: interaction.resolve("e4", {"nonsense": "yes"}))
        ok, _always = await interaction.request_approval("e4", "rm -rf build", timeout=2)
        events.unregister("e4")
        return ok
    assert _run(go()) is False


def test_double_submit_second_is_ignored():
    """Two POSTs for one ask (double-click / retry) — the second resolve returns False, no crash, and
    the first answer is the one used."""
    async def go():
        events.register("e5")

        async def answer():
            await asyncio.sleep(0.03)
            first = interaction.resolve("e5", {"value": "first"})
            second = interaction.resolve("e5", {"value": "second"})  # nothing waiting now
            return first, second

        t = asyncio.create_task(answer())
        v = await interaction.request_input("e5", "x", "text", timeout=2)
        first, second = await t
        events.unregister("e5")
        return v, first, second
    v, first, second = _run(go())
    assert v == "first" and first is True and second is False


def test_resolve_with_no_pending_is_safe():
    """A stale card submitting after timeout (nothing waiting) → resolve returns False, never raises."""
    assert interaction.resolve("nobody-waiting", {"value": "late"}) is False


def test_no_session_id_returns_immediately():
    """No session → can't ask; return None/False at once (no hang, no emit)."""
    assert _run(interaction.request_input(None, "x", "text")) is None
    assert _run(interaction.request_approval(None, "x")) == (False, False)


def test_timeout_emits_clear_to_dismiss_stale_card():
    """If the user never answers, the wait times out AND a need_input{mode:clear} frame is emitted so
    the frontend dismisses the now-dead card (no zombie modal blocking the call)."""
    async def go():
        q = events.register("e6")
        v = await interaction.request_input("e6", "x", "text", timeout=0.2)
        # drain frames: first the need_input(input), then the clear
        frames = []
        while not q.empty():
            frames.append(q.get_nowait())
        events.unregister("e6")
        return v, frames
    v, frames = _run(go())
    assert v is None
    kinds = [(f.get("kind"), f.get("mode")) for f in frames if f.get("type") == "task"]
    assert ("need_input", "input") in kinds
    assert ("need_input", "clear") in kinds


def test_approval_timeout_emits_clear():
    async def go():
        q = events.register("e7")
        ok, _always = await interaction.request_approval("e7", "delete things", timeout=0.2)
        frames = []
        while not q.empty():
            frames.append(q.get_nowait())
        events.unregister("e7")
        return ok, frames
    ok, frames = _run(go())
    assert ok is False
    kinds = [(f.get("kind"), f.get("mode")) for f in frames if f.get("type") == "task"]
    assert ("need_input", "clear") in kinds


def test_two_cards_on_one_session_are_both_outstanding():
    """In work mode a helper and its parent SHARE a session_id. The second card used to overwrite
    the first one's Future, so the first waiter could only ever time out. Both must stay answerable:
    a bare resolve() answers the NEWEST card (the one the single-card UI is showing), and the older one
    is still reachable by its own request_id."""
    async def go():
        events.register("e8")
        first = asyncio.create_task(interaction.request_input("e8", "parent asks", "text", timeout=5))
        await asyncio.sleep(0.05)
        second = asyncio.create_task(interaction.request_approval("e8", "rm -rf build", timeout=5))
        await asyncio.sleep(0.05)
        both_open = len(interaction._pending["e8"])

        newest = interaction.resolve("e8", {"approved": True})   # no request_id → newest card
        ok, _always = await second
        older_id = next(iter(interaction._pending["e8"]))          # parent's card, still waiting
        targeted = interaction.resolve("e8", {"value": "typed"}, older_id)
        val = await first
        events.unregister("e8")
        return both_open, newest, ok, targeted, val

    both_open, newest, ok, targeted, val = _run(go())
    assert both_open == 2            # neither Future was evicted
    assert newest is True and ok is True
    assert targeted is True and val == "typed"


def test_need_input_frames_carry_a_request_id():
    """Each blocking card gets its own identity on the wire so a client can answer that exact card."""
    async def go():
        q = events.register("e9")
        await interaction.request_approval("e9", "delete things", timeout=0.2)
        frames = [q.get_nowait() for _ in range(q.qsize())]
        events.unregister("e9")
        return frames

    frames = _run(go())
    card = next(f for f in frames if f.get("kind") == "need_input" and f.get("mode") == "approval")
    assert isinstance(card.get("request_id"), str) and card["request_id"]


def test_approval_window_is_channel_aware():
    """25s is an ElevenLabs constraint on a LIVE voice turn, not a universal one. A typed channel
    gets the roomy DEFAULT_TIMEOUT; an unknown/absent channel falls back to the safe short window."""
    assert interaction.approval_timeout("voice") == interaction.VOICE_APPROVAL_TIMEOUT == 25.0
    assert interaction.approval_timeout("text") == interaction.DEFAULT_TIMEOUT == 180.0
    assert interaction.approval_timeout(None) == interaction.VOICE_APPROVAL_TIMEOUT


def test_request_approval_uses_the_channel_window_by_default(monkeypatch):
    """The channel picks the window unless an explicit timeout is passed, which still wins."""
    # A registered queue is a precondition: request_approval refuses to open a card nobody can answer.
    from kotoba.core import events
    events.register("e10")
    seen = {}

    async def spy(session_id, request_id, fut, timeout):
        seen["timeout"] = timeout
        return None

    monkeypatch.setattr(interaction, "_await_response", spy)
    _run(interaction.request_approval("e10", "rm -rf /", channel="text"))
    assert seen["timeout"] == interaction.TEXT_APPROVAL_TIMEOUT
    _run(interaction.request_approval("e10", "rm -rf /", channel="voice"))
    assert seen["timeout"] == interaction.VOICE_APPROVAL_TIMEOUT
    _run(interaction.request_approval("e10", "rm -rf /", timeout=7))  # explicit still wins
    assert seen["timeout"] == 7
    events.event_queues.pop("e10", None)
    interaction._pending.pop("e10", None)  # the spy skipped the real finally that pops these


def test_note_turn_never_raises_even_when_its_own_state_is_broken(monkeypatch, caplog):
    """It is a diagnostic, and both callers invoke it unguarded before the turn is under way — a raise
    here ends the turn before `turn_end` and the client waits forever."""
    class Exploding(dict):
        def items(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(interaction, "_pending", Exploding())
    monkeypatch.setattr(interaction, "_seen_sessions", set())
    interaction.note_turn("fresh-session")
    assert "note_turn diagnostic failed" in caplog.text


def test_a_timing_out_card_dismisses_only_itself():
    """Live QA found the timeout clear naming no card, so a single-slot UI dropped whatever was on
    screen — a genuine two-command request lost the one nobody had answered yet. Each clear must carry
    the request_id of the card that actually died."""
    async def go():
        q = events.register("e11")
        dying = asyncio.create_task(interaction.request_approval("e11", "rm -rf build", timeout=0.1))
        await asyncio.sleep(0.02)
        surviving = asyncio.create_task(interaction.request_input("e11", "paste the URL", "text", timeout=5))
        await asyncio.sleep(0.4)  # the approval times out; the typed card is still up

        frames = [q.get_nowait() for _ in range(q.qsize())]
        cards = {f["mode"]: f.get("request_id") for f in frames
                 if f.get("kind") == "need_input" and f.get("mode") != "clear"}
        clears = [f for f in frames if f.get("kind") == "need_input" and f.get("mode") == "clear"]

        answered = interaction.resolve("e11", {"value": "https://example.test"})
        typed = await surviving
        await dying
        events.unregister("e11")
        return cards, clears, answered, typed

    cards, clears, answered, typed = _run(go())
    assert len(clears) == 1
    assert clears[0]["request_id"] == cards["approval"], "the clear must name the card that died"
    assert clears[0]["request_id"] != cards["input"], "…and never the one still waiting"
    assert answered is True and typed == "https://example.test"


def test_a_cancelled_approval_can_name_the_card_it_opened():
    """A caller cancelled mid-wait must be able to dismiss ITS card: an unnamed clear takes every card
    on screen with it, including one the user is still answering."""
    async def go():
        q = events.register("e12")
        card: dict = {}
        waiter = asyncio.create_task(
            interaction.request_approval("e12", "rm -rf build", timeout=5, card=card)
        )
        await asyncio.sleep(0.05)
        opened = [f for f in [q.get_nowait() for _ in range(q.qsize())]
                  if f.get("kind") == "need_input" and f.get("mode") == "approval"]
        waiter.cancel()
        try:
            await waiter
        except asyncio.CancelledError:
            pass
        events.unregister("e12")
        return card, opened

    card, opened = _run(go())
    assert card.get("request_id"), "the caller must learn the id before it can be cancelled"
    assert opened and opened[0]["request_id"] == card["request_id"], "…and it must be THAT card's id"


def test_the_card_out_param_is_optional():
    """Every existing caller passes no card and must keep working untouched."""
    async def go():
        events.register("e13")
        asyncio.get_event_loop().call_soon(lambda: interaction.resolve("e13", {"approved": True}))
        ok, always = await interaction.request_approval("e13", "npm test", timeout=2)
        events.unregister("e13")
        return ok, always
    assert _run(go()) == (True, False)


def test_the_card_names_which_ending_produced_no_value():
    """Four endings come back as the same falsy value, and only one of them is a decision the person
    made. A caller that cannot tell them apart reports a timeout as a refusal — or, where this was
    found, reports the person's own choice as the tool failing."""
    async def unreachable():
        card: dict = {}
        v = await interaction.request_input("nobody-listening", "k", "key", timeout=1, card=card)
        return v, card.get("verdict")

    async def timed_out():
        events.register("v1")
        card: dict = {}
        v = await interaction.request_input("v1", "k", "key", timeout=0.05, card=card)
        events.unregister("v1")
        return v, card.get("verdict")

    async def answered_empty():
        events.register("v2")
        card: dict = {}
        asyncio.get_event_loop().call_soon(lambda: interaction.resolve("v2", {"value": ""}))
        v = await interaction.request_input("v2", "k", "key", timeout=2, card=card)
        events.unregister("v2")
        return v, card.get("verdict")

    async def waved_away():
        events.register("v4")
        card: dict = {}
        asyncio.get_event_loop().call_soon(lambda: interaction.dismiss("v4"))
        v = await interaction.request_input("v4", "k", "key", timeout=2, card=card)
        events.unregister("v4")
        return v, card.get("verdict")

    async def answered():
        events.register("v3")
        card: dict = {}
        asyncio.get_event_loop().call_soon(lambda: interaction.resolve("v3", {"value": "sk-x"}))
        v = await interaction.request_input("v3", "k", "key", timeout=2, card=card)
        events.unregister("v3")
        return v, card.get("verdict")

    assert _run(unreachable()) == (None, interaction.UNREACHABLE)
    assert _run(timed_out()) == (None, interaction.UNANSWERED)
    assert _run(answered_empty()) == ("", interaction.DECLINED)
    assert _run(waved_away()) == (None, interaction.DISMISSED)
    assert _run(answered()) == ("sk-x", interaction.APPROVED)
