"""The completion list, painted by us over the rows above the input box.

prompt_toolkit sizes a non-full-screen app to `max(min_available_height, LAST height, preferred)`, and
that last height is a high-water mark: a list taller than the room below the cursor scrolls the terminal
to open and the app keeps those rows for the rest of the prompt. So the app never grows — the list is
drawn on top of the transcript with absolute cursor moves, prompt_toolkit is not told, and closing it
writes those rows back out of `Screen.tail`. Nothing scrolls, so nothing has to come back, however many
times it opens. The one row prompt_toolkit still owns is the frame's top border, which the last match
sits on; it is redrawn every render, so a repaint by prompt_toolkit heals rather than tearing.
"""
from __future__ import annotations

import sys

from prompt_toolkit.buffer import CompletionState
from prompt_toolkit.completion import Completion

from kotoba.cli import settings_view, slash
from kotoba.cli.render import footer, rows
from kotoba.cli.render.markdown import prose
from kotoba.cli.render.portrait import INDENT
from kotoba.cli.render.rows import _clip, _count
from kotoba.cli.render.text import column, fit, wrap
from kotoba.core.text_security import scrub

#: What a row the bank cannot answer for is worth to a reader that only wants the art off it.
BLANK = ("", "", INDENT, 0)


class Menu:
    #: The whole command table plus room to grow — the list IS the discovery surface, and a cap that
    #: hides the last command spends the row it saves on saying `… 1 more`.
    TALL = 20

    def __init__(self, screen, prompt) -> None:
        self.screen, self.prompt = screen, prompt
        self.held: dict[int, tuple[str, str, int]] = {}
        self.top = 0
        self.last: dict[int, str] = {}
        self.band = None
        self.framed = False
        self._size = (screen.caps.width, screen.caps.height)
        self._resizes = prompt.resizes if prompt is not None else 0

    def sync(self) -> None:
        try:
            self._sync()
        except Exception:
            self.close()

    def _sync(self) -> None:
        """One painter, one precedence: a completion list or a panel takes the surface from `bottom` up
        and the band cedes; with no list on the glass the band paints ending at `bottom - 1`, one row
        above the frame's top border. Coming back from a list goes through `close()` FIRST and paints
        nothing this round — close hands prompt_toolkit its frame row back by forgetting `_last_screen`,
        that repaint erases the app's rows down, and only the sync after it can paint a band that stays.

        A band taller than the rows there are goes back to the BUILDER for a smaller one, never sliced
        here: a blind `[-bottom:]` keeps the LAST rows, and since the at-rest shape ends on the done
        steps that slice showed a plan's leavings and dropped its header and every open step."""
        buf = self.prompt.session.default_buffer
        if self.prompt.picker is not None and not self.prompt.picker.sync(buf):
            self.prompt.picker = None
            buf.complete_state = None
        state = buf.complete_state
        caps = self.screen.caps
        caps.sync_size()
        self._fit(caps)
        listed = state is not None and bool(state.completions)
        band = self.band() if (self.band is not None and not listed) else []
        if not listed and not band:
            self.close()
            return
        where = self.prompt.geometry(caps.height)
        if where is None:
            self.close()
            return
        bottom, self.top = where
        if bottom < 1:
            self.close()
            return
        if listed:
            drawn = footer.menu_rows(caps, state.completions, state.complete_index,
                                     min(self.TALL, bottom))
            self.framed = True
            self._paint(max(0, bottom - len(drawn) + 1), drawn[-(bottom + 1):])
            return
        if self.framed:
            self.close()
            return
        room = bottom - self._face_floor(bottom, min(len(band), bottom))
        drawn = band if len(band) <= room else (
            self.band(room)[-bottom:] if room > 0 else [])
        if not drawn:
            self.close()
            return
        self._paint(bottom - len(drawn), drawn)

    def _face_floor(self, bottom: int, tall: int) -> int:
        """The first row below every face the band's rows could reach. A sixel is pixels, not cells: a
        band row's text and its erase-to-end-of-line scrub the pixels under them, and the per-row diff
        then never writes those rows again — a permanent tenant tearing her for as long as anything
        runs. So the band folds to the rows beneath her, absent entirely when none are free; WHICH rows
        it gives up is decided by the builder from the count this returns, because the rows are not
        interchangeable and the ones a blind slice took were the plan's. How far a face reaches is the
        face's OWN height, banked with its art, and the tallest of the two tiers only for a row banked
        without one — measured as the tallest, every reply face was given the boot face's rows, two
        more than it has at a 25 px cell, and a chat turn over a long job then left the band nowhere
        to be at all."""
        box = self.screen.portrait
        if not (box and box.mode == "sixel"):
            return 0
        reach = max(box.rows, box.irows)
        floor = 0
        for r in range(max(0, bottom - tall - reach), bottom):
            _, art, _, spans = self.held.get(r) or self.screen.frozen(r, self.top) or BLANK
            if art:
                floor = max(floor, r + (spans or reach))
        return min(floor, bottom)

    def _fit(self, caps) -> None:
        """The diff's premise, checked. prompt_toolkit answers ANY resize with erase-down plus a full
        repaint, so what this painter wrote is off the glass and a diff against `last` would refuse to
        put it back — the defect that makes a permanent band vanish on a resize.

        The EVENT is asked for, never the size: POSIX coalesces signals, so a drag out and back delivers
        one SIGWINCH at a size that has not changed, a guard reading the size sees a difference of zero
        and lets the diff stand, and the rows that carry no clock stay off the glass for good. The size
        still decides ONE thing, and only when it really changed: the rows banked for restore were
        rendered at the old width, so a width change forgets the bank rather than pasting it back wrong
        — and forgotten means forgotten, since a row it cannot answer for is not a blank row."""
        size, resizes = (caps.width, caps.height), self.prompt.resizes
        if size == self._size and resizes == self._resizes:
            return
        if size[0] != self._size[0]:
            self.screen.tail.clear()
        self._size, self._resizes = size, resizes
        self.held = {}
        self.last = {}
        self.framed = False

    def _paint(self, first: int, drawn: list) -> None:
        """Only what changed: the rows another surface vacated are restored, the rows whose bytes differ
        from what was last written are rewritten, and an unchanged row costs nothing. That is what lets
        row one of the band carry a clock without the whole band paying for it — at 96 columns,
        rewriting five rows once a second because one of them ticked is 639 B/s where the changed row
        alone is 218.

        A row the bank cannot answer for is not banked at all, and so is never restored. That is the
        only honest thing left after a width change: the old bytes are the wrong width, and a blank is a
        claim the row was empty — put back over a line she had printed it deleted it, and the gap sat
        between her transcript and the box until something scrolled."""
        keep = {r: banked for r in range(first, first + len(drawn))
                if (banked := self.held.get(r) or self.screen.frozen(r, self.top)) is not None}
        gone = sorted(r for r in self.held if r not in keep)
        fresh = {first + i: self.screen.ansi(t) for i, t in enumerate(drawn)}
        changed = {r: s for r, s in fresh.items() if self.last.get(r) != s}
        if not gone and not changed:
            self.held = keep
            return
        out = ["\x1b7"]
        out += [self._row(r, self.held[r][0]) for r in gone]
        out += [self._row(r, s) for r, s in sorted(changed.items())]
        out += self._art(min(gone), max(gone) + 1) if gone else []
        out.append("\x1b8")
        self.held = keep
        self.last = fresh
        _emit("".join(out))

    def close(self) -> None:
        caps = self.screen.caps
        caps.sync_size()
        self._fit(caps)
        self.last = {}
        framed, self.framed = self.framed, False
        if not self.held:
            return
        low, high = min(self.held), max(self.held) + 1
        out = ["\x1b7"]
        out += [self._row(r, self.held[r][0]) for r in sorted(self.held)]
        out += self._art(low, high)
        out.append("\x1b8")
        self.held = {}
        _emit("".join(out))
        if not framed:
            return
        # The list's last row is the frame's top border, which is prompt_toolkit's, and it writes only
        # cells its own diff calls changed.
        app = self.prompt.session.app
        try:
            app.renderer._last_screen = None
            app.invalidate()
        except Exception:
            pass

    def _row(self, row: int, ansi: str) -> str:
        return _at(row, ansi)

    def _art(self, low: int, high: int) -> list[str]:
        return _faces(self.screen, lambda r: self.held.get(r) or self.screen.frozen(r, self.top),
                      low, high)


