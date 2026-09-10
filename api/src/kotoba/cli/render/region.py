"""The live region, let go of in place, and the erase that spares her portrait.

Rich ends a transient region with a newline and only then erases it — a row of scroll every turn, and
the frame off the glass until prompt_toolkit repaints. So `Region.stop` erases only the rows above the
frame, leaves it and the bar standing, and puts the cursor on the region's first row; nothing may print
in between, which is why the turn's last blank and its `· 1 tool` footer go INSIDE it. `Gutter` is the
other half: rich clears a region one whole line at a time and no terminal keeps a sixel through an
erase, so her portrait's rows are cleared from the gutter rightwards. When it cannot be spared — no
span, or a changed height — it says so, or the committed block trusts a face the next byte rubs out.
"""
from __future__ import annotations

from dataclasses import replace

from rich.console import Group
from rich.control import Control
from rich.live import Live
from rich.segment import Segment
from rich.text import Text

from kotoba.cli.render import footer
from kotoba.cli.render.text import Raw


def walk(line, cols: int):
    """(line, spared). The indent as a cursor move instead of spaces: a space is a character and a
    character rubs out the sixel under it. `spared` says the first `cols` columns are left alone — the
    erase has to agree, or a row that does write there is half-cleared and leaves a ghost."""
    out, need = [], cols
    for seg in line:
        if need and not seg.control:
            if seg.text.strip():
                return line, False
            width = seg.cell_length
            if width <= need:
                need -= width
                continue
            out.append(Segment(seg.text[need:], seg.style))
            need = 0
            continue
        out.append(seg)
    if need:
        return out, True
    return [Segment(f"\x1b[{cols}C", None, True), *out], True


def erase(height: int, spared: frozenset, col: int) -> str:
    out = ["\r"]
    for i in range(height - 1, -1, -1):
        out.append(f"\x1b[{col}C\x1b[K\r" if i in spared else "\x1b[2K")
        if i:
            out.append("\x1b[A")
    return "".join(out)


class Gutter:
    """Pushed after Live's own hook so it sees the control Live prepends, and popped before Live stops,
    because `pop_render_hook` only pops the last."""

    def __init__(self, screen, live: Live) -> None:
        self.screen, self.live = screen, live

    def __enter__(self) -> "Gutter":
        self.screen.console.push_render_hook(self)
        return self

    def __exit__(self, *exc) -> None:
        self.screen.console.pop_render_hook()

    def process_renderables(self, renderables):
        span = self.screen.erase_span
        if not (renderables and isinstance(renderables[0], Control)):
            return renderables
        height = self.live._live_render.last_render_height
        if span and height == span[0]:
            renderables[0] = Raw(erase(*span))
        elif height:
            self.screen.face_art = None
        return renderables


