"""Answering her cards from a terminal.

A `need_input` frame is a question with a Future behind it: the turn stays blocked until someone
resolves it, and the answer must carry the frame's OWN request_id — given none, resolve matches
newest-first, so a terminal working through cards in the order they opened answers the wrong one.

resolve also has to run ON the event loop: Future.set_result schedules with call_soon, which never
wakes the selector, so called from the thread reading stdin it records the answer and the turn still
sleeps out the whole window — 25 s over voice, 180 s here. `answer` uses call_soon_threadsafe.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass

from kotoba.cli import secret
from kotoba.core import interaction

log = logging.getLogger("kotoba.cli")

SECRET_KINDS = frozenset({"key", "secret"})


@dataclass(frozen=True)
class Card:
    """One `need_input` frame, unpacked."""

    request_id: str | None
    mode: str
    label: str = ""
    detail: str | None = None
    input_kind: str | None = None
    family: str | None = None
    can_always: bool = False
    can_always_exact: bool = False
    always_note: str = ""
    url: str | None = None
    wait: bool = False
    # The facts behind the label. A count is not something anybody can consent to; a rich
    # surface needs the names to draw, and the terminal reads the same field.
    notice: dict | None = None

    @classmethod
    def from_frame(cls, frame: dict) -> "Card":
        return cls(
            request_id=frame.get("request_id"),
            mode=str(frame.get("mode") or ""),
            label=str(frame.get("label") or ""),
            detail=frame.get("detail"),
            input_kind=frame.get("input_kind"),
            family=frame.get("family"),
            can_always=bool(frame.get("can_always")),
            can_always_exact=bool(frame.get("can_always_exact")),
            always_note=str(frame.get("always_note") or ""),
            url=frame.get("url"),
            wait=bool(frame.get("wait")),
            notice=frame.get("notice") if isinstance(frame.get("notice"), dict) else None,
        )

    @property
    def blocking(self) -> bool:
        """Whether a Future is waiting on this one. An open_link card and a wait=False input card are
        announcements — nothing to resolve, and answering one would steal a real card's answer."""
        return self.mode == "approval" or (self.mode == "input" and self.wait)

    @property
    def secret(self) -> bool:
        return self.input_kind in SECRET_KINDS


Ask = Callable[[Card], object]


def ask_at_terminal(card: Card) -> object | None:
    """The no-dependency prompt: y/N/a for an approval, a line for an input, echo off for a secret.

    Still the live path, and on more surfaces than the name suggests. `kotoba --once` and `kotoba setup`
    open a Session with no `ask=`, so every card they meet lands here — and the full renderer hands it
    every card that is not an approval too, because nothing in the renderer draws an input box or a
    secret field. Only the approval branch below is dead in
    the interactive app, which draws its own rail.

    Returns None for "no answer"."""
    if card.mode == "approval":
        return _ask_approval(card)
    prompt = f"{card.label}{f' ({card.detail})' if card.detail else ''}: "
    try:
        # `cli.secret`, never input(): a key must not survive in the scrollback, and it is never
        # logged or echoed back afterwards either.
        return secret.read(prompt) if card.secret else input(prompt)
    except EOFError:
        return None


def _ask_approval(card: Card) -> tuple[bool, bool, bool]:
    """y/N, plus whichever standing-permission keys this card really offers: `a` for the whole family,
    `t` for this line and nothing else. They are two keys and not one, because the same key granting
    "every npm forever" on one card and "just this line" on the next is a safety gate whose blast radius
    depends on state the person cannot see. When the broad one is withheld the reason is printed, so an
    absent key never has to be guessed at."""
    keys = "y/N" + ("/a" if card.can_always else "") + ("/t" if card.can_always_exact else "")
    forever = f" — a = always allow {card.family}" if card.can_always and card.family else ""
    if card.can_always_exact:
        forever += " — t = always allow just this line"
    note = f"\n{card.always_note}" if card.always_note and not card.can_always else ""
    # The question goes to STDERR: `input()` writes its prompt to stdout, where `--once` puts her
    # answer, so a card opened there arrived glued to her reply on the same line. Piping an answer in
    # still works, which an isatty guard would have taken away.
    print(f"{card.label}{note}\nallow? [{keys}]{forever} ", end="", file=sys.stderr, flush=True)
    try:
        answer = input().strip().lower()
    except EOFError:
        return None
    if answer in ("a", "always"):
        return (True, card.can_always, False)
    if answer in ("t", "this"):
        return (True, False, card.can_always_exact)
    return (answer in ("y", "yes"), False, False)


