"""The gateway client and the decision of whether a message is even for her.

She answers a mention, a reply to something of hers, a DM, a channel named as hers, and a short
window after she just spoke to the same person. Everything else she reads and leaves alone — a bot
that answers every line in a busy channel is a bot people mute.
"""
from __future__ import annotations

import asyncio
import logging
import time

from kotoba.discord import authority, bridge, cards, config, state

log = logging.getLogger("kotoba.discord")

# A ceiling on TOTAL time cannot tell thinking from wedged, and the number that fits a short answer
# is shorter than the approval card's own window: at 45 s she was cancelling turns whose card was
# still on screen waiting to be read. What is actually wrong is SILENCE — nothing written, no step,
# no card open — so that is what is timed.
STALL_SECONDS = 75.0
TURN_CEILING = 600.0


def intents():
    import discord

    want = discord.Intents.none()
    want.guilds = True
    want.guild_messages = True
    want.dm_messages = True
    want.voice_states = True
    want.message_content = True     # privileged: without it every content is ""
    want.members = True             # privileged: guild_permissions and display names
    return want                     # presences stays off — heaviest cache, nothing here reads it


def my_role_tokens(msg) -> list[str]:
    """The mention tokens of the roles she wears here, minus @everyone.

    Typing her name offers two different things: the bot, and the integration role that carries the
    same name. Half the room picks the role, whose token is `<@&id>` and which appears in neither the
    user mention list nor the user token."""
    guild = getattr(msg, "guild", None)
    mine = getattr(guild, "me", None) if guild is not None else None
    out = []
    for role in getattr(mine, "roles", ()) or ():
        rid = getattr(role, "id", None)
        if rid is None or rid == getattr(guild, "id", None):
            continue
        out.append(f"<@&{rid}>")
    return out


def mentions_me(msg, me) -> bool:
    """Read the raw text, not the parsed list.

    `Message.mentions` is resolved against the guild's member cache and a miss is dropped silently,
    so a real mention arrives as an empty list and she says nothing, with no error anywhere."""
    if me is None:
        return False
    if any(getattr(u, "id", None) == me.id for u in getattr(msg, "mentions", ()) or ()):
        return True
    content = msg.content or ""
    if f"<@{me.id}>" in content or f"<@!{me.id}>" in content:
        return True
    return any(token in content for token in my_role_tokens(msg))


def wants_reply(msg, *, me, home: frozenset[int], last_spoke: dict,
                prev_author: int | None = None) -> bool:
    if msg.author.bot or not ((msg.content or "").strip() or getattr(msg, "attachments", None)):
        return False
    if msg.guild is None:
        return True
    if mentions_me(msg, me):
        return True
    ref = getattr(msg, "reference", None)
    resolved = getattr(ref, "resolved", None) if ref else None
    author = getattr(resolved, "author", None) if resolved is not None else None
    if author is not None and getattr(author, "id", None) == me.id:
        return True
    if msg.channel.id in home:
        return True
    if getattr(msg, "mention_everyone", False):
        return False        # an announcement to the room is not a line in anyone's conversation
    # Somebody else spoke in between, so the room moved on without her. Measured: four people
    # talking, and every line one of them wrote landed as if it had been addressed to her.
    if prev_author is not None and prev_author not in (msg.author.id, getattr(me, "id", None)):
        return False
    who, when = last_spoke.get(msg.channel.id, (None, 0.0))
    fresh = time.monotonic() - when < config.ATTENTION_SECONDS
    return bool(fresh and who == msg.author.id and not msg.mentions)


