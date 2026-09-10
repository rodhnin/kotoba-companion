"""Full-duplex voice session: browser WS <-> local backend <-> ElevenLabs (outbound only).

One socket per session: binary PCM-16k mic frames and JSON controls in, JSON events and TTS audio
out. Emotion and task events stay on the SSE channel; this socket never carries them.

TTS is synthesized in SEGMENTS because a stream nobody feeds dies on its own and tool runs outlast
it. SEGMENT_IDLE_SECS must stay under whichever death applies -- the fast engine's socket closes
after TTS_INACTIVITY_TIMEOUT with no input, the expressive REST path after its 30s receive timeout -- and under the
bound that really pins it: closing a segment emits `audio_end`, which reopens the client's
microphone, so this budget is also how long the mic stays shut during a tool run."""
from __future__ import annotations

import asyncio
import json
import logging
import re

from kotoba.core import stream as sse
from kotoba.core import turns
from kotoba.core.context import is_trigger_sentinel, load_context
from kotoba.core.loop import agentic_loop
from kotoba.core.memory import extract_and_save_memory
from kotoba.core.session_state import is_muted, set_muted
from kotoba.core.voice.config import (
    VoiceAuthError,
    VoiceError,
    VoiceQuotaError,
    VoiceStreamClosed,
    default_voice_id,
    own_cancellation_swallowed,
    stt_filter_background_audio,
    stt_language_for,
)
from kotoba.core.voice.stt import SttClient, SttCommitted, SttError, SttPartial
from kotoba.core.voice.tts import create_tts_client as TtsClient
from kotoba.models.schemas import ChatRequest

log = logging.getLogger("kotoba.voice.session")

AUDIO_IN = {"format": "pcm_16000", "sample_rate": 16000, "encoding": "s16le"}
# Raw s16le plays gapless via Web Audio and feeds lip-sync directly; 24 kHz is the best PCM rate below Pro-only 44.1k.
TTS_OUTPUT_FORMAT = "pcm_24000"
AUDIO_OUT = {"format": TTS_OUTPUT_FORMAT, "sample_rate": 24000, "encoding": "s16le"}

SEGMENT_IDLE_SECS = 8.0
TTS_INACTIVITY_TIMEOUT = 60
SEGMENT_DEATH_RETRIES = 1

MIC_HOLD_POLL_SECS = 2.0

PARTIAL_HOT_SECS = 2.0
HOLD_DEFER_CAP_SECS = 10.0

# Phantom-commit threshold: below it, a commit arriving mid-playback is echo or an EL hallucination.
TRIVIAL_COMMIT_MAX_ALPHA = 12

# Noise cost cap: VAD needs 1.5s of silence per commit, so no person sustains one turn every 5s for a minute.
VOICE_TURN_BURST = 12
VOICE_TURN_WINDOW_SECS = 60.0
NOISE_LATCH_WINDOWS = 2

_STT_QUICK_DEATH_SECS = 5.0
_STT_MAX_STRIKES = 3


def _normalize(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).lower()


def _unspoken_tail(tts) -> str:
    """What a dead client accepted but provably never delivered, for re-feeding to a fresh segment.
    Only the REST engine can answer — its text stays local until a request's audio is read back —
    so engines without the accounting (text down the flash WS is gone the moment it is sent)
    recover nothing: that is the honest floor, not an oversight."""
    drain = getattr(tts, "unspoken_tail", None)
    return drain() if drain is not None else ""


class _Segment:
    """One live TTS stream + the task forwarding its audio frames to the browser."""

    def __init__(self, tts, reader: asyncio.Task, turn_no: int) -> None:
        self.tts = tts
        self.reader = reader
        self.turn_no = turn_no


