"""The line she is typed into: multiline, in a frame of her own, on the last rows of the terminal.

A non-full-screen prompt_toolkit app sizes itself to the rows *below* the cursor (CSI 6n) and hands the
spare ones to whichever child can grow — stock, the input window, so the bar lands on the last line with
the input stranded a screen above it. A filler on top takes them instead and the footer sinks to the
bottom: pinned, growing upward as the input wraps, no alt screen, no scroll region, scrollback intact.

Enter is `c-m`, the same enum member as `Keys.Enter`, so binding `c-j` catches Ctrl+J and never Enter;
Alt+Enter (`escape` then `enter`) is the only Enter chord a terminal does not collapse onto plain Enter.
"""
from __future__ import annotations

import functools
import logging
import os
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, has_completions, to_filter
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import FloatContainer, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu, MultiColumnCompletionsMenu
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from kotoba.cli.input import commands
from kotoba.core.path_security import validate_within_dir
from kotoba.paths import home_dir

log = logging.getLogger("kotoba.cli")

ESC_FLUSH = 0.05
#: Keys that are text when they arrive inside a burst. Everything else keeps the meaning it has.
TYPED = {Keys.ControlM, Keys.ControlJ, Keys.ControlI}
HEAVY = {"┌": "┏", "┐": "┓", "└": "┗", "┘": "┛", "─": "━", "│": "┃"}
PLAIN_BOX = {"┌": "+", "┐": "+", "└": "+", "┘": "+", "─": "-", "│": "|"}
PLACEHOLDER = "talk to her · @ a file · / for commands"
BOX_CHROME = 3
BOX_SHARE = 2
FLOOR = 3
PASTED = "[Pasted text #{n}]"
EXPAND = ("{lines} lines pasted — paste again to expand", "paste again to expand")

DEPTH = {"truecolor": ColorDepth.TRUE_COLOR, "256": ColorDepth.DEPTH_8_BIT,
         "16": ColorDepth.DEPTH_4_BIT, "none": ColorDepth.DEPTH_1_BIT}

# The rich side's coral in prompt_toolkit's notation — one step of hue off would show as a seam.
PT_PAL = {
    "truecolor": {
        "dark": {"coral": "#ff5a3c", "live_chip": "bg:#ff3b5c #ffffff", "shadow": "fg:#943e2f"},
        "light": {"coral": "#c73a1e", "live_chip": "bg:#c8093a #ffffff", "shadow": "fg:#db8372"},
        "mid": {"coral": "#e13c21", "live_chip": "bg:#eb214e #ffffff", "shadow": "fg:#852f21"}},
    "256": {
        "dark": {"coral": "#ff5f00", "live_chip": "bg:#ff005f #ffffff", "shadow": "fg:#4e4e4e"},
        "light": {"coral": "#af0000", "live_chip": "bg:#d7005f #ffffff", "shadow": "fg:#808080"},
        "mid": {"coral": "#d70000", "live_chip": "bg:#ff005f #ffffff", "shadow": "fg:#4e4e4e"}},
    "16": {
        "dark": {"coral": "ansibrightred", "live_chip": "bg:ansired ansiwhite",
                 "shadow": "fg:ansibrightblack"},
        "light": {"coral": "ansired", "live_chip": "bg:ansired ansiwhite",
                  "shadow": "fg:ansibrightblack"},
        "mid": {"coral": "ansibrightred", "live_chip": "bg:ansired ansiwhite",
                "shadow": "fg:ansibrightblack"}},
    "none": {b: {"coral": "", "live_chip": "reverse", "shadow": ""}
             for b in ("dark", "light", "mid")},
}
ARM = {"truecolor": "bg:#ffb22e #211a2e bold", "256": "bg:#ffaf00 #262626 bold",
       "16": "bg:ansiyellow ansiblack bold", "none": "reverse"}


def history_path() -> Path:
    return Path(os.getenv("KOTOBA_CLI_HISTORY", str(home_dir() / "cli_history"))).expanduser()


def _inside(path: str | Path, root: Path) -> bool:
    """Whether `path` lands within `root` once `..`, `~` and every symlink on the way are resolved.

    Fails CLOSED on purpose: a path that cannot be resolved at all — a symlink loop, a component that
    is not a directory, a home that cannot be found — is not a path worth offering."""
    try:
        validate_within_dir(path, root)
        return True
    except Exception:
        return False


