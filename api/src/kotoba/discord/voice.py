"""Her in a voice channel: who is talking, whether they said her name, and what she says back.

Three measured facts. Discord sends NOTHING during silence, so the end of a sentence is noticed here
on a wall clock and committed by hand — feeding the transcriber manufactured zeroes until it decided
for itself was slower and billed for them. A transcriber is opened per speaker and kept open while
they are in the room, because a handshake costs a fifth of a second out of the front of whatever
they say next. And `AudioSource.read` returning `b""` ends playback and cuts her off mid-sentence,
so while a turn is live an empty queue means silence.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import re
import time
import unicodedata

from kotoba.discord import audio as audio_mod
from kotoba.discord import voice_recv

log = logging.getLogger("kotoba.discord")

SILENCE_FRAME = b"\x00" * audio_mod.STT_FRAME_BYTES
DISCORD_SILENCE = b"\x00" * audio_mod.DISCORD_FRAME

PUMP_SECONDS = 0.25        # the cadence her ears expect, and the clock the filler runs on
SPEECH_GAP = 0.15          # Discord sends every 20 ms, so this much nothing means they stopped
COMMIT_WAIT = 1.0          # measured: half a second faster than letting the server hear the silence
COMMIT_WAIT_UNSURE = 2.0   # ...but a pause mid-sentence must not be read as the end of one
SHORT_SPEECH = 1.2         # under this she probably has not finished the thought
MIN_COMMIT = 0.35          # ElevenLabs refuses a commit with less than 0.3 s it has not seen yet
KEEPALIVE = 10.0           # an idle transcriber is closed at fifteen or sixteen seconds
IDLE_LEAVE = 300.0         # she does not sit in an empty channel
FOLLOW_UP = 8.0            # called and asked nothing, the answer to "dime" needs no name

# A last word that is grammatically waiting for the next one. Ending on any of these is the shape of
# somebody thinking, not somebody done.
TRAILING_OFF = {
    "y", "e", "o", "u", "pero", "que", "porque", "si", "cuando", "como", "para", "de", "con",
    "eh", "este", "mmm", "pues", "osea", "and", "or", "but", "so", "the", "a", "to", "um", "uh",
}


_KANA = {
    "ア": "a", "イ": "i", "ウ": "u", "エ": "e", "オ": "o",
    "カ": "ka", "キ": "ki", "ク": "ku", "ケ": "ke", "コ": "ko",
    "サ": "sa", "シ": "shi", "ス": "su", "セ": "se", "ソ": "so",
    "タ": "ta", "チ": "chi", "ツ": "tsu", "テ": "te", "ト": "to",
    "ナ": "na", "ニ": "ni", "ヌ": "nu", "ネ": "ne", "ノ": "no",
    "ハ": "ha", "ヒ": "hi", "フ": "fu", "ヘ": "he", "ホ": "ho",
    "マ": "ma", "ミ": "mi", "ム": "mu", "メ": "me", "モ": "mo",
    "ヤ": "ya", "ユ": "yu", "ヨ": "yo",
    "ラ": "ra", "リ": "ri", "ル": "ru", "レ": "re", "ロ": "ro",
    "ワ": "wa", "ヲ": "o", "ン": "n",
    "ガ": "ga", "ギ": "gi", "グ": "gu", "ゲ": "ge", "ゴ": "go",
    "ザ": "za", "ジ": "ji", "ズ": "zu", "ゼ": "ze", "ゾ": "zo",
    "ダ": "da", "ヂ": "ji", "ヅ": "zu", "デ": "de", "ド": "do",
    "バ": "ba", "ビ": "bi", "ブ": "bu", "ベ": "be", "ボ": "bo",
    "パ": "pa", "ピ": "pi", "プ": "pu", "ペ": "pe", "ポ": "po",
    "ー": "", "ッ": "",
}


def romanise(text: str) -> str:
    """Katakana out to plain letters, so a name written in one script matches the same name in the
    other.

    Not decoration: transcribers pick a script from what they think they heard, and hers is a real
    Japanese word — asked in Spanish, ElevenLabs wrote “コトバオラ” for “Kotoba, hola” and nothing
    matched anything. Hiragana rides along because the same audio can land in either.
    """
    out = []
    for ch in text:
        if ch in _KANA:
            out.append(_KANA[ch])
        elif "\u3041" <= ch <= "\u3096":       # hiragana shares the layout one block down
            out.append(_KANA.get(chr(ord(ch) + 0x60), ch))
        else:
            out.append(ch)
    return "".join(out)


def normalise(text: str) -> str:
    plain = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in plain if unicodedata.category(c) != "Mn")


def match_key(word: str) -> str:
    """The form a word is COMPARED in. Never the form it is passed on in.

    Romanising the whole utterance would hand her `chiょto待te` for a real Japanese sentence — the
    map is crude on purpose, good enough for a name and wrong for prose. So it touches the
    comparison and nothing else."""
    return normalise(romanise(unicodedata.normalize("NFC", word)))


def strip_marks(word: str) -> str:
    return "".join(c for c in word if not unicodedata.category(c).startswith("P")).strip()


def tokenise(text: str) -> list[str]:
    """Split on ANY punctuation, not the pieces of it one language happens to use.

    `。、！` end a clause as surely as `.,!` do, and a tokeniser that only knows the ASCII ones glues
    a name to the word before it — so “ちょっと待って、Kotoba” never matched anything.

    Splitting only, never folding: every caller re-normalises through `match_key` before comparing,
    and folding here reached further than comparison. It de-accented what she is HANDED, so a
    question arrived as “que hora es en japon”, and it stripped the dakuten before `match_key` could
    romanise — leaving her own name to be matched as `kotoha`, inside the fuzzy margin by luck."""
    cut = "".join(" " if unicodedata.category(c).startswith("P") else c for c in text or "")
    return cut.split()


def edits(a: str, b: str) -> int:
    """Plain Levenshtein. Small alphabet, short words, no dependency worth adding for it."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = row
    return prev[-1]


