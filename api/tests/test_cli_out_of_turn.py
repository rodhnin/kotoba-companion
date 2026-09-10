"""Everything that reaches the screen with nobody's turn around it: gifts, held cards, the goodbye."""
from __future__ import annotations

import asyncio
import io
import time
from types import SimpleNamespace

from conftest import needs_posix_terminal
from kotoba.cli import state
from kotoba.cli.app import App, clipboard_image
from kotoba.cli.approvals import Card
from kotoba.cli.render import footer, rows
from kotoba.cli.render.caps import Caps
from kotoba.cli.render.portrait import Portrait
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.text import Unwrapped
from kotoba.cli.render.theme import GLYPHS_UNICODE, build_console


def wired(*, interactive: bool = False) -> tuple[App, io.StringIO]:
    caps = Caps(color="none", background="dark", unicode=True, interactive=interactive,
                width=96, g=dict(GLYPHS_UNICODE))
    buf = io.StringIO()
    screen = Screen(caps, console=build_console(caps, file=buf), portrait=Portrait(caps, wanted=False))
    app = App(caps, screen, prompt=None)
    app.session = SimpleNamespace(session_id="s1", events=SimpleNamespace(steps={}, settle=lambda: None))
    return app, buf


def a_card(**over) -> Card:
    frame = {"request_id": "r1", "mode": "approval", "label": "rm -rf build",
             "family": "rm", "can_always": False}
    return Card.from_frame({**frame, **over})


# --- the gift pipeline -----------------------------------------------------------------------------

def test_a_report_names_the_artifact_that_follows_it_and_a_bare_one_is_only_saved():
    app, buf = wired()
    app.turn_start = time.monotonic()
    app._event("report_ready", {"kind": "report_ready", "title": "the ws write-up"})
    app._event("artifact", {"kind": "artifact", "path": "reports/ws.html", "action": "created"})
    app._event("artifact", {"kind": "artifact", "path": "research/latency.md", "action": "created"})
    out = buf.getvalue()
    assert "READY" in out and "the ws write-up" in out and "/open 1" in out
    assert "SAVED" in out and "research/latency.md" in out and "/open 2" in out
    assert [g.kind for g in app.gifts] == ["ready", "saved"]


def test_a_link_card_is_a_gift_and_its_url_goes_out_whole_and_unwrapped():
    """A cut URL is a dead URL, so the row hands the screen an `Unwrapped` and the description takes
    the ellipsis instead."""
    url = ("https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API/"
           "Writing_WebSocket_servers#the_closing_handshake")
    app, buf = wired()
    app.turn_start = time.monotonic()
    app._event("need_input", {"kind": "need_input", "mode": "open_link", "url": url,
                              "label": "the spec section on the close handshake", "wait": False})
    out = buf.getvalue()
    assert "LINK" in out and "/open 1" in out
    drawn = rows.gift_rows(app.caps, app.gifts[0], 1, app.screen.rw)
    assert isinstance(drawn[-1], Unwrapped) and drawn[-1].plain == url
    assert "…" not in out and url in out.replace("\n", "")


def test_a_files_changed_frame_hands_over_nothing_so_it_draws_nothing():
    app, buf = wired()
    app.turn_start = time.monotonic()
    app._event("files_changed", {"kind": "files_changed"})
    assert buf.getvalue() == "" and app.gifts == []


def test_one_path_is_one_open_number_however_many_times_she_writes_it_in_a_turn():
    """Seen live: two `write_file` calls at the same path, 2614 then 2592 characters, and the CLI
    handed the same file over twice — `/open 1` and `/open 2` were one file, and the second row said
    `created` about something that already existed."""
    app, buf = wired()
    app.turn_start = time.monotonic()
    for _ in range(2):
        app._event("artifact", {"kind": "artifact", "path": "research/live2d-history.md",
                                "action": "created"})
    out = buf.getvalue()
    assert out.count("research/live2d-history.md") == 1
    assert "/open 1" in out and "/open 2" not in out
    assert [g.target for g in app.gifts] == ["research/live2d-history.md"]


def test_the_same_file_handed_over_in_a_later_turn_is_news_again_under_the_same_number():
    app, buf = wired()
    app.turn_start, app.epoch = time.monotonic(), 1
    app._event("artifact", {"kind": "artifact", "path": "notes.md", "action": "created"})
    app.epoch = 2
    app._event("artifact", {"kind": "artifact", "path": "notes.md", "action": "edited"})
    out = buf.getvalue()
    assert out.count("notes.md") == 2 and out.count("/open 1") == 2
    assert "created" in out and "edited" in out
    assert len(app.gifts) == 1 and app.gifts[0].note == "edited"