def _at(row: int, ansi: str) -> str:
    return f"\x1b[{row + 1};1H\x1b[0m{ansi}\x1b[0m\x1b[K"


def _faces(screen, get, low: int, high: int) -> list[str]:
    """Every face anchored on, or reaching into, the rows just restored — and always after the last
    of them, never interleaved. Each restored row ends in an erase-to-end-of-line, so a sixel put
    back one row earlier is scrubbed off by the row under it and comes back as a sliver of hair."""
    box = screen.portrait
    if not (box and box.mode == "sixel"):
        return []
    out = []
    for r in range(max(0, low - max(box.rows, box.irows)), high):
        _, art, col, _ = get(r) or BLANK
        if art:
            # The column it was DRAWN at, not a constant: the header face and the reply face do
            # not share one, and restoring both to INDENT is her jumping when a panel opens.
            out.append(f"\x1b[{r + 1};{col + 1}H" + art)
    return out


def _emit(payload: str) -> None:
    sys.__stdout__.write(payload)
    sys.__stdout__.flush()


class Overlay:
    """The HEAD of the open card — the rows its seat could not take — painted like the completion list:
    over the transcript with absolute cursor moves, banked out of `Screen.tail`, put back on close. Every
    card comes here, one cover-and-restore being the only way two card surfaces cannot disagree. The
    card's place is the BOTTOM: the region seats its last rows in its own pad and only the overflow
    arrives here, growing upward, so on a roomy screen this paints nothing. A card is a safety gate,
    shown whole or not at all, so `open` is asked its TALLEST height (`?` raises it) and refuses when the
    rows above the region cannot hold it or the bank cannot answer for a covered row — unbanked is never
    restored, which is stale card pixels left in the transcript; the caller falls back to the flow, as it
    does when a resize sends `wipe` in. The region can still move under the card — a band row appearing
    grows `reserve` by one — so `sync` re-reads the top and shifts the bank and the paint with it."""

    def __init__(self, screen, build) -> None:
        self.screen, self.build = screen, build
        self.top = -1
        self.bank: dict[int, tuple[str, str, int, int]] = {}
        self.last: dict[int, str] = {}

    def open(self, whole: int) -> bool:
        top = self.screen.caps.height - self.screen.reserve
        if whole < 0 or whole > top or (
                whole and self.screen.frozen(top - whole, top) is None):
            return False
        self.top = top
        self.sync()
        return True

    def sync(self) -> None:
        if self.top < 0:
            return
        top = self.screen.caps.height - self.screen.reserve
        if top != self.top:
            delta = top - self.top
            self.bank = {r + delta: v for r, v in self.bank.items() if r + delta >= 0}
            self.last = {r + delta: v for r, v in self.last.items() if r + delta >= 0}
            self.top = top
        drawn = self.build()[-top:]
        base = top - len(drawn)
        for r in range(base, top):
            if r not in self.bank:
                self.bank[r] = self.screen.frozen(r, top) or BLANK
        fresh = {base + i: self.screen.ansi(t) for i, t in enumerate(drawn)}
        gone = sorted(r for r in self.last if r not in fresh)
        changed = {r: s for r, s in fresh.items() if self.last.get(r) != s}
        self.last = fresh
        if not gone and not changed:
            return
        out = ["\x1b7"]
        out += [_at(r, self.bank.get(r, BLANK)[0]) for r in gone]
        out += [_at(r, s) for r, s in sorted(changed.items())]
        if gone:
            out += _faces(self.screen, self._under, min(gone), max(gone) + 1)
        out.append("\x1b8")
        _emit("".join(out))

    def close(self) -> None:
        shown, self.last = self.last, {}
        bank, self.bank = self.bank, {}
        top, self.top = self.top, -1
        if not shown:
            return
        out = ["\x1b7"]
        out += [_at(r, (bank.get(r) or BLANK)[0]) for r in sorted(shown)]
        out += _faces(self.screen, lambda r: bank.get(r) or self.screen.frozen(r, top),
                      min(shown), max(shown) + 1)
        out.append("\x1b8")
        _emit("".join(out))

    def wipe(self) -> None:
        shown, self.last, self.bank, self.top = self.last, {}, {}, -1
        if shown:
            _emit("\x1b7" + "".join(_at(r, "") for r in sorted(shown)) + "\x1b8")

    def forget(self) -> None:
        """Let go without writing: the exception path's belt, where the glass is not ours to fix."""
        self.bank, self.last, self.top = {}, {}, -1

    def _under(self, r: int):
        return self.bank.get(r) or self.screen.frozen(r, self.top)


