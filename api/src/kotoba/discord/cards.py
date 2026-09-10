"""Her cards, as Discord buttons.

`discord` is imported lazily so this module loads without the extra — the view class is built on first
use, which is also the only place a decorator can hang off a class that does not exist yet.

Two refusals are deliberate. A card asking for a SECRET is never answerable here: a key typed into a
channel is a key everyone in it now has. And no button ever grants a standing permission, because a
blanket "always allow" made from a chat window has a blast radius nobody in that window can see.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from kotoba.cli.approvals import Card
from kotoba.core import interaction

log = logging.getLogger("kotoba.discord")

_VIEW = None

DENIED = (False, False, False)
APPROVED = (True, False, False)

# Channel id -> how many cards are open on it right now. A spoken turn waiting on a button is not a
# stuck turn, and without this the voice watchdog cannot tell the two apart: both are silent.
_OPEN: dict[int, int] = {}


def pending(channel_id: int | None) -> bool:
    return bool(_OPEN.get(channel_id or 0))


def owner_only(card: Card) -> bool:
    """Anything this surface did not raise itself may be running on the operator's machine, and
    consenting to that on their behalf is not an admin's to give.

    The old rule read the command family, which an unparseable line leaves empty — so the one card
    nobody could parse was also the one nobody had to own. Asked the other way round, a new tool
    added anywhere else is protected by default instead of by whoever remembers."""
    return (card.notice or {}).get("surface") != "discord"


def may_answer(card: Card, uid: int, *, asker: int, owner: int | None, is_admin: bool) -> bool:
    """Ownership is asked FIRST. The asker shortcut lets somebody answer their own question, and it
    must never outrank the rule above — it used to, so whoever spoke first in a channel could approve
    a command on somebody else's machine."""
    if owner_only(card):
        return owner is not None and uid == owner
    return uid == asker or is_admin


def _facts(card: Card) -> list[tuple[str, str]]:
    notice = card.notice or {}
    out = []
    for pair in notice.get("facts") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            out.append((str(pair[0]), str(pair[1])))
    return out


def _view_class():
    global _VIEW
    if _VIEW is not None:
        return _VIEW
    import discord

    class ApprovalView(discord.ui.View):
        def __init__(self, card: Card, *, asker: int, owner: int | None, timeout: float) -> None:
            super().__init__(timeout=timeout)
            self.card = card
            self.asker = asker
            self.owner = owner
            self.answer: asyncio.Future = asyncio.get_running_loop().create_future()

        async def interaction_check(self, itx) -> bool:
            """Re-read the permission at click time: an admin demoted between card and click must not
            still be able to press it."""
            uid = itx.user.id
            perms = getattr(itx.user, "guild_permissions", None)
            allowed = may_answer(self.card, uid, asker=self.asker, owner=self.owner,
                                 is_admin=bool(getattr(perms, "administrator", False)))
            if not allowed:
                await itx.response.send_message(
                    "This one isn't yours to answer.", ephemeral=True)
            return allowed

        async def _settle(self, itx, value, word: str) -> None:
            if not self.answer.done():
                self.answer.set_result(value)
            for child in self.children:
                child.disabled = True
            self.stop()
            await itx.response.edit_message(
                content=f"{word} by {itx.user.display_name}.", view=self)

        @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
        async def approve(self, itx, button) -> None:
            await self._settle(itx, APPROVED, "Approved")

        @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
        async def deny(self, itx, button) -> None:
            await self._settle(itx, DENIED, "Denied")

        async def on_timeout(self) -> None:
            if not self.answer.done():
                self.answer.set_result(DENIED)

    _VIEW = ApprovalView
    return _VIEW


class DiscordCards:
    """The `ask` handed to `Approvals`. A coroutine, so it runs on the loop and stays cancellable."""

    def __init__(self, channel, *, asker: int, owner: int | None) -> None:
        self.channel = channel
        self.asker = asker
        self.owner = owner

    async def ask(self, card: Card):
        if card.mode == "approval":
            async with self._counted():
                return await self._approval(card)
        if card.secret:
            await self._refuse_secret(card)
            return None
        if card.mode == "input" and card.wait:
            async with self._counted():
                return await self._input(card)
        # Only a card with a Future behind it is handed here at all, so an announcement card —
        # a link offer, a non-blocking box — never arrives and a branch for one could not run.
        # That is why the tools that open them are withheld from this surface instead.
        return None

    @contextlib.asynccontextmanager
    async def _counted(self):
        cid = getattr(self.channel, "id", 0) or 0
        _OPEN[cid] = _OPEN.get(cid, 0) + 1
        try:
            yield
        finally:
            left = _OPEN.get(cid, 1) - 1
            if left > 0:
                _OPEN[cid] = left
            else:
                _OPEN.pop(cid, None)

    async def _approval(self, card: Card):
        import discord

        window = interaction.approval_timeout("text", el_agent=False)
        view = _view_class()(card, asker=self.asker, owner=self.owner, timeout=window)
        embed = discord.Embed(
            title=(card.notice or {}).get("head") or "May I?",
            description=card.label[:4000] or None,
        )
        for name, value in _facts(card)[:20]:
            embed.add_field(name=name[:256], value=value[:1024] or "—", inline=False)
        public = getattr(self.channel, "guild", None) is not None
        if owner_only(card):
            embed.add_field(
                name="Where the answer lands",
                value=("Everyone in this channel will read the result." if public
                       else "Only you will read the result."),
                inline=False)
            embed.set_footer(text="Runs on the host — only its owner can allow it.")
        msg = await self.channel.send(embed=embed, view=view)
        try:
            return await asyncio.wait_for(view.answer, timeout=window + 5)
        except asyncio.TimeoutError:
            return None
        finally:
            if not view.is_finished():
                view.stop()
                for child in view.children:
                    child.disabled = True
                with contextlib.suppress(Exception):
                    await msg.edit(view=view)

    async def _input(self, card: Card):
        """Answered by the next message the asker writes here. No modal: a modal can only be opened
        from an interaction, and this card was not born of one."""
        bot = getattr(self.channel, "_state", None)
        client = getattr(bot, "_get_client", lambda: None)() if bot else None
        if client is None:
            return None
        prompt = card.label + (f"\n{card.detail}" if card.detail else "")
        await self.channel.send(f"**{prompt}**\n_Reply here to answer._")

        def mine(msg) -> bool:
            return msg.channel.id == self.channel.id and msg.author.id == self.asker

        try:
            reply = await client.wait_for("message", check=mine, timeout=180.0)
        except asyncio.TimeoutError:
            return None
        return reply.content

    async def _refuse_secret(self, card: Card) -> None:
        await self.channel.send(
            f"**{card.label}** — I won't take a key or a password in a Discord channel; "
            "everyone here would be able to read it. Give it to me in the terminal or in Settings."
        )