def test_a_gift_out_of_turn_is_banked_on_the_job_and_lands_with_its_receipt():
    """A print with prompt_toolkit's frame pinned splits it, so the row waits for the one block the
    long job lands in — with the number still pointing at the same file."""
    app, buf = wired()
    app.work = state.Work("read both pages")
    app._event("artifact", {"kind": "artifact", "path": "reports/latency.md", "action": "created"})
    assert buf.getvalue() == "" and app.work.gift_ns == [1]
    app.work.state, app.work.stopped = "ok", time.monotonic()
    assert app._land_work() is True
    assert "reports/latency.md" in buf.getvalue() and "/open 1" in buf.getvalue()


# --- the card that arrives between turns ------------------------------------------------------------

@needs_posix_terminal
def test_a_card_with_no_turn_around_it_prints_no_row_and_the_bar_still_carries_it():
    """Arrival prints nothing: the card is not a committed row, it OPENS. The beat ends an idle
    prompt and `_deferred` draws the card unasked — the earlier rule that a hold waits for Enter was
    overturned. With no live prompt to
    take, as here, the hold stays on the bar, which is also the mid-typing state, so the phrase and
    the `⏎` hint must still stand."""
    app, buf = wired(interactive=True)

    async def go():
        app._event("need_input", {"kind": "need_input", **a_card().__dict__})
        return app._state()

    st = asyncio.run(go())
    assert buf.getvalue() == "", "the card opens through _deferred — arrival commits no row"
    assert [h.state for h in app.holds] == ["ask"]
    assert footer.bar_kind(st) == "card"
    assert "waiting on a yes from you" in footer.out_twins(st, "card")[0]


@needs_posix_terminal
def test_two_cards_can_be_outstanding_and_the_older_is_the_one_offered_first():
    app, _ = wired(interactive=True)

    async def go():
        app._event("need_input", {"kind": "need_input", **a_card(request_id="a").__dict__})
        app._event("need_input", {"kind": "need_input", **a_card(request_id="b",
                                                                 label="npm ci").__dict__})

    asyncio.run(go())
    assert [h.rid for h in app.holds] == ["a", "b"]
    assert app._state().hold_at("ask").rid == "a"
    assert "2 of hers are waiting" in footer.out_twins(app._state(), "card")[0]


@needs_posix_terminal
def test_a_held_card_keeps_the_beat_on_a_short_leash_and_a_settled_one_lets_it_sleep():
    """RE-RECORDED. The old pin was the overturned premise itself: a held card cost one wake at the
    end of its window because nothing could happen to it before then — it waited for Enter. Now the
    box emptying is the thing that can happen (the beat then opens the card through `_yield_prompt`),
    so an undeliverable hold polls at a half-second instead of sleeping to the window's end; a
    deliverable one wakes at 0.02. What survives of the old pin is
    its second half: a settled card may not keep the beat awake at all."""
    app, _ = wired(interactive=True)
    assert app._wake() is None

    async def go():
        app._event("need_input", {"kind": "need_input", **a_card().__dict__})

    asyncio.run(go())
    assert 0.02 <= app._wake() <= 0.5
    app._settle(app.holds[0], "y")
    assert app._wake() is None, "a settled card may not keep the beat awake"


@needs_posix_terminal
def test_the_window_running_out_stops_the_bar_asking_for_a_yes_nobody_can_give():
    app, _ = wired(interactive=True)

    async def go():
        app._event("need_input", {"kind": "need_input", **a_card().__dict__})

    asyncio.run(go())
    app.holds[0].asked -= state.HOLD_SECONDS + 1
    app._tick_held(time.monotonic())
    assert app.holds[0].state == "late"
    assert footer.bar_kind(app._state()) == "okdone"
    assert "ran out of time" in footer.out_twins(app._state(), "okdone")[0]


@needs_posix_terminal
def test_the_answer_reaches_the_ask_that_is_asleep_on_that_exact_card():
    """`interaction.resolve` matches newest-first without a request_id, so a terminal working through
    two cards in the order they opened would otherwise answer the wrong one."""
    app, _ = wired(interactive=True)

    async def go():
        app._event("need_input", {"kind": "need_input", **a_card(request_id="a").__dict__})
        app._event("need_input", {"kind": "need_input", **a_card(request_id="b").__dict__})
        first = asyncio.create_task(app._ask(a_card(request_id="a")))
        await asyncio.sleep(0)
        app._settle(next(h for h in app.holds if h.rid == "a"), "y")
        return await first, [h.rid for h in app.holds]

    answer, left = asyncio.run(go())
    assert answer == (True, False, False) and left == ["b"]