class Panel:
    """A list you walk with the arrow keys, on the surface above — the completion list with different
    content in it. The rows are a `CompletionState` we hand the buffer ourselves, and every row's
    completion text is empty, so moving the selection never touches what you have typed: that is what
    lets the one box be a filter while you are choosing and the editor for a value once you have chosen.
    The selected index is ours, not `Buffer.complete_next`'s, which deselects as it wraps — and a menu
    with nothing selected is not a menu. `filters` says whether the box narrows the list: false for a
    panel opened over a message you are halfway through typing, where clearing the box on `esc` would
    throw it away. `gives_way` keeps ONE list on the glass — a panel stands aside when the word under
    the cursor starts with `/` or `@`, so the most recent intention wins. `sync` returning false closes
    the panel. A panel is never `sun`: `sun` means pending on YOU, and a browsed list pends on nobody."""

    WIDE: dict[str, int] = {}
    filters = True
    gives_way = False

    def __init__(self, app, kind: str) -> None:
        self.app = app
        self.stack = [(kind, "")]
        self.marks: dict[int, int] = {}
        self.index = 0
        self.ids: list[str] = []

    @property
    def level(self) -> str:
        return self.stack[-1][0]

    @property
    def arg(self) -> str:
        return self.stack[-1][1]

    def wide(self, caps) -> int:
        """Cells the first column takes, so the dim one after it starts on the same cell in every row."""
        return self.WIDE[self.level]

    def trail(self) -> str:
        head = self.stack[0][0]
        rest = [a for _, a in self.stack[1:] if a]
        return " · ".join([head] + rest)

    def twins(self) -> tuple:
        raise NotImplementedError

    def rows(self, text: str) -> list[tuple]:
        """One shape per row: (id, what it says, what the dim column says)."""
        raise NotImplementedError

    def cedes(self, document) -> bool:
        """Whether the completer owns the list right now instead of us."""
        return self.gives_way and document.get_word_before_cursor(WORD=True)[:1] in ("/", "@")

    def sync(self, buf) -> bool:
        """Rebuilt every render and identical to the last one when nothing moved, which is what `Menu`
        needs to write nothing at rest. False when there is nothing left to draw.

        The scrub happens HERE, before `column` measures, and not only in `footer.menu_rows` where the
        row finally reaches a terminal. A control character is zero cells and the space it becomes is
        one, so a row fitted to `wide` and scrubbed afterwards is a row wider than the column it was
        just fitted to — measured, a helper's summary grew ten cells past its budget and the outcome
        word on its right was ellipsised away. Every panel's rows come through this one call, so a
        subclass cannot forget it."""
        if self.cedes(buf.document):
            return True
        drawn = self.rows(buf.text if self.filters else "")
        if not drawn:
            return False
        self.ids = [r[0] for r in drawn]
        self.index = max(0, min(self.index, len(drawn) - 1))
        caps = self.app.caps
        wide = self.wide(caps)
        buf.complete_state = CompletionState(buf.document, [
            Completion("", 0, display_meta=scrub(caps.t(m)),
                       display=column(scrub(caps.t(d)), wide, caps.unicode))
            for _, d, m in drawn], self.index)
        return True

    def move(self, delta: int) -> None:
        self.index = (self.index + delta) % max(1, len(self.ids))

    def push(self, level: str, arg: str, buf) -> None:
        """One level in, remembering the row you came from — `esc` puts you back on it rather than at the
        top of a list you have already read."""
        self.marks[len(self.stack)] = self.index
        self.stack.append((level, arg))
        self.index = 0
        if self.filters:
            buf.text = ""

    def back(self, buf) -> bool:
        if self.filters:
            buf.text = ""
        if len(self.stack) == 1:
            return False
        self.stack.pop()
        self.index = self.marks.pop(len(self.stack), 0)
        return True

    def stand_in(self) -> bool:
        """Whether what is on the glass is the ONE row a list shows instead of a list. Nothing there is
        pickable — `picked` is empty and `enter` returns () — so the bar and the placeholder inside the
        box both have to know it before either promises a key.

        Asked at render time, never banked. Both readers are drawn DURING a render and `sync` runs
        after one, so a fact left behind there answers for the frame before this: the bar would go on
        promising `enter` for a keystroke past the truth, and out at the prompt nothing repaints on its
        own to correct it. False here, for a panel that never draws such a row."""
        return False

    def picked(self) -> str:
        return self.ids[self.index] if self.index < len(self.ids) else ""

    def enter(self, buf) -> tuple:
        """What the loop has to do out of the prompt, or () when the panel did it itself. Only a pick
        that prints or asks costs a round trip."""
        return ()


