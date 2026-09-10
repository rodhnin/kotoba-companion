"""Draining her event channel into whatever draws the terminal.

`core.events.register` hands back a queue nothing reads by itself: unread it grows for the life of the
process, and a `need_input` frame in it is a turn blocked on a question nobody was asked. The bridge
reads it for good, and remembers what one frame cannot — a done-frame marked `pending` has not run.

How an action ENDED is read off `outcome` alone. `ok` answers a different question — the tool handed
back usable text — so a command exiting 2, one killed at its timeout and one the user refused all drew
the ✓ of a clean run. An unknown word stays `unknown`; the older ladder is read for an older backend.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from kotoba.cli.approvals import Approvals, Card

log = logging.getLogger("kotoba.cli")


def kind_of(frame: dict) -> str:
    """emotion · step · peek · working · work_started · work_done · artifact · files_changed · reminder ·
    report_ready · subagent_spawned · subagent_step · subagent_done · task_list · need_input ·
    recalled_image. The whole wire vocabulary, not the part this client draws:
    `peek` is the terminal's own and `recalled_image` is the web's, and a list that keeps only what
    has a branch today is a list the next kind is silently missing from."""
    if frame.get("type") == "emotion":
        return "emotion"
    return str(frame.get("kind") or "")


OUTCOMES = ("ok", "failed", "refused", "interrupted", "pending")


@dataclass
class Step:
    """A terminal row. `state` is the loop's own `outcome` word: `ok` ran and exited 0, `failed` ran and
    did not, `refused` never ran, `interrupted` was cut mid-run, `pending` is carded and waiting on a
    human, `unknown` is a word this build cannot place. Reading only `ok` calls three of six a success.

    `note` is the gate's verdict when it granted without asking: a command covered by a saved family
    otherwise draws the row of a read that never needed a gate.

    `result` is the phrase that fits on a row and `full` is everything the tool returned — one string
    for both is why `/last`, whose own help says "in full", reprinted the row above it. Empty from a
    producer older than the field."""

    id: str
    kind: str = "tool"
    action: str = ""
    state: str = "running"      # running | pending | ok | failed | refused | interrupted | unknown
    result: str = ""
    full: str = ""
    note: str = ""


class EventBridge:
    def __init__(
        self,
        queue: asyncio.Queue,
        *,
        on_event: Callable[[str, dict], None] | None = None,
        approvals: Approvals | None = None,
    ) -> None:
        self.queue = queue
        self.steps: dict[str, Step] = {}
        self.working = False
        self._on_event = on_event
        self._approvals = approvals
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._drain(), name="kotoba-cli-events")

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass
            self._task = None
        if self._approvals is not None:
            self._approvals.close(None)

    def handle(self, frame: dict) -> str:
        """Fold one frame into the state a renderer reads, then hand it over. Returns its flat kind."""
        kind = kind_of(frame)
        if kind == "step":
            self._step(frame)
        elif kind == "working":
            self.working = bool(frame.get("on"))
        elif kind in ("work_started", "work_done"):
            self.working = kind == "work_started"
        elif kind == "need_input":
            self._card(frame)
        if self._on_event is not None:
            self._on_event(kind, frame)
        return kind

    def settle(self) -> None:
        """Everything already on the wire, folded now. The drain task reads the same queue, but a caller
        about to decide what a row FINALLY says cannot wait for the loop to schedule it: her last frames
        — the `interrupted` a cut turn closes each open step with — and that decision would land in the
        other order, and the guess would be the one committed."""
        while True:
            try:
                frame = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._fold(frame)

    async def _drain(self) -> None:
        while True:
            frame = await self.queue.get()
            self._fold(frame)

    def _fold(self, frame: dict) -> None:
        try:
            self.handle(frame)
        except Exception:
            # One bad frame must never end the drain: after it, no card would ever be answered again.
            log.warning("dropped an event frame", exc_info=True)

    def _step(self, frame: dict) -> None:
        step_id = str(frame.get("id") or "")
        if not step_id:
            return
        # deferred_exec re-sends the done-frame much later, carrying step_kind/action again so the row can
        # be rebuilt from it alone — never assume the matching `start` is still around.
        step = self.steps.get(step_id) or Step(id=step_id)
        step.kind = str(frame.get("step_kind") or step.kind)
        step.action = str(frame.get("action") or step.action)
        if frame.get("phase") == "start":
            step.state = "running"
        else:
            step.result = str(frame.get("result") or frame.get("text") or "")
            step.full = str(frame.get("full") or "")
            step.note = str(frame.get("note") or "")
            outcome = str(frame.get("outcome") or "")
            if outcome:
                step.state = outcome if outcome in OUTCOMES else "unknown"
            elif frame.get("pending"):
                step.state = "pending"
            elif frame.get("interrupted"):
                step.state = "interrupted"
            else:
                step.state = "ok" if frame.get("ok") else "failed"
        self.steps[step_id] = step

    def _card(self, frame: dict) -> None:
        card = Card.from_frame(frame)
        if self._approvals is None:
            if card.blocking:
                log.warning("a card opened with nothing to answer it: %s", card.label)
            return
        if card.mode == "clear":
            self._approvals.close(card.request_id)
        else:
            self._approvals.present(card)