def sounds_like(word: str, name: str, *, extra: int = 0) -> bool:
    """Close enough to be her name as a transcriber would have written it.

    A name is exactly the kind of word transcription gets wrong: it has no dictionary to fall back
    on, so it comes out spelled how it sounded — “Cotoba”, “Kotova”, “Kotoua”. Matched exactly, she
    never wakes for half the times she was called, and the person concludes she is broken.

    The tolerance scales with length so a short name cannot be reached from an ordinary word.
    """
    if not word or not name:
        return False
    if word == name:
        return True
    allowed = (2 if len(name) >= 6 else (1 if len(name) >= 4 else 0)) + extra
    if abs(len(word) - len(name)) > allowed:
        return False
    return edits(word, name) <= allowed


HEAD_WORDS = 2       # "Kotoba, ..." and "oye Kotoba, ..."
SHORT_UTTERANCE = 5  # a whole short line is addressed to somebody, wherever the name sits in it

def said_her_name(text: str, names: list[str]) -> str | None:
    """Whether this was said TO her, and what remains once her name is taken out.

    Position inside the utterance, never grammar. Languages disagree about where a vocative goes —
    Spanish alone puts it at both ends ("Kotoba, ¿qué hora es?" and "¿qué hora es, Kotoba?"), and a
    rule written for one word order silently ignores half the people who use it.

    So: her name at the FRONT, at the END, or anywhere inside a short line. What stays out is her
    name buried mid-sentence in a long one, which is a room TALKING ABOUT her.
    """
    wanted = {match_key(n) for n in names if n}
    if not wanted:
        return None
    # The DECISION is per clause: the transcriber glues sentences together, so a name opening the
    # third one sat "mid-sentence" and read as people talking about her. What she is HANDED is the
    # whole utterance minus the name — "¡Kotoba! ayúdame" is one request, not a name and a fragment.
    if not any(_addressed_in(clause, wanted) for clause in re.split(r"[.!?¿¡…]+", text)):
        return None
    return _without_name(text, wanted)