class SlashCompleter(Completer):
    """`/` completes her commands, `@` completes a path INSIDE her workdir. Anything else completes
    nothing — a menu that opens while someone is writing a sentence is noise on every keystroke.

    `@` is jailed and `/attach` is not: `/attach ~/Downloads/x.png` is a path typed out in full, once,
    knowingly, where `@~/` painted a whole home directory into the box on ONE keystroke, onto a terminal
    that gets screenshotted and recorded. So the fragment and every row it offers go through the same
    containment her file tools use — tilde and absolute spellings still expand, since what is enforced is
    containment and not spelling. A fragment pointing outside SAYS so rather than showing an empty list,
    and that row DELETES the fragment if taken: prompt_toolkit culls a lone completion that would insert
    nothing, so a notice with empty text and a zero start_position is never drawn at all."""

    OUTSIDE = "that's outside her workdir"
    OUTSIDE_META = "/attach reaches anywhere else"

    def __init__(self, workdir: str | None = None, prompt: "Prompt | None" = None) -> None:
        self.workdir = workdir
        self.prompt = prompt

    def root(self) -> Path:
        """Resolved on every call: the workdir may be created, or turn into a symlink, after this is
        built, and a Mac or a container hands us a symlinked one as a matter of course."""
        return Path(self.workdir or os.getcwd()).expanduser().resolve()

    def get_completions(self, document, complete_event):
        """A panel owns the list while it is open — its rows go on the buffer directly — so this yields
        nothing rather than racing it, unless it has given way to a `/` or an `@`.

        `WORD=True`, always: the plain form strips the leading `/`, which puts start_position off by
        one and replaces the slash with the completion."""
        panel = self.prompt.picker if self.prompt is not None else None
        if panel is not None and not panel.cedes(document):
            return
        word = document.get_word_before_cursor(WORD=True)
        if word.startswith("/"):
            for name in commands.complete(word):
                yield Completion(name, start_position=-len(word),
                                 display_meta=commands.COMMANDS[name])
        elif word.startswith("@"):
            yield from self._paths(document, word)

    def _paths(self, document, word: str):
        """Rows for an `@` fragment, every one inside the jail — `get_paths` only bases a RELATIVE
        fragment and jails nothing, so `file_filter` sees every row."""
        from prompt_toolkit.document import Document

        root, frag = self.root(), word[1:]
        if not _inside(frag or ".", root):
            yield Completion("", start_position=-len(frag), display=self.OUTSIDE,
                             display_meta=self.OUTSIDE_META)
            return
        paths = PathCompleter(expanduser=True, get_paths=lambda: [str(root)],
                              file_filter=lambda full: _inside(full, root))
        for hit in paths.get_completions(Document(frag, len(frag)), None):
            yield Completion(hit.text, start_position=hit.start_position, display=hit.display)