class KotobaClient:
    """Thin glue: the library's client, plus one session per channel over a shared engine."""

    def __init__(self, engine, *, guilds: frozenset[int]) -> None:
        import discord

        self.engine = engine
        self.guilds_allowed = guilds
        self.sessions: dict[int, bridge.ChannelSession] = {}
        self.askers: dict[int, object] = {}
        self._last_spoke: dict[int, tuple[int, float]] = {}
        self._last_author: dict[int, int | None] = {}
        self.rooms: dict[int, object] = {}          # guild id -> VoiceRoom
        self._progress: dict[int, float] = {}       # channel id -> last sign of life this turn
        self._bg: set = set()                       # a bare create_task is collected mid-flight
        # Everyone/here is off at the client, so no sentence anybody talks her into can ring a
        # whole server. People and roles still resolve, which is what a reply needs.
        quiet = discord.AllowedMentions(everyone=False, users=True, roles=True, replied_user=True)
        self.client = discord.Client(intents=intents(), allowed_mentions=quiet)
        self.client.event(self.on_ready)
        self.client.event(self.on_message)

    async def on_ready(self) -> None:
        state.set_runtime_live(True)
        names = ", ".join(g.name for g in self.client.guilds) or "none"
        log.info("connected as %s in %s", self.client.user, names)

    async def on_message(self, msg) -> None:
        me = self.client.user
        home = config.home_channels()
        prev = self._last_author.get(msg.channel.id)
        self._last_author[msg.channel.id] = getattr(msg.author, "id", None)
        want = wants_reply(msg, me=me, home=home, last_spoke=self._last_spoke, prev_author=prev)
        log.debug("message from %s: %d chars, mentions=%s, want=%s",
                  getattr(msg.author, "name", "?"), len(msg.content or ""),
                  [getattr(u, "id", u) for u in getattr(msg, "mentions", ())], want)
        if not want:
            return
        guild = msg.guild
        if self.guilds_allowed and not self._allowed(msg, guild):
            return
        who = authority.actor_from_member(
            msg.author, owner=config.owner_id(), guild_id=guild.id if guild else None)
        try:
            await self._turn(msg, who)
        except Exception:
            log.exception("discord turn failed")
            await msg.channel.send("Something went wrong on my side there. Try me again?")

    def _allowed(self, msg, guild) -> bool:
        """The allow-list has to cover the DM too.

        A DM has no guild, so the check used to be skipped there entirely: anyone who shared any
        server with her — including one deliberately left off the list — got a full turn in private.
        Now a DM is answered only by somebody who is in a server she is allowed to work in."""
        if guild is not None:
            return guild.id in self.guilds_allowed
        uid = getattr(msg.author, "id", None)
        return any(g.get_member(uid) is not None for g in self.client.guilds
                   if g.id in self.guilds_allowed)

    async def _turn(self, msg, who: authority.Actor) -> None:
        from kotoba.discord.text import StreamingReply

        from kotoba.discord import media

        session = await self._session(msg, who)
        reply = StreamingReply(msg.channel, reply_to=msg)
        text = self._clean(msg)
        refused = await media.stash(msg, session.session_id)
        if not text and getattr(msg, "attachments", None):
            # Naming her with nothing but a picture is a whole message; stripping the mention left
            # the turn empty and she answered a blank.
            text = "(no words, just what I attached)"
        if refused:
            # She must say it herself: a file reported as received and then answered around is the
            # one shape of this that reads as her lying.
            text += ("\n\n[these files did NOT reach you — say so plainly: "
                     + ", ".join(refused) + "]")
        async with msg.channel.typing():
            def on_text(chunk: str) -> None:
                reply.feed(chunk)

            said = await session.ask(text, who, on_text=on_text)
            await reply.flush(final=True)
        # Sizes and ids, never the words: whoever runs her needs to see that turns are happening
        # and how big they are, and a log that quotes a channel is a second copy of it.
        log.info("answered %s in #%s: %d chars in, %d out", who.user_id,
                 getattr(msg.channel, "name", msg.channel.id), len(text), len(said or ""))
        if said:
            self._last_spoke[msg.channel.id] = (who.user_id, time.monotonic())

    def _clean(self, msg) -> str:
        """Her own mention is addressing, not content — left in, the model reads a raw id as a word."""
        text = msg.content or ""
        me = self.client.user
        for form in [f"<@{me.id}>", f"<@!{me.id}>"] + my_role_tokens(msg):
            text = text.replace(form, "")
        return text.strip()

    async def _session(self, msg, who: authority.Actor) -> bridge.ChannelSession:
        """One session per channel, and the asker moves with whoever is speaking.

        Assigning a fresh asker onto `approvals` looked right and did nothing: it reads a private
        attribute, so the first person to speak stayed the asker for the life of the process. The
        card object is kept instead, and only the id inside it moves."""
        existing = self.sessions.get(msg.channel.id)
        if existing is not None:
            return existing
        asking = cards.DiscordCards(msg.channel, asker=who.user_id, owner=config.owner_id())
        session = bridge.ChannelSession(
            self.engine, msg.guild.id if msg.guild else None, msg.channel.id, bot=self.client,
            surface=self, on_event=self._noted(msg.channel.id), ask=asking.ask,
            on_turn=lambda actor: setattr(asking, "asker", actor.user_id))
        session.start()
        self.sessions[msg.channel.id] = session
        self.askers[msg.channel.id] = asking
        return session

    async def join_voice(self, channel):
        from kotoba.discord.voice import VoiceRoom

        await self.leave_voice(channel.guild.id)
        session = self.sessions.get(channel.id)
        names = session.my_names() if session is not None else []
        if not names:
            probe = bridge.ChannelSession.__new__(bridge.ChannelSession)
            probe.bot, probe.guild_id = self.client, channel.guild.id
            names = probe.my_names()
        gid = channel.guild.id
        room = VoiceRoom(self.client, channel, names=names, on_turn=self._voice_turn,
                         language=await self._stt_language(),
                         on_empty=lambda: self.leave_voice(gid))
        await room.join()
        self.rooms[gid] = room
        return room

    async def _stt_language(self) -> str:
        """Pin transcription to HER configured language, not to what ElevenLabs guesses.

        Left to auto-detect, a Spanish "Kotoba, hola" came back as コトバオラ — her name is a real
        Japanese word, so the guess was defensible and the transcript was useless."""
        from kotoba.core.voice.config import stt_language_for

        try:
            soul = await self.engine.db.fetch_soul_config()
        except Exception:
            soul = None
        return stt_language_for((soul or {}).get("language"))

    async def leave_voice(self, guild_id: int) -> bool:
        room = self.rooms.get(guild_id)
        if room is None:
            return False
        if room._turn_live():
            # The tool that asked runs INSIDE the turn: the goodbye she is about to say has not been
            # written yet, let alone spoken. Disconnecting here cuts it off before it exists.
            room.depart_after_turn()
            return True
        self.rooms.pop(guild_id, None)
        await room.leave()
        return True

    async def _voice_turn(self, room, user_id: int, text: str, turn_no: int, speech) -> None:
        """One spoken turn: her words go to the voice channel, and a caption to the text one.

        The room owns both the task and the voice, so a second person saying her name replaces this
        whole thing rather than queueing behind it — she has one mouth in that room."""
        from kotoba.core.stream import AudioTagFilter

        guild = room.channel.guild
        member = guild.get_member(user_id)
        if member is None:
            log.warning("voice: %s is not a member I can see", user_id)
            return
        who = authority.actor_from_member(member, owner=config.owner_id(), guild_id=guild.id)
        cid = room.channel.id
        try:
            await speech.start()
        except Exception:
            # Her voice not opening is not a reason to answer nothing at all. Dead, every feed is a
            # no-op, so the turn runs and the reply lands in the text channel instead of vanishing.
            log.warning("her voice would not open; answering in writing", exc_info=True)
            speech.kill()

        feeder = None
        finished = False
        try:
            session = await self._session_for(room.channel, who)
            # Fed as she writes, not once she has finished. Buffered to the end, the first sound
            # arrived five to six seconds after she was called — long enough that the next thing
            # anybody said cancelled the turn before a word of it was audible.
            outgoing: asyncio.Queue = asyncio.Queue()

            def on_text(chunk: str) -> None:
                self._progress[cid] = time.monotonic()
                outgoing.put_nowait(chunk)

            feeder = asyncio.create_task(self._feed(speech, outgoing))
            log.info("voice: turn %s starting (%r)", turn_no, text[:60])
            self._progress[cid] = time.monotonic()
            said = await self._until_stalled(
                asyncio.create_task(session.ask(text, who, on_text=on_text, register="voice")), cid)
            log.info("voice: turn %s wrote %s chars", turn_no, len(said or ""))
            outgoing.put_nowait(None)
            await feeder
            feeder = None
            await speech.finish()
            finished = True
            from kotoba.discord.text import tidy_links

            # The caption is what she SAID, so the address is already gone from it — and the
            # citation's brackets are not, which is how a sentence ends in a bare "()".
            caption = AudioTagFilter(keep_valid=False, text_surface=True)
            shown = tidy_links(caption.feed(said) + caption.flush())
            if shown.strip():
                await room.channel.send(shown[:1900])
        except asyncio.TimeoutError:
            log.warning("voice: turn %s went silent for %ss", turn_no, STALL_SECONDS)
            await room.channel.send("I got stuck on that one — ask me again?")
        except asyncio.CancelledError:
            log.info("voice: turn %s was cut off", turn_no)
            raise
        except Exception:
            log.exception("spoken turn failed")
        finally:
            if feeder is not None:
                feeder.cancel()
                try:
                    await feeder
                except BaseException:
                    pass
            if not finished:
                await speech.abandon()
            self._progress.pop(cid, None)

    async def _until_stalled(self, turn: asyncio.Task, channel_id: int) -> str:
        """What is wrong with a turn is never that it took long — it is that nothing happened.

        A card on screen counts as something happening: it is a person reading, and its own window is
        longer than any total limit worth setting."""
        from kotoba.core.voice.config import own_cancellation_swallowed
        from kotoba.discord import cards as cards_mod

        started = time.monotonic()
        try:
            while True:
                done, _ = await asyncio.wait({turn}, timeout=5.0)
                if done:
                    return turn.result()
                now = time.monotonic()
                if cards_mod.pending(channel_id):
                    self._progress[channel_id] = now
                quiet = now - self._progress.get(channel_id, started)
                if quiet > STALL_SECONDS or now - started > TURN_CEILING:
                    raise asyncio.TimeoutError
        finally:
            if not turn.done():
                turn.cancel()
                try:
                    await turn
                except BaseException:
                    if own_cancellation_swallowed():
                        raise

    def _noted(self, channel_id: int):
        """A running tool is a sign of life even though nothing is being said: her events are the
        only thing that separates a long piece of work from a turn that died on its feet.

        `work_done` is the one frame that has to become WORDS. A background job ends in silence
        otherwise: she says she is on it, the job finishes, and nobody in the channel is ever told.
        Every other surface fires this turn; this one ignored the frame."""
        def note(kind: str, _frame: dict) -> None:
            self._progress[channel_id] = time.monotonic()
            if kind == "work_done":
                self._track(asyncio.create_task(self._say_work_done(channel_id)))
        return note

    def _track(self, task) -> None:
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _say_work_done(self, channel_id: int) -> None:
        """Her own summary of the finished job, posted where it was asked for.

        Attributed to whoever is holding the cards in that channel: `start_work` is withheld from a
        guest, so the person who started it is her person."""
        from kotoba.core import work_state

        from kotoba.discord.text import StreamingReply

        session = self.sessions.get(channel_id)
        channel = self.client.get_channel(channel_id)
        if session is None or channel is None:
            return
        # Claimed BEFORE the await: a deferred result emits the same frame, and both tasks used
        # to pass this check and then queue behind the session lock, announcing the job twice.
        if work_state.get(session.session_id)["announced"]:
            return
        work_state.mark_announced(session.session_id)
        # HER PERSON, never the last speaker. `start_work` is theirs alone, so the job is theirs — and the
        # card's asker moves to whoever wrote most recently, which had a guest being handed somebody else's job
        # summary. A DM has no guild and no member to look up, and that is where this used to give up
        # and leave the job ending in the silence it exists to break.
        owner = config.owner_id()
        if owner is None:
            return
        guild = getattr(channel, "guild", None)
        member = guild.get_member(owner) if guild is not None else None
        who = (authority.actor_from_member(member, owner=owner, guild_id=getattr(guild, "id", None))
               if member is not None else
               authority.Actor(user_id=owner, guild_id=getattr(guild, "id", None), display="",
                               handle="", is_owner=True, is_guild_admin=False, is_guild_owner=False))
        reply = StreamingReply(channel)
        try:
            await session.ask(work_state.WORK_DONE, who, on_text=reply.feed)
            await reply.flush(final=True)
        except Exception:
            log.warning("could not tell the channel the job finished", exc_info=True)

    async def _feed(self, speech, outgoing) -> None:
        """One sentence at a time, so the expressive engine can start synthesising the first while
        she is still writing the last."""
        while True:
            chunk = await outgoing.get()
            if chunk is None:
                return
            await speech.feed(chunk)

    async def _session_for(self, channel, who: authority.Actor):
        """The spoken path's own session, and it has to keep the card object like the written one.

        Built inline, the card was unreachable afterwards: whoever first woke her by voice stayed the
        approver of every card in that text channel for the life of the process."""
        existing = self.sessions.get(channel.id)
        if existing is not None:
            return existing
        asking = cards.DiscordCards(channel, asker=who.user_id, owner=config.owner_id())
        session = bridge.ChannelSession(
            self.engine, channel.guild.id if channel.guild else None, channel.id, bot=self.client,
            surface=self, on_event=self._noted(channel.id), ask=asking.ask,
            on_turn=lambda actor: setattr(asking, "asker", actor.user_id))
        session.start()
        self.sessions[channel.id] = session
        self.askers[channel.id] = asking
        return session

    async def close(self) -> None:
        state.set_runtime_live(False)
        for gid in list(self.rooms):
            await self.leave_voice(gid)
        for session in list(self.sessions.values()):
            await session.close()
        self.sessions.clear()
        if not self.client.is_closed():
            await self.client.close()