def _addressed_in(clause: str, wanted: set[str]) -> bool:
    words = tokenise(clause)
    if not words:
        return False
    clean = [match_key(strip_marks(w)) for w in words]
    hits = [i for i, w in enumerate(clean) if any(sounds_like(w, n) for n in wanted)]
    if not hits:
        # "Coto va", "Otho va" — the transcriber hears two words where there is one name.
        for i in range(len(clean) - 1):
            if any(sounds_like(clean[i] + clean[i + 1], n) for n in wanted):
                hits = [i]
                break
    if not hits and _head_of(clause, clean, wanted):
        hits = [0]
    if not hits:
        return False
    at = hits[0]
    return (at < HEAD_WORDS
            or at >= len(clean) - 1
            or len(clean) <= SHORT_UTTERANCE)


def _vocative_head(clause: str) -> list[int]:
    """How many opening words are set off by a pause, or none.

    Calling somebody by name puts a pause after it, and a transcriber writes that pause as a comma,
    a dash, or the end of the sentence. Without the mark the same letters are an ordinary word
    opening an ordinary sentence — “toma esto”, “coloca eso ahí” — which is what makes it safe to be
    less strict about the spelling of the word that carries the mark."""
    parts = clause.strip().split()
    out = []
    for count in (1, 2):
        if len(parts) < count:
            break
        if len(parts) == count:                 # the whole clause is the name
            out.append(count)
        elif parts[count - 1].rstrip("\"'»)”").endswith((",", ";", ":", "-", "—", "–")):
            out.append(count)
    return out


def _head_of(clause: str, clean: list[str], wanted: set[str]) -> bool:
    """One more edit, but only for a name the punctuation says was used to address somebody.

    Measured over three hours of a real room: her name opening a sentence came back as “Toma”,
    “Boba”, “Cómo va”, “Tova”, “Au tora” — seventeen requests she never answered, and the people
    talking concluded she was deaf. The same tolerance without the pause reaches ordinary words."""
    if len(clean) < 2:
        return False
    for count in _vocative_head(clause):
        cand = clean[0] if count == 1 else clean[0] + clean[1]
        if not any(sounds_like(cand, n, extra=1) for n in wanted):
            continue
        # The extra edit is spent on a vocative, so there has to be something after it worth
        # answering. "Toto, Toto." is one word said twice — it reached her because a comma bought
        # the edit, and she woke three times in a row on it while the room asked her to stop.
        rest = clean[count:]
        if rest and all(any(sounds_like(w, n, extra=1) for n in wanted) for w in rest):
            continue
        return True
    return False


def _without_name(text: str, wanted: set[str]) -> str:
    words = tokenise(text)
    clean = [match_key(strip_marks(w)) for w in words]
    drop = {i for i, w in enumerate(clean) if any(sounds_like(w, n) for n in wanted)}
    # Only where NEITHER half is already the name: "es" + "kotoba" glues to within two edits of it
    # and swallowed the verb out of "¿qué hora es, Kotoba?".
    for i in range(len(clean) - 1):
        if i in drop or (i + 1) in drop:
            continue
        if any(sounds_like(clean[i] + clean[i + 1], n) for n in wanted):
            drop |= {i, i + 1}
    if not drop and len(clean) >= 2:
        opening = next((c for c in re.split(r"[.!?¿¡…]+", text) if c.strip()), text)
        for count in _vocative_head(opening):
            cand = clean[0] if count == 1 else clean[0] + clean[1]
            if any(sounds_like(cand, n, extra=1) for n in wanted):
                drop = set(range(count))
                break
    return " ".join(w for i, w in enumerate(words) if i not in drop).strip(" ,.")


def opens_with_her_name(text: str, names: list[str]) -> bool:
    """Strictly at the head. A partial is short by construction, so the whole-short-line rule would
    call any mention "addressed" — measured: six of sixteen partial rescues were her walking into
    somebody else's conversation."""
    wanted = {match_key(n) for n in names if n}
    if not wanted:
        return False
    for clause in re.split(r"[.!?¿¡…]+", text):
        clean = [match_key(strip_marks(w)) for w in tokenise(clause)][:HEAD_WORDS]
        if any(sounds_like(w, n) for w in clean for n in wanted):
            return True
    return False