class Picker(Panel):
    """`--sessions` and `--settings`: the launch menus.

    Launch modes only, and that is the whole reason this one is cheap: nothing has been said yet, so
    there is no transcript underneath to protect and no turn to interrupt. A pick that has to print or
    ask leaves the prompt the way a slash command does; a pick that only goes a level deeper never does.

    It never gives way. The box here is the filter while you are choosing and the editor for a value once
    you have chosen, so a `/` or a `~/` typed into it is the value and not a command.
    """

    WIDE = {"sessions": 19, "settings": 12, "keys": 42, "values": 14, "edit": 42}
    BARE = {
        "sessions": ("no conversations", "this is your first one — esc to leave and talk to her"),
        "MCP": ("no servers yet", "nothing of yours is connected to her"),
        "MEMORY": ("nothing kept yet", "she starts keeping things as you talk"),
        "REMINDERS": ("no reminders", "ask her to remind you and it'll be here"),
        "KEYS": ("no keys saved", "give her one and only its name shows"),
        "PLUGINS": ("no plugins", "nothing installed — one you add lands here"),
    }

    def __init__(self, app, kind: str) -> None:
        super().__init__(app, kind)
        self.sessions: list[dict] = []
        self.data: dict = {}

    async def load(self) -> None:
        """The two reads a row cannot do while the screen is being painted: the conversations before
        this one, and the sections `/set` cannot touch. Both come off the same call `/sessions` and
        `/settings` use, so the menu and the listing cannot disagree.

        The settable values are NOT cached — see `values`."""
        engine = self.app.session.engine
        if self.stack[0][0] == "sessions":
            listed = await engine.db.list_sessions(slash.SESSIONS_SHOWN)
            self.sessions = [r for r in listed if r["id"] != self.app.session.session_id]
        else:
            from kotoba.core.settings import build_settings

            self.data = await build_settings(engine.db, engine.mcp)

    @property
    def values(self) -> dict:
        """Read fresh on every frame, never cached: a row showing a value the engine does not hold is
        worse than no row, and a pick two rows up changes one between renders."""
        from kotoba.core import app_settings

        return app_settings.runtime_all()

    def twins(self) -> tuple:
        """What the keys do, and only what they do. On the stand-in row there is one row, it cannot be
        opened, and `enter` there returns () — so the arrows and `enter` come out of the line rather
        than promising a press that does nothing, and `esc`, which works, is what is left.

        Giving `enter` a meaning of its own on that row was the other way out, and it is worse: one key
        would mean two things depending on which row was under it, and both meanings it could carry —
        leave the menu, widen the filter — are `esc` already."""
        back = "leaves it" if len(self.stack) == 1 else "goes back"
        if self.stand_in():
            return (f"esc {back}", f"esc {back}", "esc")
        verb = {"sessions": "reads it back", "settings": "opens it", "keys": "changes it",
                "values": "sets it", "edit": "saves it"}[self.level]
        lead = "type it · " if self.level == "edit" else "arrows move · "
        return (f"{lead}enter {verb} · esc {back}", f"enter {verb} · esc {back}", "enter · esc")

    def stand_in(self) -> bool:
        """The two stand-in rows this menu draws — an empty source and a filter that matched nothing —
        told apart from a list by the one thing they have in common: no row here carries an id, which
        is exactly what makes `picked` empty and `enter` a no-op. Never by the sentence in the row,
        which is copy somebody will rewrite.

        `edit` is not one of them: its single row is the value you are editing and `enter` saves it.

        False when it cannot tell. The bar and the placeholder are read inside prompt_toolkit's render,
        where a raise takes the whole prompt down with it (`prompt._guard`); `Menu.sync` answers the
        same throw by closing the list a frame later, so the window this covers is one frame wide."""
        if self.level == "edit":
            return False
        editor = self.app.prompt.session
        text = editor.default_buffer.text if editor is not None else ""
        try:
            return not any(r[0] for r in self.rows(text))
        except Exception:
            return False

    def rows(self, text: str) -> list[tuple]:
        """One shape per row: (id, what it says, what the dim column says).

        Two different facts, two different sentences. An EMPTY SOURCE is answered before the filter
        runs: a fresh install has no conversations and no servers, and the miss line accused a person
        of a filter they had not typed — `nothing here says ''`, with nothing to backspace. A filter
        that matched nothing keeps that line, because there the accusation is true."""
        out = getattr(self, "_rows_" + self.level)()
        if self.level == "edit":
            return out
        if not out:
            return [self._bare()]
        needle = text.strip().lower()
        kept = [r for r in out if needle in (r[1] + " " + r[2]).lower()]
        return kept or [("", "no match", f"nothing here says {needle!r} — backspace to widen it, "
                         "esc to leave it")]

    def _bare(self) -> tuple:
        """The row a list with nothing in it shows, in the words of what it was LOOKING for — the
        source's sentence, never the picker's guess: `no servers yet` and `no reminders` are different
        facts and the one line that served both said neither. Keyed by the level, except `keys`, where
        the subject is the section you opened; the two cannot collide, levels being lower case and
        sections upper. Only `sessions` and `keys` can arrive here — the other three lists are built
        from constant tables — so an unlisted subject gets the bare fact and a sentence to add here."""
        said, meta = self.BARE.get(self.arg if self.level == "keys" else self.level,
                                   ("nothing here yet", ""))
        return ("", said, meta)

    def _rows_sessions(self) -> list[tuple]:
        """The past ones and only the past ones. `/sessions` marks the live conversation `this one`; a
        launch menu opens before it exists — `Session.ask` calls `ensure_session` on the first turn, so
        there is genuinely no row for it yet — and the menu closes for good once you pick."""
        return [(r["id"], settings_view.when(r["started_at"]),
                 f'"{r["opened"] or ""}" · {_count(r["turns"], "turn")}')
                for r in self.sessions]

    def _rows_settings(self) -> list[tuple]:
        out = []
        for name, keys in settings_view.SECTIONS:
            frozen = len(settings_view.section_info(name, self.data))
            bits = ([f"{_count(len(keys), 'setting')} to change"] if keys else []
                    ) + ([f"{frozen} read-only"] if frozen else [])
            out.append((name, name, " · ".join(bits)))
        return out

    def _rows_keys(self) -> list[tuple]:
        values, out = self.values, []
        for key in dict(settings_view.SECTIONS)[self.arg]:
            value, note = settings_view.shows(key, values)
            out.append((key, f"{key:<20}{value or f'({note})'}", settings_view.accepts(key)))
        for left, right, tail in settings_view.section_info(self.arg, self.data):
            out.append(("!" + left, f"{left:<20}{right or tail}", "read-only"))
        return out

    def _rows_values(self) -> list[tuple]:
        key, values = self.arg, self.values
        vals = (["on", "off"] if key in settings_view.BOOLS
                else [v for v in settings_view.ENUMS[key] if v])
        now = settings_view.shows(key, values)[0]
        out = []
        for v in vals:
            said = [s for s in (settings_view.value_note(key, v, values),
                                "what it is now" if v == now else "") if s]
            out.append((v, v, " · ".join(said)))
        return out

    def _rows_edit(self) -> list[tuple]:
        key = self.arg
        value, note = settings_view.shows(key, self.values)
        return [("", f"{key:<20}{value or f'({note})'}", settings_view.accepts(key))]

    def enter(self, buf) -> tuple:
        """What the loop has to do out of the prompt, or () when the menu did it itself. Only a pick
        that prints or asks costs a round trip."""
        self.sync(buf)
        if self.level == "edit":
            return ("set", self.arg, buf.text.strip())
        pick = self.picked()
        if not pick:
            return ()
        if self.level == "sessions":
            return ("replay", pick)
        if self.level == "settings":
            self.push("keys", pick, buf)
        elif self.level == "values":
            return ("set", self.arg, pick)
        elif pick.startswith("!"):
            return ("frozen", pick[1:])
        elif pick in settings_view.ENUMS or pick in settings_view.BOOLS:
            self.push("values", pick, buf)
        else:
            self.push("edit", pick, buf)
            # `shows`, not the raw value: `work_timeout` is a float in the table and `3600.0` in the
            # box under a row that says `3600` is the two disagreeing about one number.
            buf.text = settings_view.shows(pick, self.values)[0]
            buf.cursor_position = len(buf.text)
        return ()


