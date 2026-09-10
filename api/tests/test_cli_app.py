"""The cable: her stream and her event queue arriving at a screen that has to print each exactly once."""
from __future__ import annotations

import asyncio
import io
import time
from types import SimpleNamespace

import pytest
from conftest import needs_posix_terminal

from kotoba.cli import state
from kotoba.cli.app import App
from kotoba.cli.approvals import Card
from kotoba.cli.events_bridge import Step
from kotoba.cli.render import rows
from kotoba.cli.input import commands, keys
from kotoba.cli.render import footer
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console


def wired(chunks: list[str] = (), *, interactive: bool = False) -> tuple[App, io.StringIO]:
    caps = Caps(color="none", background="dark", unicode=True, interactive=interactive,
                width=76, g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    asked: list[str] = []

    async def ask(text: str, on_text=None, on_face=None) -> str:
        """The real drain, so a tag in `chunks` is eaten and reported exactly as it is in production."""
        from kotoba.cli.session import Session
        from kotoba.core.stream import DONE_SENTINEL

        asked.append(text)
        queue: asyncio.Queue = asyncio.Queue()
        for chunk in chunks:
            queue.put_nowait(chunk)
        queue.put_nowait(DONE_SENTINEL)
        return await Session._drain(Session.__new__(Session), queue, [], on_text, on_face)

    app.session = SimpleNamespace(ask=ask, events=SimpleNamespace(steps={}, settle=lambda: None), asked=asked)
    return app, buf


def test_her_stream_reaches_the_screen_as_blocks_and_each_one_is_printed_once():
    app, buf = wired(["Hello ", "there.\n", "\nAnd a se", "cond one."])
    asyncio.run(app._turn("hola"))
    out = buf.getvalue()
    assert out.count("Hello there.") == 1
    assert out.count("And a second one.") == 1


def test_every_turn_gets_her_nameplate_once_even_after_a_greeting_said_outside_one():
    app, buf = wired(["Hello there."])
    app.screen.say("a greeting, before any turn")
    asyncio.run(app._turn("hola"))
    asyncio.run(app._turn("otra vez"))
    assert buf.getvalue().count("KOTOBA") == 3


def test_a_fence_she_never_closed_still_arrives_as_code_and_not_as_three_backticks():
    app, buf = wired(["Here:\n\n```python\nx = 1"])
    asyncio.run(app._turn("code please"))
    assert "```" not in buf.getvalue()
    assert "x = 1" in buf.getvalue()


def a_step(app, kind: str, phase: str, **frame) -> None:
    app.session.events.steps["a"] = Step("a", "shell", "pytest -q", kind)
    app._event("step", {"id": "a", "phase": phase, **frame})


def test_a_running_step_lives_in_the_region_and_only_its_landing_is_committed():
    """Scrollback never repaints, so a spinner printed into it is an animation that has stopped. The
    running row belongs to the live region and the transcript gets exactly one row, when it lands."""
    app, buf = wired()
    app.turn_start = time.monotonic()
    a_step(app, "running", "start")
    assert [t.state for t in app.tools] == ["running"]
    assert "pytest" not in buf.getvalue()
    a_step(app, "ok", "done", ok=True)
    lines = [line for line in buf.getvalue().split("\n") if "pytest" in line]
    assert len(lines) == 1 and lines[0].startswith(app.caps.g["ok"])
    assert len(app.tools) == 1


def test_a_step_with_no_turn_around_it_prints_nothing_over_the_pinned_frame():
    """Out at the prompt the frame belongs to prompt_toolkit and a print splits it, so the long job's
    rows are banked on the job and land with its receipt. Nothing at all when there is no job either."""
    app, buf = wired()
    a_step(app, "running", "start")
    assert buf.getvalue() == "" and app.tools == []
    app.work = state.Work("the long one")
    a_step(app, "running", "start")
    a_step(app, "ok", "done", ok=True)
    assert buf.getvalue() == ""
    assert [t.state for t in app.work.tools] == ["ok"]


def test_an_emotion_frame_moves_her_face_and_nothing_else():
    app, buf = wired()
    app._event("emotion", {"type": "emotion", "emotion": "excited"})
    app.screen.face.settle()
    assert app.screen.face.emotion == "excited"
    assert buf.getvalue() == ""


def test_a_frame_the_renderer_chokes_on_never_takes_the_turn_down():
    app, _ = wired()
    app._event("step", {"id": None})
    app._event("task_list", {"tasks": "not a list"})


def test_an_open_plan_lives_in_the_band_and_the_transcript_gets_one_receipt_at_close():
    """The receipt stopped being the progress channel: while the list is open not one row of it may
    reach the transcript — the band above the box is where it lives — and the close commits the plan's
    rows exactly once, however many frames repeat the closed state."""
    from kotoba.cli.render import rows as rowmod

    app, buf = wired()
    app.turn_start = time.monotonic()
    tasks = [{"id": "1", "text": "one", "status": "active", "order": 1},
             {"id": "2", "text": "two", "status": "pending", "order": 2}]
    app._event("task_list", {"list_id": "L", "title": "the plan", "status": "open", "tasks": tasks})
    tasks[0]["status"] = "done"
    tasks[1]["status"] = "active"
    app._event("task_list", {"list_id": "L", "title": "the plan", "status": "open", "tasks": tasks})
    assert buf.getvalue().count("the plan") == 0, "an open plan may not print into her messages"
    band = rowmod.band_rows(app.caps, app._state(), 74)
    assert any("two" in r.plain for r in band), "the band is where the open plan lives"
    tasks[1]["status"] = "done"
    closed = {"list_id": "L", "title": "the plan", "status": "done", "tasks": tasks}
    app._event("task_list", closed)
    app._event("task_list", dict(closed))
    assert buf.getvalue().count("the plan") == 1
    assert rowmod.band_rows(app.caps, app._state(), 74) == []


def test_an_approval_no_terminal_can_answer_is_refused_rather_than_left_to_time_out():
    app, buf = wired()
    card = Card(request_id="r1", mode="approval", label="rm -rf build", family="command")
    assert asyncio.run(app._ask(card)) == (False, False)
    assert "rm -rf build" in buf.getvalue()


def test_the_turn_footer_only_appears_when_the_turn_actually_cost_something():
    app, buf = wired(["done."])
    asyncio.run(app._turn("hola"))
    assert "tools" not in buf.getvalue()


def test_a_slash_command_is_answered_by_the_cli_and_never_reaches_her():
    app, buf = wired()
    assert asyncio.run(app._slash(commands.parse("/help"))) is False
    assert app.session.asked == []
    # The heading, not a command down the list: /help is fitted to the window and this app has the
    # default 24 rows, so the tail of the table is named rather than drawn (`render/listing`).
    assert "C O M M A N D S" in buf.getvalue()


def test_an_unknown_slash_command_says_so_instead_of_being_sent_as_a_question():
    app, buf = wired()
    assert asyncio.run(app._slash(commands.parse("/nope"))) is False
    assert "/nope" in buf.getvalue()
    assert app.session.asked == []


def test_quit_is_the_one_command_that_ends_the_loop():
    app, _ = wired()
    assert asyncio.run(app._slash(commands.parse("/quit"))) is True
    assert asyncio.run(app._slash(commands.parse("/exit"))) is True


def test_a_bare_slash_is_someone_opening_the_list_and_not_a_command():
    assert commands.is_command("/") is False
    assert commands.is_command("/help") is True


def test_the_state_the_region_reads_is_the_turns_and_it_goes_back_to_rest_after_one():
    app, _ = wired(["hello"])
    assert app._state().at_rest and app._state().turn_start == 0.0
    app.turn_start = time.monotonic()
    app.status = "shell"
    app.tools.append(state.Tool("shell", "pytest -q"))
    st = app._state()
    assert not st.at_rest and st.tools[0].state == "running" and st.status == "shell"
    asyncio.run(app._turn("hola"))
    # The idle bar carries her model and never a clock, which is what a zero turn_start means.
    assert app._state().at_rest and app._state().turn_start == 0.0


@needs_posix_terminal
def test_the_in_turn_clock_runs_on_wall_time_and_a_card_does_not_slow_it():
    """Her stream yields nothing across a tool call, so the region is refreshed from here.

    RE-RECORDED. This pinned the opposite: a card halved the beat to 2 Hz, justified as saving the
    10 kB/s of a whole-region reprint where nothing moved but a pulse. Measured on the real binary,
    that trade was the lag being reported — every reaction a gate has, the refused-key flash
    included, waited for the next half-second frame (key-to-glass p50 240 ms, p90 410 ms, against
    52 ms for a running tool at 12 Hz). And the bill it was sized against is smaller than the
    17.5 kB/s the running tool already spends at 12 Hz, which is considered fine. `--calm` still folds;
    a card no longer does."""
    app, _ = wired(interactive=True)
    app.caps.fps = 50
    beats: list[float] = []
    app.region = SimpleNamespace(refresh=lambda: beats.append(time.monotonic()))

    async def spin(seconds: float) -> None:
        clock = asyncio.create_task(app._clock())
        await asyncio.sleep(seconds)
        clock.cancel()

    asyncio.run(spin(0.25))
    busy = len(beats)
    beats.clear()
    app.approval = object()
    asyncio.run(spin(0.25))
    assert busy >= 8, busy
    assert len(beats) >= 8, f"a card slowed the beat to {len(beats)} frames in a quarter second"

    beats.clear()
    app.caps.reduced_motion = True
    asyncio.run(spin(0.25))
    assert beats == [], "--calm is the one fold left, and it still folds"


def test_the_beat_wakes_only_for_the_long_job_and_the_landing_it_owes():
    app, _ = wired()
    assert app._wake() is None
    app.work = state.Work("read both pages")
    # One frame of the band's mark: the beat and the animation are the same number by construction
    # (`rows.BEAT_HZ`), so neither can drift into waking without drawing or drawing without waking.
    assert app._wake() == pytest.approx(1.0 / rows.BEAT_HZ)
    app.work.state, app.work.stopped = "ok", time.monotonic()
    assert app._wake() == 0.02 and app._deliverable()
    app.turn_start = time.monotonic()
    assert not app._deliverable(), "a landing may never split a turn's own region"


def test_a_terminal_that_will_not_say_where_the_cursor_is_stops_being_offered_the_landing():
    app, _ = wired()
    app.work = state.Work("read both pages", state="ok")
    app._no_room = True
    assert not app._deliverable() and app._wake() is None


def test_the_long_jobs_receipt_lands_once_slim_and_the_bar_lets_go_of_it():
    """The receipt is a receipt now, not the roster's third copy: the closing bracket, her words and
    the `/work N` pointer land; the per-tool rows stay reachable through `/work` — nothing the person
    can no longer reach anywhere may be deleted."""
    from kotoba.cli import slash
    from kotoba.cli.input import commands

    app, buf = wired()
    app.work = state.Work("read both pages", state="ok", summary="Wrote the note up.")
    app.work.tools.append(state.Tool("web", "web_search 'x'", state="ok", detail="7 results"))
    app.works.append(app.work)
    assert app._land_work() is True
    out = buf.getvalue()
    assert "web_search 'x'" not in out, "the tool rows live in /work N, not in the receipt"
    assert "Wrote the note up." in out and "/work 1 opens the whole of it" in out
    assert app._land_work() is False
    assert buf.getvalue().count("Wrote the note up.") == 1
    assert footer.bar_kind(app._state()) == "idle"
    asyncio.run(slash.run(app, commands.Command("/work", "1")))
    assert "web_search 'x'" in buf.getvalue()


def test_the_bar_is_recorded_at_the_render_and_not_at_the_wake():
    """Two wakes inside the same half-period have to compare equal, or the beat repaints what the
    screen already shows and the idle invariant is a number that is usually right."""
    app, _ = wired()
    app.work = state.Work("read both pages")
    app._rendered(None)
    assert app._bar_state() == app._painted
    app.work.state = "ok"
    assert app._bar_state() != app._painted


def test_her_plate_settles_when_it_is_printed_and_blinks_when_it_is_repainted():
    """`still()` settles the pending mood first, and settling is what the blink is made of — so the
    live copy the region draws every frame may never ask for it."""
    app, _ = wired()
    app.screen.face.set("excited")
    assert not app.screen.face.settled
    app.parts.head_plate(live=True)
    assert not app.screen.face.settled, "the repainted plate settled the mood the blink is made of"
    app.parts.head_plate()
    assert app.screen.face.settled and app.screen.face.emotion == "excited"


def running(app, n: int) -> None:
    app.tools += [state.Tool("shell", f"pytest -q #{i}") for i in range(n)]


def test_what_is_typed_while_she_works_is_echoed_in_the_box_and_never_swallowed():
    app, _ = wired()
    app.turn_start = time.monotonic()
    app._typeahead("and then check the pro")
    assert app._state().typing == "and then check the pro"
    app._typeahead("\x7f\x7f\x7fxy")
    assert app._state().typing == "and then check the xy"


def test_enter_queues_the_line_mid_turn_and_the_turn_hands_it_to_the_loop_to_send():
    """A second message would kill the one running, so `enter` parks it visibly and the queue is
    drained at the prompt — where `flow_view` has already been drawing it as `⏎ queued`."""
    app, _ = wired(["ok."])

    async def ask(text: str, on_text=None, on_face=None) -> str:
        app.session.asked.append(text)
        app._typeahead("and then check the proxy\r")
        app._typeahead("  \r")
        on_text("ok.")
        return "ok."

    app.session.ask = ask
    asyncio.run(app._turn("hola"))
    assert app.pending_send == ["and then check the proxy"], "blank lines are not messages"
    assert app.queued == () and app.typing == ""


def test_a_burst_typed_mid_turn_is_one_message_and_not_one_turn_per_line():
    """`tmux send-keys`, `xdotool type` and the ssh and serial clients that send no paste markers write
    the whole paragraph in ONE read. Split on every line ending it queued a message per line, and each
    of those became its own turn and its own bill. A person types a line and stops, so their Enter is
    still the last thing in its own read and still goes on its own."""
    app, _ = wired()
    app.turn_start = time.monotonic()
    app._typeahead("look at the log\rthen the config\rand tell me what broke\r")
    assert app.queued == ("look at the log\nthen the config\nand tell me what broke",)

    app.queued, app.typing = (), ""
    app._typeahead("h")
    app._typeahead("ola")
    app._typeahead("\r")
    assert app.queued == ("hola",), "a line a person typed and sent still goes on its own"

    app.queued, app.typing = (), ""
    app._typeahead("one\r\ntwo\r\n")
    assert app.queued == ("one\ntwo",), "CRLF is one line ending, not two"

    app.queued, app.typing = (), ""
    app._typeahead("half a line\rand the rest")
    assert app.queued == () and app.typing == "half a line\nand the rest", (
        "a burst with no line ending on the end waits in the box, as a paste does at the prompt")


def test_a_queued_line_is_taken_before_the_prompt_is_ever_drawn_and_in_the_order_typed():
    app, _ = wired(["ok."])
    app.prompt = SimpleNamespace(session=None, pending="", below=0,
                                 ask_async=lambda pre_run=None: _eof())
    app.pending_send = ["first", "second"]
    asyncio.run(app._loop())
    assert app.session.asked == ["first", "second"]


async def _eof():
    return None


def test_esc_with_one_thing_running_stops_her_and_with_two_it_names_what_would_die_first():
    """`esc` on a single tool is the gesture everybody already means; with a line-up out there the
    first press only arms, because it is somebody else's twenty minutes."""
    app, _ = wired()
    app.turn_start = time.monotonic()
    running(app, 1)
    with pytest.raises(keys.Interrupted):
        app._typeahead("\x1b")
    app.tools = []
    running(app, 2)
    app._typeahead("\x1b")
    assert app._state().armed_until > time.monotonic()
    assert "esc again" in footer.armed_twins(app._state())[0]
    with pytest.raises(keys.Interrupted):
        app._typeahead("\x1b")


def test_the_arm_lapses_so_an_esc_three_seconds_later_is_a_first_press_again():
    app, _ = wired()
    app.turn_start = time.monotonic()
    running(app, 2)
    app._typeahead("\x1b")
    app.armed_until = time.monotonic() - 0.01
    app._typeahead("\x1b")
    assert app._state().armed_until > time.monotonic(), "the lapsed arm killed the turn instead"


def test_an_arrow_key_in_the_same_read_as_a_fast_typist_never_touches_the_turn():
    r"""N2: `qzx!\x1b[A` arrives in ONE read and a bare `\x1b` is the tail of every arrow key, so the
    sequences come out FIRST — guarding on `chunk[:2]` shipped in all three earlier directions."""
    app, _ = wired()
    app.turn_start = time.monotonic()
    running(app, 1)
    app._typeahead("qzx!\x1b[A")
    app._typeahead("\x1b[A")
    app._typeahead("\x1b[H")
    app._typeahead("\x1bOB")
    assert app._state().typing == "qzx!" and not app._state().armed_until


def test_tab_peeks_at_one_helper_and_ctrl_r_folds_the_whole_line_up():
    app, _ = wired()
    app.turn_start = time.monotonic()
    app.helpers += [state.Helper("h1", "helper", "read page 1"),
                    state.Helper("h2", "helper", "read page 2")]
    app._typeahead("\t")
    assert app.peeked == 1
    app._typeahead("\t\t")
    assert app.peeked == 0, "the peek cycles off the end rather than sticking on the last one"
    app._typeahead("\x12")
    assert app._state().folded


def test_the_backends_own_word_for_a_cut_step_is_the_row_that_lands_not_the_sweeps_guess():
    """`agentic_loop`'s finally closes every row it left open — `interrupted=True`, one per still-running
    tool — a loop-turn AFTER the await returns. Swept first, three `delegate` rows read `stopped` while
    their helpers were plainly still out, and the real frames were then dropped: `_step` has nowhere to
    put one once `turn_start` is back to zero."""
    from kotoba.cli.events_bridge import EventBridge

    app, buf = wired()
    queue: asyncio.Queue = asyncio.Queue()
    app.session.events = EventBridge(queue, on_event=app._event)

    async def ask(text: str, on_text=None, on_face=None) -> str:
        queue.put_nowait({"type": "task", "kind": "step", "phase": "start", "id": "c1",
                          "step_kind": "tool", "action": "delegate"})
        queue.put_nowait({"type": "task", "kind": "subagent_spawned", "id": "h1", "goal": "look up A"})
        await asyncio.sleep(0)
        app.session.events.settle()
        queue.put_nowait({"type": "task", "kind": "subagent_done", "id": "h1", "ok": False,
                          "interrupted": True, "summary": "The helper was stopped."})
        queue.put_nowait({"type": "task", "kind": "step", "phase": "done", "id": "c1", "ok": False,
                          "interrupted": True, "result": "(interrupted)"})
        return ""

    app.session.ask = ask
    asyncio.run(app._turn("delega tres"))
    assert [t.state for t in app.tools] == ["interrupted"]
    assert [t.detail for t in app.tools] == ["stopped"], "a machine token is not a phrase for the row"
    assert [h.state for h in app.helpers or app.last_helpers] == ["interrupted"], \
        "a helper the turn was cut over is not one that failed"
    assert "(interrupted)" not in buf.getvalue()


def test_the_boot_buffer_is_the_editors_opening_line_and_is_never_read_a_second_time():
    """Typed while the terminal was still being probed, it is the box's opening line and nothing else.
    Read again by the turn it seeds a `typing` nobody typed, which the next enter queues — and the whole
    message goes out, and is echoed, twice."""
    app, _ = wired()
    assert app.caps.leftover == ""
    app.caps.leftover = "puedes delegar un agente"
    app2 = App(app.caps, app.screen, prompt=None)
    assert app2.prompt.pending == "puedes delegar un agente" and app2.caps.leftover == ""


def test_the_terminal_keeps_every_bracket_that_is_not_one_of_her_audio_tags():
    """`keep_valid=False` dropped ALL of them, so a citation she wrote as `([Forbes](url))` reached the
    screen as `((url))`, a footnote as nothing, and `arr[0]` in a snippet as `arr`."""
    from kotoba.cli.session import Session
    from kotoba.core.stream import DONE_SENTINEL

    async def drained(text: str) -> str:
        queue: asyncio.Queue = asyncio.Queue()
        for ch in text:
            queue.put_nowait(ch)
        queue.put_nowait(DONE_SENTINEL)
        return await Session._drain(Session.__new__(Session), queue, [])

    said = "[warmly] Mira ([Forbes](https://x.example/a)). Y [1], y arr[0]."
    assert asyncio.run(drained(said)) == "Mira ([Forbes](https://x.example/a)). Y [1], y arr[0]."


def test_a_voice_still_refuses_every_bracket_it_cannot_perform():
    from kotoba.core import stream

    assert stream.AudioTagFilter(keep_valid=False).feed("[warmly] Mira [Forbes].") == " Mira ."
    assert stream.AudioTagFilter(keep_valid=True).feed("[warmly] Mira [Forbes].") == "[warmly] Mira ."


def _drained(text: str, faces: list | None = None) -> str:
    from kotoba.cli.session import Session
    from kotoba.core.stream import DONE_SENTINEL

    async def go() -> str:
        queue: asyncio.Queue = asyncio.Queue()
        for ch in text:
            queue.put_nowait(ch)
        queue.put_nowait(DONE_SENTINEL)
        return await Session._drain(Session.__new__(Session), queue, [], None,
                                    None if faces is None else faces.append)

    return asyncio.run(go())


def test_a_bracket_is_a_tag_only_when_its_word_is_one_and_nothing_is_glued_to_it():
    """The rule, case by case. Keeping every unknown bracket printed `[thinking]` and `[determined]` on
    her replies; dropping every one rewrote her code. A tag stands alone, a link is followed by `(`, an
    index is preceded by a word character — and a word that is not a tag is never one of them."""
    for said, shown in (
        ("[thinking] Mmm... a ver.", "Mmm... a ver."),
        ("[determined] Ya lo estoy delegando.", "Ya lo estoy delegando."),
        ("[warmly] De nada.", "De nada."),
        ("[happy](https://a.b) es un enlace", "[happy](https://a.b) es un enlace"),
        ("x[happy] es un indice", "x[happy] es un indice"),
        ("m[i][j] y la nota [2]", "m[i][j] y la nota [2]"),
        ("[Forbes](https://x.example/a) y [1]", "[Forbes](https://x.example/a) y [1]"),
        ("un [tag roto", "un [tag roto"),
        ("[warmly]", ""),
    ):
        assert _drained(said) == shown, said


def test_her_face_is_named_by_the_tag_before_a_single_character_of_hers_is_echoed():
    """The tag is the first thing she writes and the filter holds everything until the `]` closes, so
    the face is chosen with zero text on the glass — which is the whole difference between a portrait
    painted right and a portrait repainted."""
    from kotoba.cli.session import Session
    from kotoba.core.stream import DONE_SENTINEL

    seen: list[tuple[str, str]] = []

    async def go() -> str:
        queue: asyncio.Queue = asyncio.Queue()
        for chunk in ("[determi", "ned]", " Ya voy.", " Un momento."):
            queue.put_nowait(chunk)
        queue.put_nowait(DONE_SENTINEL)
        return await Session._drain(Session.__new__(Session), queue, [],
                                    lambda t: seen.append(("text", t)),
                                    lambda f: seen.append(("face", f)))

    asyncio.run(go())
    assert seen[0] == ("face", "determined")
    assert [kind for kind, _ in seen[1:]] == ["text"] * (len(seen) - 1)


def test_the_mood_the_backend_settles_on_never_moves_her_face_after_she_has_spoken():
    """The backend emits its emotion with her LAST chunk. Applied, it erases the portrait the region
    already painted and paints another — the flash. Her tag got there first, so it is ignored."""
    app, _ = wired(["[determined] Ya voy."])
    real = app.session.ask

    async def ask(text, on_text=None, on_face=None):
        out = await real(text, on_text=on_text, on_face=on_face)
        app._event("emotion", {"type": "emotion", "emotion": "happy"})
        return out

    app.session.ask = ask
    asyncio.run(app._turn("hola"))
    assert app.screen.face.emotion == "determined"


def test_a_reply_with_no_tag_at_all_keeps_the_face_it_started_speaking_in():
    """The fallback has to not flash either: with nothing to choose from, the face freezes on her first
    character rather than catching up once the reply is already committed — and with nothing to choose
    from, what she started speaking in is the clean mood the turn began on, never the last reply's."""
    app, _ = wired(["Sin tag ninguno."])
    real = app.session.ask

    async def ask(text, on_text=None, on_face=None):
        out = await real(text, on_text=on_text, on_face=on_face)
        app._event("emotion", {"type": "emotion", "emotion": "excited"})
        return out

    app.session.ask = ask
    app.screen.face.set("thinking", instant=True)
    asyncio.run(app._turn("hola"))
    assert app.screen.face.emotion == "neutral"


def test_a_new_turn_starts_from_a_clean_mood_and_never_wears_the_last_replys():
    """Reported live: once a reply had put `thinking` on her nameplate, every nameplate after it read
    `( ￣_￣ )?`. The only reset there was lived in the prompt's toolbar, so a line queued while she
    worked — or any path that reaches a turn without drawing a prompt first — carried the mood over."""
    app, buf = wired()
    plates = []
    for chunks in (["[thinking] Dejame pensarlo."], ["Ya lo tengo."], ["[happy] Aqui esta."]):
        app.session.ask = _replying(app, chunks)
        asyncio.run(app._turn("hola"))
        plates.append([ln for ln in buf.getvalue().splitlines() if "KOTOBA" in ln][-1])
    assert "( ￣_￣ )?" in plates[0], "her own tag still decides the turn it opened"
    assert "( ･ω･ )" in plates[1], "a reply with no tag is neutral, not the last reply's thinking"
    assert "( ^ω^ )" in plates[2]


def _replying(app, chunks):
    async def ask(text, on_text=None, on_face=None):
        from kotoba.cli.session import Session
        from kotoba.core.stream import DONE_SENTINEL

        queue: asyncio.Queue = asyncio.Queue()
        for chunk in chunks:
            queue.put_nowait(chunk)
        queue.put_nowait(DONE_SENTINEL)
        return await Session._drain(Session.__new__(Session), queue, [], on_text, on_face)

    return ask


def test_a_tool_still_puts_its_own_face_on_while_she_has_not_started_speaking():
    """The latch is her speech, not the turn: a focus frame before her first character is the face she
    works in, and refusing it would leave her neutral through every tool call."""
    app, _ = wired()
    app.turn_start = time.monotonic()
    app._event("emotion", {"type": "emotion", "emotion": "thinking"})
    app.screen.face.settle()
    assert app.screen.face.emotion == "thinking"
    app._chunk("Ya lo tengo.")
    app._event("emotion", {"type": "emotion", "emotion": "sad"})
    app.screen.face.settle()
    assert app.screen.face.emotion == "thinking"


def test_a_card_nobody_answered_is_never_read_as_always_allow():
    """`key in "aA"` is a SUBSTRING test and `"" in "aA"` is True, so the value every abandoned card
    carries answered "always allow this family, forever" — the one thing this surface may never do."""
    from kotoba.cli.render import cards

    app, _ = wired()
    card = Card(request_id="r1", mode="approval", label="subl notes.md", family="subl",
                can_always=True, can_always_exact=True)
    assert app._answer("", card) == (False, False, False)
    assert app._answer("n", card) == (False, False, False)
    assert app._answer("y", card) == (True, False, False)
    assert app._answer("a", card) == (True, True, False)
    assert app._answer("t", card) == (True, False, True)
    assert cards.answered("")[1] == "no"


def test_the_inline_card_stops_echoing_her_prose_and_carries_the_wire_grant():
    """The headline used to be `blocks.partial` — the same prose the person is already reading two rows
    above, and across loop iterations one glued lump with no separator. The card now names its intent
    with the fixed line and takes `can_always` off the wire instead of always offering `a`."""

    app, _ = wired()
    app._chunk("Mmm, claro, Jordan. Voy a buscar ese archivo, te lo debía.")
    card = Card(request_id="r1", mode="approval", label="pwd && ls -la", family="pwd",
                can_always=False)
    captured = {}

    def reader(drawn, held=None):
        captured["drawn"] = drawn
        return "n"

    app._read_approval = reader
    asyncio.run(app._inline(card))
    drawn = captured["drawn"]
    assert app.blocks.partial, "her prose was in flight — the card had something to echo and refused"
    assert drawn.intent == "" and drawn.title == "I'd like to run something on your machine"
    assert drawn.can_always is False


@needs_posix_terminal
def test_a_held_card_carries_the_wire_grant_and_not_her_prose():
    app, _ = wired(interactive=True)
    app._chunk("Ya lo encontré, qué alivio.")
    card = Card(request_id="h1", mode="approval", label="npm run build && ./deploy.sh",
                family="npm", can_always=False)
    captured = {}

    def reader(drawn, held=None):
        captured["drawn"] = drawn
        return "n"

    app._read_approval = reader

    async def go():
        held = app._hold(card)
        await app._answer_held(held)
        return held

    held = asyncio.run(go())
    assert held.intent == "" and held.can_always is False
    drawn = captured["drawn"]
    assert drawn.can_always is False
    assert drawn.title == "I'd like to run something on your machine"


def test_an_a_pressed_on_a_card_that_cannot_persist_is_discarded_with_the_flash(monkeypatch):
    """The rail never offered `a`, so the keystroke is junk, not an answer: it flashes and the card
    stays open for a key that means what it says."""
    from kotoba.cli.input import keys as keymod
    from kotoba.cli.render import cards

    app, _ = wired()
    drawn = cards.Approval("npm run build && ./deploy.sh", "", "npm", can_always=False)
    app.approval = drawn
    reads = iter(["a", "y"])
    monkeypatch.setattr(keymod, "read_input", lambda timeout=0.25: next(reads, ""))
    assert app._read_approval(drawn) == "y"
    assert drawn.flash_until > 0.0, "the card has to SAY it refused the a"


def test_a_refused_keys_flash_is_the_demos_at_the_turns_rate_and_outlives_calms_frame(monkeypatch):
    """RE-RECORDED by the animation audit. This pinned `> CARD_S` on EVERY surface, from the days a
    card was sampled at 2 Hz and a 0.45 s span could fall whole between two frames. The card is
    sampled at the turn's rate now, so at 12 Hz the flash is the design prototype's 0.45 s again —
    `footer.flash_secs` — and only `--calm`, the one surface still at `CARD_S`, keeps the longer
    span. Either way it outlives one frame of the sampler that draws it, which is the invariant."""
    from kotoba.cli.input import keys as keymod
    from kotoba.cli.render import cards

    for calm, floor in ((False, 1.0 / 12), (True, footer.CARD_S)):
        app, _ = wired()
        app.caps.reduced_motion = calm
        drawn = cards.Approval("npm run build && ./deploy.sh", "", "npm", can_always=False)
        app.approval = drawn
        reads = iter(["a", "y"])
        monkeypatch.setattr(keymod, "read_input", lambda timeout=0.25: next(reads, ""))
        t0 = time.monotonic()
        assert app._read_approval(drawn) == "y"
        span = drawn.flash_until - t0
        assert span > floor, (calm, span, "a flash shorter than one frame can vanish between samples")
        assert span <= footer.flash_secs(app.caps, app._state()) + 0.01, (calm, span)
        if not calm:
            assert span <= footer.FLASH_S + 0.01, "at the turn's rate the flash is the demo's"


def test_a_word_typed_into_an_open_card_flashes_and_leaves_it_open(monkeypatch):
    """The whole defect, at the seam: she was working, he was writing, the card took the keyboard with
    no bell, and `ahora` granted a command family forever."""
    from kotoba.cli.input import keys as keymod
    from kotoba.cli.render import cards

    app, _ = wired()
    drawn = cards.Approval("subl notes.md >/dev/null 2>&1 &", "", "subl")
    app.approval = drawn
    reads = iter(["ahora lo veo", "\x1b[A", "y"])

    def read_input(timeout=0.25) -> str:
        return next(reads, "")

    monkeypatch.setattr(keymod, "read_input", read_input)
    assert app._read_approval(drawn) == "y"
    assert drawn.flash_until > 0.0, "the card has to SAY it refused what was typed at it"


def _plan_frame(*statuses, list_id="L", status="open"):
    return {"list_id": list_id, "title": "informe", "status": status,
            "tasks": [{"id": str(i), "text": f"paso {i}", "status": s, "order": i}
                      for i, s in enumerate(statuses, 1)]}


def test_the_long_jobs_plan_rides_the_band_and_only_its_close_is_banked_on_the_receipt():
    """The runner is detached and emits these while you sit at the prompt, where a print splits the
    pinned frame. The open frames move the BAND — the bar's `_bar_state` fingerprint changes, which is
    what wakes the one surface carrying them — and the receipt banks exactly the close."""
    app, buf = wired()
    app.work = state.Work("the long one")
    before = app._bar_state()
    app._event("task_list", _plan_frame("active", "pending", "pending"))
    app._event("task_list", _plan_frame("done", "active", "pending"))
    app._event("task_list", _plan_frame("done", "done", "active"))
    assert buf.getvalue() == "", "nothing may print over the pinned frame"
    assert app.work.plans == [], "an open plan banks nothing — the band carries it"
    assert app._bar_state() != before, "a landed step has to move the band's fingerprint"
    app._event("task_list", _plan_frame("done", "done", "done", status="done"))
    assert len(app.work.plans) == 1
    app.work.state = "ok"
    app._land_work()
    assert buf.getvalue().count("informe") == 1 and "3 of 3" in buf.getvalue()


def test_the_plan_she_closes_on_the_announce_turn_reaches_the_receipt():
    """`__work_done__` is the one turn on which `task_list.prompt_note` reaches her — it is the only
    place the CLI runs the plan through `load_context` again — so it is where she ticks the steps off
    and closes the list. `_work_words` takes the job off `self.work` for that turn so its own steps
    cannot bank rows on a receipt about to print, and the closing frame went with them: the receipt
    ended on the job's last frame, steps unticked, while she was saying they were done."""
    from kotoba.core import work_state

    app, buf = wired()

    async def ask(text, on_text=None, on_face=None) -> str:
        app._event("task_list", _plan_frame("done", "done", "done", status="done"))
        return "las tres, hechas"

    app.session.session_id = "plan_sess"
    app.session.ask = ask
    app.work = state.Work("the long one")
    app._event("task_list", _plan_frame("done", "active", "pending"))
    app.work.state = "ok"
    work_state.start("plan_sess", "the long one")
    work_state.finish("plan_sess", "listo", [])
    try:
        asyncio.run(app._work_words())
    finally:
        work_state.clear("plan_sess")
    assert len(app.work.plans) == 1, "the close she sent on the announce turn belongs to the receipt"
    assert app.announcing is None
    app._land_work()
    out = buf.getvalue()
    assert "3 of 3" in out and out.count("informe") == 1
    assert "las tres, hechas" in out


def test_a_frame_that_only_moves_the_pointer_still_moves_the_bar():
    """A frame that only moves the pointer does not reprint twenty rows: it updates `bar_kind()` and
    the phrase. `_state` never carried the plan at all, so that phrase could not exist."""
    app, buf = wired()
    app._event("task_list", _plan_frame("done", "active", "pending"))
    st = app._state()
    assert st.plan is not None and st.plan.done == 1 and st.plan.total == 3
    assert footer.bar_kind(st) == "plan"
    assert footer.out_twins(st, "plan")[0] == "step 2 of 3"


def test_a_second_list_takes_the_band_over_and_each_close_still_prints_its_own_receipt():
    """Two lists in one turn: neither prints while open — the band simply follows the latest frame —
    and a close is one receipt per LIST, so B closing may not be swallowed as a duplicate of A's."""
    app, buf = wired()
    app.turn_start = time.monotonic()
    app._event("task_list", _plan_frame("active", "pending", list_id="A"))
    app._event("task_list", _plan_frame("active", "pending", list_id="B"))
    assert buf.getvalue() == "" and app.plan.list_id == "B"
    app._event("task_list", _plan_frame("done", "done", list_id="A", status="done"))
    app._event("task_list", _plan_frame("done", "done", list_id="B", status="done"))
    assert buf.getvalue().count("informe") == 2


def test_a_cut_turn_ends_the_demos_way_at_her_gutter_and_never_as_an_error():
    """The prototype's cut ending: the `◇ stopped` row at her gutter in the machine's dim, one clear
    row after it, her face embarrassed — a refusal to continue, so nothing here may wear the `×` of a
    failure. The prototype's canned closing sentence is deliberately not ported: what she already said
    is the closing prose, and nothing is invented in her name."""
    app, buf = wired()

    async def ask(text, on_text=None, on_face=None):
        on_text("Iba a contarte lo del")
        raise asyncio.CancelledError

    app.session.ask = ask
    asyncio.run(app._turn("hola"))
    out = buf.getvalue()
    assert "\n   ◇ stopped\n\n" in out, f"the row belongs at the gutter with its clear row: {out!r}"
    assert app.screen.face.emotion == "embarrassed"
    assert "×" not in out, "an interrupt is never drawn as an error"
    assert "Iba a contarte lo del" in out, "what she managed to say is kept"


def test_prose_then_tools_then_prose_reads_as_one_turn_with_her_plate_back():
    """The shape the prototype never faced: announce, tool rows at column 0, closing prose. Committed bare
    at the gutter, the closing paragraph read as a fragment of nobody's — so it re-anchors with her
    plate, the way the prototype re-plates the prose after its own `stopped` row."""
    app, buf = wired()

    async def ask(text, on_text=None, on_face=None):
        on_text("Voy a abrir el informe.\n\n")
        a_step(app, "running", "start")
        a_step(app, "ok", "done", ok=True, result="exit 0")
        on_text("Listo — ya lo tienes en pantalla.")
        return "..."

    app.session.ask = ask
    asyncio.run(app._turn("abre el informe"))
    out = buf.getvalue()
    assert out.count("KOTOBA") == 2, "her voice re-anchors after the machine rows"
    announce, tool, closing = (out.index("Voy a abrir"), out.index("pytest -q"),
                               out.index("Listo — ya"))
    assert announce < tool < closing, "the transcript keeps the order it happened"
    assert tool < out.rindex("KOTOBA") < closing, "the fresh plate sits above the closing prose"
    tool_row = next(line for line in out.split("\n") if "pytest -q" in line)
    assert not tool_row.startswith(" "), "machine rows stay at column 0 — the demo's own layout"


def test_a_read_verb_never_outlives_the_turn_it_was_read_in():
    """The same family as the stale work verb. `core/loop._peek` clears the field at its own exit, but a
    turn cut mid-read never receives that frame — and `bar_kind` puts `peek` ahead of everything out of
    turn, so `reading the file…` sat at the prompt with the long job's row hidden behind it."""
    app, _ = wired()

    async def cut(text, on_text=None, on_face=None):
        app._event("peek", {"tool": "read_file"})
        raise asyncio.CancelledError()

    app.session.ask = cut
    asyncio.run(app._turn("lee el fichero"))
    assert app.peek == ""
    assert footer.bar_kind(app._state()) != "peek"
