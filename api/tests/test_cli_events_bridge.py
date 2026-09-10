"""The terminal's half of the event channel: draining it, and answering the cards it carries.

Every card is a live Future in core.interaction, so these drive the REAL asker (request_approval /
request_input) and assert on what it returns. The two guarantees worth the loop time are the last pair:
an answer must carry its card's own request_id (resolve matches newest-first without one, and a terminal
asks oldest-first), and it must reach the loop through call_soon_threadsafe (a plain resolve off the
stdin thread records the answer and leaves the turn asleep until the window expires — measured below).
"""
from __future__ import annotations

import asyncio
import getpass
import io
import threading
import time

from kotoba.cli import secret
from kotoba.cli.approvals import Approvals, Card, ask_at_terminal
from kotoba.cli.events_bridge import EventBridge, kind_of
from kotoba.core import events, interaction


def _run(coro):
    return asyncio.run(coro)


async def _card_frame(queue: asyncio.Queue) -> dict:
    """The next need_input frame, read straight off the queue — only for the tests that run NO bridge
    (one draining it would take the frame first). request_approval emits an emotion first.

    Bounded: a frame that never comes is a defect, and an unbounded get() reports it by wedging the
    whole run instead of failing this one line."""
    async def _next() -> dict:
        while True:
            frame = await queue.get()
            if frame.get("kind") == "need_input":
                return frame

    return await asyncio.wait_for(_next(), timeout=10.0)


async def _until(ready, tries: int = 2000) -> None:
    """Yield until the drain task has caught up. Bounded, so a broken bridge fails an assertion instead
    of hanging the suite."""
    for _ in range(tries):
        if ready():
            return
        await asyncio.sleep(0)


def _bridge(session_id: str, ask=None, seen=None) -> tuple[asyncio.Queue, EventBridge]:
    queue = events.register(session_id)
    bridge = EventBridge(queue, on_event=seen, approvals=Approvals(session_id, ask=ask))
    bridge.start()
    return queue, bridge


def test_an_emotion_frame_and_a_task_frame_land_in_one_flat_namespace():
    assert kind_of({"type": "emotion", "emotion": "happy"}) == "emotion"
    assert kind_of({"type": "task", "kind": "report_ready", "title": "x"}) == "report_ready"


def test_the_bridge_drains_the_queue_so_every_frame_reaches_the_renderer():
    seen: list[tuple[str, dict]] = []

    async def go():
        _, bridge = _bridge("b1", seen=lambda k, f: seen.append((k, f)))
        await events.emit_emotion("b1", "happy")
        await events.emit_task("b1", "artifact", path="report.md", action="created")
        await events.emit_task("b1", "files_changed")
        await _until(lambda: len(seen) >= 3)
        await bridge.aclose()
        events.unregister("b1")

    _run(go())
    assert [k for k, _ in seen] == ["emotion", "artifact", "files_changed"]
    assert seen[1][1]["path"] == "report.md"


def test_the_queue_is_left_empty_so_a_long_session_does_not_grow_forever():
    async def go():
        queue, bridge = _bridge("b2")
        for _ in range(50):
            await events.emit_emotion("b2", "neutral")
        await _until(lambda: queue.qsize() == 0)
        await bridge.aclose()
        events.unregister("b2")
        return queue.qsize()

    assert _run(go()) == 0


def test_a_deferred_action_is_not_shown_as_done_until_it_has_actually_run():
    """`pending` on a done-frame means the human has not approved it yet; deferred_exec completes the
    same id later with the real output."""
    async def go():
        _, bridge = _bridge("b3")
        await events.emit_task("b3", "step", phase="start", id="c1", step_kind="exec", action="npm test")
        await events.emit_task("b3", "step", phase="done", id="c1", ok=True, pending=True, result="")
        await _until(lambda: bridge.steps.get("c1") is not None and bridge.steps["c1"].state != "running")
        parked = bridge.steps["c1"].state
        await events.emit_task("b3", "step", phase="done", id="c1", ok=True, pending=False, result="3 passed")
        await _until(lambda: bridge.steps["c1"].state != "pending")
        await bridge.aclose()
        events.unregister("b3")
        return parked, bridge.steps["c1"]

    parked, step = _run(go())
    assert parked == "pending", "an action awaiting approval must never read as finished"
    assert (step.state, step.result, step.action) == ("ok", "3 passed", "npm test")