class LiveView:
    """What the turn is doing, then a pad, then the frame — its last row the window's last.

    Fixed height, because rich re-anchors a region at its first row: a shrinking one walks the box up the
    screen. The pad goes BELOW her, or paragraphs jump as they stop moving; the flow's last row is the
    blank each committed block ends on, held from her first frame so the commit never buys it with a
    scroll. `lead` comes off the SCREEN's own `rows_committed`, never the snapshot's copy, or her block
    walks off its painted face by that row. Rich crops a too-tall region at its LAST rows — the frame and
    the bar: a 34-line fence took the input box off a 24-row terminal for 1.2 s. So the crop is made here
    and from the TOP, `reserve` is held to the window, and her portrait goes with it.
    """

    def __init__(self, screen, spin, get_state, parts) -> None:
        self.screen, self.spin, self.get_state, self.parts = screen, spin, get_state, parts

    def _lines(self, console, rows, options):
        return console.render_lines(Group(*rows), options, pad=False) if rows else []

    def _landed(self, flow_out, state):
        """The machine rows `Screen.row` is holding back behind her un-committed block, drawn on the
        rows they will land on: under her block — after its links and the blank her gap will spend —
        and above what is still running, which lands later than they will. With no block of hers up
        they ride at the top, where they would have printed. Returns (rows, her_at, tail_stop) with
        the marks moved past the insertion."""
        rows, at = flow_out.rows, flow_out.her_at
        stop = flow_out.tail_at if flow_out.tail_at >= 0 else len(rows)
        held = self.screen.pending
        if not held:
            return rows, at, stop
        if at < 0:
            first = 1 if state.owes_gap else 0
            rows[first:first] = list(held)
            return rows, at, stop + len(held)
        edge = len(rows) - len(state.queued)
        gap = next((i for i in range(stop, edge)
                    if isinstance(rows[i], Text) and not rows[i].plain), -1)
        if gap < 0:
            rows[edge:edge] = [Text(""), *held]
        else:
            rows[gap + 1:gap + 1] = list(held)
        return rows, at, stop

    def __rich_console__(self, console, options):
        screen = self.screen
        state = self.get_state()
        if state.rows_committed != screen.rows_committed:
            state = replace(state, rows_committed=screen.rows_committed)
        self.spin.tick(screen.face.emotion, hz=footer.frame_hz(screen.caps, state))
        flow_out = footer.flow_view(screen.caps, state, self.parts)
        rows, at, stop = self._landed(flow_out, state)
        head = self._lines(console, rows[:at], options) if at > 0 else []
        flow = head + self._lines(console, rows[at:stop] if at >= 0 else rows[:stop], options)
        tail = self._lines(console, rows[stop:], options)
        frame = screen.face_frame(len(head) if at >= 0 else -1)
        if frame:
            # Her block claims the art's height from the first frame, as the committed one will: a
            # one-line opener would put her chin on the box.
            while len(flow) < frame[0] + frame[1]:
                flow.append([])
        flow += tail
        if flow_out.owes_tail:
            flow.append([])
        pinned = self._lines(console, footer.pinned_view(screen.caps, screen.face, state,
                                                         spin=self.spin), options)
        screen.content_h = len(flow) + len(pinned)
        pad = max(0, screen.reserve - screen.content_h)
        room = max(1, options.size.height)
        screen.reserve = min(max(screen.reserve, screen.content_h), room)
        # The overlaid card is SEATED: its last rows take the bottom of the pad, against the pinned
        # block, and `content_h` never counts them — a seat that grew the region would scroll.
        seat = self._lines(console, self.parts.seated_rows(), options)[-pad:] if pad else []
        self.parts.seated_k = len(seat)
        lines = flow + [[] for _ in range(pad - len(seat))] + seat + pinned
        # Rich crops a region taller than the window at its LAST rows, which are the frame and the bar.
        drop = max(0, len(lines) - room)
        lines = lines[drop:]
        first = frame[0] - drop if frame else -1
        if frame and first >= 0:
            tall, payload = frame[1], frame[2]
            spared = []
            for i in range(first, first + tall):
                lines[i], ok = walk(lines[i], screen.gutter)
                if ok:
                    spared.append(i)
            screen.erase_span = (len(lines), frozenset(spared), screen.gutter)
        else:
            payload = None
            screen.erase_span = None
            if frame:
                screen.face_art = None
        for i, line in enumerate(lines):
            if i:
                yield Segment("\n")
            yield from line
        if payload:
            up = len(lines) - 1 - first
            yield Segment("\x1b7" + (f"\x1b[{up}A" if up > 0 else "") + "\r"
                          + f"\x1b[{screen.portrait.inline_indent}C" + payload + "\x1b8", None, True)


class Region(Live):
    KEEP = 4
    released = 0

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
            h = self.released = self._live_render.last_render_height
            self.console.clear_live()
            self._disable_redirect_io()
            self.console.pop_render_hook()
            self.console.show_cursor(True)
            if h > self.KEEP:
                out = f"\x1b[{self.KEEP}A" + erase(h - self.KEEP, frozenset(), 0)
            elif h > 1:
                out = f"\r\x1b[{h - 1}A"
            else:
                out = "\r" if h else ""
            if out:
                self.console.file.write(out)
                self.console.file.flush()
