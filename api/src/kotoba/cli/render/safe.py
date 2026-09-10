"""The `rich.Text` a card is allowed to be built out of.

`rich.Text` is a container, not an escape: whatever string it is handed reaches the file descriptor
intact, so an ESC in a command the model asked to run is an ESC the terminal obeys, and nothing else
in this process will catch it. React saves the web half; nothing saves this one.

The gate is a TYPE rather than a call at each site: a module that sanitises fifteen strings sanitises
fourteen the day someone adds the sixteenth, and the field nobody thought to audit is exactly the one
an attacker writes into. Building every row out of `Safe` covers a new field by having drawn it.
"""
from __future__ import annotations

from rich.text import Text

from kotoba.core.text_security import scrub


class Safe(Text):
    """A row that cannot carry a cursor move, a reading-order override or an invisible break."""

    def __init__(self, text: str = "", *args, **kwargs) -> None:
        super().__init__(scrub(text), *args, **kwargs)

    def append(self, text, style=None) -> Text:
        return super().append(scrub(text) if isinstance(text, str) else text, style)
