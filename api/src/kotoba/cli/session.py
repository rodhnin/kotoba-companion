"""One CLI session: the engine, an event queue, and turns driven in process.

The queue is not optional: `events._put` is a silent no-op when nothing is registered, and the loop
refuses to open a card nobody can answer, so a CLI that skips `events.register` has every approval
denied instantly with the model told it asked. Registered but undrained, the card is a turn asleep.

`channel="text"` is the approval CLOCK — the 180 s a person needs to read a gated command — not the
register; `register` is what she WRITES, so a typed web turn is channel="text", register="voice" and
is spoken aloud. `WORK_DONE` runs with `start_work` and `delegate` withheld, and is never persisted.
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
import uuid
from collections.abc import Callable

from kotoba.cli.approvals import Approvals, Ask
from kotoba.cli.events_bridge import EventBridge
from kotoba.core import engine as core_engine
from kotoba.core import events, interaction, pending_reminder, session_sandbox, turns, work_state
from kotoba.core.context import is_trigger_sentinel, load_context
from kotoba.core.loop import _OFFLINE_MSG, agentic_loop
from kotoba.core.memory import extract_and_save_memory
from kotoba.core import stream as _stream
from kotoba.core.stream import (
    TAG_TO_FACE,
    AudioTagFilter,
    CitationFilter,
    HeadTagFilter,
    ToolCallLeakFilter,
    collapse_repeats,
)
from kotoba.models.schemas import ChatRequest


log = logging.getLogger("kotoba.cli")

WORK_DONE = work_state.WORK_DONE

_DRAIN_LIMIT = 10.0     # seconds close() waits for the turn's memory extraction before giving up on it

_GAP = re.compile(r"(\S)[ \t]{2,}")

def _spoken_failure(e: Exception) -> str:
    """What she says when the turn itself died. A rejected key gets its own sentence: the generic
    apology on every turn, with the 401 visible only in the log, left a stranger no way to tell a
    wrong key from a broken install."""
    from openai import AuthenticationError

    if isinstance(e, AuthenticationError):
        return ("My API key was rejected by the provider, so I can't think right now — "
                "run `kotoba setup` to save a working one.")
    return _stream.crash_apology()


def explain_startup_failure(e: BaseException) -> int:
    """The sentence every entry point prints when the engine cannot even come up — a missing soul
    file, an unopenable database. The full traceback is already in the log; the terminal gets what
    happened and the one command that says how to fix it. Returns the exit code to hand back."""
    log.error("she could not start", exc_info=e)
    print(f"She could not start: {e}", file=sys.stderr)
    print("`kotoba doctor` checks everything she needs and says what is missing.", file=sys.stderr)
    return 1


def _tidy(text: str) -> str:
    """Close the hole a stripped tag leaves mid-sentence (`I did it  and it worked`) without touching
    INDENTATION — collapsing leading whitespace turned a four-space Python body into one space."""
    out, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            out.append(line.rstrip())
            continue
        out.append(line.rstrip() if fenced else _GAP.sub(r"\1 ", line).rstrip())
    return "\n".join(out).strip()


class Session:
    def __init__(self, engine: core_engine.Engine, ask: Ask | None = None,
                 on_event: Callable[[str, dict], None] | None = None) -> None:
        self.engine = engine
        self.session_id = uuid.uuid4().hex     # never a browser's: register() would steal its queue
        self.run_id = ""
        # Whether the LAST turn produced an answer or an apology. `ask` returns text either way, so
        # without this a script driving `--once` cannot tell them apart and files the apology as the
        # result. Read by the exit code, never by anything that renders.
        self.last_turn_failed = False
        # `--once` and `setup` open a session with nothing to paint an announcement card, so saying
        # otherwise would have her report a link card that reached nobody. Approvals are unaffected:
        # they come back through `ask`, not through this.
        self.queue = events.register(self.session_id, draws_cards=on_event is not None)
        self.approvals = Approvals(self.session_id, ask=ask)
        self.events = EventBridge(self.queue, on_event=on_event, approvals=self.approvals)
        self._extractions: set[asyncio.Task] = set()

    @classmethod
    async def open(cls, ask: Ask | None = None,
                   on_event: Callable[[str, dict], None] | None = None) -> "Session":
        """A raise between start() and the return must stop the engine it just built — the database's
        worker thread is not a daemon, so an engine nobody will ever close keeps the process alive."""
        engine = await core_engine.start(tickers=True, refresh_oauth=False)
        try:
            self = cls(engine, ask=ask, on_event=on_event)
            self.events.start()
            session_sandbox.note_connect(self.session_id)
            return self
        except BaseException:
            await core_engine.stop(engine)
            raise

    async def close(self) -> None:
        from kotoba.core import deferred_exec, task_list, work_state

        pending = work_state.pop_task(self.session_id)
        if pending is not None:
            pending.cancel()
        work_state.clear(self.session_id)
        task_list.clear(self.session_id)
        deferred_exec.cancel(self.session_id)
        deferred_exec.forget_session(self.session_id)
        await turns.supersede(self.session_id)
        await session_sandbox.release(self.session_id)
        await self.events.aclose()
        events.unregister(self.session_id, self.queue)
        if self._extractions:
            # The server outlives its detached extraction; this process does not. A `--once`
            # turn stating a fact would lose it without this drain — bounded, because a
            # hung provider must not hold the exit hostage.
            await asyncio.wait(self._extractions, timeout=_DRAIN_LIMIT)
            for straggler in self._extractions:
                straggler.cancel()
        await core_engine.stop(self.engine)

    def abandon_extractions(self) -> None:
        """Give up on the memory drain close() runs, from a signal handler or anywhere else.

        `kotoba --once` installs SIGINT on the turn's task; once that task is done every further Ctrl+C
        is a no-op, so the bounded wait was un-interruptible and the terminal read as hung. Cancelling
        the tasks makes close()'s `asyncio.wait` return at once and the engine still stops — which
        cancelling close() itself would not do, and the database's worker thread is not a daemon."""
        for task in list(self._extractions):
            task.cancel()

    async def ask(self, text: str, on_text: Callable[[str], None] | None = None,
                  on_face: Callable[[str], None] | None = None) -> str:
        """Run one turn and return what she said. `on_text` sees each filtered chunk as it lands, which
        is the only way a renderer can show her writing rather than her having written. `on_face` sees
        the emotion her own tag asked for, and sees it first — the tag closes before any text is
        released, so a renderer can have her face right on the frame she starts speaking.

        Ctrl+C keeps whatever she had already said: a half-answer she gave is still something the next
        turn has to know she said. A producer that DIES is different — the partial plus the apology are
        persisted so the transcript never presents the fragment as a finished answer, and nothing is
        marked delivered, so a pending work announcement and a due reminder survive for the next turn.
        The peek at reminders happens before load_context, or one stashed mid-turn would be cleared."""
        await self.engine.db.ensure_session(self.session_id)
        interaction.note_turn(self.session_id)
        pending_rem = pending_reminder.has_pending(self.session_id)
        persisted = not is_trigger_sentinel(text)
        if persisted:
            await self.engine.db.insert_turn(self.session_id, "user", text)

        request = ChatRequest(messages=[{"role": "user", "content": text}], session_id=self.session_id)
        items = await load_context(request, self.engine.db, self.session_id,
                                   connected_mcp=self._servers(), register="text",
                                   persisted=persisted)

        stream: asyncio.Queue = asyncio.Queue()
        said: list[str] = []
        outcome = {"failed": False}
        self.last_turn_failed = False
        # Every frame the turn emits names this run, so rows are routed by a positive "this is mine"
        # rather than by "no work is running" — which is what keeps a detached job out of her reply.
        self.run_id = uuid.uuid4().hex[:8]
        excluded = None if persisted else frozenset({"start_work", "delegate"})
        async with turns.lock(self.session_id):
            await turns.supersede(self.session_id)
            producer = asyncio.create_task(self._produce(items, stream, self.run_id, excluded, outcome))
            turns.register(self.session_id, producer)
        try:
            return await self._drain(stream, said, on_text, on_face)
        finally:
            if not producer.done():
                producer.cancel()
            turns.clear(self.session_id, producer)
            if persisted:
                # Every other client extracts memory in its turn's finally; a CLI that skips it forgets
                # "I live in Barcelona" unless the model volunteers a memory_write. Started BEFORE the
                # assistant row on purpose: a second Ctrl+C lands on whichever await the finally holds,
                # and her words are on screen already while the fact exists nowhere. Detached, with a
                # strong reference; close() drains the set.
                mem = asyncio.create_task(extract_and_save_memory(text, self.engine.db))
                self._extractions.add(mem)
                mem.add_done_callback(self._extractions.discard)
            partial = _tidy("".join(said))
            # A turn with no brain to think with never raised, so it is not in `outcome` — and it is
            # the commonest way of all to get a sentence back instead of an answer.
            self.last_turn_failed = bool(outcome["failed"]) or partial.strip() == _OFFLINE_MSG
            if partial:
                # The terminal keeps what she wrote; history must not re-send a block she wrote twice.
                await self.engine.db.insert_turn(self.session_id, "assistant", collapse_repeats(partial))
                if not outcome["failed"]:
                    snap = work_state.get(self.session_id)
                    if snap["status"] in ("done", "failed") and not snap["announced"]:
                        work_state.mark_announced(self.session_id)
                    if pending_rem:
                        pending_reminder.clear(self.session_id)

    async def _produce(self, items: list[dict], stream: asyncio.Queue, run_id: str,
                       exclude_tools: frozenset[str] | None, outcome: dict) -> None:
        """The web's producer, mirrored. A loop that dies mid-stream must apologise INTO the queue:
        swallowed, the DONE below made _drain return the half-sentence as a finished answer while the
        exception sat unread in the task. Cancellation is not failure and keeps its silence. The seam
        is the text register's paragraph break between the loop's iterations; no
        voice caller passes one, so the /v1 wire is untouched by construction."""
        try:
            await agentic_loop(
                items, self.session_id, self.engine.db, stream, self.engine.soul_patterns,
                mcp=self.engine.mcp, mode="companion", channel="text",
                run_id=run_id, exclude_tools=exclude_tools, seam="\n\n", register="text",
            )
        except Exception as e:
            outcome["failed"] = True
            log.exception("agentic_loop failed for session %s", self.session_id)
            await stream.put(f"\n\n{_spoken_failure(e)}")
        finally:
            await stream.put(_stream.DONE_SENTINEL)

    async def _drain(self, stream: asyncio.Queue, said: list[str],
                     on_text: Callable[[str], None] | None = None,
                     on_face: Callable[[str], None] | None = None) -> str:
        """Three filters and a head-watch, not the spoken chain: the terminal keeps the URLs, fences and
        markdown the voice chain exists to remove. Her tags go, and nothing else in brackets does —
        dropping links, footnotes and index expressions turned every citation into `((https://…))`.

        `CitationFilter` is FIRST, closest to the wire: everything downstream then reads prose, and the
        head-watch gives up on the first printable character — a PUA marker there took her face tag.

        The flush sentinel is truthy, so an unguarded `if chunk:` hands an `object` to the leak filter
        and kills the turn; its flush is NOT `partial=True`, or the held fragment merges into the next
        iteration's first words and the strip eats real reply text on a surface nothing speaks."""
        face = None if on_face is None else lambda n: self._face(on_face, n)
        cites = CitationFilter()
        leak = ToolCallLeakFilter()
        tags = AudioTagFilter(keep_valid=False, text_surface=True, on_tag=face)
        head = HeadTagFilter(on_tag=face)
        while True:
            chunk = await stream.get()
            if chunk is _stream.DONE_SENTINEL:
                rest = leak.feed(cites.flush()) + leak.flush()
                last = head.feed(tags.feed(rest) + tags.flush()) + head.flush()
                said.append(last)
                self._echo(on_text, last)
                return _tidy("".join(said))
            if chunk is _stream.FLUSH_SENTINEL:
                held = head.feed(tags.feed(leak.feed(cites.flush()) + leak.flush()))
                if held:
                    said.append(held)
                    self._echo(on_text, held)
                continue
            if chunk:
                shown = head.feed(tags.feed(leak.feed(cites.feed(chunk))))
                said.append(shown)
                self._echo(on_text, shown)

    def _echo(self, on_text: Callable[[str], None] | None, chunk: str) -> None:
        """A renderer that raises must not take the turn down with it — she is still talking."""
        if on_text is None or not chunk:
            return
        try:
            on_text(chunk)
        except Exception:
            log.warning("the renderer dropped a chunk", exc_info=True)

    def _face(self, on_face: Callable[[str], None], name: str) -> None:
        """One tag, translated to a face by the single map both channels read."""
        face = TAG_TO_FACE.get(name)
        if face is None:
            return
        try:
            on_face(face)
        except Exception:
            log.warning("the renderer dropped a face", exc_info=True)

    def _servers(self) -> list[dict] | None:
        tools = getattr(self.engine.mcp, "server_tools", {})
        return [{"name": n, "tools": t} for n, t in tools.items() if t] or None