class Speaker:
    """One person's audio, from Discord's packets to her transcriber."""

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        self.bridge = audio_mod.ToStt()
        self.stt = None
        self.pump = None
        self.sender = None
        # Frames wait here instead of being dropped while the transcriber is still connecting: the
        # handshake takes about a fifth of a second, which is where the first syllable of the name
        # was going. A queue also keeps them in order, which a task per frame never promised.
        self.outbox: asyncio.Queue = asyncio.Queue(maxsize=240)
        self.last_audio = 0.0
        self.last_sent = 0.0
        self.name_in_partial = False
        self.cut_her_off = False
        self.speaking = False
        self.spoke_seconds = 0.0
        self.last_partial = ""
        self.reopen_at = 0.0
        self.attention_until = 0.0


class Playback:
    """Her voice into the channel. Fed from the loop, read from the player thread — so the only
    thing crossing that line is a queue and a flag."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._done = False

    def push(self, frame: bytes) -> None:
        self._q.put(frame)

    def finish(self) -> None:
        self._done = True

    def abandon(self) -> None:
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._done = True

    def read(self) -> bytes:
        """3840 bytes, always, until the turn is genuinely over."""
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return b"" if self._done else DISCORD_SILENCE

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        return None


class VoiceRoom:
    """One voice channel she is sitting in."""

    def __init__(self, client, channel, *, names, on_turn, language: str = "",
                 on_empty=None) -> None:
        self.client = client
        self.language = language
        self.channel = channel
        self.names = list(names)
        self.on_turn = on_turn
        self.on_empty = on_empty
        self.vc = None
        self.receiver = None
        self.loop = asyncio.get_running_loop()
        self.speakers: dict[int, Speaker] = {}
        self.speaking_turn = 0
        self._closing = False
        self._watch = None
        # The turn belongs to the ROOM, not to the ear loop of whoever woke her. Run inside that
        # loop it could not be stopped by the person it was answering, and the filler that keeps
        # their transcriber alive was closing it underneath.
        self._turn: asyncio.Task | None = None
        self._speech = None
        self._turn_lock = asyncio.Lock()
        self._turn_ended_at = 0.0
        self._leaving_after_turn = False
        self._bg: set[asyncio.Task] = set()
        self._empty_since = 0.0

    def _track(self, task: asyncio.Task) -> asyncio.Task:
        """A task nobody holds can be collected mid-await; the loop only keeps a weak reference."""
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    async def join(self):
        cls = voice_recv.voice_client_class()
        self.vc = await self.channel.connect(cls=cls, timeout=30.0)
        self.vc.on_speaking = self._learned
        self.receiver = voice_recv.VoiceReceiver(self.vc, self._heard, candidates=self._humans)
        self.receiver.start()
        self._track(asyncio.create_task(self._warm()))
        await self._prime()
        self._watch = asyncio.create_task(self._counters())
        log.info("listening in %s", getattr(self.channel, "name", "?"))
        return self.vc

    async def _warm(self) -> None:
        from kotoba.discord.speak import warm_acks

        await warm_acks()

    async def _prime(self) -> None:
        """Announce her own speaking state once, so the gateway starts telling her about everyone
        else's.

        Discord holds those events back until a client has sent one — which a bot that only listens
        never does. Without them there is no ssrc-to-person map, the encryption has nothing to
        decrypt with, and every packet is dropped in silence.
        """
        try:
            from discord.gateway import SpeakingState

            await self.vc.ws.speak(SpeakingState.none)
        except Exception:
            log.debug("could not prime the speaking state", exc_info=True)

    async def _counters(self) -> None:
        """Where a silent pipe actually stopped. Every stage here can fail into nothing, and
        "she said nothing" looks the same from outside whichever one it was. At info it was 860
        lines a session, which is how a log stops being read."""
        while not self._closing and self.receiver is not None:
            await asyncio.sleep(5)
            log.debug("voice: decoded=%s dropped=%s speakers=%s ssrc_known=%s",
                      self.receiver.decoded, self.receiver.dropped,
                      len(self.speakers), len(self.receiver._ssrc))
            await self._check_empty()

    async def _check_empty(self) -> None:
        """Sitting alone in a channel bills a transcriber and a gateway for nobody."""
        now = time.monotonic()
        if not self.alone():
            self._empty_since = 0.0
            return
        if not self._empty_since:
            self._empty_since = now
            return
        if now - self._empty_since > IDLE_LEAVE and self.on_empty is not None:
            log.info("voice: nobody left in %s", getattr(self.channel, "name", "?"))
            self._empty_since = 0.0
            await self.on_empty()

    def _humans(self):
        """Everyone in the room the audio could belong to. The decryption picks the right one."""
        return [m.id for m in getattr(self.channel, "members", []) if not m.bot]

    def _learned(self, user_id: int, ssrc: int) -> None:
        log.info("voice: SPEAKING user=%s ssrc=%s", user_id, ssrc)
        if self.receiver is not None:
            self.receiver.learn(user_id, ssrc)

    def _heard(self, user_id: int, pcm: bytes) -> None:
        """Reader thread. Everything after this hop belongs to the loop."""
        self.loop.call_soon_threadsafe(self._on_pcm, user_id, pcm)

    def _on_pcm(self, user_id: int, pcm: bytes) -> None:
        if self._closing or user_id == getattr(self.client.user, "id", None):
            return
        speaker = self.speakers.get(user_id)
        if speaker is None:
            speaker = self.speakers[user_id] = Speaker(user_id)
            log.info("voice: first audio from %s, opening a transcriber", user_id)
            self._track(asyncio.create_task(self._open_ears(speaker)))
        speaker.last_audio = time.monotonic()
        speaker.spoke_seconds += len(pcm) / float(audio_mod.DISCORD_FRAME) * 0.02
        for frame in speaker.bridge.feed(pcm):
            self._queue(speaker, frame)

    def _queue(self, speaker: Speaker, frame: bytes) -> None:
        try:
            speaker.outbox.put_nowait(frame)
        except asyncio.QueueFull:
            # A minute of backlog means the socket is gone, and the newest audio is the useful one.
            try:
                speaker.outbox.get_nowait()
                speaker.outbox.put_nowait(frame)
            except Exception:
                pass

    async def _send(self, speaker: Speaker, frame: bytes) -> None:
        if speaker.stt is None:
            return
        speaker.last_sent = time.monotonic()
        try:
            await speaker.stt.send_audio(frame)
        except Exception:
            log.debug("stt send failed for %s", speaker.user_id, exc_info=True)

    async def _open_ears(self, speaker: Speaker, *, restart: bool = True) -> None:
        """One transcriber per person, kept open while they are in the room. Closed between
        sentences it reopened forty-two per cent of the time within five seconds, and each handshake
        took a fifth of a second out of the front of whatever they said next — which is where the
        name goes. A frame of silence every ten seconds costs less than that."""
        try:
            # Inside the try, not above it: the discord extra can be installed without voice, and this
            # import then raised out of a detached task — she joined the room, heard nothing, said
            # nothing, and no line anywhere explained it.
            from kotoba.core.voice.stt import SttClient

            client = SttClient(language_code=self.language or None,
                               keyterms=self.names,
                               filter_background_audio=True)
            await client.connect()
        except Exception as exc:
            log.warning("could not open a transcriber: %s", exc)
            if restart:
                self.speakers.pop(speaker.user_id, None)
            return
        log.info("voice: transcriber open for %s", speaker.user_id)
        speaker.stt = client
        speaker.last_sent = time.monotonic()
        if restart:
            speaker.sender = self._track(asyncio.create_task(self._drain_outbox(speaker)))
            speaker.pump = self._track(asyncio.create_task(self._pump(speaker)))
        self._track(asyncio.create_task(self._listen(speaker)))

    async def _drain_outbox(self, speaker: Speaker) -> None:
        """One sender per person, so the frames that piled up during the handshake go out in order."""
        while True:
            frame = await speaker.outbox.get()
            if frame is None:
                return
            if speaker.stt is not None:
                await self._send(speaker, frame)

    async def _pump(self, speaker: Speaker) -> None:
        """The clock a stopped speaker does not provide.

        Discord sends nothing at all while somebody is quiet, so the end of a sentence has to be
        noticed here. It used to be noticed by ElevenLabs, by feeding it a second and a half of
        manufactured silence and waiting for it to decide — which was half a second slower and made
        more than a third of the billed audio zeroes. Detecting the gap locally and committing is
        the same thing without either cost.
        """
        try:
            while not self._closing:
                await asyncio.sleep(PUMP_SECONDS)
                if not self._still_here(speaker):
                    break
                try:
                    if speaker.stt is None:
                        await self._reopen(speaker)
                    else:
                        await self._tick(speaker)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # One bad round must not end her hearing of this person for the whole session:
                    # the loop is now what keeps the socket alive, not something it can walk away
                    # from. The next round reopens.
                    log.debug("voice: a round of the pump failed", exc_info=True)
                    if speaker.stt is not None:
                        await self._forget(speaker, speaker.stt)
        except asyncio.CancelledError:
            raise
        finally:
            await self._close_ears(speaker)

    def _still_here(self, speaker: Speaker) -> bool:
        """A transcriber held open for somebody who walked out bills for an empty chair."""
        members = getattr(self.channel, "members", None)
        if members is None:
            return True
        return any(getattr(m, "id", None) == speaker.user_id for m in members)

    async def _tick(self, speaker: Speaker) -> None:
        if speaker.stt is None:
            return
        now = time.monotonic()
        if now - speaker.last_audio < SPEECH_GAP:
            speaker.speaking = True
            return
        if speaker.speaking:
            # The framer holds whatever did not fill a whole frame, and that remainder is the end of
            # the sentence: five per cent of transcripts came back with the last word cut in half.
            speaker.speaking = False
            rest = speaker.bridge.drain()
            if rest:
                self._queue(speaker, rest)
            return
        if speaker.spoke_seconds >= MIN_COMMIT and now - speaker.last_audio >= self._commit_wait(speaker):
            speaker.spoke_seconds = 0.0
            await speaker.stt.commit()
            return
        if speaker.spoke_seconds < MIN_COMMIT and now - speaker.last_sent >= KEEPALIVE:
            await self._send(speaker, SILENCE_FRAME)

    def _commit_wait(self, speaker: Speaker) -> float:
        """How long a pause has to be before it is the end of the sentence rather than a breath.

        One second everywhere would split about one utterance in forty. The three shapes that get
        longer are the ones people actually pause inside: a half-finished short phrase, a sentence
        the transcriber has not written a word of yet, and one left hanging on a conjunction."""
        if speaker.spoke_seconds < SHORT_SPEECH or not speaker.last_partial:
            return COMMIT_WAIT_UNSURE
        tail = tokenise(speaker.last_partial)
        if tail and match_key(tail[-1]) in TRAILING_OFF:
            return COMMIT_WAIT_UNSURE
        return COMMIT_WAIT

    async def _reopen(self, speaker: Speaker) -> None:
        """Her ears stay open for as long as she is in the room: at four hundred openings a session
        the handshake was landing inside somebody's first syllable."""
        now = time.monotonic()
        if now < speaker.reopen_at:
            return
        speaker.reopen_at = now + 2.0
        await self._open_ears(speaker, restart=False)

    async def _listen(self, speaker: Speaker) -> None:
        from kotoba.core.voice.stt import SttCommitted, SttError, SttPartial

        client = speaker.stt
        try:
            async for event in client.events():
                # Partials are logged too. A name has no dictionary behind it, so the only way to
                # know how a transcriber writes THIS one is to read what it wrote.
                if isinstance(event, SttPartial) and event.text.strip():
                    partial = speaker.last_partial = event.text.strip()
                    log.info("voice: partial %r", partial)
                    # The name lives in the partials and the final rewrite loses it: measured,
                    # "¿Cotoba?" became "¿Qué hubo?" and "Toba." became "¿Qué haces?". Reading only
                    # the committed text throws away the clearest evidence she was called.
                    if opens_with_her_name(partial, self.names):
                        speaker.name_in_partial = True
                        # Her name IS the interrupt, and it is heard HERE — seconds before the
                        # committed text, because the transcriber waits out the silence and Discord
                        # adds its own delay. Waiting for the commit meant she talked over them for a
                        # whole sentence, and no list of words to say instead can buy that time back.
                        # Repeats need no guard of their own: once she is quiet the test below is
                        # false, and a flag standing in for it would disarm the interrupt whenever it
                        # got stuck.
                        if self._turn_live() or (self.vc is not None and self.vc.is_playing()):
                            log.info("voice: cut off by %s saying her name", speaker.user_id)
                            speaker.cut_her_off = True
                            await self._stop_turn()
                elif isinstance(event, SttError):
                    log.warning("voice: transcriber said %s: %s", event.code, event.message)
                elif isinstance(event, SttCommitted):
                    said = event.text.strip()
                    if said:
                        await self._committed(speaker, said)
                    else:
                        # An utterance ended having transcribed to nothing. The name it may have
                        # carried belongs to THAT utterance, so leaving the mark set makes her answer
                        # the next unrelated sentence this person says.
                        speaker.name_in_partial = False
                        speaker.cut_her_off = False
                        speaker.last_partial = ""
        except asyncio.CancelledError:
            raise
        except Exception:
            # NOT debug when it is a surprise. A turn that raises anywhere downstream of the
            # transcript surfaces here, and buried at debug it looked exactly like a transcriber
            # closing normally: she went green and stayed there with nothing to say why. Our own
            # close IS normal, and at warning it was 64 lines a session of nothing.
            if speaker.stt is not client or self._closing:
                log.debug("voice: the listen loop for %s ended", speaker.user_id, exc_info=True)
            else:
                log.warning("voice: the listen loop for %s raised", speaker.user_id, exc_info=True)
        if speaker.stt is client and not self._closing:
            # The socket died between the name and the sentence it belonged to, and the reopened
            # one is a new utterance: a mark kept across that gap answers the wrong words.
            await self._forget(speaker, client)     # the pump opens another; her ears are not optional

    async def _committed(self, speaker: Speaker, text: str) -> None:
        log.info("voice: heard %r from %s (names=%s)", text, speaker.user_id, self.names)
        speaker.last_partial = ""
        speaker.spoke_seconds = 0.0
        asked = said_her_name(text, self.names)
        if asked is None and speaker.name_in_partial:
            log.info("voice: the name was in a partial and the final rewrote it away")
            asked = text
        speaker.name_in_partial = False
        cut, speaker.cut_her_off = speaker.cut_her_off, False
        if asked is None and time.monotonic() < speaker.attention_until:
            asked = text        # she just asked them to go on; making them say the name again is rude
        speaker.attention_until = 0.0
        if asked is None:
            log.info("voice: %r was not addressed to me", text)
            return
        if not asked:
            # Her name and nothing else. Said OVER her it is the whole message — she has already been
            # cut off by the partial, and answering "dime" is more talking at somebody who wanted less.
            log.info("voice: called by %s with no question", speaker.user_id)
            if cut:
                return
            if self._turn_live() or (self.vc is not None and self.vc.is_playing()):
                await self._stop_turn()
                return
            if await self._acknowledge():
                speaker.attention_until = time.monotonic() + FOLLOW_UP
                return
            asked = "?"     # no clip ready, so answering badly still beats answering not at all
        log.info("woken by %s: %r", speaker.user_id, asked)
        await self._start_turn(speaker.user_id, asked)

    async def _acknowledge(self) -> bool:
        from kotoba.discord.speak import Speech, an_ack

        if self.vc is None or self._turn_live() or not an_ack():
            return False
        try:
            speech = Speech(self.vc)
            await speech.start()
            await speech.finish()
            return True
        except Exception:
            log.debug("could not answer with a noise", exc_info=True)
            return False

    def _turn_live(self) -> bool:
        return self._turn is not None and not self._turn.done()

    async def _start_turn(self, user_id: int, text: str) -> None:
        """Whoever says her name gets her, now. She has one voice in that room, so a second waker
        replaces the first rather than queueing behind it — a queued answer arrives after the room
        has moved on, and reads as her malfunctioning rather than as somebody interrupting."""
        from kotoba.discord.speak import Speech

        async with self._turn_lock:
            if self._closing or self.vc is None:
                return
            await self._stop_turn(held=True)
            self.speaking_turn += 1
            speech = self._speech = Speech(self.vc, generation=self.speaking_turn)
            task = self._turn = asyncio.create_task(
                self.on_turn(self, user_id, text, self.speaking_turn, speech))
            task.add_done_callback(self._turn_done)

    def _turn_done(self, task: asyncio.Task) -> None:
        self._turn_ended_at = time.monotonic()
        if self._turn is task:
            self._turn = None
            self._speech = None
        if not task.cancelled() and task.exception() is not None:
            log.warning("voice: turn died", exc_info=task.exception())
        if self._leaving_after_turn and self.on_empty is not None:
            self._leaving_after_turn = False
            self._track(asyncio.create_task(self._depart()))

    async def _stop_turn(self, *, held: bool = False) -> None:
        """Her mouth closes BEFORE anything is awaited. Cancelling the turn first would leave the
        queued audio playing for as long as the loop takes to tear down, which is seconds."""
        from kotoba.core.voice.config import own_cancellation_swallowed

        if not held:
            async with self._turn_lock:
                await self._stop_turn(held=True)
                return
        speech, task = self._speech, self._turn
        self._speech = None
        if speech is not None:
            speech.kill()
        # Her mouth outlives her turn: generation ends while the audio is still draining, and by then
        # both handles above are already None — so this killed nothing and "cállate" was answered
        # instead of obeyed. The player is the floor, and it is the thing they can actually hear.
        vc = self.vc
        if vc is not None and vc.is_playing():
            vc.stop()
        if task is not None and task is not asyncio.current_task() and not task.done():
            self._turn = None
            task.cancel()
            try:
                await task
            except BaseException:
                if own_cancellation_swallowed():
                    raise
        self._turn_ended_at = time.monotonic()

    def depart_after_turn(self) -> None:
        """Asked to leave from inside her own turn, she has not said so yet — the tool result is
        still on its way to the model. Going now cuts off the goodbye she is about to speak."""
        self._leaving_after_turn = True

    async def _depart(self) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            vc = self.vc
            if vc is None or not vc.is_playing():
                break
            await asyncio.sleep(0.25)
        if self.on_empty is not None:
            await self.on_empty()

    async def _forget(self, speaker: Speaker, client) -> None:
        if speaker.stt is client:
            speaker.stt = None
        speaker.name_in_partial = False
        speaker.cut_her_off = False
        try:
            await client.close()
        except Exception:
            pass

    async def _close_ears(self, speaker: Speaker) -> None:
        client, speaker.stt = speaker.stt, None
        self.speakers.pop(speaker.user_id, None)
        try:
            speaker.outbox.put_nowait(None)     # the sender is parked on a get that never returns
        except Exception:
            pass
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass

    def alone(self) -> bool:
        members = [m for m in getattr(self.channel, "members", []) if not m.bot]
        return not members

    async def leave(self) -> None:
        self._closing = True
        await self._stop_turn()
        if self._watch is not None:
            self._watch.cancel()
        if self.receiver is not None:
            self.receiver.stop()
        for speaker in list(self.speakers.values()):
            if speaker.pump is not None:
                speaker.pump.cancel()
            await self._close_ears(speaker)
        for task in list(self._bg):
            task.cancel()
        if self.vc is not None:
            try:
                await self.vc.disconnect(force=True)
            except Exception:
                pass
        self.vc = None