def _said(row) -> str:
    """A row's own characters, without the run of spaces its right edge pads out with. That padding is
    where the clock used to sit; the dim column after it belongs to `footer.menu_rows`, and a row that
    reached into it would push every state word off the same cell."""
    return row.plain.rstrip()


class Roster(Panel):
    """`^r` at the prompt: the long job, every tool it has run, and every helper it sent out.

    This is the surface those rows have never had. Inside a turn the live region draws all of it; out at
    the prompt — exactly when the long job runs — they accumulate silently and never reach the screen,
    because the landing prints a RECEIPT and not the roster, and only a helper that FAILED keeps a row in
    it. `^r` is the same key that folds the line-up inside a turn. No row here carries a clock, and that
    is what makes it affordable: an unchanged payload is one `Menu` does not write, so a long job at
    169 B/s cost 198 with this open over it, the twenty-nine bytes being the bar's own — both measured
    before the band's mark began animating, which costs a running job ~1.5 kB/s by itself, and a list
    open over the band cedes it anyway. It closes itself: the job lands and `sync` finds nothing to draw."""

    filters = False
    gives_way = True

    def __init__(self, app) -> None:
        super().__init__(app, "running")

    def wide(self, caps) -> int:
        """The box's own two borders, the leading space and the widest state word, off the terminal."""
        return max(24, caps.width - 16)

    @property
    def job(self):
        """The long job while it is still hers to show. Once it has landed the transcript has it."""
        job = self.app.work
        return job if job is not None and not job.landed else None

    def twins(self) -> tuple:
        if self.level == "running":
            return ("arrows move · enter opens it · esc leaves",
                    "enter opens it · esc leaves", "enter · esc")
        return ("arrows move · esc goes back", "esc goes back", "esc")

    def rows(self, _text: str) -> list[tuple]:
        job = self.job
        if job is None:
            return []
        return getattr(self, "_rows_" + self.level)(job)

    def enter(self, buf) -> tuple:
        """One level in, never out of the prompt: every leaf is drawn from what the client already holds,
        so opening one costs a render and not a round trip."""
        self.sync(buf)
        pick = self.picked()
        if not pick or self.level != "running":
            return ()
        level, _, arg = pick.partition(":")
        self.push(level, arg, buf)
        return ()

    def _rows_running(self, job) -> list[tuple]:
        caps, w = self.app.caps, self.wide(self.app.caps)
        out = [("job", _said(rows.work_row(caps, job, w, opening=True, clock=False)),
                rows.state_word(job.state))]
        for i, tool in enumerate(job.tools, 1):
            out.append((f"tool:{i}", _said(rows.tool_text(caps, tool, w, still=True, clock=False)),
                        rows.state_word(tool.state)))
        for i, helper in enumerate(job.helpers, 1):
            # The plate folds under ROSTER_MIN_W, and both of its rows answer to the same helper:
            # a continuation row with no id is a stop the arrows make that enter cannot take.
            out += [(f"helper:{i}", _said(row), rows.state_word(helper.state) if k == 0 else "")
                    for k, row in enumerate(
                        rows.helper_rows(caps, helper, i, w, still=True, clock=False))]
        return out

    def _rows_job(self, job) -> list[tuple]:
        """`/work` on this surface: the same opening row, the same tool rows, the same line-up, off the
        same three builders in the same order. It is the level above with its ids taken off rather than a
        second listing of the same job — two of those is how a panel and a receipt start disagreeing, and
        the ids are the only difference, because a leaf has nowhere further to go."""
        return [("", said, meta) for _, said, meta in self._rows_running(job)]

    def _rows_helper(self, job) -> list[tuple]:
        """Her steps, all of them — the peek inside a turn shows the last three and `/helpers` is the
        only other place the rest have ever been readable."""
        caps, w = self.app.caps, self.wide(self.app.caps)
        n, helper = self._nth(job.helpers)
        if helper is None:
            return []
        out = [("", _said(row), rows.state_word(helper.state) if k == 0 else "")
               for k, row in enumerate(
                   rows.helper_rows(caps, helper, n, w, still=True, clock=False))]
        out += [("", rows.step_line(caps, step, w), "") for step in helper.steps]
        if not helper.steps:
            out.append(("", "     " + caps.t("nothing yet"), ""))
        out += [("", "     " + line, "") for line in
                (wrap(scrub(caps.t(helper.summary)), w - 5) if helper.summary else [])]
        return out

    def _rows_tool(self, job) -> list[tuple]:
        """A command and everything it left. `LocalSandbox.run` waits on the PROCESS and pumps its pipes
        alongside it, so the whole output comes back in one piece; the leaf used to show the four lines
        the agentic loop had already cut it to, because that trim was the only copy on the wire. The
        frame carries both now, so this is the log it always read as — walked with the arrow keys like
        any other panel."""
        caps, w = self.app.caps, self.wide(self.app.caps)
        _, tool = self._nth(job.tools)
        if tool is None:
            return []
        out = [("", _said(rows.tool_text(caps, tool, w, still=True, clock=False)),
                rows.state_word(tool.state))]
        out += [("", "     " + _clip(caps, line, w - 5), "")
                for line in (tool.full or "").splitlines() if line.strip()]
        said = fit(w - 5, caps.t("what she's thinking isn't kept — this is what she's done, not how "
                                 "she got there"),
                   caps.t("what she's thinking isn't kept — only what she did"),
                   caps.t("what she's thinking isn't kept"), "")
        return out + ([("", "     " + said, "")] if said else [])

    def _nth(self, kept: list) -> tuple:
        n = int(self.arg) if self.arg.isdigit() else 0
        return (n, kept[n - 1]) if 1 <= n <= len(kept) else (n, None)


