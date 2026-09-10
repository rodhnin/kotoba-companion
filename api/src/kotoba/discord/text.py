"""Getting her words onto Discord: the 2000-character cut, and the edit budget.

The splitter is pure so it can be tested without a gateway. `StreamingReply` needs a channel, but only
the two methods any channel object has.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

log = logging.getLogger("kotoba.discord")

LIMIT = 2000
MAX_PARTS = 5           # five messages in a row is spam, and Discord rate-limits her into a stall

# She writes code, so a cut inside a fence is the one that will actually happen.
_FENCE = re.compile(r"^\s*```(\S*)")
_SENTENCE = re.compile(r"[.!?…](?:[\"'”’)\]]+)?\s")


def _fence_after(open_fence: str, chunk: str) -> str:
    """Which fence, if any, is still open once this chunk has been written."""
    state = open_fence
    for line in chunk.splitlines():
        if _FENCE.match(line) and line.count("```") % 2:
            state = "" if state else line.strip()
    return state


def _best_cut(s: str, budget: int) -> int:
    """A paragraph break beats a sentence end beats a line break beats the budget itself."""
    window = s[:budget]
    for finder in (lambda: window.rfind("\n\n"),
                   lambda: max((m.end() for m in _SENTENCE.finditer(window)), default=-1),
                   lambda: window.rfind("\n")):
        at = finder()
        if at > budget // 4:
            return at
    return budget


def split_for_discord(text: str, limit: int = LIMIT) -> list[str]:
    """Cut at `limit`, and never leave a fence hanging: an unbalanced ``` swallows the next message
    whole. The fence is closed at the cut and reopened with its own language on the next part."""
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    open_fence = ""
    rest = text
    while rest:
        prefix = f"{open_fence}\n" if open_fence else ""
        room = limit - len(prefix) - 4
        if len(rest) <= room:
            out.append(prefix + rest)
            break
        cut = _best_cut(rest, room)
        head, rest = rest[:cut], rest[cut:].lstrip("\n")
        still_open = _fence_after(open_fence, head)
        piece = prefix + head.rstrip()
        if still_open:
            piece += "\n```"
        out.append(piece)
        open_fence = still_open
    return out


# A cited source arrives as the model wrote it: wrapped in a second pair of brackets, carrying the
# search provider's tracking parameter, and repeated after every paragraph it supports. In a chat a
# link is worth keeping — it is clickable — but once, clean, and not in double parentheses.
_WRAPPED = re.compile(r"\(\((https?://[^\s()]+)\)\)")
_TRACKING = re.compile(r"([?&])(?:utm_[a-z_]+|ref|ref_src|source)=[^&\s)>]*(&?)")
_CODE = re.compile(r"(```.*?```|`[^`\n]+`)", re.S)
_CUT_NOTE = "\n\n*(cut here — too long for Discord)*"


# What a filter takes out leaves its wrapper behind: the spoken chain removes the address and the
# citation's own brackets stay as "()" at the end of a sentence. A task list is the same shape and
# means something, so a pair opening a list item is left alone.
_EMPTY = re.compile(r"(?<!^)(?<!^- )(?<!^\* )(?<!^\+ )[ \t]*[(\[]{1,2}\s*[)\]]{1,2}", re.M)


def _untracked(hit: re.Match) -> str:
    return hit.group(1) if hit.group(2) else ""


def _tidy_prose(text: str) -> str:
    text = _WRAPPED.sub(r"<\1>", text)
    # To a fixed point: the pattern eats the `&` that ends the parameter it removes, so the scanner
    # resumed past the separator the NEXT one needed and two side by side left the second behind.
    while True:
        once = _TRACKING.sub(_untracked, text)
        if once == text:
            break
        text = once
    return _EMPTY.sub("", text)


def tidy_links(text: str) -> str:
    pieces = _CODE.split(text)
    text = "".join(piece if i % 2 else _tidy_prose(piece) for i, piece in enumerate(pieces))
    seen, out = set(), []
    for line in text.splitlines():
        bare = line.strip()
        if bare.startswith("<http") and bare.endswith(">"):
            if bare in seen:
                continue
            seen.add(bare)
        out.append(line)
    return "\n".join(out)


class StreamingReply:
    """Her reply arriving as she writes it, without eating the channel's edit budget.

    Discord allows roughly five edits per five seconds per channel; past that the library sleeps
    through a 429 and the reply visibly stalls. So an edit costs 1.2 s and 40 new characters, and a
    short answer that finishes inside the first window is sent once with no edit at all — which is the
    common case and has no rate-limit exposure.
    """

    EVERY = 1.2
    ENOUGH = 40

    def __init__(self, channel, reply_to=None) -> None:
        self._channel = channel
        self._reply_to = reply_to
        self._buf = ""
        self._sent: list = []
        self._shown = 0
        self._last = 0.0
        self._lock = asyncio.Lock()

    def feed(self, chunk: str) -> None:
        self._buf += chunk

    def due(self) -> bool:
        now = time.monotonic()
        return (len(self._buf) - self._shown >= self.ENOUGH) and (now - self._last >= self.EVERY)

    async def flush(self, *, final: bool = False) -> None:
        """The final flush is unconditional: whatever the budget said, the true text has to land."""
        async with self._lock:
            if not final and not self.due():
                return
            text = tidy_links(self._buf.strip())
            if not text:
                return
            self._last = time.monotonic()
            self._shown = len(self._buf)
            parts = split_for_discord(text)
            if len(parts) > MAX_PARTS:
                parts = parts[:MAX_PARTS]
                parts[-1] = split_for_discord(parts[-1], LIMIT - len(_CUT_NOTE))[0] + _CUT_NOTE
            await self._render(parts)

    async def _render(self, parts: list[str]) -> None:
        for i, body in enumerate(parts):
            if i < len(self._sent):
                if self._sent[i].content != body:
                    await self._edit(self._sent[i], body)
            else:
                self._sent.append(await self._send(body, first=not self._sent))

    async def _send(self, body: str, *, first: bool):
        if first and self._reply_to is not None:
            return await self._reply_to.reply(body, mention_author=False)
        return await self._channel.send(body)

    async def _edit(self, message, body: str) -> None:
        try:
            await message.edit(content=body)
        except Exception:
            log.debug("discord edit failed; the final flush will carry it", exc_info=True)
