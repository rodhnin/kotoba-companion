"""The cable: one Session, one Screen, one Prompt, and the loop that turns keys into her.

Nothing is designed here — the renderers draw and the session runs the turn; this is only the wiring,
and an approval is a coroutine so it answers ON the loop, not from a thread that cannot wake it.
Two clocks drive a turn's live region. `_clock` refreshes it while a tool runs, because her stream
yields nothing at all across that interval. `_beat` sleeps exactly as long as `_wake` says and returns
the moment nothing is left; prompt_toolkit's `refresh_interval` cannot replace it, because an empty
diff still costs ~32 B of cursor chatter per render — 64 B/s of nothing and the 0 B idle invariant
gone. Every frame arriving with no turn around it is committed above the region in a turn and BANKED
on the long job at the prompt, because a print with the frame pinned splits it."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
import time
from dataclasses import replace

try:
    import termios
except ModuleNotFoundError:     # Windows has no POSIX terminal layer, and every command imports this
    termios = None

# Resolved at import so the handlers below stay valid with no `termios` to read `termios.error` off.
_NO_TERMINAL: tuple[type[BaseException], ...] = (
    (ValueError, OSError, AttributeError) if termios is None
    else (termios.error, ValueError, OSError, AttributeError)
)

from prompt_toolkit.formatted_text import FormattedText
from rich.cells import cell_len
from rich.text import Text

from kotoba.cli import approvals, slash, state
from kotoba.cli.facts import facts
from kotoba.cli.input import commands, keys
from kotoba.cli.input import menu as launch_menu
from kotoba.cli.input.menu import Menu, Overlay, Roster
from kotoba.cli.input.prompt import Prompt
from kotoba.cli.render import cards, footer, rows, theme
from kotoba.cli.render.caps import detect
from kotoba.cli.render.markdown import Blocks, lift_urls, prose
from kotoba.cli.render.region import Gutter
from kotoba.cli.render.screen import Screen
from kotoba.cli.render.text import CSI, fit
from kotoba.core import workspace
from kotoba.core.sandbox import backend_name
from kotoba.core.text_security import scrub
from kotoba.cli import session as cli_session
from kotoba.cli.session import Session

log = logging.getLogger("kotoba.cli")

GREETING = "Oh! It's you~ Just type — `/help` shows the rest."
#: How long an UNASKED card refuses every key after it lands — `_read_approval` says the rest.
CARD_GRACE_S = 0.35
IDLE_TWINS = ("you're connected — just type", "just type")
LEAVING = ("ctrl-c again to leave — /quit works too", "ctrl-c again to leave", "ctrl-c again")

CLIP_TOOLS = (
    ("wl-paste", ["wl-paste", "--no-newline", "--type", "image/png"]),
    ("xclip", ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"]),
    ("pngpaste", ["pngpaste", "-"]),
)
CLIP_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF")
NO_CLIP = ("no wl-paste, xclip or pngpaste here — /attach a path instead",
           "no clipboard tool here — /attach a path", "no clipboard tool here")
NO_IMAGE = ("nothing image-shaped on your clipboard — copy one and try again",
            "no image on your clipboard", "no image")
NO_ROOM = ("I couldn't put that anywhere I can reach — your temp dir said no",
           "nowhere to put it", "no room")


@contextlib.contextmanager
def keep_output_on_interrupt():
    """`NOFLSH` on the terminal for as long as a live region stands — so a ctrl-c does not throw away
    the frame she was in the middle of drawing.

    A tty in cooked or cbreak mode answers INTR by raising SIGINT and, without `NOFLSH`, flushing its
    input and output queues. The region is reprinted whole at 12 Hz, so a frame is nearly always in
    that queue: a ctrl-c mid-frame leaves the cursor rows above where every relative move that follows
    assumes it is, and the next prompt paints over the band with its box half gone. prompt_toolkit
    never meets this because its raw mode turns ISIG off; the region keeps ISIG on purpose — ctrl-c
    must still be a signal — so it keeps the queues instead. The key readers save and restore whatever
    they find, so the flag set here outlives their round trips."""
    if termios is None:
        yield
        return
    try:
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
    except _NO_TERMINAL:
        yield
        return
    kept = list(saved)
    kept[3] |= termios.NOFLSH
    try:
        termios.tcsetattr(fd, termios.TCSANOW, kept)
    except termios.error:
        yield
        return
    try:
        yield
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        except termios.error:
            pass


def hazard(command: str) -> tuple[str, tuple]:
    """(the danger label, what the command would reach) — the two safety facts nothing on the wire
    carries, so a card that wants them has to measure them itself.

    One function because there are two cards and they must not know different things: both the held
    card and the in-turn one call this, or the common card — the one somebody actually presses `y` on
    — falls back to a generic sandbox sentence and draws no blast rows on a surface whose whole job is
    to say what is about to happen to somebody's machine.

    `blast_radius` is the only I/O either card does. The in-turn caller hands it to a thread, because
    the frame clock is the only thing saying she is not stuck and it must tick while the walk runs."""
    from kotoba.core.approval import detect_dangerous

    return detect_dangerous(command) or "", cards.blast_radius(command)


def clipboard_image() -> tuple[bytes, tuple]:
    """(png bytes, ()) or (b"", why not — in her voice, with its short twins).

    A terminal paste on Wayland delivers text and only text: the image bytes never reach the
    application, so the CLI has to go and read the system clipboard itself. KOTOBA_CLIPBOARD gives the
    scenes a deterministic answer."""
    forced = os.environ.get("KOTOBA_CLIPBOARD", "")
    if forced == "none":
        return b"", NO_CLIP
    if forced == "empty":
        return b"", NO_IMAGE
    src = os.environ.get("KOTOBA_CLIPBOARD_PNG", "")
    if src:
        try:
            with open(src, "rb") as fh:
                data = fh.read()
        except OSError:
            return b"", NO_IMAGE
        return (data, ()) if data[:4] in CLIP_MAGIC else (b"", NO_IMAGE)
    import shutil
    import subprocess

    found = False
    for exe, argv in CLIP_TOOLS:
        if not shutil.which(exe):
            continue
        found = True
        try:
            out = subprocess.run(argv, capture_output=True, timeout=2.0).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if out[:4] in CLIP_MAGIC:
            return out, ()
    return b"", (NO_IMAGE if found else NO_CLIP)


class Parts:
    """The row builders `flow_view` arranges but does not own. Every one of them already exists in
    `render/`; this only says which of them the region gets, at what measure, off whose spinner."""

    def __init__(self, app: "App") -> None:
        self.app = app
        self.seated_k = 0

    def tool_text(self, tool) -> Text:
        return rows.tool_text(self.app.caps, tool, self.app.screen.rw, spin=self.app.spin)

    def roster_rows(self) -> list:
        return rows.roster_rows(self.app.caps, self.app.helpers, self.app.screen.rw,
                                spin=self.app.spin, folded=self.app.folded, peeked=self.app.peeked)

    def approval_rows(self) -> list:
        """Empty while the card is overlaid — it is already on the glass above the region
        (`app._overlay_open`), and a copy in the flow would grow the region under it."""
        if self.app.overlay is not None:
            return []
        return cards.approval_rows(self.app.caps, self.app.approval, self.app.card_w)

    def seated_rows(self) -> list:
        """The overlaid card, whole — `LiveView` seats what its pad can hold and writes the count
        back to `seated_k`; `app._card_head` draws the rest above the region. Whichever rail is
        up: held, inline and confirm are one card surface."""
        if self.app.overlay is None:
            return []
        if self.app.approval is not None:
            return cards.approval_rows(self.app.caps, self.app.approval, self.app.card_w)
        if self.app.confirm is not None:
            return cards.confirm_rows(self.app.caps, self.app.confirm, self.app.card_w)
        return []

    def confirm_rows(self) -> list:
        if self.app.overlay is not None:
            return []
        return cards.confirm_rows(self.app.caps, self.app.confirm, self.app.card_w)

    def head_plate(self, live: bool = False) -> Text:
        return self.app.screen.plate(live=live)

    def at_gutter(self, row):
        return self.app.screen.at_gutter(row)

    def prose(self, block: str):
        """The same URL lift the committed copy makes, or the two draw different rows the moment she
        gives you a link."""
        screen = self.app.screen
        return screen.at_gutter(prose(self._lifted(block)[0], self.app.caps))

    def link_rows(self, block: str) -> list:
        gutter = self.app.screen.gutter
        return [Text(" " * gutter + scrub(url)) for url in self._lifted(block)[1]]

    def _lifted(self, block: str):
        screen = self.app.screen
        return lift_urls(block, screen.w - screen.gutter)


class App:
    def __init__(self, caps, screen: Screen, prompt: Prompt | None = None) -> None:
        self.caps = caps
        self.screen = screen
        self.prompt = prompt if prompt is not None else Prompt(caps)
        self.caps.leftover = ""     # the Prompt has it; read twice it seeds a ghost line, sent twice
        self.menu = Menu(screen, self.prompt) if self.prompt.session is not None else None
        if self.menu is not None:
            self.menu.band = self._band
        self.session: Session | None = None
        self.launch = ""            # `--sessions` / `--settings`: a menu instead of her greeting
        self.blocks = Blocks()
        self.spin = rows.Spin(caps)
        self.parts = Parts(self)
        self.region = None
        self.room = 0
        self.model = ""
        self.facts: tuple[list[tuple[str, str, int]], str] = ([], "")
        self.last_result = ""
        self.plan: dict | None = None
        self.started: dict[str, state.Tool] = {}
        self.works: list[state.Work] = []
        self.last_helpers: list[state.Helper] = []
        self.gifts: list[state.Gift] = []
        self.given: dict[tuple[str, str], tuple[int, int]] = {}
        self.epoch = 0
        self.report_title = ""
        self.tools: list[state.Tool] = []
        self.helpers: list[state.Helper] = []
        self.work: state.Work | None = None
        self.announcing: state.Work | None = None
        self.approval: cards.Approval | None = None
        self.confirm: cards.Confirm | None = None
        self.overlay: Overlay | None = None
        # Cards nobody's turn could answer, cron's turnless reminders, ctrl-v's clipboard images.
        self.holds: list[state.Held] = []
        self.due: list[state.Due] = []
        self.clips: list[tuple[str, str, int, str]] = []
        self.clip_dir = ""
        self.shared_n = 0
        self.paste_note: tuple = ()
        self.status = ""
        self.peek = ""
        self.typing = ""
        self.queued: tuple[str, ...] = ()
        self.pending_send: list[str] = []
        self.turn_start = 0.0
        self.armed_until = 0.0
        self.rows_committed = False
        self.face_fixed = False
        self.folded = False
        self.peeked = 0
        self.keys: keys.Keys | None = None
        self._painted: tuple = ()
        self._no_room = False
        self._landing = False
        self._resized = False
        self.gutter = None
        self._beating = False
        self._note_at = ""
        self._held_keys: dict[str, asyncio.Future] = {}
        self._auto_opened = False
        self._card_not_before = 0.0
        self._card_cut = False
        self._room_at = 0

    @property
    def card_w(self) -> int:
        return self.screen.w - self.screen.gutter

    def restate_model(self) -> None:
        """Re-ask the one fact the bar's right slot draws, after a write that could have moved it.

        `self.model` is assigned at boot and nothing else reassigns it, so without this `/set provider
        xai` leaves the bar saying OpenAI for the rest of the session — telling somebody who switched
        provider for privacy or for cost, permanently and on screen, that their words still go to the
        old one.

        Only the MODEL line is re-asked: the rest of `facts()` counts her turns and walks the workdir,
        which is not a thing to do on a keystroke. The header's copy moves with it."""
        from kotoba.cli.facts import _model
        from kotoba.core import llm, providers

        line = _model(llm, providers)
        self.model = self.caps.t(line)
        stats, note = self.facts
        self.facts = ([(k, line if k == "MODEL" else v, r) for k, v, r in stats], note)

    async def run(self) -> int:
        """Boot, the prompt loop, and the goodbye — with first run answered before her greeting.

        An unconfigured install would otherwise get a header, a greeting and no way to fix it. The
        wizard borrows THIS screen, so first run and the session it hands over to are one transcript,
        and her name is re-read after it because that wizard is where it is chosen. The MODEL line is
        pulled out of the gathered facts on its own because the bar's right slot is the one fact that
        reaches the screen past every row builder."""
        from kotoba.cli import wizard

        self.screen.take_screen()
        self.screen.curtain()
        self._catch_resize()
        try:
            self.session = await Session.open(ask=self._ask, on_event=self._event)
        except Exception as e:
            from kotoba.cli.session import explain_startup_failure

            return explain_startup_failure(e)
        try:
            if self.caps.interactive and await wizard.needed(self.session.engine.db):
                await wizard.run(self.session.engine.db, self.caps, screen=self.screen)
            soul = await self.session.engine.db.fetch_soul_config()
            self.screen.called(soul.get("name") or "")
            self.facts = await facts(self.session.engine.db)
            self.model = self.caps.t(next((v for k, v, _ in self.facts[0] if k == "MODEL"), ""))
            self.screen.header(*self.facts)
            if self.launch:
                await launch_menu.launch(self, self.launch)
            else:
                self.screen.say(GREETING, last=True)
            if self.prompt.session is not None:
                self.prompt.session.app.after_render += self._rendered
                self.prompt.paste = self.paste
                self.prompt.note = self._note
                self.prompt.line_up = self._line_up
                self.prompt.on_leaving = self._poke
            return await self._loop()
        finally:
            self._bye()
            await self.session.close()

    async def _loop(self) -> int:
        """The rows the region let go of are the rows prompt_toolkit is about to paint, so `room` is
        handed over here and its first render is already pinned rather than asking the terminal where
        it is. Whatever the out-of-turn channel is holding lands on the next write: `enter` on an empty
        line is the free gesture that asks for it, because nothing is not a message. A card she is
        holding opens here and only here — through your Enter, or through the beat ending an idle
        prompt; a half-typed line still blocks the unasked one, so nothing draws a rail under a message
        in progress. A line queued while she worked is taken before the prompt is drawn, one per pass
        and in the order it was typed: `enter` already sent it as far as the person is concerned. The
        finally closes the completion list itself — the list is ours, so nothing prompt_toolkit erases
        on its way out takes it off."""
        while True:
            if self.pending_send:
                line = self.pending_send.pop(0)
            else:
                try:
                    self.prompt.below = self._pin_room()
                    line = await self.prompt.ask_async(pre_run=self._beat)
                except KeyboardInterrupt:
                    continue
                finally:
                    if self.menu:
                        self.menu.close()
                if line is None:
                    return 0
                if self.prompt.picked:
                    await launch_menu.picked(self)
                    continue
            line = line.strip()
            await self._work_words()
            self._land_work()
            self._land_due()
            await self._deferred()
            if not line:
                continue
            self.screen.commit_user(line)
            if commands.is_command(line):
                if await self._slash(commands.parse(line)):
                    return 0
                continue
            sent = self._flush_clips(line)
            if sent:
                await self._turn(sent)

    async def _turn(self, text: str) -> None:
        """Ctrl+C cuts the turn, never the process: the partial is already persisted by the time the
        cancellation lands, so it stays on the screen and she says she stopped.

        The line editor is gone for the length of this, so the keyboard is read here in cbreak for
        exactly as long as the region stands, inside a `with` because a turn that raises may not leave
        the terminal without its echo. `esc` arrives as a cancel rather than an exception, and the
        shield turns that inner cancel back into the one the Ctrl+C arm ends on. The turn's last blank
        and its footer print INSIDE the region — out there they would land on a frame still standing.
        Her text sink is CLOSED at the end, so a block echoed after the wait is dropped: it is already
        persisted, and a transcript out of order is worse than one stopping where the turn did."""
        self.blocks = Blocks()
        self.epoch += 1
        self.tools, self.helpers = [], []
        self.started.clear()
        self.status, self.peek = "thinking", ""
        self.approval = self.confirm = None
        self.armed_until = 0.0
        self.rows_committed = self.face_fixed = False
        self.peeked = 0
        self.typing, self.queued = self.prompt.pending + self.caps.leftover, ()
        self.prompt.pending = self.caps.leftover = ""
        self.screen.begin_turn()
        self.screen.measure()
        if self.work is not None:
            self.work.cleared = True   # news outranks the last job's ending; it stops holding the band
        self.turn_start = time.monotonic()
        cut = hearing = False

        def said(block: str) -> None:
            if hearing:
                self._chunk(block)

        def wore(emotion: str) -> None:
            if hearing:
                self._face(emotion)

        hearing = True
        task = asyncio.create_task(self.session.ask(text, on_text=said, on_face=wore))

        def typed(chunk: str) -> None:
            try:
                self._typeahead(chunk)
            except keys.Interrupted:
                task.cancel()

        region = self.screen.region(self.spin, self._state, self.parts)
        with keep_output_on_interrupt(), region as live, Gutter(self.screen, live) as guard, keys.Keys(
                typed, enabled=self.caps.interactive) as reader:
            self.region = live if self.caps.interactive else None
            self.gutter = guard
            self.keys = reader
            clock = asyncio.create_task(self._clock())
            try:
                await asyncio.shield(task)
            except (KeyboardInterrupt, asyncio.CancelledError):
                task.cancel()
                cut = True
            finally:
                clock.cancel()
                self.region = self.keys = None
            await self._last_frames(task)
            if self.overlay is not None:
                # The exception path's belt — `_inline`'s finally has closed by now or never will.
                self.overlay.forget()
                self.overlay = None
            hearing = False
            tail = self.blocks.flush()
            for i, block in enumerate(tail):
                self.screen.say(block, last=i == len(tail) - 1)
            self.screen.flush_held()
            for tool in self.tools:
                if tool.state == "running":
                    tool.state, tool.stopped = "interrupted", time.monotonic()
                    tool.detail = "stopped" if cut else ""
                    self._commit(rows.tool_text(self.caps, tool, self.screen.rw))
            for helper in self.helpers:
                if helper.state in ("running", "queued"):
                    helper.state, helper.stopped = "interrupted", time.monotonic()
            self._commit_roster()
            if cut:
                self.screen.face.set("embarrassed", instant=True)
                self.screen.chrome(f"{self.caps.g['cut']} stopped", at_gutter=True)
                self.screen.blank()
            self.screen.footer(time.monotonic() - self.turn_start, len(self.tools))
            self.screen.gap()
        self._release(region)
        self.prompt.pending, self.typing = self.typing, ""
        self.pending_send += list(self.queued)
        self.queued = ()
        self.turn_start, self.status, self.peek = 0.0, "", ""
        self.approval = self.confirm = None
        self.screen.end_turn()

    async def _last_frames(self, task: asyncio.Task) -> None:
        """Her side of the turn ends after the await does, and the rows are decided only once it has.

        The agentic loop's own finally closes every terminal row it left open, one per still-running
        tool, and stamps the helpers a delegate had out. Cancelling only REQUESTS that, so those frames
        land a loop-turn later: swept the instant the await returns, `delegate` rows read `stopped`
        while their helpers were plainly still going, and the true frames were then DROPPED.

        Half a second is a ceiling on FRAMES, not a cost: a turn whose steps all landed leaves on the
        first pass having awaited nothing. The task may outlive it, still talking."""
        end = time.monotonic() + 0.5
        while True:
            self.session.events.settle()
            if task.done() and not any(t.state == "running" for t in self.tools):
                return
            if time.monotonic() >= end:
                return
            await asyncio.sleep(0.005)

    def _typeahead(self, chunk: str) -> None:
        r"""Keys typed while she works are never swallowed: they queue visibly and send when the turn
        ends, because a second message would kill this one.

        Complete escape sequences are stripped FIRST — guarding on `chunk[:2]` instead means a fast
        typist whose keystrokes arrive in the same read as an arrow key (`qzx!\x1b[A`) kills the turn,
        and ↑ is the most-pressed key in a REPL. A line ending with more of the SAME READ behind it is
        a burst, not an Enter: a person's Enter is the last thing in its read however fast they type,
        while `tmux send-keys`, `xdotool type` and ssh clients that send no paste markers write a
        paragraph in one go — sent per line ending, a three-line burst queues three messages and three
        bills. CRLF is folded first, or `\r` then `\n` is two Enters; one 1024-byte read is the split."""
        rest = CSI.sub("", chunk).replace("\r\n", "\n").replace("\r", "\n")
        for i, ch in enumerate(rest):
            if ch == "\t":
                if self.helpers:
                    self.peeked = 0 if self.peeked >= len(self.helpers) else self.peeked + 1
            elif ch == "\x12":
                self.folded = not self.folded
            elif ch == "\x1b":
                busy = (sum(1 for h in self.helpers if h.state in ("running", "queued"))
                        + sum(1 for t in self.tools if t.state == "running"))
                if busy > 1 and time.monotonic() >= self.armed_until:
                    self.armed_until = time.monotonic() + 3.0
                    continue
                raise keys.Interrupted
            elif ch == "\n":
                if i < len(rest) - 1:
                    self.typing += "\n"
                    continue
                if self.typing.strip():
                    self.queued += (self.typing.strip(),)
                self.typing = ""
            elif ch == "\x7f":
                self.typing = self.typing[:-1]
            elif ch.isprintable():
                self.typing += ch

    async def _clock(self) -> None:
        """The in-turn clock, and the reason the interval a tool runs in stops looking dead: her stream
        yields nothing at all across it, so the region is refreshed from here rather than off her text.
        Each refresh ticks the spinner, the pulse, her mouth and every elapsed clock together off
        wall-clock seconds. It sleeps on `footer.frame_hz`, the one name every moving mark is phased
        off, so the rate a frame is drawn at is never a second copy of the rate this loop wakes at.

        A frame that raises may not take the clock with it. Rich renders a whole frame into a local
        list before extending its buffer, so a throw emits nothing and leaves the last good frame on
        the glass — but the exception would end this task, freezing the spinner and every elapsed clock
        for the rest of the turn. The next frame is a new snapshot, so it is tried."""
        if not self.caps.interactive:
            return
        while self.region is not None:
            await asyncio.sleep(1.0 / footer.frame_hz(self.caps, self._state()))
            if self._resized:
                self._on_resize()
            if self.region is None:
                continue
            try:
                self.region.refresh()
                if self.overlay is not None:
                    self.overlay.sync()
            except Exception:
                log.debug("could not draw a frame", exc_info=True)

    def _catch_resize(self) -> None:
        """The handler only raises a flag: re-pinning a live region from inside a signal is a write in
        the middle of whatever write it interrupted. The clock services it on its next frame, and out at
        the prompt prompt_toolkit re-asks and re-pins itself."""
        try:
            signal.signal(signal.SIGWINCH, lambda *_: setattr(self, "_resized", True))
        except (ValueError, OSError, AttributeError):
            pass

    def _on_resize(self) -> None:
        """Rich's `stop()` pops whatever render hook is LAST — the gutter guard, not its own — and
        `start()` pushes a second Live. Left alone, one resize costs her the guard and renders the
        region twice per print, so the order here is the fix and not a preference."""
        self._resized = False
        self.caps.sync_size()
        if self.overlay is not None:
            self.overlay.wipe()
            self.overlay = None
        if self.region is None:
            return
        try:
            self.region.stop()
            self.screen.console.pop_render_hook()
            sys.__stdout__.write("\x1b[J")
            sys.__stdout__.flush()
            self.screen.measure()
            self.region.start(refresh=False)
            self.screen.console.push_render_hook(self.gutter)
            self.region.refresh()
        except Exception:
            log.debug("could not re-pin the region after a resize", exc_info=True)

    def _state(self) -> footer.State:
        """One frozen snapshot per frame. `at_rest` is "no turn is running", which is also why
        `turn_start` goes back to zero at the end of one: the idle bar carries her model, never a
        clock."""
        return footer.State(
            model=self.model,
            turn_start=self.turn_start,
            armed_until=self.armed_until,
            status=self.status,
            peek=self.peek,
            partial=self.blocks.partial,
            held=self.screen.held,
            typing=self.typing,
            queued=self.queued,
            tools=tuple(self.tools),
            helpers=tuple(self.helpers),
            holds=tuple(self.holds),
            due=tuple(self.due),
            work=self.work,
            plan=self.plan,
            approval=self.approval,
            confirm=self.confirm,
            said_plate=self.screen.said_plate,
            resumed=self.screen.resumed,
            rows_committed=self.rows_committed,
            owes_gap=self.screen.owes_gap,
            folded=self.folded,
            at_rest=not self.turn_start,
        )

    def _chunk(self, chunk: str) -> None:
        self.screen.face.feed(len(chunk))
        self.face_fixed = True
        self.status = ""
        for block in self.blocks.feed(chunk):
            self.screen.say(block)

    def _face(self, emotion: str) -> None:
        """The face this reply wears, from the tag she opened it with, and it is set INSTANTLY: the
        live region refuses to paint her portrait mid-blink, and one frame of refusal is the frame she
        starts speaking on.

        Her mood is decided once per turn and then held. The loop emits its considered emotion
        after her LAST chunk, which is the repaint the person sees as a flash — arriving late, it is
        news about a reply that is already on the glass. So the first thing to reach the face wins: her
        tag, or failing that whatever she was wearing when her first character landed (`_chunk`)."""
        if self.face_fixed:
            return
        self.face_fixed = True
        self.screen.face.set(emotion, instant=True)

    def _commit(self, renderable) -> None:
        """A row printed mid-turn goes above the live region, into the scrollback, for good — the print
        repaints the region on its way out, so the row lands at the position it already occupied."""
        self.rows_committed = True
        self.screen.row(renderable)

    def _commit_roster(self) -> None:
        """The line-up rises as one block once nobody is still out there. Committing each helper the
        moment it lands is what made the survivors jump up the screen mid-read.

        Taken off the list BEFORE the loop, never after. Every `_commit` reprints the live region on its
        way out and `Parts.roster_rows` reads this same list, so a line-up emptied afterwards is on the
        glass twice for the length of the print — the discipline `Screen.flush_held` already keeps for
        her staged prose, for the same reason.

        `last_helpers` keeps the line-up so `/helpers` can read it back after the turn that sent it
        out has ended."""
        line_up, self.helpers = self.helpers, []
        for i, helper in enumerate(line_up, 1):
            for row in rows.helper_rows(self.caps, helper, i, self.screen.rw):
                self._commit(row)
        self.last_helpers = line_up or self.last_helpers

    def _event(self, kind: str, frame: dict) -> None:
        """Folded state first, raw frame second: EventBridge has already decided what a step's state is
        by the time this runs, and a renderer reading `ok` alone reports failures that never happened.

        An `emotion` frame is the backend's guess and it only lands while her face is still open (see
        `_face`) — a tool's focus face before she speaks, never a mood after she has stopped. And
        never the detached job's while a turn is running: mid-turn her face belongs to the turn, so a
        frame naming the job's run is dropped there; out at the prompt it moves her as it always
        could."""
        try:
            if kind == "emotion":
                if not self.face_fixed and not (self.turn_start and self._jobs_frame(frame)):
                    self.screen.face.set(str(frame.get("emotion") or ""))
            elif kind == "step":
                self._step(frame)
            elif kind == "peek":
                self._peek(frame)
            elif kind == "reminder":
                self._due(frame)
            elif kind == "task_list":
                self._task_list(frame)
            elif kind in ("work_started", "work_done"):
                self._work(kind, frame)
            elif kind.startswith("subagent_"):
                self._helper(kind, frame)
            elif kind == "report_ready":
                self.report_title = str(frame.get("title") or "")
            elif kind == "artifact":
                title, self.report_title = self.report_title, ""
                self._gift("ready" if title else "saved", str(frame.get("path") or ""),
                           title or str(frame.get("action") or ""),
                           run_id=str(frame.get("run_id") or ""))
            elif kind == "need_input":
                self._need_input(approvals.Card.from_frame(frame))
        except Exception:
            log.warning("could not draw a %s frame", kind, exc_info=True)

    def _jobs_frame(self, frame: dict) -> state.Work | None:
        """Positive origin: the works-list entry whose run the frame names, or None. `run_id` is
        stamped once per `agentic_loop` invocation and the work-runner hands its bracket the same id,
        so the job's frames bank on the job even while a turn is speaking — routing on "am I waiting
        right now" was the guess that split a reply in half with the job's rows. The whole list is
        searched, not just the current bracket: a SUPERSEDED job's teardown lands its frames ticks
        after the successor's `work_started`, and routed positionally those landings printed inside
        the new turn's reply and moved her face off a run already stopped. A frame without the key
        predates the field and keeps positional routing."""
        rid = str(frame.get("run_id") or "")
        if not rid:
            return None
        return next((w for w in reversed(self.works) if w.run_id == rid), None)

    def _need_input(self, card: approvals.Card) -> None:
        """The two cards that are not a question for the turn. `open_link` hands you a URL and returns at
        once (`core/interaction.open_link_card`), so it is a gift and not a gate. An approval with no
        turn around it is the long job's, and it becomes a hold: `Approvals` will call `_ask` for it a
        moment later and `_ask` reads the answer back off the hold `_deferred` drew."""
        if card.mode == "open_link":
            self._gift("link", str(card.url or ""), card.label)
        elif card.mode == "approval" and not self.turn_start and self.caps.interactive:
            self._hold(card)

    def _gift(self, kind: str, target: str, note: str, run_id: str = "") -> int:
        """The frames that hand you something: a report, the path she wrote or edited, an open_link
        URL. One row each, and the number on the right is the one `/open` takes — returned, so the long
        job can keep hold of its own and reprint them later still pointing at the same files.

        Three places a row may go: committed above the region in a turn, BANKED on the job because a
        print splits the pinned frame, or out at a safe point in the loop where column 0 is ours again.
        `run_id` decides by origin what position used to guess. One path is one number for the life of
        the session: she writes the same file twice in a turn more often than she should, and per-write
        numbering made `/open 1` and `/open 2` the same file. A repeat in a LATER turn is news again and
        draws its row under the number it has always had."""
        key = (kind, target)
        seen = self.given.get(key)
        if seen is not None:
            n, epoch = seen
            self.given[key] = (n, self.epoch)
            if epoch == self.epoch:
                return n
            gift = self.gifts[n - 1]
            gift.note = note
        else:
            gift = state.Gift(kind, target, note)
            self.gifts.append(gift)
            n = len(self.gifts)
            self.given[key] = (n, self.epoch)
        jobs = (run_id and self.work is not None and run_id == self.work.run_id
                and not self.work.landed)
        if jobs:
            self.work.gift_ns.append(n)
        elif self.turn_start:
            for row in rows.gift_rows(self.caps, gift, n, self.screen.rw):
                self._commit(row)
        elif self.work is not None and not self.work.landed:
            self.work.gift_ns.append(n)
        else:
            for row in rows.gift_rows(self.caps, gift, n, self.screen.rw):
                self.screen.row(row)
        return n

    def _step(self, frame: dict) -> None:
        """A running row belongs in the live region and only its landing is committed — a pulse printed
        into scrollback is an animation that has stopped. Out of turn there is no region and a pinned
        frame in the way, so the long job's rows are banked on it and land with its receipt.

        Banked by ORIGIN, not by the clock: a frame naming the job's run is the job's even while a turn
        is speaking. Routed on `turn_start` alone, two of the job's `WEB` rows landed inside her reply
        and split it in half. `detail` is the phrase clipped to fit beside the chip and `full` is
        everything the tool returned — the only copy a panel's leaf or `/last` can read. An interrupted
        landing carries a sentinel result meant for a client with no flag to read; we have the flag."""
        step = self.session.events.steps.get(str(frame.get("id") or ""))
        if step is None:
            return
        owner = self._jobs_frame(frame)
        into = (owner.tools if owner is not None
                else self.tools if self.turn_start
                else self.work.tools if self.work else None)
        if into is None:
            return
        if frame.get("phase") == "start":
            tool = state.Tool(step.kind, step.action)
            self.started[step.id] = tool
            into.append(tool)
            if into is self.tools:
                self.status = step.kind
            return
        tool = self.started.pop(step.id, None)
        if tool is None:
            tool = state.Tool(step.kind, step.action)
            into.append(tool)
        tool.state, tool.stopped = step.state, time.monotonic()
        tool.detail = "stopped" if step.state == "interrupted" else step.result
        tool.full = step.full or step.result
        tool.note = step.note
        self.last_result = step.full or step.result or self.last_result
        if into is self.tools:
            # Back to thinking, not to nothing: cleared with no text yet, the bar drops to idle.
            self.status = "thinking"
            self._commit(rows.tool_text(self.caps, tool, self.screen.rw))

    def _peek(self, frame: dict) -> None:
        """Which read-only tool she is inside. A read earns no `step` frame by design, so "what do you
        know about me" fires five of them and would otherwise put nothing on the screen at all — which
        is indistinguishable from a hang. The empty string clears it, and so does the next action and
        the end of the turn.

        In a turn only, and in the TURN's only. Out of turn the bar is the long job's one surface and
        `peek` outranks everything there, so a read inside the job would take the job's phrase, its
        pulse and its elapsed off the only row carrying them; a frame naming a job's run mid-turn
        would overwrite what the turn is doing on the bar the turn owns. The turn's exit clears it because a
        turn cut mid-read never receives the loop's own clearing frame."""
        if self.turn_start and self._jobs_frame(frame) is None:
            self.peek = str(frame.get("tool") or "")

    def _due(self, frame: dict) -> None:
        """A reminder cron poured out with nobody's turn around it.

        `when` and `every` stay empty, and that is a refusal rather than a gap: the only clock the table
        could still answer with is the NEXT slot the job was rescheduled to as it fired, which is not
        the time that came due. The bar shows what it knows and invents nothing.

        No bell and no row here. A reminder is a nudge, not a gate, so it rides the bar until column 0
        is ours again. Her sentence is not ours to write either: cron stashes the same message for the
        next turn to inject, so she says it herself, once, in her own words."""
        rid = str(frame.get("id") or "")
        if any(d.rid == rid for d in self.due):
            return
        self.due.append(state.Due(rid, str(frame.get("message") or ""), ""))
        self._poke()

    def _land_due(self) -> bool:
        """The reminder's row, at the same point in the loop the long job's receipt lands. The bar is not
        a record — it is one phrase, overwritten by the next thing to happen — so a reminder that only
        ever existed there is one nobody can scroll back to."""
        owed = [d for d in self.due if not d.told]
        if not owed:
            return False
        self.screen.separate()
        for d in owed:
            d.told = True
            gift = state.Gift("due", d.msg, "", tail=d.every or d.when)
            for row in rows.gift_rows(self.caps, gift, 0, self.screen.rw):
                self.screen.row(row)
        self.screen.blank()
        return True

    def _task_list(self, frame: dict) -> None:
        """The plan lives in the BAND while it is open, and the transcript gets ONE receipt, at close —
        the plan's rows, committed exactly once when the list goes done or abandoned. Reprinting it at
        every landed step put plan rows in the middle of her messages; `/plan` still prints a snapshot
        on demand, because a command is an explicit ask.

        `_poke` is not optional: out at the prompt the beat may have already returned, so a frame that
        landed then would move the band's state and wake nobody, leaving the band wrong until the next
        keystroke. The close receipt routes like every other frame — region in a turn, banked on the
        job at the prompt, banked by run for a job's own frame mid-turn — plus `announcing`, the one
        turn on which she is told to tick and close. Gated on `self.work` alone, that frame is lost."""
        plan, was = state.Plan(frame), self.plan
        self.plan = plan
        self._poke()
        if plan.is_open:
            return
        if was is not None and not was.is_open and was.list_id == plan.list_id:
            return
        job = self.work if self.work is not None else self.announcing
        drawn = rows.plan_rows(self.caps, frame, self.screen.rw)
        owner = self._jobs_frame(frame)
        if owner is not None:
            if not owner.landed:
                owner.plans.append(drawn)
        elif self.turn_start:
            for row in drawn:
                self._commit(row)
        elif job is not None and not job.landed:
            job.plans.append(drawn)

    def _work(self, kind: str, frame: dict) -> None:
        """`start_work` returns instantly and the turn carries on talking, so the opening bracket is the
        last thing the turn commits about it. From there until it lands the bar is its only surface —
        a print with the frame pinned splits it — and the receipt goes out through `_land_work`.
        A `work_done` with no bracket of ours is a deferred command's result, not the long job's, and
        announcing a `shell` as "the long job landed" is the lie this avoids: the runner stamps its
        bracket with the job's `run_id` and the deferred frame carries none, so a marked bracket only
        closes on the `work_done` naming it. A `work_started` over a bracket still marked running means
        the OLD job was stopped — closed here as interrupted, or it sits in `/work N` as a phantom
        forever and its receipt never prints. The opening bracket wakes the beat itself: arriving after
        the prompt is back it moves the job's one surface, and the job ran to the end with no receipt."""
        if kind == "work_started":
            goal = str(frame.get("goal") or "")
            prev = self.work
            if prev is not None and prev.state == "running" and not prev.landed:
                prev.state, prev.stopped, prev.landed = "interrupted", time.monotonic(), True
                if self.turn_start:
                    self._commit(rows.work_row(self.caps, prev, self.screen.rw))
            self.epoch += 1
            self.work = state.Work(goal, len(self.works) + 1,
                                   run_id=str(frame.get("run_id") or ""))
            self.works.append(self.work)
            if self.turn_start:
                self._commit(rows.work_row(self.caps, self.work, self.screen.rw, opening=True))
            else:
                self._poke()
            return
        if self.work is None or self.work.state != "running":
            return
        if self.work.run_id and str(frame.get("run_id") or "") != self.work.run_id:
            return
        self.work.stopped = time.monotonic()
        self.work.summary = self._whole_summary(frame)
        self.work.state = ("interrupted" if frame.get("cancelled")
                           else "ok" if frame.get("ok") else "failed")

    def _whole_summary(self, frame: dict) -> str:
        """The job's summary, whole. The `work_done` frame carries its first 500 characters, a cap
        sized for the web's contextual update to the voice agent — a
        client that never DRAWS it. This one draws it, in `/work N` and on a job that did not end
        well, and a job whose summary was a comparison table reached the transcript cut mid-cell at
        the 500th character. The CLI runs the engine in process, and `work_state.finish` wrote the whole text
        one line before the frame went out — so the record is read, and taken only when it names this
        run and begins with what the frame says. A stranger's record, or a test's idle one, hands the
        frame back as it came."""
        from kotoba.core import work_state

        said = str(frame.get("summary") or "")
        rid = str(frame.get("run_id") or "")
        try:
            record = work_state.get(self.session.session_id)
        except Exception:
            return said
        whole = str(record.get("summary") or "")
        if rid and str(record.get("run_id") or "") == rid and whole.startswith(said):
            return whole
        return said

    def _helper(self, kind: str, frame: dict) -> None:
        """`subagent_spawned` names the `toolset` the helper was restricted to, so the nameplate says
        which specialist it is instead of the same invented `helper` on every row. A helper the turn
        was cut over is not one that failed — `delegate` says which, and reading `ok` alone reports a
        failure that never happened.

        A step is kept as `(text, ok)`: `ok` is None on the line saying what she is about to do and
        True/False on the one reporting how it went. Dropping it forces a renderer to sniff a prefix.

        Routed by origin the way a step is: a helper the detached job sent out is the job's line-up
        even while a turn is speaking, so the nameplate banks where its work does."""
        sid = str(frame.get("id") or "")
        owner = self._jobs_frame(frame)
        into = (owner.helpers if owner is not None
                else self.helpers if self.turn_start
                else self.work.helpers if self.work else None)
        if into is None:
            return
        if kind == "subagent_spawned":
            into.append(state.Helper(sid, str(frame.get("toolset") or "helper"),
                                     str(frame.get("goal") or ""),
                                     state="running", started=time.monotonic()))
            return
        helper = next((h for h in into if h.sid == sid), None)
        if helper is None:
            return
        if kind == "subagent_step":
            ok = frame.get("ok")
            helper.steps.append((str(frame.get("text") or ""), None if ok is None else bool(ok)))
            return
        helper.state = ("interrupted" if frame.get("interrupted")
                        else "ok" if frame.get("ok") else "failed")
        helper.summary = str(frame.get("summary") or "")
        helper.stopped = time.monotonic()

    def _wake(self) -> float | None:
        """Seconds until the bar next has something different to say, or None when nothing is coming at
        all — None is what makes an idle prompt cost nothing per second.

        One frame of the job mark while the long job is moving: the band's animation and the WORKING
        chip's swell both draw at `footer.beat_hz`, and the wake IS that function, so nothing wakes
        faster or slower than it draws (it woke at 6 over SSH around marks phased at 4). Under `/calm`
        the mark is a still and only the clock turns over, so it folds to `CARD_S`: waking at the
        animation's rate for a frame that never changes measured 3.4% of a core against the clock
        rate's 1.5%. 20 ms when a landing is owed, and a half-second while a card waits on a box that
        is not free yet, so it opens the moment the half-typed line is sent or erased."""
        if self._deliverable() or self._card_deliverable():
            return 0.02
        if self.work and self.work.state == "running":
            return 1.0 / footer.beat_hz(self.caps, self._state())
        if any(h.state == "ask" for h in self.holds):
            return 0.5
        return None

    def _ring(self) -> None:
        """One bell, and only for the things that are genuinely pending on you. A card is a gate and gets
        one; a reminder is a nudge and does not, or the two stop meaning different things."""
        if self.caps.interactive:
            sys.__stdout__.write("\a")
            sys.__stdout__.flush()

    def _hold(self, card: approvals.Card) -> state.Held:
        """A card that opened with nobody's turn around it. It draws no row HERE — one bell, and the
        `_poke` wakes the beat, whose next breath opens the card through `_yield_prompt` the moment the
        box is free; only a half-typed line, a panel or an armed ctrl-c keeps it on the bar, waiting
        (it always waited for Enter until that was overturned live: a gate nobody can see
        is not a gate). `intent` stays empty — the headline is the card's own fixed line, never an echo
        of her prose — and the danger and the blast radius come off `hazard`, which
        is where both cards get them. `can_always` rides along off the wire: the card drawn at the
        prompt a moment later must not offer an `a` the gate would refuse."""
        danger, blast = hazard(card.label)
        held = state.Held(
            verb=rows.approval_kind(card), cmd=card.label,
            danger=danger, family=card.family or "",
            intent="",
            blast=blast, took=0.0, ok=False, detail="",
            rid=str(card.request_id or ""), state="ask", asked=time.monotonic(),
            can_always=card.can_always, can_always_exact=card.can_always_exact,
            always_note=card.always_note)
        self.holds.append(held)
        self._held_keys[held.rid] = asyncio.get_running_loop().create_future()
        self._ring()
        self._poke()
        return held

    def _poke(self) -> None:
        """Something turned up while the prompt was asleep. The beat is started once, at `pre_run`, and
        at that moment there was nothing coming at all — so a card that arrives after it has to wake the
        bar itself, or the one surface carrying it never repaints."""
        app = self.prompt.session.app if self.prompt.session is not None else None
        if app is None or not app._is_running:
            return
        self._beat()
        app.invalidate()

    def _line_up(self, buf) -> None:
        """`^r` out at the prompt, and it means what it already means inside a turn. In there the
        line-up is drawn every frame and `^r` folds it away; out here none of the long job's per-tool
        and per-helper rows reach the transcript at all — `_land_work` prints a receipt and keeps only a
        FAILED helper's row — so the same key opens the same line-up over the transcript, on rows it
        gives back (`input/menu.Roster`). It is where those rows live while the job runs, and `/work N`
        is where they are read back afterwards.

        A second press closes it. A first press with nothing running does nothing at all rather than
        opening an empty box and taking the bar's phrase with it for a frame."""
        if isinstance(self.prompt.picker, Roster):
            self.prompt.picker = None
            buf.complete_state = None
            return
        if self.prompt.picker is not None:
            return
        panel = Roster(self)
        if panel.rows(""):
            self.prompt.picker = panel

    def _tick_held(self, now: float) -> None:
        """The bar's copy of a clock it does not own: the window is `interaction.TEXT_APPROVAL_TIMEOUT`
        and the `clear` frame it ends on is the authority. Without this the bar goes on asking for a yes
        nobody can give any more.

        The prototype's other two transitions have no source here, and one fact answers both: nothing in
        a terminal ever defers. `shell` and `execute_code` route on the TRANSPORT — whether an ElevenLabs
        agent is holding this turn's clock — which here is always False, so nothing is ever scheduled
        for later and there is no `wait -> ask` gap to draw; a hold is
        only the long job's card or one a turn ended over. `run -> ok` was the
        prototype running the command itself; here the backend runs it and emits its `step`."""
        for held in self.holds:
            if held.state == "ask" and held.left <= 0:
                held.state = "late"

    def _band(self, room: int = 0) -> list:
        """The status band's rows, off the one builder both surfaces share. `Menu` paints these at the
        prompt; `footer.pinned_view` calls the same builder inside a turn — which is the invariant the
        whole design hangs on, and the cheapest test it has. `room` is what the prompt's painter has
        left once her portrait has taken its rows; nothing else ever passes one. `hz` is
        `footer.beat_hz`, the fold `pinned_view` hands the band inside a turn, so the prompt's mark
        is phased at the rate the prompt wakes at (`_wake`) — 4 over SSH, never `BEAT_HZ` bare."""
        st = self._state()
        return rows.band_rows(self.caps, st, footer.frame_w(self.caps), room=room,
                              hz=footer.beat_hz(self.caps, st))

    def _bar_state(self) -> tuple:
        """Everything the bar's out-of-turn half is made of, as one value. The mark is in it as the
        FRAME it draws, so two wakes inside one frame compare equal — which is what lets the beat tell
        "something changed" from "I woke up again".

        The band's rows are in it as their plain text: the beat only invalidates on this value, and a
        plan step ticking off or row one's once-a-second clock moves nothing else the bar reads, so
        without the band's fingerprint the one surface carrying them would never repaint.

        The chip is in it unconditionally, or a job that ends while something else holds `kind` leaves
        the loudest device in the bar saying she is still on it."""
        st = self._state()
        kind = footer.bar_kind(st)
        armed = time.monotonic() < st.armed_until
        return (kind, armed, footer.chip_dot(self.caps, st, kind),
                rows.work_is_live(st), footer.out_twins(st, kind),
                footer.out_right(self.caps, st, kind, armed),
                tuple(r.plain for r in rows.band_rows(self.caps, st, footer.frame_w(self.caps),
                                                      hz=footer.beat_hz(self.caps, st))))

    def _release(self, region) -> None:
        """The rows a region let go of, and the count of the transcript at that moment — so what is
        printed between here and the next prompt can be taken off them (`_pin_room`)."""
        self.room, self._room_at = region.released, self.screen.printed

    def _pin_room(self) -> int:
        """The rows below the cursor the next prompt is pinned with, or 0 to ask the terminal.

        A region's `released` is exact at the moment it stops — the cursor is on its first row and that
        many rows stand under it — and every row printed afterwards moves the cursor down one while the
        number stays put. Released 7 with two rows printed since leaves the prompt pinned over a cursor
        that has 5: two rows of the header scroll off for good and two blanks sit between the chrome
        and the box. The arithmetic is the released room less what `Screen.printed` counted since the
        release, floored at the one row the cursor stands on once the print reaches the foot.

        With nothing released the terminal is asked, or the last frame's room is offered again."""
        room, self.room = self.room, 0
        if not room:
            return self._still_pinned()
        return max(1, room - (self.screen.printed - self._room_at))

    def _still_pinned(self) -> int:
        """The room the last frame stood in, offered again when nothing has been printed into the gap.

        An empty `enter` ENDS one prompt and opens another, and prompt_toolkit opens every prompt by
        drawing at its preferred height and only then asking the terminal where the cursor is — so the
        box paints itself just under the transcript and drops back to the foot a round trip later.
        That lurch is what this answers.

        `Screen.printed` is what says the gap was empty. A row committed between the two prompts moved
        the frame down by exactly as many rows as it took, and a room claimed too large is the one
        failure worth avoiding here: the terminal would have to scroll to honour it."""
        return 0 if self.screen.printed else self.prompt.pinned_room()

    def _rendered(self, _app) -> None:
        """After every render, whoever asked for it: the completion list is put back in step and the
        bar's state is recorded, so the beat can tell what is already on the glass from what it woke up
        to say. The list is painted HERE, after the render rather than during it, so a keystroke that
        changes it costs one paint and not two — prompt_toolkit never draws the list, `Menu` does.

        Not during a landing: the list paints straight at the fd while this render still sits in
        prompt_toolkit's buffer, so it would go on the glass ahead of the block it belongs above.

        The row count restarts here, from a frame rather than from boot, because `_still_pinned` asks
        whether anything has been printed since the frame was last where it says it is."""
        if self.menu and not self._landing:
            self.menu.sync()
        self.screen.printed = 0
        self._painted = self._bar_state()

    def _beat(self) -> None:
        """The live states that exist at the prompt — and they pay for themselves and stop.

        It never paints what the screen already shows. Its last sleep outlives whatever it was watching
        and a keystroke in the meantime has already repainted the bar, so it compares against the state
        at the last RENDER, not the state it last saw; without that, one empty diff lands a beat later.

        A wake that raises is logged and the next one is tried. This is prompt_toolkit's background
        task, and an exception leaving it prints the whole traceback over her transcript and then eats
        the next Enter, with the beat dead for the rest of the prompt. The retry sleep is `CARD_S`, so
        a builder that raises on every wake costs two log lines a second and never a hot loop."""
        if self.prompt.session is None:
            return
        app = self.prompt.session.app

        async def beat() -> None:
            try:
                while True:
                    try:
                        nxt = self._wake()
                        if nxt is None:
                            return
                        await asyncio.sleep(nxt)
                        self._tick_held(time.monotonic())
                        if self._deliverable():
                            await self._deliver()
                            continue
                        if self._card_deliverable():
                            self._yield_prompt()
                            return
                        if self._bar_state() != self._painted:
                            app.invalidate()
                    except Exception:
                        log.warning("the beat stumbled — the bar sits still until the next wake",
                                    exc_info=True)
                        await asyncio.sleep(footer.CARD_S)
            finally:
                self._beating = False

        if not self._beating and self._wake() is not None:
            self._beating = True
            app.create_background_task(beat())

    def _deliverable(self) -> bool:
        """What the PRINT door may put on the screen unasked: her news — the finished job's receipt,
        which is rows and nothing else, so `_deliver` can erase, print and repaint in one write. Its
        clauses are that door's own: `_no_room` because the erase-and-repaint needs the terminal to
        answer where the cursor is and a refusal latches, `turn_start` because mid-turn the region owns
        the glass, and the work clauses because a receipt is owed exactly once, by a job that has ended
        and not yet landed. A held card is NOT news — it needs the keyboard, and under a live prompt
        its rail keys would land in the input box — so it goes through the other door instead
        (`_card_deliverable` / `_yield_prompt`), which ends the prompt rather than printing past it."""
        return bool(not self._no_room and not self.turn_start and self.work
                    and not self.work.landed and self.work.state != "running")

    def _card_deliverable(self) -> bool:
        """Whether the beat may end this prompt so a held card can open, unasked. Only over a box that
        is the card's own: nothing half-typed that a rail key could eat, no panel borrowing the box, no
        armed ctrl-c whose second press must keep meaning leave — and a live, running prompt, because
        this door is the prompt's exit. `_no_room` does not bind it: `_deferred` measures for itself
        and tolerates a terminal that will not say. The holds are read FIRST — the list is empty for
        almost every wake, and nothing else here is worth asking about until it is not."""
        if rows.hold_at(self.holds, "ask") is None:
            return False
        if not self.caps.interactive or self.turn_start:
            return False
        session = self.prompt.session
        if session is None or not session.app._is_running:
            return False
        return (not session.default_buffer.text and self.prompt.picker is None
                and not self.prompt.leaving)

    def _yield_prompt(self) -> None:
        """End the idle prompt through the door a menu pick already leaves by (`input/prompt._send`:
        `exit(result="")`), so `_loop` runs its existing `_deferred` pass — one renderer, one key
        reader, no second card surface. The caller checks `_card_deliverable` in the same loop turn,
        with no await between check and exit, so no keystroke can land in the box in between; one
        already in flight is met by `_deferred`'s arming grace instead."""
        self._auto_opened = True
        try:
            self.prompt.session.app.exit(result="")
        except Exception:
            self._auto_opened = False
            log.debug("could not end the prompt for a held card", exc_info=True)

    def _land_work(self, *, parting: bool = False) -> bool:
        """The long job's receipt, told once — and a RECEIPT, not a third copy of the roster: the
        closing bracket, the plan's close receipt, what she hands you, and her sentence. The per-tool
        and per-helper rows stay reachable in `/work N` and live in the panel while the job runs. A
        helper that FAILED still leaves its row: a panel may never be the only place a fact appeared.

        `parting` is the session ending. `/work N` is about to stop existing, so the whole roster
        lands — deleting rows nobody can reach any other way is the one thing this slim may not do.
        The block ENDS on her: the hint belongs with the rows it points at, and printed under her
        sentence it sat at column 0 inside the rows her portrait reserves. The tail is `gap` then
        `separate`, each inert when the other fires — `blank` was unconditional and would leave two."""
        job = self.work
        if not job or job.state == "running" or job.landed:
            return False
        job.landed = True
        # The job's line-up is the last one back from here on — what `/helpers` reads once nothing
        # runs (`slash._line_up`); a turn's line-up reaches the same field through `_commit_roster`.
        self.last_helpers = job.helpers or self.last_helpers
        self.screen.separate()
        for drawn in job.plans:
            for row in drawn:
                self.screen.row(row)
        if parting:
            for tool in job.tools:
                self.screen.row(rows.tool_text(self.caps, tool, self.screen.rw))
        for i, helper in enumerate(job.helpers, 1):
            if parting or helper.state == "failed":
                for row in rows.helper_rows(self.caps, helper, i, self.screen.rw):
                    self.screen.row(row)
        self.screen.row(rows.work_row(self.caps, job, self.screen.rw))
        for n in job.gift_ns:
            for row in rows.gift_rows(self.caps, self.gifts[n - 1], n, self.screen.rw):
                self.screen.row(row)
        if not parting and (job.tools or job.helpers):
            self.screen.chrome(f"/work {job.n} opens the whole of it")
        if job.state == "ok":
            self._announce(job.said or job.summary, "excited")
        else:
            for row in self.screen.receipt_rows(job.summary) if job.summary else []:
                self.screen.row(row)
        self.screen.gap()
        self.screen.separate()
        return True

    def _announce(self, md: str, emotion: str) -> None:
        """Her own words for something that happened with no turn around it — `_work_words` has already
        asked her for them, and the stored summary is what she is given when it could not.

        Printed as HER block and not as chrome: a receipt at the gutter has no plate, no portrait and
        no face, so it does not read as her speaking, and it shows its own `**asterisks**` and breaks a
        URL across the row it was cut on. Her emotion is set instantly because this row commits at once
        and a face mid-blink is the one she is leaving.

        Never for a job YOU stopped: that is your decision and the row is the whole receipt. The backend
        agrees — a cancelled job's record is cleared, and the idle record that restores is announced."""
        if not md:
            return
        self.screen.said_plate = self.screen.resumed = False
        self.screen.face_art = self.screen.erase_span = None
        self.rows_committed = True
        self.screen.face.set(emotion, instant=True)
        self.screen.say(md, last=True)

    async def _work_words(self) -> None:
        """The `__work_done__` turn every other client fires, run here in process, so the long job's
        receipt lands with her voice on it instead of a raw summary and nothing said.

        Never once the job has already been announced. The sentinel is not a message — it is dropped
        before the model sees it — and the job's prompt note goes empty the moment any turn has spoken
        with the job finished. Fired then, she is asked to continue a conversation with no instruction
        in it and answers the message before this one.

        Off `self.work` for its length, or a `step` frame arriving now banks rows on a receipt about to
        print. It stays on `announcing` just as long: one frame of this turn IS the receipt's."""
        from kotoba.core import work_state

        job = self.work
        if job is None or job.landed or job.said or job.state != "ok":
            return
        if work_state.get(self.session.session_id)["announced"]:
            return
        self.work, self.announcing = None, job
        try:
            job.said = await self.session.ask(cli_session.WORK_DONE)
        except Exception:
            log.warning("could not ask her about the long job", exc_info=True)
        finally:
            self.work, self.announcing = job, None

    async def _deliver(self) -> None:
        """The block, printed with you sitting at the prompt.

        Three phases — erase the app, print, repaint. `run_in_terminal` and `patch_stdout` do the same
        three and then ASK the terminal where the cursor is, so their first frame back is a bare box
        with no bar and no pin until a CPR round trip later: 31.1 ms measured, against a 16 ms
        threshold. This one ANSWERS instead, reading the app's top row before the erase destroys it.

        All three phases go out as ONE write. Rich flushes per print and prompt_toolkit per render —
        three writes with the frame off the glass in between — so the block is rendered into a string
        with the flush held shut. A terminal that cannot say where the cursor is prints nothing."""
        app = self.prompt.session.app
        if not app._is_running or app._running_in_terminal:
            return
        await self._work_words()
        if app.output.responds_to_cpr:
            await app.renderer.wait_for_cpr_responses()
        try:
            top = app.renderer.rows_above_layout
        except Exception:
            self._no_room = True
            return
        if self.menu:
            self.menu.close()
        self.caps.sync_size()
        height = self.caps.height
        self.screen.printed = 0
        out = app.output
        flush, out.flush = out.flush, lambda: None
        app._running_in_terminal = self._landing = True
        try:
            with self.screen.console.capture() as cap:
                self._land_work()
            app.renderer.erase()
            out.write_raw(cap.get())
            self.prompt.below = max(1, height - min(top + self.screen.printed, height - 1))
            app._running_in_terminal = False
            app._request_absolute_cursor_position()
            app._redraw()
        finally:
            app._running_in_terminal = self._landing = False
            out.flush = flush
            out.flush()
        if self.menu:
            self.menu.sync()

    def _toolbar(self) -> FormattedText:
        """prompt_toolkit's copy of the bar — the same mark, the same phrase, the same right slot, on
        the only surface the long job has out here. A seam between the two renderers would be a seam in
        the one thing carrying it: change one, change the other.

        That includes the scrub. A `FormattedText` fragment is not a `Safe`, and prompt_toolkit draws a
        control byte as caret notation — two cells where `cell_len` measured none — so a reminder
        carrying a few of them fits by the measure and wraps the toolbar onto a second row, moving the
        frame the box is pinned to. The right slot gives up its words before its seconds, or a 21-cell
        model name draws a 41-cell row in 40 columns. The kaomoji is DERIVED, never stored — out here
        the face states what she is doing, recomputed on every repaint, so `/face` is only a preview."""
        caps = self.caps
        typed = self.prompt.session.default_buffer.text if self.prompt.session else ""
        self._tick_held(time.monotonic())
        if self.paste_note and typed != self._note_at:
            self.paste_note = ()
        st = self._state()
        kind = footer.bar_kind(st)
        armed = time.monotonic() < st.armed_until
        self.screen.face.set("thinking" if typed.startswith("/") else
                             {"card": "confused", "long": "determined",
                              "okayed": "determined"}.get(kind, "neutral"), instant=True)
        caps.sync_size()
        dot = footer.chip_dot(caps, st, kind)
        working = rows.work_is_live(st)
        frags = [(theme.PT_WORK_CHIP[caps.color] if working else "class:bar.chip",
                  f" {dot} {'WORKING' if working else 'LIVE'} "), ("", "  "),
                 ("class:bar.face", self.screen.face.still()), ("", "  ")]
        used = sum(cell_len(t) for _, t in frags)
        picker = self.prompt.picker
        right = scrub(picker.trail() if picker
                      else footer.out_right(caps, st, kind, armed) or self.model)
        edge = caps.width - used - 1
        if cell_len(right) > edge:
            right = fit(edge, footer.out_right(caps, st, kind, True), "")
        room = caps.width - used - cell_len(right) - 3
        sun = armed or kind in footer.SUN_KINDS
        twins = (picker.twins() if picker else
                 footer.armed_twins(st) if armed and kind in footer.OUT_KINDS
                 else LEAVING if self.prompt.leaving
                 else self.paste_note or footer.out_twins(st, kind) or IDLE_TWINS)
        phrase = fit(room, *[caps.t(scrub(v)) for v in twins], "")
        frags.append(("class:bar.arm" if sun and phrase else "class:bar.dim",
                      f" {phrase} " if sun and phrase else phrase))
        used += cell_len(frags[-1][1])
        frags.append(("", " " * max(1, caps.width - used - cell_len(right) - 1)))
        frags.append(("class:bar.dim", right))
        return FormattedText(frags)

    def _hint(self) -> str:
        """The one clause inside the frame, and it is `box_rows`' own — the two renderers hand the
        footer to each other and the placeholder is a cell of it."""
        return footer.box_hint(self.caps, self._state(), footer.frame_w(self.caps) - 4)

    async def _ask(self, card: approvals.Card) -> object | None:
        """Answered on the loop, so the Future behind the card is actually woken. A terminal that cannot
        ask — piped, or with stdin gone — refuses instead of leaving the turn asleep for 180 s.

        Two ways in, and which one it is was decided when the frame arrived: a card already on `holds`
        is the long job's and is answered out at the prompt, anything else is this turn's and is drawn
        inline in the region the turn already owns."""
        if card.mode != "approval":
            return await asyncio.to_thread(approvals.ask_at_terminal, card)
        if not self.caps.interactive:
            self.screen.separate()
            self.screen.row(rows.approval_row(self.caps, card, self.screen.rw))
            self.screen.chrome("nothing here can answer that, so I left it alone")
            return (False, False)
        held = next((h for h in self.holds if h.rid == card.request_id), None)
        if held is None:
            key, held = await self._inline(card)
            if held is None:
                return self._answer(key, card)
        return await self._wait_for_hold(held, card)

    async def _inline(self, card: approvals.Card) -> tuple[str, state.Held | None]:
        """The card of this turn's own tool, in this turn's own region. A Held comes back when it was
        NOT answered: the turn ended over it, and a card nobody can still answer goes back to the
        prompt rather than being declined by nobody.

        It is TRANSITORY exactly as the held card is: seated in the region's pad, the overflow covering
        the transcript, and the glass after the answer is the glass before the card plus the one
        receipt row. Riding the flow worked only while the turn kept committing rows after the answer —
        a turn that answers and then sits in a long silent command commits nothing, and the card's
        height stays on the glass as blanks. It takes the keyboard off the turn's reader: two readers
        on one fd split a keystroke, and the half that went to the typeahead is a `y` nobody pressed."""
        danger, blast = await asyncio.to_thread(hazard, card.label)
        drawn = cards.Approval(card.label, danger, card.family or "", blast=blast,
                               can_always=card.can_always,
                               can_always_exact=card.can_always_exact, always_note=card.always_note,
                               sandbox=backend_name())
        self.approval = drawn
        # No await between these two, or one frame carries the card in the flow and grows the region.
        self._overlay_open(drawn)
        if self.keys is not None:
            self.keys.pause()
        try:
            key = await asyncio.to_thread(self._read_approval, drawn)
        finally:
            self.approval = None
            if self.overlay is not None:
                self.overlay.close()
                self.overlay = None
            if self.keys is not None:
                self.keys.resume()
        if not key:
            return "", self._hold(card)
        self._commit(cards.receipt_row(self.caps, card.label,
                                       cards.answered(key, card.family or "")[1]))
        return key, None

    async def _wait_for_hold(self, held: state.Held, card: approvals.Card) -> tuple[bool, bool, bool]:
        """Asleep until `_deferred` draws that card at the prompt and hands back the key. A `clear` frame
        cancels this — the window ran out, or cancel_work took it — so the hold is stamped on the way out
        and `_deferred` collapses it to the one line every unanswered card collapses to."""
        try:
            key = await self._held_keys[held.rid]
        except asyncio.CancelledError:
            if held.state == "ask":
                held.state = "late"
                self._poke()
            raise
        return self._answer(key, card)

    def _answer(self, key: str, card: approvals.Card) -> tuple[bool, bool, bool]:
        """One key, matched exactly. `key in "aA"` is a SUBSTRING test, so the empty string every
        abandoned card carries read as "always allow this family, forever". `a` is the family grant and
        `t` this line alone — two keys rather than one, so the reach never depends on which card it is."""
        if key in ("a", "A"):
            return (True, card.can_always, False)
        if key in ("t", "T"):
            return (True, False, card.can_always_exact)
        return (key in ("y", "Y"), False, False)

    def _read_approval(self, drawn: cards.Approval, held: state.Held | None = None) -> str:
        """One press, and anything that is not one is DISCARDED with a flash — "never swallow a
        keystroke" must not apply to a safety gate, or junk typed at the prompt becomes a yes: a card
        opens under a message in progress, and the first letter of an ordinary word (`and`, `all`) is
        the key that grants a command family forever. `a` on a card whose grant the gate would refuse,
        and `t` without its own flag, are junk the same way — a key the rail did not draw is never a
        decision. It polls, so a card cleared under it lets go of stdin and answers "", which is how
        the caller tells a decision from an abandonment. A card that opened UNASKED refuses every key
        until its arming grace passes: a key arriving within a third of a second of a gate appearing on
        its own was pressed at something else. Every flash lasts `footer.flash_secs`, which is longer
        than one frame of whatever samples it, or a refused key is swallowed with nothing on the glass."""
        flash = footer.flash_secs(self.caps, self._state())
        while (self.approval is drawn and not self._card_cut
               and (held is None or (held.state == "ask" and held.left > 0))):
            chunk = keys.read_input()
            if not chunk:
                continue
            key = keys.one_press(chunk)
            if key and time.monotonic() < self._card_not_before:
                drawn.flash_until = time.monotonic() + flash
                continue
            if (key in ("a", "A") and not drawn.can_always) or (
                    key in ("t", "T") and not drawn.can_always_exact):
                drawn.flash_until = time.monotonic() + flash
                continue
            if key in ("y", "Y", "a", "A", "t", "T", "n", "N", "\x1b", "\x03"):
                return "n" if key in ("\x1b", "\x03") else key
            if key == "?":
                drawn.show_why = True
            else:
                drawn.flash_until = time.monotonic() + flash
        return ""

    async def _read_card(self, read, *args) -> str:
        """One key off the reader's thread — and ctrl-c AT a card is the card's own `n`, never the
        session's end.

        `read_input` keeps ISIG, so a ctrl-c at a card is a SIGINT and not the `\\x03` the reader names:
        `asyncio.run` answers it by cancelling the main task, which is asleep right here. Left to
        propagate, it ended the whole session — card declined, long job stopped, `OFFLINE` — for the
        key that promises to stop her and never the session. A gate reads it as the safe key, the one
        direction a card may ever get easier to answer in. `_card_cut` is a flag of its own, raised
        HERE, and the thread is waited for before anything else may read stdin: two readers on one fd
        split a keystroke, and the next card's `y` would be half a key."""
        fut = asyncio.ensure_future(asyncio.to_thread(read, *args))
        try:
            return await asyncio.shield(fut)
        except (KeyboardInterrupt, asyncio.CancelledError):
            self._card_cut = True
            try:
                await asyncio.wait_for(fut, keys.POLL * 3)
            except BaseException:
                pass
            finally:
                self._card_cut = False
            return "n"

    def _read_confirm(self, card: cards.Confirm) -> str:
        """One of the rail's two keys, polled the way `_read_approval` polls — a key that is not one
        of them is DISCARDED with a flash, and a card cleared under the read lets go of stdin and
        answers "", which the caller reads as a no."""
        while self.confirm is card and not self._card_cut:
            chunk = keys.read_input()
            if not chunk:
                continue
            key = keys.one_press(chunk)
            if key in ("y", "Y"):
                return "y"
            if key in ("n", "N", "\x1b", "\x03"):
                return "n"
            card.flash_until = time.monotonic() + footer.flash_secs(self.caps, self._state())
        return ""

    async def _answer_confirm(self, card: cards.Confirm) -> bool:
        """The two-key rail on the one card surface every gate shares: seated against the pinned
        block, the overflow covering the transcript, the cover closed before the receipt commits —
        so the glass after the answer is the glass before the rail plus the receipt. The collapsed
        ANSWERED row is held for `PRESS_MS` first, as the rail always did. Ctrl-c at the rail is its
        `n` (`_read_card`); anything else that leaves the read still takes the cover off the glass on
        its way out, because a bank nobody restores is stale card pixels standing in the transcript."""
        self.confirm = card
        self._overlay_open(card)
        try:
            key = await self._read_card(self._read_confirm, card)
        except BaseException:
            self.confirm = None
            if self.overlay is not None:
                self.overlay.close()
                self.overlay = None
            raise
        yes = key == "y"
        card.state = "pressed"
        card.answer = f"y — {card.yes}" if yes else f"n — {card.no}"
        try:
            self.region.refresh()
            if self.overlay is not None:
                self.overlay.sync()
        except Exception:
            log.debug("could not draw the pressed rail", exc_info=True)
        if not self.caps.reduced_motion:
            await asyncio.sleep(cards.PRESS_MS / 1000)
        if self.overlay is not None:
            self.overlay.close()
            self.overlay = None
        self.confirm = None
        self._commit(cards.receipt_row(self.caps, card.head, card.did if yes else card.didnt))
        return yes

    async def _confirm_card(self, card: cards.Confirm) -> bool:
        """`slash._confirm`'s glass: the same band-frame-bar region `_deferred` opens at the prompt,
        with `_answer_confirm` inside it. Its own transient Live used to draw the rail at the cursor,
        and the rows it took scrolled the transcript up for good — measured 5 rows into scrollback on
        a full window, never returned.

        The gutter guard is handed over exactly as `_turn` hands its own: `_on_resize` pushes
        `self.gutter` back after re-pinning, and a resize serviced here used to push `None` before
        any turn — every print after it died on `'NoneType' object has no attribute
        'process_renderables'` — or the last turn's guard, bound to a Region that had stopped."""
        self.screen.measure()
        region = self.screen.region(self.spin, self._state, self.parts)
        yes = False
        with keep_output_on_interrupt(), region as live, Gutter(self.screen, live) as guard:
            self.region, self.gutter = live, guard
            clock = asyncio.create_task(self._clock())
            try:
                self.screen.separate()
                yes = await self._answer_confirm(card)
                self.screen.blank()
            finally:
                clock.cancel()
                if self.overlay is not None:
                    self.overlay.forget()
                    self.overlay = None
                self.confirm = None
                self.region = None
        self._release(region)
        return yes

    async def _deferred(self) -> bool:
        """The card that could not be shown when she asked for it: the long job runs detached, so its
        approval opens with the person at the prompt — possibly two at once, each with its own request
        id. Two gestures draw it: the person's Enter, and the beat's own exit of an idle prompt, which
        is how a hold puts itself on the glass unasked. The second is marked, and an unasked card
        refuses every key for `CARD_GRACE_S` after it lands, so a keystroke in flight while the prompt
        was being taken flashes instead of answering.

        Oldest first, one at a time, as an overlay above the released region so the transcript neither
        scrolls on open nor blanks on answer. A window can run out while you are reading it — 180 s is
        roomy, not infinite — so the card collapses to the one line every answer collapses to."""
        unasked, self._auto_opened = self._auto_opened, False
        if not self.caps.interactive or rows.hold_at(self.holds, "ask", "late") is None:
            return False
        if unasked:
            self._card_not_before = time.monotonic() + CARD_GRACE_S
        self.screen.measure()
        region = self.screen.region(self.spin, self._state, self.parts)
        with keep_output_on_interrupt(), region as live, Gutter(self.screen, live) as guard:
            self.region, self.gutter = live, guard
            clock = asyncio.create_task(self._clock())
            try:
                self.screen.separate()
                while True:
                    held = rows.hold_at(self.holds, "ask", "late")
                    if held is None:
                        break
                    waiting = sum(1 for h in self.holds if h.state == "ask")
                    if waiting > 1:
                        self._commit(Text(self.caps.t(
                            f"{waiting} of hers are waiting — this one first, and it's the older"),
                            style="chrome"))
                    await self._answer_held(held)
                self.screen.blank()
            finally:
                clock.cancel()
                if self.overlay is not None:
                    self.overlay.forget()
                    self.overlay = None
                self.region = None
        self._release(region)
        self.screen.face.rest()
        return True

    def _overlay_open(self, drawn) -> None:
        """The open card as a transitory overlay, the way a panel is (`input/menu.Overlay`) — set
        BEFORE the region draws a frame, so no frame ever carries the card in its flow and grows.
        Every card comes through here — held, inline, confirm — because two implementations of
        cover-and-restore is how two surfaces learn to disagree. Left unset — no region, no room for
        the WHOLE card at its tallest (`?` raises an approval to `show_why`), a bank that cannot
        answer — the next frame draws it in the flow instead: the grow-and-scroll look, kept on
        purpose, because a gate shown in part is not a gate."""
        if self.region is None:
            return
        over = Overlay(self.screen, lambda: self._card_head(drawn))
        self.overlay = over
        try:
            self.region.refresh()
            tallest = (drawn if isinstance(drawn, cards.Confirm)
                       else replace(drawn, show_why=True))
            whole = len(self._card_rows(tallest))
            seat = max(0, self.screen.reserve - self.screen.content_h)
            if not over.open(max(0, whole - seat)):
                self.overlay = None
        except Exception:
            self.overlay = None
            log.debug("could not overlay the card — it rides the region", exc_info=True)

    def _card_rows(self, drawn) -> list:
        """One builder call for whichever rail `drawn` is, so the seat, the head and the measure can
        never use different rows for one card."""
        if isinstance(drawn, cards.Confirm):
            return cards.confirm_rows(self.caps, drawn, self.card_w)
        return cards.approval_rows(self.caps, drawn, self.card_w)

    def _card_head(self, drawn) -> list:
        """What the seat could not take, drawn above the region — the card's top rows. `seated_k`
        is the LAST render's count, and `_clock` refreshes the region before it syncs the overlay,
        so a `?` that just grew the card is re-seated before this builds."""
        rows_ = self._card_rows(drawn)
        k = self.parts.seated_k
        return rows_[:len(rows_) - k] if k else rows_

    async def _answer_held(self, held: state.Held) -> None:
        if held.state == "ask":
            self.screen.face.set("surprised", instant=True)
            drawn = cards.Approval(held.cmd, held.danger, held.family, intent=held.intent,
                                   blast=held.blast, can_always=held.can_always,
                                   can_always_exact=held.can_always_exact,
                                   always_note=held.always_note, held=True, sandbox=backend_name())
            self.approval = drawn
            self._overlay_open(drawn)
            try:
                key = await self._read_card(self._read_approval, drawn, held)
            finally:
                self.approval = None
                # Closed before the receipt commits: a bank read after a scroll restores a row low.
                if self.overlay is not None:
                    self.overlay.close()
                    self.overlay = None
            if key:
                self._commit(cards.receipt_row(self.caps, held.cmd,
                                               cards.answered(key, held.family)[1]))
                self._settle(held, key)
                return
        held.state = "late"
        self._commit(cards.receipt_row(self.caps, held.cmd, "no answer in time — I left it alone"))
        self._settle(held, "n")

    def _settle(self, held: state.Held, key: str) -> None:
        """The CLI's part in this card is over: hand the key to whichever `_ask` is asleep on it and let
        go. The hold is dropped rather than kept, because what the command LEAVES is the backend's row
        — the same `step` frame as for any other action — so a settled hold has nothing further to say
        to the bar."""
        held.told = True
        fut = self._held_keys.pop(held.rid, None)
        if held in self.holds:
            self.holds.remove(held)
        if fut is not None and not fut.done():
            fut.set_result(key)

    def paste(self, buf) -> None:
        """ctrl-v, and the one entry point the prompt binds. A terminal paste hands the application
        text and only text — on Wayland the image bytes never arrive at all — so the CLI reads the
        system clipboard itself, writes it to a file and attaches that, exactly as `/attach` does.

        It lands in the box as `[Image #n]` rather than printing anything: the frame belongs to
        prompt_toolkit here. The marker is only what you SEE — the bytes travel on the clip list — so
        erasing it, or never typing a word beside it, still sends the picture.

        The number counts the SESSION, not the message: named off the pending list, a second message's
        first clip was `clip-1.png` again and overwrote the first one's file."""
        data, why = clipboard_image()
        if not data:
            self._note(why)
            return
        n = self.shared_n + 1
        name = f"clip-{n}.png"
        try:
            self.clip_dir = self.clip_dir or str(workspace.scratch_dir() / "clips")
            os.makedirs(self.clip_dir, exist_ok=True)
            path = os.path.join(self.clip_dir, name)
            with open(path, "wb") as fh:
                fh.write(data)
        except OSError:
            self.clip_dir = ""
            self._note(NO_ROOM)
            return
        self.shared_n = n
        mark = slash.marker(n, name)
        self.clips.append((name, path, len(data), mark))
        head = buf.text[:buf.cursor_position]
        word = "" if not head or head.endswith((" ", "\n")) else " "
        buf.insert_text(word + mark)
        self.paste_note = ()

    def _note(self, twins: tuple) -> None:
        """A phrase in the bar's own slot, drawing no row: the frame is prompt_toolkit's here, and the
        bar only repaints on a keystroke, so this costs nothing at rest. It clears itself the moment you
        type something else.

        Two things use it. A ctrl-v that found nothing to attach says so, and a paste folded to a
        `[Pasted text #n]` names the gesture that opens it again (`input/prompt.insert_chunk`) — both
        are answers to the key just pressed, and both are gone by the next one."""
        self.paste_note = twins
        self._note_at = self.prompt.session.default_buffer.text if self.prompt.session else ""

    def _flush_clips(self, line: str) -> str:
        """The clips of this message on their way out, and the text the turn actually sends. `/attach`'s
        own path turns each one into the `data:` part her next turn carries — one path into
        `core.attachments`, never a second — and that store is then also what says whether a line of
        nothing but markers has a file behind it, so a marker `/attach` left in the box reads alike.

        A clip `_carry` would not take leaves with its marker (`slash.unmark` says why it is dropped and
        not renumbered). Its refusal is already on screen and it draws no SENT row; what stayed behind was
        a `[Image #5]` in a line carrying four — the person told, and her handed the reference anyway. A
        message that was only that marker comes back empty, which `_loop` declines to send."""
        for name, path, size, mark in self.clips:
            note = slash._carry(self, path, name, label=mark)
            if note is None:
                line = slash.unmark(line, mark)
            else:
                self._gift("sent", name,
                           f"{size // 1024 or 1} kB from your clipboard {self.caps.g['bullet']} {note}")
        self.clips = []
        if not slash.only_marks(line):
            return line
        from kotoba.core import attachments

        return slash.said(line, attachments.has(self.session.session_id))

    def _bye(self) -> None:
        """A long job dies with the session — there is no daemon behind this prompt — so leaving with one
        running says so and prints its row rather than letting twenty minutes of it vanish off the bottom
        of a scroll. A card still waiting goes the same way, declined rather than dropped, so nothing
        runs behind you."""
        gone = ("left alone — you're going" if self.caps.interactive
                else "no — there's no terminal to ask")
        for held in list(self.holds):
            self.screen.row(cards.receipt_row(self.caps, held.cmd, gone))
            self._settle(held, "n")
        job = self.work
        if job is not None and job.state == "running":
            slash._cut(job)
            job.summary = "it goes when the session does"
        self._land_work(parting=True)
        self._land_due()
        self.screen.face.set("sleepy", instant=True)
        line = Text()
        if self.caps.color == "none" and not self.caps.interactive:
            line.append("[OFFLINE]")
        else:
            line.append_text(footer.plate(self.caps, f"{self.caps.g['ring']} OFFLINE", "ink"))
        line.append("  ")
        line.append(self.screen.face.still(), style=self.screen.face.style)
        tail = self.caps.t("  see you — the transcript stays right here")
        if cell_len(tail) > self.screen.w - line.cell_len:
            tail = self.caps.t("  see you")
        line.append(tail, style="chrome")
        self.screen.separate()
        self.screen.row(line)

    async def _slash(self, cmd: commands.Command) -> bool:
        """True means leave. Everything else prints at column 0 and returns to the prompt.

        Awaited because six of them are coroutines — `/settings` gathers what the web panel gathers,
        `/sessions` counts the turns in each past conversation, `/model`, `/set` and `/approvals` read or
        write her configuration, and `/stop` awaits `cancel_work` — and all of it runs on the loop that
        owns the one connection."""
        return await slash.run(self, cmd)


async def run(*, plain: bool = False, ascii_only: bool = False, calm: bool = False,
              no_face: bool = False, launch: str = "") -> int:
    from kotoba.core import file_library
    from kotoba.cli.render.portrait import Portrait

    caps = detect(plain=plain, ascii_only=ascii_only, calm=calm)
    screen = Screen(caps, portrait=Portrait(caps, wanted=not no_face))
    app = App(caps, screen, Prompt(caps, workdir=str(file_library.library_dir())))
    app.launch = launch
    # The reference goes both ways, so one of the two ends is tied after the fact.
    app.prompt.toolbar, app.prompt.hint = app._toolbar, app._hint
    return await app.run()
