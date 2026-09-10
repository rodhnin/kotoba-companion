"""Expressive TTS engine — eleven_v3 over REST /stream, one request per sentence BATCH, pipelined.

Same interface as tts.TtsClient, so the session drives either engine identically. v3 is REST-only
(403 on the stream-input WS) but outruns realtime, so prefetch plays gapless and no socket idles.

Error contract: send_text() only queues, so every failure surfaces through audio_chunks(). A request
that delivered no audio is retried once; one lost for good is skipped. Only a rejected key or
FAILURE_STREAK_LIMIT consecutive losses fail the stream, and _dispatch then stops launching so a
dead stream cannot burn quota. A byteless 200 past STALL_SUSPECT_SECS pauses the dispatcher; the
30s watchdog stays, since killing a merely slow request burns its retry."""
from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from typing import Any, AsyncIterator

import httpx

from kotoba.core.stream import ALLOWED_AUDIO_TAGS, is_wordless_sound
from kotoba.core.voice import config
from kotoba.core.voice.config import VoiceAuthError, VoiceError, VoiceQuotaError, VoiceStreamClosed
from kotoba.core.voice.sentences import SentenceSplitter

log = logging.getLogger("kotoba.voice.tts_rest")

RECV_TIMEOUT = 30.0
MAX_PARALLEL = 2
BATCH_CHARS = 250
RETRY_BACKOFF_SECS = 0.35
FAILURE_STREAK_LIMIT = 2
STALL_SUSPECT_SECS = 6.0
# Most of an error body a non-200 is allowed to occupy: enough for any ElevenLabs detail JSON,
# never the whole of whatever a broken proxy decides to send back.
ERROR_BODY_CAP = 2048
# Force out a complete-looking held sentence after this much stream silence — covers "announce, then
# run a tool" turns where no further text arrives to confirm the boundary.
HOLD_SECS = 0.4
_DONE = object()

_TAG_RE = re.compile(r"\[([^\[\]]{1,40})\]")
# One-shot vocalizations are never carried into later requests (she'd laugh at every sentence).
_ONE_SHOT_TAGS = frozenset({"laughs", "laughs softly", "giggles", "sighs", "gasps", "pause"})


class _Req:
    """One launched synthesis request: its exact text, its ordered audio queue, and enough state to
    answer two questions after the fact — did any of its bytes arrive from ElevenLabs (`delivered`,
    the stall-suspicion signal), and did its attempt finish (`done`, so a completed request can never
    hold the dispatcher). Whether its audio was ever HANDED TO THE READER is tracked separately, by
    position in `_flight`."""

    __slots__ = ("text", "q", "started", "delivered", "done", "odd")

    def __init__(self, text: str) -> None:
        self.text = text
        self.q: asyncio.Queue = asyncio.Queue()
        self.started = asyncio.get_running_loop().time()
        self.delivered = False
        self.done = False
        self.odd = False