def test_a_turn_cancelled_mid_tool_leaves_its_step_interrupted_not_failed():
    async def go():
        _, bridge = _bridge("b4")
        await events.emit_task("b4", "step", phase="start", id="c2", step_kind="exec", action="sleep 60")
        await events.emit_task("b4", "step", phase="done", id="c2", ok=False, interrupted=True,
                               result="(interrupted)")
        await _until(lambda: bridge.steps.get("c2") is not None and bridge.steps["c2"].state != "running")
        await bridge.aclose()
        events.unregister("b4")
        return bridge.steps["c2"].state

    assert _run(go()) == "interrupted", "she did not fail — she was stopped"


def test_a_step_whose_start_was_never_seen_is_rebuilt_from_its_done_frame():
    """deferred_exec re-sends step_kind/action minutes later precisely so a lost row can come back."""
    async def go():
        _, bridge = _bridge("b5")
        await events.emit_task("b5", "step", phase="done", id="c3", ok=True, pending=False,
                               step_kind="exec", action="rm old.log", result="done")
        await _until(lambda: bridge.steps.get("c3") is not None)
        await bridge.aclose()
        events.unregister("b5")
        return bridge.steps["c3"]

    step = _run(go())
    assert (step.kind, step.action, step.state) == ("exec", "rm old.log", "ok")


def test_one_bad_frame_does_not_end_the_drain():
    seen: list[str] = []

    def renderer(kind, frame):
        seen.append(kind)
        if kind == "reminder":
            raise ValueError("the renderer blew up")

    async def go():
        _, bridge = _bridge("b6", seen=renderer)
        await events.emit_task("b6", "reminder", message="agua", id=1)
        await events.emit_task("b6", "report_ready", title="x")
        await _until(lambda: len(seen) >= 2)
        await bridge.aclose()
        events.unregister("b6")

    _run(go())
    assert seen == ["reminder", "report_ready"], "after a dead drain no card would ever be answered again"


def test_an_approval_card_blocks_the_turn_until_the_terminal_answers_it():
    asked: list[Card] = []

    async def ask(card):
        asked.append(card)
        return (True, False)

    async def go():
        _, bridge = _bridge("a1", ask=ask)
        out = await interaction.request_approval("a1", "npm test", timeout=5.0, channel="text")
        await bridge.aclose()
        events.unregister("a1")
        return out

    assert _run(go()) == (True, False)
    assert asked and asked[0].label == "npm test"


def test_a_declined_card_comes_back_as_a_clean_deny_not_a_timeout():
    async def ask(card):
        return None                      # ctrl-C at the prompt, or a renderer that gave up

    async def go():
        _, bridge = _bridge("a2", ask=ask)
        t0 = time.monotonic()
        out = await interaction.request_approval("a2", "rm -rf /tmp/x", timeout=5.0, channel="text")
        await bridge.aclose()
        events.unregister("a2")
        return out, time.monotonic() - t0

    out, elapsed = _run(go())
    assert out == (False, False)
    assert elapsed < 1.0, "a refusal must not be delivered by waiting out the whole window"


def test_a_dangerous_command_can_never_be_remembered():
    """`rm -rf /` comes with can_always=False. However emphatically the person says 'always', the gate
    must not be handed a persist."""
    async def ask(card):
        assert card.can_always is False
        return (True, True)

    async def go():
        _, bridge = _bridge("a3", ask=ask)
        out = await interaction.request_approval("a3", "rm -rf /", timeout=5.0, channel="text")
        await bridge.aclose()
        events.unregister("a3")
        return out

    approved, always = _run(go())
    assert approved is True and always is False


def test_a_typed_card_is_answered_with_the_value_shape_the_asker_reads():
    async def ask(card):
        assert card.blocking and card.input_kind == "text"
        return "https://example.com/report"

    async def go():
        _, bridge = _bridge("a4", ask=ask)
        out = await interaction.request_input("a4", "paste the link", "text", timeout=5.0)
        await bridge.aclose()
        events.unregister("a4")
        return out

    assert _run(go()) == "https://example.com/report"


def test_only_a_card_with_a_future_behind_it_is_ever_put_to_the_person():
    """An open_link offer and a wait=False input card are announcements — the value goes back as a normal
    message, or nowhere. A `clear` is a card that has already gone. All three would otherwise stop the
    terminal to ask about something nobody is waiting on, and a clear even carries a request_id to
    resolve with."""
    asked: list[Card] = []

    async def ask(card):
        asked.append(card)
        return "yes"

    async def go():
        _, bridge = _bridge("a5", ask=ask)
        await interaction.open_link_card("a5", "https://example.com", "the source")
        await interaction.open_input_card("a5", "type your name")
        await _until(lambda: bridge.queue.qsize() == 0)
        refused = bridge._approvals.present(
            Card(request_id="gone", mode="clear", label="", wait=True)
        )
        await bridge.aclose()
        events.unregister("a5")
        return refused

    assert _run(go()) is False
    assert asked == []