async def launch(app, kind: str) -> None:
    """`--sessions` / `--settings` instead of her greeting: you came to look at something, and being
    said hello to first is two seconds of streaming in front of the thing you asked for."""
    from kotoba.cli.input import commands

    if not app.caps.interactive:
        app.screen.separate()
        app.screen.chrome("no terminal to move around in — here's the listing instead")
        await slash.run(app, commands.Command("/" + kind))
        return
    app.prompt.picker = Picker(app, kind)
    await app.prompt.picker.load()
    await _do_pick(app, app.prompt.picker, ("open", kind))


async def picked(app) -> None:
    """A pick that prints or asks goes out by the road a slash command already uses — at column 0, with
    the frame off the glass, so the pinned box does not have to survive a print."""
    action, app.prompt.picked = app.prompt.picked, ()
    picker = app.prompt.picker
    if action[0] == "replay":
        app.prompt.picker = None
    await _do_pick(app, picker, action)


async def _do_pick(app, picker: Picker, action: tuple) -> None:
    kind = action[0]
    if kind == "open":
        _intro(app, action[1])
    elif kind == "replay":
        await _replay(app, picker, action[1])
    elif kind == "frozen":
        _frozen(app, picker, action[1])
    elif kind == "set":
        await slash._set(app, f"{action[1]} {action[2]}".strip())
        picker.stack.pop()
        picker.index = 0