class VoiceSession:
    def __init__(self, ws, session_id: str, *, db, soul_patterns: dict, mcp=None) -> None:
        self.ws = ws
        self.session_id = session_id
        self.db = db
        self.soul_patterns = soul_patterns
        self.mcp = mcp
        self._stt: SttClient | None = None
        self._stt_pump_task: asyncio.Task | None = None
        self._stt_failed = False
        self._tts_failed = False
        self._turn_task: asyncio.Task | None = None
        self._turn_no = 0
        self._last_commit_text = ""
        self._audio_active = False
        self._audio_open = False
        self._segment_retries = 0
        self._last_send = 0.0
        self._stt_strikes = 0
        self._closing = False
        self._mute_notified = False
        self._voice_turns: list[float] = []
        self._noise_since: float | None = None
        self._noise_last = 0.0
        self._noise_latched = False
        self._mic_hold = False
        self._mic_yielded = False
        self._yielded_cards: frozenset[str] = frozenset()
        self._last_partial_at = 0.0
        self._hold_deferred_at: float | None = None
        self._mic_watch: asyncio.Task | None = None
        self._bracket_closed = asyncio.Event()
        self._send_lock = asyncio.Lock()
        # Strong refs: a bare create_task can be garbage-collected while it is still running.
        self._bg: set[asyncio.Task] = set()

    async def run(self) -> None:
        await self.ws.accept()
        set_muted(self.session_id, False)
        await self._send_json({
            "type": "ready", "session_id": self.session_id,
            "audio_in": AUDIO_IN, "audio_out": AUDIO_OUT,
        })
        self._mic_watch = asyncio.create_task(self._watch_cards())
        try:
            await self._receive_pump()
        finally:
            # The owner dies BEFORE the last release, or it could re-hold behind our back.
            watch, self._mic_watch = self._mic_watch, None
            if watch is not None:
                watch.cancel()
                try:
                    await watch
                except BaseException:
                    pass
            await self._release_mic(self._turn_no)
            self._closing = True
            set_muted(self.session_id, False)
            for task in (self._stt_pump_task, self._turn_task):
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await task
                    except BaseException:
                        pass
            if self._stt is not None:
                await self._stt.close()

    async def _receive_pump(self) -> None:
        while True:
            msg = await self.ws.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            data = msg.get("bytes")
            if data:
                await self._on_audio(data)
            elif msg.get("text"):
                await self._on_control(msg["text"])

    async def _on_audio(self, pcm: bytes) -> None:
        """Muted means the room does not leave this machine.

        The cut is here, ahead of the lazy _ensure_stt: while muted no STT session opens and not one
        byte reaches ElevenLabs. Enforcing it later would still have the room transcribed by a third
        party. It is deliberately not a close-on-mute — mute is a hot toggle and re-handshaking on
        each one feeds _stt_strikes, whose latch kills the mic for the whole call.

        A muted CLIENT sends no audio at all, so a frame arriving here IS the two sides disagreeing:
        say so once per episode, and the client re-declares its state. A latched mic takes the same
        cut in silence — the fatal frame that latched it has already said everything, once."""
        if is_muted(self.session_id):
            if not self._mute_notified:
                self._mute_notified = True
                await self._send_json({"type": "skipped", "reason": "muted"})
            return
        if self._noise_latched:
            return
        stt = await self._ensure_stt()
        if stt is None:
            return
        try:
            await stt.send_audio(pcm)
        except VoiceStreamClosed:
            pass

    async def _on_control(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            msg = None
        if not isinstance(msg, dict):
            await self._send_error("bad_message", "control frames must be JSON objects", fatal=False)
            return
        kind = msg.get("type")
        if kind == "text":
            text = str(msg.get("text") or "").strip()
            if text:
                self._note_human()
                await self._start_turn(text, typed=True)
        elif kind == "commit":
            stt = await self._ensure_stt()
            if stt is not None:
                try:
                    await stt.commit()
                except (VoiceError, VoiceStreamClosed) as exc:
                    await self._send_error("stt_commit", str(exc), fatal=False)
        elif kind == "interrupt":
            raw_turn = msg.get("turn")
            await self._barge_in(int(raw_turn) if isinstance(raw_turn, (int, float)) else None)
        elif kind == "mute":
            muted = bool(msg.get("muted"))
            set_muted(self.session_id, muted)
            if not muted:
                self._mute_notified = False
                self._clear_noise_latch()
        else:
            await self._send_error("bad_message", f"unknown control type: {kind!r}", fatal=False)

    async def _ensure_stt(self) -> SttClient | None:
        """Lazy: the EL STT session opens on the first mic frame, so typed-only sessions never need one.
        A muted session never opens one at all — a transcription session with nothing to transcribe is
        an open socket to a third party that exists only to be forgotten about. Neither does a latched
        one, which is what keeps a manual `commit` control from reopening the mic the room just lost.

        The soul read is a language PREFERENCE, not a precondition, and this is the receive pump: an
        exception here unwinds run() and closes the whole socket, so one locked-database blip on the
        first mic frame was a dropped call. It degrades to EL auto-detection instead."""
        if self._stt_failed or self._noise_latched:
            return None
        if self._stt is not None:
            return self._stt
        if is_muted(self.session_id):
            return None
        soul = None
        if self.db is not None:
            try:
                soul = await self.db.fetch_soul_config()
            except Exception:
                log.warning("soul config unreadable for %s — STT opens with defaults", self.session_id)
        client = SttClient(
            language_code=stt_language_for((soul or {}).get("language")) or None,
            filter_background_audio=stt_filter_background_audio(),
        )
        try:
            await client.connect()
        except VoiceAuthError as exc:
            self._stt_failed = True
            # Same split _pump_audio draws: the browser has its own sentence for an exhausted quota.
            code = "quota_exceeded" if isinstance(exc, VoiceQuotaError) else "stt_auth"
            await self._send_error(code, str(exc), fatal=True)
            return None
        except VoiceError as exc:
            self._stt_failed = True
            await self._send_error("stt_connect", str(exc), fatal=True)
            return None
        self._stt = client
        self._stt_pump_task = asyncio.create_task(self._stt_pump())
        return client

    async def _stt_pump(self) -> None:
        opened = asyncio.get_running_loop().time()
        try:
            async for event in self._stt.events():
                if isinstance(event, SttPartial):
                    if event.text and not is_muted(self.session_id):
                        self._last_partial_at = asyncio.get_running_loop().time()
                        await self._send_json({"type": "partial", "text": event.text})
                elif isinstance(event, SttCommitted):
                    self._last_partial_at = 0.0
                    text = event.text.strip()
                    if text and self._is_phantom_commit(text):
                        log.info(
                            "ignoring phantom commit during playback for %s: %r", self.session_id, text
                        )
                    elif text:
                        refusal = await self._voice_turn_refusal()
                        if refusal:
                            log.info("dropping voice commit for %s (%s)", self.session_id, refusal)
                            await self._send_json({"type": "skipped", "reason": refusal})
                            continue
                        self._last_commit_text = text
                        await self._send_json(
                            {"type": "committed", "text": event.text, "turn": self._turn_no + 1}
                        )
                        await self._start_turn(event.text, typed=False)
                elif isinstance(event, SttError):
                    if event.code == "commit_throttled":
                        await self._note_throttle()
                    await self._send_error(event.code, event.message, fatal=event.fatal)
        except asyncio.CancelledError:
            raise
        except (VoiceError, VoiceStreamClosed) as exc:
            log.debug("STT session dropped for %s: %s", self.session_id, exc)
        finally:
            if not self._closing:
                lived = asyncio.get_running_loop().time() - opened
                self._stt_strikes = self._stt_strikes + 1 if lived < _STT_QUICK_DEATH_SECS else 0
                stt, self._stt = self._stt, None
                self._stt_pump_task = None
                if stt is not None:
                    await stt.close()
                if self._stt_strikes >= _STT_MAX_STRIKES:
                    self._stt_failed = True
                    await self._send_error(
                        "stt_closed", "speech recognition keeps dropping — mic disabled for this session",
                        fatal=True,
                    )

    async def _voice_turn_refusal(self) -> str | None:
        """Why this transcript must not become a turn, or None.

        Muted first: audio that left before the mute still comes back as a transcript, and pushing it
        to the browser as a user line is the leak in visible form. Then the burst cap, which counts
        only ACCEPTED voice turns, so it decays on its own once the room goes quiet and a phantom
        already dropped during playback never spends from it.

        Only a commit the CAP already refused feeds the escalation behind it, so a paced conversation
        can never reach the latch, and the accepted-turn list is left exactly as it was: nothing here
        decides anything about the twelve."""
        if is_muted(self.session_id):
            return "muted"
        if self._noise_latched:
            return "too_many_turns"
        now = asyncio.get_running_loop().time()
        self._voice_turns = [t for t in self._voice_turns if now - t < VOICE_TURN_WINDOW_SECS]
        if len(self._voice_turns) >= VOICE_TURN_BURST:
            if self._note_saturation(now):
                await self._latch_noise()
            return "too_many_turns"
        self._voice_turns.append(now)
        return None

    def _note_saturation(self, now: float) -> bool:
        """Fold one over-cap commit into the current noise EPISODE; True when it has just crossed.

        An episode is a run of over-cap commits with no whole QUIET window between them, and it
        crosses once it has lasted NOISE_LATCH_WINDOWS windows. Duration is the axis on purpose: a
        count could not tell three seconds of ElevenLabs re-committing a fragment from a television
        left on, and the first of those is already capped, decays on its own and costs nothing more.
        A window with none of them in it ends the episode — the same refusal _stt_strikes makes when
        it declines to count a session that died after healthy use."""
        if self._noise_since is None or now - self._noise_last > VOICE_TURN_WINDOW_SECS:
            self._noise_since = now
        self._noise_last = now
        return now - self._noise_since >= VOICE_TURN_WINDOW_SECS * NOISE_LATCH_WINDOWS

    async def _note_throttle(self) -> None:
        """ElevenLabs' own commit_throttled, folded into the same episode: it is the party that bills
        us saying the commits arrive too fast, which is the fact our own cap is a local guess at."""
        if self._note_saturation(asyncio.get_running_loop().time()):
            await self._latch_noise()

    def _note_human(self) -> None:
        """A hand on the keyboard restarts the episode. Deliberately one-sided: typing proves someone
        is there, which no room can fake, but it says nothing about their microphone — so it may
        restart the clock and must never clear a latch."""
        if not self._noise_latched:
            self._noise_since = None

    def _clear_noise_latch(self) -> None:
        """Unmuting is the way back. The latch, the episode and the burst window all start again, so
        the gesture buys a whole fresh minute rather than one commit — and it is the CLIENT's own
        declaration, never a state the server invents."""
        self._noise_latched = False
        self._noise_since = None
        self._noise_last = 0.0
        self._voice_turns = []

    async def _latch_noise(self) -> None:
        """Take the mic away for the rest of the call and say so once, fatally: this is a session-long
        state, not the transient `skipped` that each refused commit already carries.

        The burst cap is a RATE, and rates do not end — the window rolls, so a room that never went
        quiet kept buying twelve turns a minute, about 720 an hour on a call nobody hung up. One
        hallucinated commit costs a whole agentic turn, a memory extraction that can rewrite durable
        facts, two history rows and a TTS bill. The way back is a deliberate act: hang up and call
        again, or the mute button."""
        if self._noise_latched:
            return
        self._noise_latched = True
        log.warning(
            "noise latch for %s: %.0fs continuously at the burst cap — mic disabled for this session",
            self.session_id, VOICE_TURN_WINDOW_SECS * NOISE_LATCH_WINDOWS,
        )
        await self._send_error(
            "mic_noise",
            "speech kept arriving faster than anyone talks — mic disabled for this session",
            fatal=True,
        )

    def _is_phantom_commit(self, text: str) -> bool:
        """While audio streams, drop commits at or under TRIVIAL_COMMIT_MAX_ALPHA or that re-commit (a
        fragment of) the previous one: EL hallucinates short commits over silence and echo residue,
        and each would supersede the turn mid-sentence.

        Dropping them is safe because a REAL interruption never lands while audio is active: genuine
        overlap trips the client's local VAD and arrives as the `interrupt` control, before any
        commit. Partials never barge in either — the client gates the mic off at audio_start, so a
        partial arriving mid-playback is a straggler of pre-gate audio. The filter is armed by the
        audio bracket, not by a clock: length alone is no defence while idle ("yes" is a sentence)."""
        if not self._audio_active:
            return False
        norm = _normalize(text)
        if len(norm) <= TRIVIAL_COMMIT_MAX_ALPHA:
            return True
        prev = _normalize(self._last_commit_text)
        return bool(prev) and norm in prev

    async def _barge_in(self, turn: int | None = None) -> None:
        """Stop the turn the CLIENT meant, under the same lock _start_turn uses.

        `turn` is which turn the user was HEARING, and passing it is not optional: _start_turn
        advances self._turn_no across two awaits, so reading the counter here killed the wrong turn —
        an interrupt landing inside supersede had the client bury the incoming turn and discard all
        its audio; landing just after, it killed the brand-new turn. An older `turn` is a straggler.

        Behind a HELD bracket neither half applies: there may be no turn at all (the card shut the
        mic) and a hold never advances the client's scheduler. So the mic is yielded, the cards STAY,
        and the turn goes only if there still is one."""
        async with turns.lock(self.session_id):
            task = self._turn_task
            live = task is not None and not task.done()
            if self._mic_hold:
                self._yield_mic_to_user()
            elif live and turn is not None and turn != self._turn_no:
                log.debug("barge-in for turn %s ignored (current is %s)", turn, self._turn_no)
                return
            if not live:
                return
            await self._send_json({"type": "interrupted", "turn": self._turn_no})
            self._note_gate_forced_open()
            await turns.supersede(self.session_id)

    async def _start_turn(self, user_text: str, *, typed: bool) -> None:
        if is_muted(self.session_id) and not typed:
            await self._send_json({"type": "skipped", "reason": "muted"})
            return
        async with turns.lock(self.session_id):
            if self._turn_task is not None and not self._turn_task.done():
                await self._send_json({"type": "interrupted", "turn": self._turn_no})
                self._note_gate_forced_open()
            self._turn_no += 1
            turn_no = self._turn_no
            await turns.supersede(self.session_id)
            task = asyncio.create_task(self._run_turn(user_text, typed, turn_no))
            turns.register(self.session_id, task)
            self._turn_task = task

    async def _run_turn(self, user_text: str, typed: bool, turn_no: int) -> None:
        from kotoba.core import interaction, pending_reminder

        # Computed BEFORE the try: the finally reads them even if the first statement fails.
        is_trigger = is_trigger_sentinel(user_text)
        pending_rem = False
        reply = {"text": ""}
        try:
            interaction.note_turn(self.session_id)
            await self.db.ensure_session(self.session_id)
            pending_rem = pending_reminder.has_pending(self.session_id)
            if not is_trigger:
                await self.db.insert_turn(self.session_id, "user", user_text)

            connected_mcp = (
                [{"name": n, "tools": t} for n, t in getattr(self.mcp, "server_tools", {}).items() if t]
                if self.mcp is not None else None
            )
            request = ChatRequest(messages=[{"role": "user", "content": user_text}], session_id=self.session_id)
            input_items = await load_context(
                request, self.db, self.session_id,
                # NOT on a sentinel: announces arrive typed=True, and attachments.take() is one-shot — they ate uploads.
                consume_attachments=typed and not is_trigger,
                connected_mcp=connected_mcp,
                persisted=not is_trigger,
            )

            queue: asyncio.Queue = asyncio.Queue()

            async def produce() -> None:
                try:
                    reply["text"] = await agentic_loop(
                        input_items, self.session_id, self.db, queue, self.soul_patterns,
                        mcp=self.mcp, mode="companion",
                        # A typed turn on this WS is OURS — nothing re-fires it for going quiet, so an approval
                        # card may stay open; a sentinel that starts work would re-trigger forever on its finish.
                        channel="text" if typed else "voice",
                        exclude_tools=frozenset({"start_work", "delegate"}) if is_trigger else None,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("agentic_loop failed for voice session %s", self.session_id)
                    await queue.put(sse.crash_apology())
                finally:
                    await queue.put(sse.DONE_SENTINEL)

            producer = asyncio.create_task(produce())
            try:
                spoke = await self._pump_speech(queue, turn_no)
            finally:
                if not producer.done():
                    producer.cancel()
                try:
                    await producer
                except BaseException:
                    pass

            # A filter-emptied reply is silence yet persisted — session_search could quote what she never said.
            if not spoke and reply["text"].strip():
                fallback = sse.filtered_reply_fallback()
                await self._speak_line(fallback, turn_no)
                reply["text"] = fallback

            await self._send_json({"type": "turn_end", "turn": turn_no})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("voice turn failed outside the loop for session %s", self.session_id)
            try:
                await self._speak_line(sse.crash_apology(), turn_no)
            except Exception:
                pass
            await self._send_json({"type": "turn_end", "turn": turn_no})
        finally:
            # ORDER MATTERS — nothing may await above this line. Hanging up fires two cancels in one
            # tick, and the second lands inside this block: 53 of 225 hang-ups lost the announce flag
            # and the memory until everything unlosable happened before the first await.
            turns.clear(self.session_id, asyncio.current_task())
            if not is_trigger:
                try:
                    _mem = asyncio.create_task(extract_and_save_memory(user_text, self.db))
                    self._bg.add(_mem)
                    _mem.add_done_callback(self._bg.discard)
                except RuntimeError:
                    pass
            if reply["text"]:
                _save = asyncio.create_task(self._persist_reply(reply["text"], pending_rem))
                self._bg.add(_save)
                _save.add_done_callback(self._bg.discard)
                try:
                    await asyncio.shield(_save)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("could not persist the assistant turn for session %s", self.session_id)

    async def _persist_reply(self, text: str, pending_rem: bool) -> None:
        """Leaked tool-call syntax is junk in the history too, not just in the audio, and so is a block
        she said twice — the spoken chain drops it on the way out, but this text is what the loop
        returned, and history is re-sent on every later request. The rest of the spoken chain (URLs,
        fences, tags) stays, since she needs those in her own context. _run_turn shields its await of
        this write: a cancellation there tears the turn down, but the write still finishes and the
        reply stays in her history."""
        from kotoba.core import pending_reminder, work_state

        await self.db.insert_turn(
            self.session_id, "assistant", sse.collapse_repeats(sse.strip_tool_call_leaks(text))
        )
        snap = work_state.get(self.session_id)
        if snap["status"] in ("done", "failed") and not snap["announced"]:
            work_state.mark_announced(self.session_id)
        if pending_rem:
            pending_reminder.clear(self.session_id)

    async def _speak_line(self, line: str, turn_no: int) -> None:
        """Say one ready-made line (the emptied-reply fallback). Bypasses the filter chain on purpose — the
        line is ours, not model output, so there is nothing to strip and nothing that could empty it."""
        if not line:
            return
        seg = await self._open_segment(turn_no)
        if seg is None:
            return
        await self._send_json({"type": "assistant_text", "text": line, "turn": turn_no})
        try:
            await seg.tts.send_text(line)
        except (VoiceError, VoiceStreamClosed):
            await self._abort_segment(seg, end_audio=True)
            return
        await self._finish_segment(seg)

    async def _pump_speech(self, queue: asyncio.Queue, turn_no: int) -> bool:
        """Drain the reply into captions + TTS. Returns whether anything was actually SPOKEN, so the
        caller can recover a turn the filters emptied instead of leaving the user with silence.

        FLUSH_SENTINEL is the loop saying a tool is about to run, and only it can say so: two stages
        of the filter chain hold text that only LATER text releases, so her announcement otherwise
        sits there through the whole tool run and arrives glued to the result.

        SEGMENT_IDLE_SECS is spent by the TTS INPUT, never by queue traffic — the chain drops whole
        regions, so a busy queue can still be starving a stream; `_last_send` is stamped in
        _feed_segment, where text reaches the engine. The mic belongs to _watch_cards, not here."""
        leak_f, code_f = sse.ToolCallLeakFilter(), sse.CodeFenceFilter()
        url_f = sse.UrlFilter()
        tag_f, phrase_f = sse.AudioTagFilter(), sse.ForbiddenPhraseFilter()
        caption_f = sse.AudioTagFilter(keep_valid=False)
        self._segment_retries = 0
        seg: _Segment | None = None
        tts_dead = False
        spoke = False

        async def emit(spoken: str, seg: _Segment | None, tts_dead: bool) -> tuple[_Segment | None, bool]:
            nonlocal spoke
            spoke = True
            caption = caption_f.feed(spoken)
            if caption:
                await self._send_json({"type": "assistant_text", "text": caption, "turn": turn_no})
            return await self._feed_segment(seg, spoken, turn_no, tts_dead)

        loop = asyncio.get_running_loop()
        self._last_send = loop.time()
        try:
            while True:
                left = SEGMENT_IDLE_SECS - (loop.time() - self._last_send)
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=max(left, 0.0))
                except asyncio.TimeoutError:
                    seg = await self._finish_segment(seg)
                    self._last_send = loop.time()
                    continue
                if item is sse.DONE_SENTINEL:
                    break
                if item is sse.FLUSH_SENTINEL:
                    held = sse.flush_spoken(leak_f, code_f, url_f, tag_f, phrase_f, final=False)
                    if held:
                        seg, tts_dead = await emit(held, seg, tts_dead)
                    continue
                if not item:
                    continue
                spoken = phrase_f.feed(tag_f.feed(url_f.feed(code_f.feed(leak_f.feed(item)))))
                if spoken:
                    seg, tts_dead = await emit(spoken, seg, tts_dead)
            tail = sse.flush_spoken(leak_f, code_f, url_f, tag_f, phrase_f, final=True)
            if tail:
                seg, tts_dead = await emit(tail, seg, tts_dead)
            caption_tail = caption_f.flush()
            if caption_tail:
                await self._send_json({"type": "assistant_text", "text": caption_tail, "turn": turn_no})
            seg = await self._finish_segment(seg)
            return spoke
        except BaseException:
            await self._abort_segment(seg, end_audio=True)
            raise

    async def _feed_segment(
        self, seg: _Segment | None, text: str, turn_no: int, tts_dead: bool
    ) -> tuple[_Segment | None, bool]:
        """Feed one piece of spoken text, recovering from a dead segment along the way.

        One recovery contract covers BOTH engines, and it must test the READER, not just `send_text`.
        The WS engine fails by raising on send; the REST engine's send_text only queues, so its sole
        failure signal is a finished reader — hanging recovery off the raise alone meant the default
        engine could never recover: the reader died, later sentences synthesized into queues nobody
        read, and she went mute with captions still scrolling. The dead segment is aborted (closing
        its client stops background synthesis) and one fresh segment continues the turn, fed the dead
        client's drained `_unspoken_tail` first so nothing handed over is lost. Every exit closes the
        bracket via `_end_audio` — otherwise the mic stays gated for the rest of the call."""
        if tts_dead:
            return None, True
        if seg is not None and seg.reader.done():
            if self._segment_retries >= SEGMENT_DEATH_RETRIES:
                await self._abort_segment(seg, end_audio=True)
                return None, True
            await self._abort_segment(seg)
            text = _unspoken_tail(seg.tts) + text
            seg = None
            if not text.strip():
                await self._end_audio(turn_no)
                return None, False
            self._segment_retries += 1
        if seg is None:
            seg = await self._open_segment(turn_no)
            if seg is None:
                await self._end_audio(turn_no)
                return None, True
        try:
            await seg.tts.send_text(text)
        except (VoiceError, VoiceStreamClosed):
            await self._abort_segment(seg)
            seg = await self._open_segment(turn_no)
            if seg is None:
                await self._end_audio(turn_no)
                return None, True
            try:
                await seg.tts.send_text(text)
            except (VoiceError, VoiceStreamClosed) as exc:
                await self._abort_segment(seg, end_audio=True)
                await self._send_error("tts_stream", str(exc), fatal=False)
                return None, True
        self._last_send = asyncio.get_running_loop().time()
        return seg, False

    async def _open_segment(self, turn_no: int) -> _Segment | None:
        """Open a TTS stream for this turn, or None.

        Everything built here is a LOCAL until the _Segment is returned, so the try/except around the
        whole body is load-bearing: a cancellation mid-await would otherwise abandon an unowned client
        and reader — on the expressive engine, two tasks that poll until an end()/close() that never
        comes, plus an unclosed httpx client. Worse, the orphaned reader can still _send_bytes this
        turn's audio, which the next turn's audio_start re-arms the client to accept, so superseded
        speech plays as the new turn's. `_tts_failed` latches a rejected key or exhausted quota: the
        fatal was already sent, so no further segment opens for the rest of the call. The soul read
        is only a voice PREFERENCE and degrades to the default — raising escapes the caller's recovery."""
        if self._tts_failed:
            return None
        soul = None
        if self.db is not None:
            try:
                soul = await self.db.fetch_soul_config()
            except Exception:
                log.warning("soul config unreadable for %s — default voice", self.session_id)
        tts = TtsClient(
            voice_id=default_voice_id(dict(soul) if soul else None),
            output_format=TTS_OUTPUT_FORMAT,
            inactivity_timeout=TTS_INACTIVITY_TIMEOUT,
        )
        try:
            await tts.connect()
        except VoiceAuthError as exc:
            self._tts_failed = True
            await self._send_error("tts_auth", str(exc), fatal=True)
            return None
        except VoiceError as exc:
            await self._send_error("tts_unavailable", str(exc), fatal=False)
            return None
        except BaseException:
            await self._close_quietly(tts, None)
            raise
        reader: asyncio.Task | None = None
        try:
            reader = asyncio.create_task(self._segment_reader(tts, turn_no))
            self._audio_active = True
            self._audio_open = True
            self._mic_hold = False
            await self._send_json({"type": "audio_start", "turn": turn_no})
            return _Segment(tts, reader, turn_no)
        except BaseException:
            self._audio_active = False
            await self._close_quietly(tts, reader)
            raise

    async def _close_quietly(self, tts, reader: asyncio.Task | None) -> None:
        """Drop a half-built segment: cancel its reader, close its stream, swallow everything — except
        OUR OWN cancellation, which is noted, out-waited by the rest of the teardown, and re-raised at
        the end (config.own_cancellation_swallowed). Used while already unwinding, so an ordinary
        exception must still never raise over the exception in flight."""
        cancelled = False
        if reader is not None:
            reader.cancel()
            try:
                await reader
            except BaseException:
                cancelled = cancelled or own_cancellation_swallowed()
        try:
            await tts.close()
        except BaseException:
            cancelled = cancelled or own_cancellation_swallowed()
        if cancelled:
            raise asyncio.CancelledError

    async def _segment_reader(self, tts, turn_no: int) -> None:
        """Forward audio frames; classify how the stream died. A rejected key or exhausted quota is a
        voice that is NOT coming back this session — the mid-call twin of the fatal `stt_auth` at
        startup — so it latches `_tts_failed` and surfaces as its own fatal code instead of being
        flattened into the transient "cut out for a moment" tts_stream frame."""
        try:
            async for chunk in tts.audio_chunks():
                await self._send_bytes(chunk)
        except VoiceAuthError as exc:
            self._tts_failed = True
            code = "tts_quota" if isinstance(exc, VoiceQuotaError) else "tts_auth"
            await self._send_error(code, str(exc), fatal=True)
        except (VoiceError, VoiceStreamClosed) as exc:
            await self._send_error("tts_stream", str(exc), fatal=False)

    async def _finish_segment(self, seg: _Segment | None) -> _Segment | None:
        """Graceful close: end the input side, let the reader drain every remaining audio frame —
        and refund a death at the very end of the turn. A segment can die when no later feed will
        ever observe it — the last real sentence trapped in the dead client while the one reopen was
        spent on the filters' trailing whitespace — so a reader
        found dead here goes through the same recovery as mid-turn, and after any close the drained
        `_unspoken_tail` — including a final request that was lost and never heard — is re-spoken on
        one fresh segment, on the same retry budget. The `_audio_active` flag drops BEFORE the close
        await — close may re-raise a cancellation that landed in it, and a stuck `_audio_active`
        would phantom-drop the interrupting user's next short utterance."""
        while seg is not None:
            if seg.reader.done():
                seg, _ = await self._feed_segment(seg, "", seg.turn_no, False)
                continue
            try:
                await seg.tts.end()
                await seg.reader
            except (VoiceError, VoiceStreamClosed):
                pass
            finally:
                self._audio_active = False
                await seg.tts.close()
            turn_no = seg.turn_no
            tail = _unspoken_tail(seg.tts)
            if not tail.strip() or self._segment_retries >= SEGMENT_DEATH_RETRIES:
                await self._end_audio(turn_no)
                return None
            self._segment_retries += 1
            seg = await self._open_segment(turn_no)
            if seg is None:
                await self._end_audio(turn_no)
                return None
            try:
                await seg.tts.send_text(tail)
            except (VoiceError, VoiceStreamClosed):
                await self._abort_segment(seg, end_audio=True)
                return None
        return None

    async def _abort_segment(self, seg: _Segment | None, *, end_audio: bool = False) -> None:
        """Tear a segment down without draining it.

        `end_audio` must be True whenever no more audio is coming: the client clears `streaming` only
        on audio_end (or `interrupted`'s forceOpen), so an abort without one leaves the mic GATED for
        the rest of the call — frames into a 3-frame ring, STT deaf, no error shown. Only the retry
        path passes False, because a fresh segment is about to continue the audio.

        The broad excepts swallow the SEGMENT's death, never ours: a barge-in cancel eaten here
        resurrected the turn, synthesizing a reply nobody could hear while _barge_in held the lock.
        Our own cancellation is noted, the teardown finishes, and it re-raises at the end."""
        self._audio_active = False
        cancelled = False
        if seg is not None:
            seg.reader.cancel()
            try:
                await seg.reader
            except BaseException:
                cancelled = cancelled or own_cancellation_swallowed()
            try:
                await seg.tts.close()
            except BaseException:
                cancelled = cancelled or own_cancellation_swallowed()
        if end_audio:
            turn = seg.turn_no if seg is not None else self._turn_no
            await self._end_audio(turn)
        if cancelled:
            raise asyncio.CancelledError

    async def _watch_cards(self) -> None:
        """Own the microphone hold for the whole socket: THE CARD OUTLIVES THE TURN.

        Held from inside the reply pump the protection covered exactly one utterance, and every word
        after it bought a transcript, a turn and a reply (three `Hola.` lines, measured). So the
        predicate is which blocking cards are pending, re-asked on every wake — which also self-heals
        whatever closed the bracket last; `_bracket_closed` wakes it at once, so the mic is not live
        for a poll interval at each turn boundary. `_mic_yielded` steps the hold aside for the
        SNAPSHOT of cards the user spoke over, never for pending-ness. Cancellation is the only way
        out, and run()'s finally does it with an unconditional _release_mic behind it — a held
        bracket left unclosed on any exit path is a microphone shut for the rest of the call."""
        while True:
            pending = self._pending_cards()
            if not pending:
                self._mic_yielded = False
                self._hold_deferred_at = None
            elif self._mic_yielded and not pending <= self._yielded_cards:
                self._mic_yielded = False
            if pending and not self._mic_yielded:
                if not self._hold_can_wait():
                    await self._hold_mic(self._turn_no)
            else:
                await self._release_mic(self._turn_no)
            try:
                await asyncio.wait_for(self._bracket_closed.wait(), MIC_HOLD_POLL_SECS)
            except asyncio.TimeoutError:
                pass
            self._bracket_closed.clear()

    def _pending_cards(self) -> frozenset[str]:
        """The request_ids of the blocking cards still waiting on this session — identity, not just
        `has_pending`'s truth, because the yield must cover exactly the cards that voice stepped over.

        Through interaction's own door. This used to reach into `interaction._pending` and rebuild the
        set by hand: two spellings of one predicate, one of them reading a private dict, is the drift
        itself."""
        from kotoba.core import interaction

        return interaction.pending_request_ids(self.session_id)

    def _hold_can_wait(self) -> bool:
        """Whether a due hold may keep waiting because the user is provably mid-sentence.

        The predicate is the STT partial stream — EL saying it hears WORDS right now — not raw audio
        energy, so an idle room produces no partials and still gets its hold on the first poll. The
        deferral exists only at the hold's front edge: once the bracket is shut no frames reach STT,
        so this can never reopen a standing hold. PARTIAL_HOT_SECS must stay ABOVE the STT VAD's
        ~1.5s end-of-utterance silence, so a sentence that ends commits whole before the hold lands.
        HOLD_DEFER_CAP_SECS answers the noisy room: a television's continuous speech is partials that
        never go stale, so an uncapped deferral would be the unguarded mic back again. Capped, the
        exposure is a few seconds per run of hot speech, priced like any open mic."""
        now = asyncio.get_running_loop().time()
        if now - self._last_partial_at >= PARTIAL_HOT_SECS:
            self._hold_deferred_at = None
            return False
        if self._mic_hold or self._audio_open:
            return False
        if self._hold_deferred_at is None:
            self._hold_deferred_at = now
        return now - self._hold_deferred_at < HOLD_DEFER_CAP_SECS

    def _yield_mic_to_user(self) -> None:
        """The user's voice, behind a held bracket, takes the MICROPHONE back — and leaves the cards alone.

        Speaking over a card does NOT dismiss it: that reading was retired because the same gesture
        also throws away the sentence that caused it. A card ends the two ways a person can mean it —
        answered, or its own clock runs out (60s in local voice mode).

        So the hold yields for as long as THOSE cards live — a snapshot of what is pending now, so a
        card arriving later still holds the mic and the noise protection is suspended only for the
        cards they interrupted. The owner is WOKEN rather than left to its poll: behind a marked
        bracket the client's gate stays shut until the server closes it."""
        self._mic_yielded = True
        self._yielded_cards = self._pending_cards()
        self._bracket_closed.set()
        log.info("barge-in behind a held bracket: mic yielded, open cards kept")

    def _note_gate_forced_open(self) -> None:
        """`interrupted` re-opens the client's gate (lib/local-voice.ts), so no bracket is outstanding
        any more on either side and whoever owns the mic must decide again from nothing."""
        self._audio_open = False
        self._mic_hold = False
        self._bracket_closed.set()

    async def _hold_mic(self, turn_no: int) -> None:
        """Keep the microphone shut while a card waits on the human — with nothing behind it.

        The bracket, not the voice: `audio_start` is what the client gates on and what arms
        _is_phantom_commit, and it costs one JSON frame instead of a TTS request. A segment already
        streaming owns the bracket and is left alone, so the two can never both expect to close it;
        called on every poll, so whatever closed it last, the next poll reopens it.

        The `hold` marker is not decoration: an unmarked barge-in force-opens the client's gate and
        replays its pre-roll ring; marked, it cuts the turn without opening the mic. `turn_no` is the
        CURRENT turn — the client discards an audio_start naming a turn it has already buried."""
        if self._audio_open:
            return
        self._mic_hold = True
        self._audio_active = True
        self._audio_open = True
        await self._send_json({"type": "audio_start", "turn": turn_no, "hold": True})

    async def _release_mic(self, turn_no: int) -> None:
        """Hand the microphone back. A no-op unless the hold is what opened the bracket, so it can
        never close one a live segment is speaking through. Called only by the owner (_watch_cards)
        and by run()'s teardown — a turn releasing what it did not open is the defect this guards."""
        if not self._mic_hold:
            return
        self._mic_hold = False
        self._audio_active = False
        await self._end_audio(turn_no)

    async def _end_audio(self, turn_no: int) -> None:
        """Close the audio bracket iff an audio_start is still unanswered. The client keeps the mic
        gated from audio_start until audio_end (or the `interrupted` handler's forceOpen) — so every
        path on which no more audio is coming must land here, including the one where a reopen after
        a mid-turn abort fails: that path used to return without closing, and the gate stayed shut."""
        if self._audio_open:
            self._audio_open = False
            await self._send_json({"type": "audio_end", "turn": turn_no})
            self._bracket_closed.set()

    async def _send_json(self, payload: dict) -> None:
        if self._closing:
            return
        try:
            async with self._send_lock:
                await self.ws.send_text(json.dumps(payload))
        except Exception:
            self._closing = True

    async def _send_bytes(self, data: bytes) -> None:
        if self._closing:
            return
        try:
            async with self._send_lock:
                await self.ws.send_bytes(data)
        except Exception:
            self._closing = True

    async def _send_error(self, code: str, message: str, *, fatal: bool) -> None:
        await self._send_json({"type": "error", "code": code, "message": message, "fatal": fatal})