@needs_posix_terminal
def test_a_card_the_turn_ended_over_goes_back_to_the_prompt_rather_than_being_denied_by_nobody():
    app, _ = wired(interactive=True)

    async def go():
        card = a_card()
        task = asyncio.create_task(app._ask(card))
        while app.approval is None:
            await asyncio.sleep(0.01)
        app.approval = None                       # what `_turn`'s finally does
        while not app.holds:
            await asyncio.sleep(0.01)
        app._settle(app.holds[0], "n")
        return await task

    assert asyncio.run(go()) == (False, False, False)


# --- the way out -----------------------------------------------------------------------------------

@needs_posix_terminal
def test_leaving_declines_the_card_still_waiting_instead_of_dropping_it():
    app, buf = wired(interactive=True)

    async def go():
        app._event("need_input", {"kind": "need_input", **a_card().__dict__})

    asyncio.run(go())
    app._bye()
    out = buf.getvalue()
    assert "rm -rf build" in out and "left alone — you're going" in out
    assert app.holds == []
    assert "OFFLINE" in out and "see you" in out


def test_leaving_with_the_long_job_running_prints_its_row_rather_than_losing_it():
    app, buf = wired()
    app.work = state.Work("read both pages")
    app.work.tools.append(state.Tool("web", "web_search 'x'"))
    app._bye()
    out = buf.getvalue()
    assert "web_search 'x'" in out and "it goes when the session does" in out
    assert app.work.state == "interrupted" and app.work.tools[0].state == "interrupted"


# --- the clipboard ---------------------------------------------------------------------------------

def test_the_clipboard_says_which_of_the_two_ways_it_came_up_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("KOTOBA_CLIPBOARD", "none")
    assert clipboard_image() == (b"", ("no wl-paste, xclip or pngpaste here — /attach a path instead",
                                       "no clipboard tool here — /attach a path",
                                       "no clipboard tool here"))
    monkeypatch.setenv("KOTOBA_CLIPBOARD", "empty")
    assert clipboard_image()[1][0].startswith("nothing image-shaped")
    monkeypatch.delenv("KOTOBA_CLIPBOARD")
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 40)
    monkeypatch.setenv("KOTOBA_CLIPBOARD_PNG", str(shot))
    assert clipboard_image() == (shot.read_bytes(), ())
    shot.write_bytes(b"not a picture at all")
    assert clipboard_image()[0] == b""


def test_ctrl_v_lands_in_the_box_as_a_marker_and_the_row_goes_out_with_the_message(monkeypatch, tmp_path):
    """Nothing is displayed — she is the only image in this CLI — and the file rides `/attach`'s own
    path into her next turn. What lands in the box is a reference, `[Image #1]`, not a sentence: the
    canned `look at @clip-1.png` wrote English into whatever language was being spoken."""
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 4000)
    monkeypatch.setenv("KOTOBA_CLIPBOARD_PNG", str(shot))
    app, buf = wired()
    carried: list = []
    monkeypatch.setattr("kotoba.cli.slash._carry",
                        lambda a, p, n, label="": carried.append((p, n, label)) or "she can see this one")
    box = SimpleNamespace(text="look at this ", cursor_position=13,
                          insert_text=lambda s: setattr(box, "text", box.text + s))
    app.paste(box)
    assert box.text == "look at this [Image #1]" and buf.getvalue() == ""
    assert app._flush_clips(box.text) == "look at this [Image #1]"
    out = buf.getvalue()
    assert "SENT" in out and "clip-1.png" in out and "3 kB from your clipboard" in out
    assert carried and carried[0][1] == "clip-1.png" and carried[0][2] == "[Image #1]"
    assert app.clips == [] and [g.kind for g in app.gifts] == ["sent"]


def test_a_line_that_is_only_markers_sends_the_no_caption_sentinel(monkeypatch, tmp_path):
    """The "shared with NO caption" branch is keyed off the same token the web sends, and
    that branch is the one that stops her describing a picture in English. A line with words of its own
    is a caption and rides as it was typed; markers that never carried change nothing."""
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 4000)
    monkeypatch.setenv("KOTOBA_CLIPBOARD_PNG", str(shot))
    app, _buf = wired()
    carried = {"on": False}
    monkeypatch.setattr("kotoba.cli.slash._carry",
                        lambda a, p, n, label="": carried.__setitem__("on", True) or "she can see it")
    monkeypatch.setattr("kotoba.core.attachments.has", lambda sid: carried["on"])
    box = SimpleNamespace(text="", cursor_position=0,
                          insert_text=lambda s: setattr(box, "text", box.text + s))
    app.paste(box)
    assert box.text == "[Image #1]"
    assert app._flush_clips(box.text) == "__image_only__"

    app.paste(SimpleNamespace(text="", cursor_position=0, insert_text=lambda s: None))
    assert app._flush_clips("que ves aqui? [Image #2]") == "que ves aqui? [Image #2]"
    carried["on"] = False
    assert app._flush_clips("[Image #9]") == "[Image #9]", "nothing attached — nothing to be bare about"