def _intro(app, kind: str) -> None:
    """What the menu is, printed once under the header. It says the thing the bar cannot: what `enter`
    will do, and — for the conversations — what it will NOT do."""
    app.screen.separate()
    if kind == "sessions":
        slash._tag(app, "SESSIONS")
        app.screen.chrome("every conversation before this one — enter reads one back, it doesn't "
                          "reopen it")
        app.screen.chrome("say what you're after afterwards and she'll go and find it across all of "
                          "them")
    else:
        slash._tag(app, "SETTINGS")
        app.screen.chrome("everything she runs on — enter opens a section, esc comes back out of one")
        app.screen.chrome("sandbox and provider say what they'd change before they change it; the "
                          "other fifteen just change")
    app.screen.blank()


async def _replay(app, picker: Picker, sid: str) -> None:
    """A past conversation, read back into the scrollback. Two columns and no more, because `role` and
    `content` are the only two of the five in `turns` that any caller has ever written: `emotion` and
    `tools_used` take a default of None at every insert, so her mood and her tools are as absent from a
    past conversation as `ended_at` is.

    No portrait either, and that one is a rule rather than a gap: nothing new draws sixel, and a
    nameplate per reply would be a promise of one. Her lines are hers by where they sit — the gutter —
    and yours are yours by the `›` at the margin, which is the distinction the transcript already
    makes."""
    screen, bullet = app.screen, app.caps.g["bullet"]
    row = next(r for r in picker.sessions if r["id"] == sid)
    screen.separate()
    slash._tag(app, "SESSION")
    screen.chrome(f"{settings_view.when(row['started_at'])} {bullet} "
                  f"{_count(row['turns'], 'turn')} {bullet} {sid}")
    screen.blank()
    for turn in await app.session.engine.db.fetch_recent_turns(sid, row["turns"]):
        if turn["role"] == "user":
            screen.commit_user(slash.shown(turn["content"]))
        else:
            screen.out(prose(turn["content"], app.caps), at_gutter=True)
        screen.blank()
    screen.chrome("that's the whole of it — read back out of the table, not carried on from")
    screen.chrome("ask her about any of it and she'll search every conversation, not only that one")
    screen.blank()


def _frozen(app, picker: Picker, label: str) -> None:
    """`check` says why for the ones `/set` knows the name of; the rest are a skill, a server, a key —
    somebody else's row, and there is one sentence for all of them."""
    named = label in settings_view.ENV_ONLY or label in settings_view.HERS
    app.screen.separate()
    app.screen.chrome(settings_view.check(label, "", picker.values)[1] if named else
                      f"{label} isn't something /set reaches — it's what she's got, and it changes "
                      "where it was made")
    app.screen.blank()