class Approvals:
    """Answers this session's cards, one at a time, in the order they opened."""

    def __init__(self, session_id: str, ask: Ask | None = None) -> None:
        self.session_id = session_id
        self._ask = ask or ask_at_terminal
        self._loop = asyncio.get_running_loop()
        self._lock = asyncio.Lock()
        self._open: dict[str, asyncio.Task] = {}

    def present(self, card: Card) -> bool:
        """Take responsibility for one card. Returns at once — the prompt is its own task so the queue
        keeps draining while the human reads (a `clear` for THIS card arrives on it)."""
        if not card.blocking:
            return False
        if not card.request_id:
            log.warning("card %r carries no request_id — leaving it alone", card.label)
            return False
        self._open[card.request_id] = asyncio.create_task(self._answer(card))
        return True

    def close(self, request_id: str | None) -> None:
        """A `clear` frame: the card timed out, or the turn was cancelled under it. A bare clear takes
        them all — that is a cancel_work or a /leave, not one card giving up."""
        for rid in ([request_id] if request_id else list(self._open)):
            task = self._open.pop(rid, None)
            if task is not None:
                task.cancel()

    def answer(self, request_id: str, value: dict | None) -> None:
        """Resolve a card from ANY thread: `call_soon` from another one never wakes the selector."""
        try:
            self._loop.call_soon_threadsafe(interaction.resolve, self.session_id, value, request_id)
        except RuntimeError:
            log.debug("the loop is gone — dropping the answer to %s", request_id)

    async def _answer(self, card: Card) -> None:
        assert card.request_id is not None
        try:
            async with self._lock:
                choice = await self._prompt(card)
            self.answer(card.request_id, self._value(card, choice))
        except Exception:
            # Never let the prompt's failure become a hang: a card with no answer is a turn asleep.
            log.warning("could not ask about %r — leaving it unanswered", card.label, exc_info=True)
            self.answer(card.request_id, self._value(card, None))
        finally:
            self._open.pop(card.request_id, None)

    async def _prompt(self, card: Card) -> object | None:
        """An async ask runs on the loop and is cancellable. A sync one (the default) reads in a worker
        thread so the queue keeps draining — that read cannot be interrupted, so a card cleared while it
        is open leaves it holding stdin until the user presses enter."""
        if inspect.iscoroutinefunction(self._ask):
            return await self._ask(card)
        return await asyncio.to_thread(self._ask, card)

    def _value(self, card: Card, choice: object | None) -> dict | None:
        """The answer, with each grant re-checked against what the card was allowed to offer.

        A `can_always` / `can_always_exact` of False is the gate saying this command may never be
        persisted that way — however emphatically the person at the keyboard said always, and whichever
        renderer handed the answer back. Two flags because they are two grants of different width: the
        family one covers every command starting with that token, the exact one covers this line alone."""
        if card.mode == "approval":
            if choice is None:
                return None
            answer = choice if isinstance(choice, tuple) else (bool(choice), False)
            approved, always = bool(answer[0]), bool(answer[1])
            always_exact = bool(answer[2]) if len(answer) > 2 else False
            return {"approved": approved,
                    "always": always and card.can_always,
                    "always_exact": always_exact and card.can_always_exact and not always}
        # None is nobody having answered — a closed stdin, a prompt that raised. Folding it into ""
        # made it indistinguishable from an empty box, which is a decision they DID make.
        return {"value": choice if isinstance(choice, str) else None}