def test_a_card_that_arrives_with_no_request_id_is_left_alone():
    """Resolving without one matches newest-first — it would answer somebody else's question."""
    asked: list[Card] = []

    async def go():
        approvals = Approvals("a6", ask=lambda card: asked.append(card) or (True, False))
        return approvals.present(Card(request_id=None, mode="approval", label="rm -rf /"))

    assert _run(go()) is False
    assert asked == []


def test_a_card_cleared_under_the_prompt_stops_being_asked_and_the_other_still_is():
    """A turn cancelled mid-wait emits `need_input{mode:clear}` naming its own card. The terminal must
    drop that prompt and move on to the one still waiting for an answer."""
    asked: list[str] = []

    async def go():
        opened: list[dict] = []
        both, cleared = asyncio.Event(), asyncio.Event()

        async def ask(card):
            asked.append(card.label)
            await cleared.wait()
            return (True, False)

        def seen(kind, frame):
            if kind != "need_input":
                return
            if frame.get("mode") == "clear":
                cleared.set()
            else:
                opened.append(frame)
                if len(opened) == 2:
                    both.set()

        _, bridge = _bridge("a7", ask=ask, seen=seen)
        first = asyncio.create_task(interaction.request_approval("a7", "one", timeout=5.0, channel="text"))
        second = asyncio.create_task(interaction.request_approval("a7", "two", timeout=5.0, channel="text"))
        await asyncio.wait_for(both.wait(), timeout=10.0)
        first.cancel()                       # the turn was interrupted while its card was on screen
        try:
            await first
        except asyncio.CancelledError:
            pass
        out = await second
        await bridge.aclose()
        events.unregister("a7")
        return out

    assert _run(go()) == (True, False)
    assert asked == ["one", "two"], f"the cleared card was still holding the prompt: {asked}"


def test_each_answer_carries_its_own_card_id_so_the_oldest_is_the_one_answered():
    """Two cards can be open on one session (in work mode a helper and its parent share it). A terminal
    asks in the order they opened; resolve() with no request_id matches NEWEST-first, so the first answer
    would land on the second card — the person approves something they never read."""
    order: list[str] = []

    async def go():
        opened: list[dict] = []
        both = asyncio.Event()

        async def ask(card):
            order.append(card.label)
            await both.wait()                # hold, so both cards really are open at once
            return (card.label == "second", False)

        def seen(kind, frame):
            if kind == "need_input":
                opened.append(frame)
                if len(opened) == 2:
                    both.set()

        _, bridge = _bridge("a8", ask=ask, seen=seen)
        first = asyncio.create_task(interaction.request_approval("a8", "first", timeout=5.0, channel="text"))
        second = asyncio.create_task(interaction.request_approval("a8", "second", timeout=5.0, channel="text"))
        out = await asyncio.gather(first, second)
        await bridge.aclose()
        events.unregister("a8")
        return out

    first_out, second_out = _run(go())
    assert order == ["first", "second"], "the terminal asks oldest-first"
    assert first_out == (False, False), "the first card was given the SECOND card's answer"
    assert second_out == (True, False)


def test_the_default_prompt_reads_off_the_loop_and_still_wakes_the_turn(monkeypatch):
    """The whole default path at once: a blocking read that must not sit on the event loop (nothing would
    drain while the person reads the command), answered back across the thread boundary."""
    where: list[str] = []

    def fake_input(prompt=""):
        where.append(threading.current_thread().name)
        return "y"

    monkeypatch.setattr("builtins.input", fake_input)

    async def go():
        _, bridge = _bridge("a9")            # no ask= → the real ask_at_terminal
        t0 = time.monotonic()
        out = await interaction.request_approval("a9", "npm test", timeout=5.0, channel="text")
        await bridge.aclose()
        events.unregister("a9")
        return out, time.monotonic() - t0, threading.current_thread().name

    out, elapsed, main = _run(go())
    assert out == (True, False)
    assert where and where[0] != main, "the read must happen off the loop, or the queue stops draining"
    assert elapsed < 1.0