class Prompt:
    """One line editor for the whole session, so its history and its keys outlive a turn."""

    def __init__(self, caps, *, workdir: str | None = None, complete_while_typing: bool = True,
                 toolbar=None, hint=None, input=None, output=None) -> None:
        self.caps = caps
        self.pending = caps.leftover
        self.session: PromptSession | None = None
        self.body: Window | None = None
        self.below = 0
        self.toolbar = toolbar
        self.paste = None
        self.hint = hint or (lambda: caps.t(PLACEHOLDER))
        # The panel borrowing this box, and what its last enter left for the loop.
        self.picker = None
        self.picked: tuple = ()
        self.line_up = None
        self.note = None
        self.pastes: list[tuple[str, str]] = []
        self.pasted_n = 0
        # The room the last frame stood in and the window it stood in (`_noted`, `pinned_room`).
        self.seen = 0
        self.seen_rows = 0
        self.seen_cols = 0
        # Resizes counted, because the size cannot answer for them — see `_resized`.
        self.resizes = 0
        # The ctrl-c awaiting a second, and the hook that says so in the bar (`_stop`).
        self.leaving = False
        self.on_leaving = None
        if not caps.interactive:
            return
        self.session = PromptSession(
            message=lambda: FormattedText([("class:prompt", caps.g["prompt"] + " ")]),
            multiline=True,
            key_bindings=_keys(self),
            completer=SlashCompleter(workdir, prompt=self),
            complete_while_typing=complete_while_typing,
            enable_history_search=False,
            auto_suggest=AutoSuggestFromHistory(),
            reserve_space_for_menu=0,
            show_frame=True,
            erase_when_done=True,
            bottom_toolbar=self._bar,
            placeholder=lambda: FormattedText([("class:placeholder", self._placeholder())]),
            history=_history(),
            prompt_continuation=lambda w, ln, sw: [("class:cont", caps.g["cont"] + " ")],
            style=_style(caps),
            color_depth=DEPTH[caps.color],
            input=input, output=output,
        )
        self.pin()

    def pin(self) -> "Prompt":
        app = self.session.app
        app.output.get_rows_below_cursor_position = self.room
        box = HEAVY if self.caps.unicode else PLAIN_BOX
        for w in app.layout.walk():
            c = getattr(w, "content", None)
            if isinstance(c, BufferControl) and c.buffer is self.session.default_buffer:
                w.dont_extend_height = to_filter(True)
                w.height = lambda: Dimension(max=self.ceiling())
                self.body = w
            ch = getattr(w, "char", None)
            if isinstance(ch, str) and ch in box:
                w.char = box[ch]
        self._drop_menu()
        self._unstick_esc()
        root = app.layout.container
        root.children[0] = VSplit([
            root.children[0],
            Window(width=1, char=self.caps.g["shadow"], style="class:box.shadow"),
        ])
        root.children.insert(0, Window())
        app.after_render += self._noted
        app._on_resize = functools.partial(self._resized, app._on_resize)
        return self

    def ceiling(self) -> int:
        """Rows of text the box may grow to before it stops growing and scrolls inside itself instead.

        One `BOX_SHARE` of the window less `BOX_CHROME` — the two borders and the bar — never below
        `FLOOR`. Half the window, and the half is measured: at 30 rows the landing survived a 12-line
        paste (box 15, exactly half) and lost rows at 16 (box 19, near two thirds); at 24 rows it
        survived 8 (box 11) and lost four at 12 (box 15, two thirds again). Past half is where the
        transcript starts paying, on both. With no ceiling at all a 40-line paste at 96x30 put the top
        border on row 0 and the bar on row 29, her whole header scrolled away for good. A fraction and
        not a constant because it must hold on a 24-row ssh window and a 60-row one; under about twelve
        rows no fraction leaves a box somebody can type into, which is what `FLOOR` is for."""
        return max(FLOOR, self.caps.height // BOX_SHARE - BOX_CHROME)

    def rows_of(self, chunk: str) -> int:
        """Rows `chunk` would take in the box, wrapping counted. Wide glyphs are two cells and a line
        of them wraps twice as soon, so this asks `get_cwidth` rather than `len` — the same question
        the renderer will ask when it lays the chunk out."""
        width = max(1, self.caps.width - BOX_CHROME - 2)
        return sum(max(1, -(-get_cwidth(line) // width)) for line in chunk.split("\n"))

    def insert_chunk(self, buf, chunk: str) -> None:
        """A whole arrival on its way into the box: itself, or a reference standing for it.

        The question is SIZE and never "was that a real paste". A paragraph typed at her by a program
        arrives with no paste markers at all, and a person typing fast is indistinguishable from either
        — so a rule reading the markers would replace one typist's sentence with a placeholder and leave
        another's forty lines filling the screen. Anything the box could have held goes in verbatim;
        anything taller than `ceiling` goes in as `[Pasted text #n]`, a DISPLAY whose text is kept here
        and put back before the line is ever accepted. A chunk opening on a line break brings its own
        separator, and a space in front would be one the person never typed. The number counts the
        SESSION, never an index; it counts LINES where the ceiling counts ROWS, so anyone can check it."""
        if self.unfold_one(buf, chunk):
            return
        if self.rows_of(chunk) <= self.ceiling():
            buf.insert_text(chunk)
            return
        self.pasted_n += 1
        mark = PASTED.format(n=self.pasted_n)
        self.pastes.append((mark, chunk))
        head = buf.text[:buf.cursor_position]
        joined = not head or head[-1].isspace() or chunk[:1] in ("\n", "\r")
        buf.insert_text(("" if joined else " ") + mark)
        if self.note is not None:
            lines = chunk.strip("\n").count("\n") + 1
            self.note(tuple(t.format(lines=lines) for t in EXPAND))

    def unfold_one(self, buf, chunk: str) -> bool:
        """Paste the same thing again and the reference it made turns back into the text, in place.

        This is the whole of what makes the note honest. The gesture in the reference design is "paste
        again", and here it is the only one available: `ctrl-v` is already the clipboard IMAGE (see
        `app.paste`), and printing a hint that names a key doing something else is the failure this
        project keeps finding. It works on a terminal that brackets its pastes and on one that does not,
        because both reach this through `insert_chunk` carrying the same text."""
        for i, (mark, text) in enumerate(self.pastes):
            if text == chunk and mark in buf.text:
                at = buf.text.index(mark)
                self.pastes.pop(i)
                buf.document = Document(buf.text[:at] + text + buf.text[at + len(mark):],
                                        at + len(text))
                return True
        return False

    def unfold(self, buf) -> None:
        """Every reference still in the box turned back into what it stands for, before the line goes.

        One place, and it is the last one before `validate_and_handle`. Expanding on the way out of
        `ask_async` instead would be a second door: prompt_toolkit appends to the history INSIDE
        `validate_and_handle`, so the history file that outlives the session would keep the reference
        and `↑` would recall a marker with nothing behind it. Done here, the accepted line, the history
        entry, the transcript row and the turn are all the same bytes. A reference the person edited or
        deleted is not found and its text does not travel. The store belongs to the line being written
        and is emptied with it: kept longer, typing `[Pasted text #1]` out by hand would ride a paste
        from a minute ago out on it."""
        text = buf.text
        for mark, chunk in self.pastes:
            text = text.replace(mark, chunk)
        self.pastes = []
        if text != buf.text:
            buf.document = Document(text, len(text))

    def room(self) -> int:
        """prompt_toolkit's own way out of its CPR wait, which it only ever implemented for Windows.
        Without an answer here the first render happens before the reply arrives: no bar, and none of the
        rows that pin the frame — so the box paints itself at the cursor, a screen above where it
        belongs, and drops into place a frame later. That is the flash. Answered once, because after that
        the cursor has moved and only the terminal knows where to."""
        n, self.below = self.below, 0
        if not n:
            raise NotImplementedError
        return n

    def _noted(self, app) -> None:
        """The room the frame just stood in and the window it stood in, kept for the next question about
        it. Read after the render rather than before, because that is the one moment prompt_toolkit and
        the terminal agree; and the old value survives a render that knows nothing — the erase every
        prompt ends on is one of those, and it moves the cursor back to exactly the row this measured.

        Both axes of the window are kept, because a room is only worth as much as the window it was
        measured on and `pinned_room` has to know whether this one has lost rows or columns since."""
        below = app.renderer._min_available_height
        if below > 0:
            size = app.output.get_size()
            self.seen, self.seen_rows, self.seen_cols = below, size.rows, size.columns

    def _resized(self, repaint) -> None:
        """SIGWINCH: the room answered before prompt_toolkit erases the frame and goes asking.

        Left alone it erases, asks the terminal where the cursor is, and redraws AT ONCE at the
        preferred height, because nothing has answered yet. Measured on a 30-row window: one resize drew
        four frames where one would do, three of them three rows tall against seventeen rows of room,
        and the fourth had to GROW into place — deliberate scrolling on prompt_toolkit's part, leaving
        each short frame in the scrollback. `resizes` counts the erases, because the SIZE cannot answer
        for them: POSIX coalesces signals, so a drag out and back arrives as ONE SIGWINCH at a size that
        has not changed while the erase happened anyway, and anything holding a diff against the glass
        has to be told by the EVENT."""
        self.resizes += 1
        self.caps.sync_size()
        self.below = self.pinned_room()
        repaint()

    def pinned_room(self) -> int:
        r"""Rows from the frame's top border to the window's foot as the last frame knew them, or 0 to ask.

        A window that changed HEIGHT gets 0: whether it dropped rows off the bottom or scrolled them off
        the top is the terminal's decision alone. So does one that got NARROWER — what is kept here is
        `total_rows - cursor_row + 1`, a number about the CURSOR, and a terminal that rewraps its
        scrollback on a narrower window pushes the cursor down, so the frame is drawn to a row that is
        not there: the renderer reaches the foot of the room it was handed with real `"\r\n"`, and every
        newline past the last row is a row of her header gone for good. Measured on a 40-row window, 17
        rows offered where a reflow moved the cursor down 6 writes 6 newlines below the foot. WIDER is
        KEPT — rejoined lines pull the cursor UP, so the box merely stands short of the foot."""
        if not self.seen or self.session is None:
            return 0
        try:
            size = self.session.app.output.get_size()
        except Exception:
            return 0
        if size.rows != self.seen_rows or size.columns < self.seen_cols:
            return 0
        return self.seen

    def _drop_menu(self) -> None:
        """prompt_toolkit's own list, out of the layout entirely. Any list it draws is a list it sizes
        the app for, and an app that grows never gives the rows back."""
        for c in list(self.session.app.layout.walk()):
            if isinstance(c, FloatContainer):
                for f in [f for f in c.floats
                          if isinstance(f.content, (CompletionsMenu, MultiColumnCompletionsMenu))]:
                    c.floats.remove(f)

    def geometry(self, height: int) -> tuple[int, int] | None:
        """(row of the frame's top border, row the app starts on). Both are absolute, both move as the
        input wraps, and the second is the first row that is the app's rather than the transcript's."""
        info = self.body.render_info if self.body else None
        if info is None:
            return None
        try:
            top = self.session.app.renderer.rows_above_layout
        except Exception:
            return None
        return (height - 3 - info.window_height, top)

    def owns_list(self) -> bool:
        """Whether the list on the glass is a panel's rather than the completer's. Every key a panel
        answers to hangs off this one question, so `/` and `@` take the arrows, `tab`, `enter` and `esc`
        back with them and there is never a key that moves a list nobody can see."""
        if self.picker is None or self.session is None:
            return False
        return not self.picker.cedes(self.session.default_buffer.document)

    def _bar(self):
        return self.toolbar() if self.toolbar else FormattedText([])

    def _placeholder(self) -> str:
        """The clause inside the empty box. A launch menu takes it over, because the one box is the
        filter while you are choosing and the editor for a value once you have chosen, and this is the
        only thing on the glass that says which of the two it is right now.

        A panel that does not filter leaves it alone: the box is still the place you talk to her from,
        and telling someone to narrow a list they can still type a message into would be a lie. So is
        telling someone to narrow a list with nothing in it — the first screen of a fresh install. The
        panel is ASKED whether that is what it drew; reading the sentence in the row instead gives a
        hint that matches on copy and goes quietly back to lying the day the copy is rewritten."""
        if self.picker is None or not self.picker.filters:
            return self.hint()
        if self.picker.level == "edit":
            return self.caps.t("type the new value")
        return self.caps.t("nothing to narrow yet" if self.picker.stand_in()
                           else "type to narrow the list down")

    async def ask_async(self, pre_run=None) -> str | None:
        """One line from the human. None means they left — ctrl-d on an empty line, or stdin ending.

        `pre_run` fires once the app is up and is where the out-of-turn beat starts: prompt_toolkit's
        own `refresh_interval` is read at app start and runs until the prompt ends, which is 64 B/s of
        empty diffs for the rest of the session.

        `show_frame` is passed AGAIN, and it has to be. `prompt()` defaults that argument to None and
        leaves the session's own value alone; `prompt_async()` defaults it to False and then assigns it
        unconditionally, so every await turned the frame off and the box was simply missing for the
        whole length of the prompt — drawn inside a turn by us, gone the moment the turn ended."""
        if self.session is None:
            return self._piped()
        default, self.pending, self.leaving = self.pending, "", False
        self.pastes = []
        try:
            return await self.session.prompt_async(default=default, pre_run=pre_run, show_frame=True)
        except EOFError:
            return None

    def _piped(self) -> str | None:
        """Not a tty means no line editor at all: plain readline, and zero escape bytes."""
        line = sys.stdin.readline()
        return line.rstrip("\n") if line else None

    def _unstick_esc(self) -> None:
        r"""A lone `esc` is held twice by default — the raw byte is a prefix of every escape sequence
        there is (`ttimeoutlen`, 0.5 s), and the Escape it finally emits is a prefix of our own
        `escape enter` (`timeoutlen`, 1.0 s). A second and a half reads as a key that does not work, so
        you press it again; and then `esc` pairs with whatever you type next, so `esc` then `/help` sent
        `help` without the slash. Alt+Enter survives at 50 ms: a terminal sends `\x1b\r` in one write, so
        both keys are in the buffer in the same read and match with no timer involved."""
        app = self.session.app
        app.ttimeoutlen = app.timeoutlen = ESC_FLUSH


def _guard(handler):
    """A binding that raises is not lost quietly — it is lost loudly, over her transcript.

    prompt_toolkit hands the exception to the event loop, and `Application._handle_exception` prints
    the whole Python traceback through `run_in_terminal` and then blocks on `Press ENTER to
    continue...`: the scrollback is gone, the box is frozen, and the next Enter is eaten by a prompt
    nobody asked for. Eight of the nine handlers below call OUT — into the app's line-up, its clipboard
    and what it says on the way out, and into a panel that reads settings off the engine — so none of
    them is really the two lines it looks like. The log goes to a file, never to her terminal, and a key
    that failed is a key that did nothing."""
    @functools.wraps(handler)
    def guarded(event) -> None:
        try:
            handler(event)
        except Exception:
            log.warning("the %s key binding failed", handler.__name__, exc_info=True)
    return guarded


def _burst(processor) -> str | None:
    """What the Enter just handled really was, when the read it arrived in had not finished: the rest of
    that read as paste content. None means the Enter WAS the end of it, and the line goes.

    prompt_toolkit reads up to 1024 bytes at a time and feeds the lot to one `process_keys` pass, so a
    handler running mid-pass sees what came in behind it — and a person types a line and stops, where a
    `tmux send-keys` writes the paragraph in one go with every `\\n` in it a real Enter. Only TEXT is
    taken, and TEXT IS ALSO WHAT DECIDES IT: the drain stops at the first key that is not one — an arrow,
    an `esc`, the terminal's own CPR reply. Asking whether ANYTHING was queued declared a burst over
    Enter held down and over ssh, where two presses 100 ms apart coalesce into one TCP segment so an `↑`
    dropped history onto text that had not gone. CRLF is folded, or `\\r` then `\\n` is two Enters."""
    queue = processor.input_queue
    ahead = []
    for press in queue:
        if isinstance(press.key, Keys) and press.key not in TYPED:
            break
        ahead.append(press.data)
    if not any(ch not in "\r\n" for ch in "".join(ahead)):
        return None
    out = ["\r"]
    for _ in ahead:
        out.append(queue.popleft().data)
    return "".join(out).replace("\r\n", "\n").replace("\r", "\n")


def _keys(prompt: "Prompt") -> KeyBindings:
    kb = KeyBindings()
    picking = Condition(lambda: prompt.owns_list())

    @kb.add("enter")
    @_guard
    def _send(event) -> None:
        """With a menu open it picks the row instead, and only a pick that has to print or ask leaves
        the prompt for the loop to run. Not while the panel has given way to a `/` or an `@`: the list
        on the glass is the completer's then, and this is the line you were writing.

        An Enter with the rest of its own read still queued behind it is not an Enter at all — it is
        the line break inside something typed at her by a program, and `_burst` hands back the whole
        rest of that paragraph to go in the box. It is asked FIRST, before the panel and before the
        send: a burst never picks a row and never sends, whatever is on the glass."""
        prompt.leaving = False
        pasted = _burst(event.key_processor)
        if pasted is not None:
            prompt.insert_chunk(event.current_buffer, pasted)
            return
        if prompt.owns_list():
            prompt.picked = prompt.picker.enter(event.current_buffer)
            if prompt.picked:
                event.app.exit(result="")
            return
        prompt.unfold(event.current_buffer)
        event.current_buffer.validate_and_handle()

    @kb.add(Keys.BracketedPaste)
    @_guard
    def _pasted(event) -> None:
        """A terminal that brackets its pastes hands the whole thing over in one key, and stock
        prompt_toolkit drops it straight into the buffer (`bindings/basic._paste`). Same folding of the
        line endings — iTerm2 and friends paste CRLF — and then the same door the unbracketed burst
        goes through, so one size rule covers both kinds of terminal."""
        prompt.insert_chunk(event.current_buffer,
                            event.data.replace("\r\n", "\n").replace("\r", "\n"))

    @kb.add("c-c")
    @_guard
    def _stop(event) -> None:
        """A line with something on it is cleared; an empty box arms, and the next ctrl-c leaves.

        prompt_toolkit's own ctrl-c aborts the prompt, which this app catches and answers by opening
        another one — so it cost a restart and a repaint and led nowhere, and the two presses everybody
        tries did nothing at all. `on_leaving` is what says so in the bar's own slot, because that band
        is one phrase overwritten by whatever happens next and this is exactly that kind of phrase.

        It leaves by ctrl-d's door, not by KeyboardInterrupt: `ask_async` already reads EOFError as
        somebody going, and a second way out would be a second thing to keep in step with `_bye`."""
        buf = event.current_buffer
        if buf.text:
            buf.reset()
            prompt.pastes = []
            prompt.leaving = False
            return
        if prompt.leaving:
            event.app.exit(exception=EOFError, style="class:exiting")
            return
        prompt.leaving = True
        if prompt.on_leaving is not None:
            prompt.on_leaving()

    @kb.add("escape", "enter")
    @_guard
    def _newline(event) -> None:
        event.current_buffer.insert_text("\n")

    @kb.add("up", filter=picking)
    @kb.add("c-p", filter=picking)
    @_guard
    def _previous_row(event) -> None:
        prompt.picker.move(-1)

    @kb.add("down", filter=picking)
    @kb.add("tab", filter=picking)
    @kb.add("c-n", filter=picking)
    @_guard
    def _next_row(event) -> None:
        prompt.picker.move(1)

    @kb.add("escape", filter=has_completions)
    @_guard
    def _close_menu(event) -> None:
        """`esc` backs out of a list, the way it backs out of everything else. Stock prompt_toolkit
        cancels a completion on `c-g` alone, which is a key nobody presses; and the list is ours to
        draw, so it has to be ours to dismiss.

        A panel goes one level, then out, and neither of the two has anything to kill: the launch menu
        opens before anything is running, and the roster is a window on a job that goes on running once
        it closes. So `esc` keeps the meaning it has everywhere else in a menu."""
        if prompt.owns_list():
            if prompt.picker.back(event.current_buffer):
                return
            prompt.picker = None
        event.current_buffer.complete_state = None

    @kb.add("c-r")
    @_guard
    def _no_reverse_search(event) -> None:
        """Bound so prompt_toolkit's own reverse-i-search cannot open, and then given the meaning it
        already has inside a turn: the line-up."""
        if prompt.line_up is not None:
            prompt.line_up(event.current_buffer)

    @kb.add("c-v")
    @_guard
    def _clipboard(event) -> None:
        if prompt.paste:
            prompt.paste(event.current_buffer)

    return kb


def _history():
    path = history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return FileHistory(str(path))
    except OSError:
        # A read-only home is not a reason to refuse to talk to her.
        return InMemoryHistory()


def _style(caps) -> Style:
    pal = PT_PAL[caps.color][caps.background]
    coral = pal["coral"]
    return Style.from_dict({
        "prompt": f"bold fg:{coral}" if coral else "bold",
        "placeholder": "fg:#888888 italic",
        "cont": "fg:#888888",
        "bottom-toolbar": "noreverse noinherit",
        "frame.border": f"fg:{coral}" if coral else "",
        "box.shadow": pal["shadow"],
        "bar.chip": pal["live_chip"],
        "bar.arm": ARM[caps.color],
        "bar.face": f"fg:{coral}" if coral else "",
        "bar.dim": "fg:#888888",
        "completion-menu.completion": "bg:default fg:default",
        "completion-menu.completion.current": f"bg:{coral} fg:#ffffff" if coral else "reverse",
        "completion-menu.meta.completion": "bg:default fg:#888888",
        "completion-menu.meta.completion.current": f"bg:{coral} fg:#ffffff" if coral else "reverse",
        "auto-suggestion": "fg:#666666",
    })