class ExpressiveTtsClient:
    def __init__(
        self,
        voice_id: str | None = None,
        *,
        model_id: str = config.EXPRESSIVE_TTS_MODEL_ID,
        voice_settings: dict | None = None,
        output_format: str | None = None,
        recv_timeout: float = RECV_TIMEOUT,
        http_client: Any = None,
        **_ignored: Any,
    ) -> None:
        self.voice_id = voice_id or config.default_voice_id()
        self._url = config.tts_rest_url(self.voice_id, output_format=output_format)
        self._model_id = model_id
        self._voice_settings = voice_settings if voice_settings is not None else config.expressive_voice_settings()
        self._recv_timeout = recv_timeout
        self._client = http_client
        self._own_client = http_client is None
        self._splitter = SentenceSplitter()
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._order: asyncio.Queue = asyncio.Queue()
        self._flight: deque[_Req] = deque()
        self._undispatched: list[str] = []
        self._sem = asyncio.Semaphore(MAX_PARALLEL)
        self._tasks: set[asyncio.Task] = set()
        self._dispatcher: asyncio.Task | None = None
        self._watchdog: asyncio.Task | None = None
        self._last_feed = 0.0
        self._ended = False
        self._active_tag = ""
        self._failure_streak = 0
        self._failed = False

    async def connect(self) -> None:
        config.auth_headers()  # raise VoiceAuthError before opening a segment, like the WS engine
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=self._recv_timeout))
            self._own_client = True
        self._last_feed = asyncio.get_running_loop().time()
        if self._dispatcher is None:
            self._dispatcher = asyncio.create_task(self._dispatch())
            self._watchdog = asyncio.create_task(self._watch_hold())

    async def send_text(self, text: str) -> None:
        if self._ended:
            raise VoiceStreamClosed("TTS input already ended (end() was called)")
        if not text:
            return
        self._last_feed = asyncio.get_running_loop().time()
        for sentence in self._splitter.feed(text):
            await self._inbox.put(sentence)

    async def end(self) -> None:
        if self._ended:
            return
        self._ended = True
        rest = self._splitter.flush()
        if rest:
            await self._inbox.put(rest)
        await self._inbox.put(None)

    async def audio_chunks(self) -> AsyncIterator[bytes]:
        """Yield audio in launch order. A request's first yielded byte retires it and everything
        before it from `_flight` — from that byte on, its speech has been heard (or the skips before
        it overtaken), so none of that text is ever refundable again."""
        while True:
            try:
                req = await asyncio.wait_for(self._order.get(), self._recv_timeout)
            except asyncio.TimeoutError:
                raise VoiceError(f"TTS stream stalled (no request within {self._recv_timeout}s)")
            if req is None:
                return
            heard = False
            while True:
                try:
                    item = await asyncio.wait_for(req.q.get(), self._recv_timeout)
                except asyncio.TimeoutError:
                    raise VoiceError(f"TTS stream stalled (no audio within {self._recv_timeout}s)")
                if item is _DONE:
                    break
                if isinstance(item, Exception):
                    raise item
                if not heard:
                    heard = True
                    while self._flight and self._flight.popleft() is not req:
                        pass
                yield item

    def unspoken_tail(self) -> str:
        """Everything this client accepted that provably never reached its reader, in speaking
        order: launched requests no byte of which was ever yielded, a batch the dispatcher still
        held, queued sentences, and last the splitter's raw remainder — appended uncut so
        `tail + next_chunk` reconstructs the text stream seamlessly. The session re-feeds this to
        the fresh segment after a stream death; anything partially heard is deliberately absent
        (resending it would repeat speech), and the drained state is cleared so a second call can
        never refund the same text twice."""
        parts = [req.text for req in self._flight]
        self._flight.clear()
        parts.extend(self._undispatched)
        self._undispatched.clear()
        while True:
            try:
                item = self._inbox.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is not None:
                parts.append(item)
        joined = " ".join(p for p in parts if p.strip())
        rest = self._splitter.flush()
        if not joined:
            return rest
        return f"{joined} {rest}" if rest else f"{joined} "

    async def _watch_hold(self) -> None:
        while not self._ended:
            await asyncio.sleep(HOLD_SECS / 2)
            loop_now = asyncio.get_running_loop().time()
            if loop_now - self._last_feed >= HOLD_SECS:
                held = self._splitter.pending_sentence()
                if held:
                    await self._inbox.put(held)

    async def _dispatch(self) -> None:
        """Launch requests in order, but never behind a suspected stall, and never lose text in
        hand: a batch held when this task exits — give-up, cancellation — lands in `_undispatched`,
        where unspoken_tail() can still refund it."""
        batch: list[str] = []
        try:
            while True:
                item = await self._inbox.get()
                if item is None:
                    break
                batch.append(item)
                if self._failed:
                    break
                eos = False
                # Coalesce whatever is already queued: when the LLM outruns synthesis, adjacent
                # sentences merge into one request → fewer round-trips, better prosody.
                while sum(len(s) for s in batch) < BATCH_CHARS:
                    try:
                        nxt = self._inbox.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if nxt is None:
                        eos = True
                        break
                    batch.append(nxt)
                await self._sem.acquire()
                while not self._failed and self._suspect_stall():
                    await asyncio.sleep(0.05)
                if self._failed:
                    self._sem.release()
                    break
                req = _Req(self._carry_emotion(" ".join(batch)))
                batch = []
                self._flight.append(req)
                self._order.put_nowait(req)
                task = asyncio.create_task(self._synth(req))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
                if eos:
                    break
        finally:
            self._undispatched.extend(batch)
            self._order.put_nowait(None)

    def _suspect_stall(self) -> bool:
        """True while any still-running request has delivered no byte for STALL_SUSPECT_SECS —
        the shape of a dead 200, and the one window where launching more requests only synthesizes
        discards (ordered playback: nothing launched behind it could be heard first)."""
        now = asyncio.get_running_loop().time()
        return any(
            not req.done and not req.delivered and now - req.started >= STALL_SUSPECT_SECS
            for req in self._flight
        )

    def _carry_emotion(self, text: str) -> str:
        """Each REST request is context-free, so a tag only colors the request it lands in — carry the
        active sustained emotion into tagless requests, matching what v3 does with the full reply.

        Never onto a wordless hum. The tool heartbeat (loop._NEUTRAL_FILLER) reaches this engine as a
        request of its own — the splitter holds "Mmm..." and _watch_hold forces it out alone — and a
        rare tag on a six-character body is the least reliable shape ElevenLabs documents, with the
        tag NAME spoken aloud as its failure mode. loop._heartbeat_lines argues that case at length;
        prepending here silently undid it. The emotion is not cleared, only skipped: the prose after
        the hum is still coloured."""
        carry = (
            self._active_tag and not text.lstrip().startswith("[") and not is_wordless_sound(text)
        )
        prepend = self._active_tag if carry else ""
        for m in _TAG_RE.finditer(text):
            name = m.group(1).strip().lower()
            if name in ALLOWED_AUDIO_TAGS and name not in _ONE_SHOT_TAGS:
                self._active_tag = f"[{name}]"
        return f"{prepend} {text}" if prepend else text

    async def _synth(self, req: _Req) -> None:
        """One request, at most two attempts. See the module docstring for what a failure costs."""
        body: dict = {"text": req.text, "model_id": self._model_id}
        if self._voice_settings:
            body["voice_settings"] = self._voice_settings
        log.debug("expressive synth %d chars: %r", len(req.text), req.text)
        try:
            retried = False
            while True:
                delivered = False
                try:
                    async with self._client.stream(
                        "POST", self._url, json=body, headers=config.auth_headers()
                    ) as resp:
                        if resp.status_code != 200:
                            raw = bytearray()
                            async for part in resp.aiter_bytes():
                                raw.extend(part)
                                if len(raw) >= ERROR_BODY_CAP:
                                    break  # a body is diagnostics, never something to buffer whole
                            detail = bytes(raw[:ERROR_BODY_CAP]).decode("utf-8", errors="replace")
                            if resp.status_code == 401:
                                if "quota_exceeded" in detail:
                                    raise VoiceQuotaError(f"ElevenLabs quota exhausted: {detail[:200]}")
                                raise VoiceAuthError(f"ElevenLabs rejected the API key: {detail[:200]}")
                            raise VoiceError(f"TTS request failed (HTTP {resp.status_code}): {detail[:200]}")
                        async for chunk in resp.aiter_bytes():
                            if chunk:
                                delivered = True
                                req.delivered = True
                                if len(chunk) & 1:
                                    req.odd = not req.odd
                                req.q.put_nowait(chunk)
                except asyncio.CancelledError:
                    req.q.put_nowait(VoiceStreamClosed("TTS request cancelled"))
                    raise
                except VoiceAuthError as exc:
                    self._failed = True
                    req.q.put_nowait(exc)
                    return
                except Exception as exc:
                    err = exc if isinstance(exc, VoiceError) else VoiceError(
                        f"TTS request failed: {exc.__class__.__name__}: {exc}"
                    )
                    if not delivered and not retried:
                        retried = True
                        log.warning("expressive synth retrying once after: %s", err)
                        await asyncio.sleep(RETRY_BACKOFF_SECS)
                        continue
                    self._failure_streak += 1
                    # A request truncated mid-sample would byte-shift every s16 frame behind it —
                    # one padding byte restores parity for the sentences that still play.
                    if req.odd:
                        req.q.put_nowait(b"\x00")
                    if self._failure_streak >= FAILURE_STREAK_LIMIT:
                        self._failed = True
                        req.q.put_nowait(err)
                    else:
                        log.warning("expressive synth dropped %d chars after: %s", len(req.text), err)
                        req.q.put_nowait(_DONE)
                    return
                self._failure_streak = 0
                req.q.put_nowait(_DONE)
                return
        finally:
            req.done = True
            self._sem.release()

    async def close(self) -> None:
        """Idempotent teardown. The broad excepts swallow the AWAITED tasks' cancellations; our OWN
        (a barge-in landing while close is in flight) is noted, the teardown still completes, and it
        re-raises at the end — see config.own_cancellation_swallowed."""
        self._ended = True
        pending = [t for t in (self._dispatcher, self._watchdog, *self._tasks) if t is not None and not t.done()]
        for task in pending:
            task.cancel()
        cancelled = False
        for task in pending:
            try:
                await task
            except BaseException:
                cancelled = cancelled or config.own_cancellation_swallowed()
        self._dispatcher = self._watchdog = None
        client, self._client = self._client, None
        if client is not None and self._own_client:
            try:
                await client.aclose()
            except Exception:
                log.debug("expressive client close raced", exc_info=True)
        if cancelled:
            raise asyncio.CancelledError

    async def __aenter__(self) -> "ExpressiveTtsClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
