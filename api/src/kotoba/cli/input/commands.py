"""The slash table: every word she answers to at the prompt, and what each one is worth.

Nineteen, and the completion list shows all of them without a `… N more` — a cap that hides the last
command turns `/` from *everything I can do* into *some of it*. `/work` and `/stop` are the pair that
makes a background job answerable; neither is possible from a keypress. `/approvals` is the terminal's
way to revoke an "always allow", which an OSS install has no web to do. `/exit` is the twentieth and
deliberately NOT in the table — it is `/quit` under another name, and a listing carrying both spends a
row saying the same thing twice; `parse` folds it. A command never runs itself: `parse` returns the
verb and its argument, and the caller decides.
"""
from __future__ import annotations

from dataclasses import dataclass

COMMANDS = {
    "/help": "what I can do here: /help keys",
    "/plan": "the current plan — steps and where it is",
    "/settings": "everything she's configured: /settings brain",
    "/set": "change one of them: /set sandbox docker",
    "/approvals": "see or revoke what she runs without asking: /approvals rm 3",
    "/work": "look inside a long job, running or finished: /work 1",
    "/stop": "call off the long job she's running in the background",
    "/sessions": "the conversations you've had before this one",
    "/open": "open something she handed you: /open 1",
    "/attach": "send her a file: /attach ~/shot.png",
    "/helpers": "the full log of her helpers, out now or last time: /helpers 2",
    "/last": "print the last tool result in full",
    "/model": "see or switch the model: /model grok-4.3",
    "/face": "set one emotion: /face excited",
    "/emotions": "all 14 faces at once",
    "/clear": "clear the screen, keep the session",
    "/calm": "toggle reduced motion",
    "/plate": "quieten the nameplate on her everyday replies",
    "/quit": "leave — ctrl-d works too",
}

ALIASES = {"/exit": "/quit"}


@dataclass(frozen=True)
class Command:
    name: str
    arg: str = ""
    known: bool = True


def is_command(line: str) -> bool:
    """A lone `/` is someone opening the completion list, not a command."""
    return line.startswith("/") and len(line.strip()) > 1


def parse(line: str) -> Command:
    head, _, arg = line.strip().partition(" ")
    head = ALIASES.get(head, head)
    return Command(head, arg.strip(), head in COMMANDS)


def complete(word: str) -> list[str]:
    """What `/` and a few letters could still become, in the order they are listed."""
    return [name for name in COMMANDS if name.startswith(word)]


def help_rows() -> list[tuple[str, str]]:
    """A two-column list is as wide as its own longest entry — the caller pads from this."""
    return list(COMMANDS.items())