def test_the_clip_number_counts_the_session_so_a_second_message_keeps_the_first_ones_file(monkeypatch,
                                                                                          tmp_path):
    """Named off the pending list, a second message's first clip was `clip-1.png` again and wrote over
    the first one's bytes — harmless while nothing read them back, and the whole point once something
    does."""
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 4000)
    monkeypatch.setenv("KOTOBA_CLIPBOARD_PNG", str(shot))
    monkeypatch.setenv("KOTOBA_TMP_DIR", str(tmp_path / "scratch"))
    app, _buf = wired()
    monkeypatch.setattr("kotoba.cli.slash._carry", lambda a, p, n, label="": "she can see this one")
    box = SimpleNamespace(text="", cursor_position=0, insert_text=lambda s: None)
    app.paste(box)
    first = app.clips[0][1]
    app._flush_clips("[Image #1]")
    app.paste(box)
    second = app.clips[0][1]
    assert first != second and app.clips[0][3] == "[Image #2]"
    assert sorted(p.name for p in (tmp_path / "scratch" / "clips").iterdir()) == \
        ["clip-1.png", "clip-2.png"]


def test_a_ctrl_v_that_found_nothing_says_so_in_the_bar_and_draws_no_row(monkeypatch):
    monkeypatch.setenv("KOTOBA_CLIPBOARD", "none")
    app, buf = wired()
    app.paste(SimpleNamespace(text="", cursor_position=0, insert_text=lambda s: None))
    assert app.paste_note[0].startswith("no wl-paste")
    assert buf.getvalue() == "" and app.clips == []


def test_the_long_jobs_receipt_ends_in_her_own_words_and_not_in_a_raw_summary():
    """A receipt at the gutter has no plate, no portrait and no face, so it does not read as her
    speaking — and printed as chrome it shows its own `**asterisks**`. `_work_words` asks her for the
    sentence first; the stored summary is what she is given when it could not."""
    app, buf = wired()
    app.work = state.Work("read both pages", state="ok", summary="**Done** — three of them.")
    app.work.said = "Ya está~ te dejé el resumen."
    assert app._land_work() is True
    out = buf.getvalue()
    assert "Ya está~ te dejé el resumen." in out and "KOTOBA" in out
    assert "**Done**" not in out, "her block goes through the markdown renderer like any other"


def test_a_job_you_stopped_keeps_its_receipt_and_she_says_nothing_over_it():
    """`cancel_work` is your decision and the row is the whole receipt. The backend agrees —
    `work_state.finish` and `.fail` clear `announced`, `cancel` does not."""
    app, buf = wired()
    app.work = state.Work("read both pages", state="interrupted", summary="you called it off")
    assert app._land_work() is True
    out = buf.getvalue()
    assert "you called it off" in out and "KOTOBA" not in out


def test_the_announce_turn_never_fires_for_a_job_somebody_already_told_him_about():
    """The sentinel is not a message: it is dropped, so the model gets the transcript plus
    a developer note — and that note is empty once any turn has spoken with the job finished. Fired then,
    she is handed a conversation with nothing new at the end of it and answers the message BEFORE this
    one, in her own voice, as the long job's receipt."""
    from kotoba.core import work_state

    app, _ = wired()
    asked: list[str] = []

    async def ask(text, on_text=None, on_face=None) -> str:
        asked.append(text)
        return "ya está"

    app.session.session_id = "wd_sess"
    app.session.ask = ask
    work_state.start("wd_sess", "look into X")
    work_state.finish("wd_sess", "listo", [])
    app.work = state.Work("look into X")
    app.work.state = "ok"
    work_state.mark_announced("wd_sess")
    asyncio.run(app._work_words())
    assert asked == [], "nothing left to announce — the turn must not run at all"
    assert app.work.said == ""
    work_state.clear("wd_sess")
    work_state.start("wd_sess", "look into X")
    work_state.finish("wd_sess", "listo", [])
    asyncio.run(app._work_words())
    assert asked == ["__work_done__"] and app.work.said == "ya está"
    work_state.clear("wd_sess")