def test_an_answer_from_the_stdin_thread_wakes_the_turn_at_once():
    """The default prompt reads stdin in a worker thread so the queue keeps draining. See the next test
    for what a plain interaction.resolve() from there costs."""
    async def go():
        queue = events.register("t1")
        approvals = Approvals("t1")
        task = asyncio.create_task(interaction.request_approval("t1", "npm test", timeout=1.0,
                                                                channel="text"))
        frame = await _card_frame(queue)

        def worker():
            time.sleep(0.05)             # let the loop settle into its select() first
            approvals.answer(frame["request_id"], {"approved": True, "always": False})

        t0 = time.monotonic()
        threading.Thread(target=worker).start()
        out = await task
        events.unregister("t1")
        return out, time.monotonic() - t0

    out, elapsed = _run(go())
    assert out == (True, False)
    assert elapsed < 0.5, f"answered in {elapsed:.3f}s — call_soon_threadsafe is what wakes the selector"


def test_resolving_off_the_loop_records_the_answer_and_leaves_the_turn_asleep():
    """Why Approvals.answer is not a plain call. Future.set_result schedules its callbacks with
    call_soon, which appends to the ready queue WITHOUT waking the selector: the turn sits there until
    its own timer fires — 180 s on this channel in production."""
    async def go():
        queue = events.register("t2")
        task = asyncio.create_task(interaction.request_approval("t2", "npm test", timeout=1.0,
                                                                channel="text"))
        frame = await _card_frame(queue)
        recorded: list[bool] = []

        def worker():
            time.sleep(0.05)
            recorded.append(interaction.resolve("t2", {"approved": True, "always": False},
                                                frame["request_id"]))

        t0 = time.monotonic()
        threading.Thread(target=worker).start()
        await task
        events.unregister("t2")
        return recorded, time.monotonic() - t0

    recorded, elapsed = _run(go())
    assert recorded == [True], "the answer WAS recorded — that is what makes this so easy to miss"
    assert elapsed > 0.5, f"woke in {elapsed:.3f}s — if this ever fails the platform changed, check answer()"


def test_a_secret_is_read_with_the_echo_off_and_never_printed_back(monkeypatch, capsys):
    typed: list[str] = []

    def fake_getpass(prompt=""):
        typed.append("getpass")
        return "sk-live-do-not-print"

    def fake_input(prompt=""):
        typed.append("input")
        return "sk-live-do-not-print"

    # Pinned rather than measured: this asks what happens on a terminal that CAN hide a key, and
    # whether the one running the suite is such a terminal is not this test's question.
    monkeypatch.setattr(secret, "can_hide", lambda: True)
    monkeypatch.setattr(getpass, "getpass", fake_getpass)
    monkeypatch.setattr("builtins.input", fake_input)

    value = ask_at_terminal(Card(request_id="r", mode="input", label="API key", input_kind="key", wait=True))
    plain = ask_at_terminal(Card(request_id="r", mode="input", label="a link", input_kind="text", wait=True))

    assert typed == ["getpass", "input"], "a key must never go through an echoing read"
    assert value == "sk-live-do-not-print" and plain == "sk-live-do-not-print"
    out = capsys.readouterr()
    assert "sk-live" not in out.out + out.err, "and it must not end up in the scrollback either"


def test_a_terminal_that_cannot_hide_a_secret_still_reads_it_and_says_nothing(monkeypatch, capsys):
    """No `/dev/tty` and a pipe for stdin — a CI runner, `docker run` without `-t`. `getpass` gives up
    there, prints two lines of CPython onto whatever she had just drawn, and reads stdin anyway. The
    read is ours instead, so the screen stays hers; `EOFError` at end of input is what this caller
    already treats as no answer."""
    card = Card(request_id="r", mode="input", label="API key", input_kind="key", wait=True)
    monkeypatch.setattr(secret, "can_hide", lambda: False)

    monkeypatch.setattr("sys.stdin", io.StringIO("sk-live-do-not-print\n"))
    assert ask_at_terminal(card) == "sk-live-do-not-print"

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert ask_at_terminal(card) is None, "end of input is an answer, not a hang"

    said = "".join(capsys.readouterr())
    assert "GetPassWarning" not in said and "may be echoed" not in said, said
    assert "sk-live" not in said, "and it must not end up in the scrollback either"


def test_the_session_drains_its_own_queue_and_answers_its_own_cards():
    from kotoba.cli.session import Session

    async def ask(card):
        return (True, False)

    async def go():
        session = await Session.open(ask=ask)
        out = await interaction.request_approval(session.session_id, "npm test", timeout=5.0,
                                                 channel="text")
        empty = session.queue.qsize() == 0
        await session.close()
        return out, empty

    out, empty = _run(go())
    assert out == (True, False)
    assert empty, "a registered but undrained queue is a card nobody sees"
