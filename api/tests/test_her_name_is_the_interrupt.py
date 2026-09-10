"""Saying her name stops her. That is the whole rule, and it replaces a list of words for silence.

The list could never win. Discord adds its own delay and the transcriber ends a turn by hearing 1.5 s
of silence, so the COMMITTED text of "cállate" lands seconds after the mouth it was meant to close —
by then she has talked over them for a sentence, and no vocabulary buys that time back. The name is
already recognised in the PARTIAL, which is where the interrupt now happens.

What follows the name is then just a turn. Told to be quiet she answers as she would answer anything,
which is the operator's call: one rule, no vocabulary, nothing to remember in a second language.
"""
from __future__ import annotations

import asyncio

import pytest

from kotoba.discord import voice


class _VC:
    def __init__(self, playing: bool) -> None:
        self._playing = playing
        self.stopped = False

    def is_playing(self) -> bool:
        return self._playing

    def stop(self) -> None:
        self.stopped = True
        self._playing = False


class _Stt:
    """One transcriber's event stream, replayed exactly as the listen loop consumes it."""

    def __init__(self, events) -> None:
        self._events = events

    async def events(self):
        for one in self._events:
            yield one


def _room(*, playing: bool):
    room = object.__new__(voice.VoiceRoom)
    room.names = ["Kotoba"]
    room.speakers = {}
    room.vc = _VC(playing)
    room._turn = None
    room._speech = None
    room._turn_ended_at = 0.0
    room._closing = False
    room._turn_lock = asyncio.Lock()
    room.started = []

    async def _start(user_id, text):
        room.started.append((user_id, text))

    room._start_turn = _start
    return room


def _partials(room, uid: int, texts):
    """Drive the real listen loop over partials only — no commit, as in the moment being measured."""
    from kotoba.core.voice.stt import SttPartial

    spk = room.speakers[uid] = voice.Speaker(uid)
    spk.stt = _Stt([SttPartial(text=t) for t in texts])
    asyncio.run(room._listen(spk))
    return spk


@pytest.mark.parametrize("heard", [
    "Kotoba",
    "Kotoba cállate",
    "Kotoba shut up",
    "Kotoba una pregunta",          # not an order at all — the name is still the interrupt
    "Kotoba çeneni kapa",           # a language nobody wrote a word list for
    "Cotoba",                       # as a transcriber writes it when it has no dictionary
])
def test_her_name_cuts_her_off_before_the_sentence_is_even_finished(heard):
    room = _room(playing=True)
    _partials(room, 1, [heard])
    assert room.vc.stopped, f"{heard!r} did not close her mouth"


def test_it_happens_on_the_partial_and_not_on_the_committed_text():
    """The whole point is the seconds between the two. A test that drives the commit would pass
    against the old code as well."""
    room = _room(playing=True)
    _partials(room, 1, ["Kotoba"])
    assert room.vc.stopped
    assert room.started == [], "the interrupt is not a turn — the words have not arrived yet"


def test_somebody_elses_sentence_does_not_close_her_mouth():
    room = _room(playing=True)
    _partials(room, 2, ["oye pásame el mando", "no, el otro"])
    assert not room.vc.stopped


def test_the_name_has_to_open_the_sentence():
    """A partial is short, so any mention of her would otherwise count and she would stop every time
    the room talked about her."""
    room = _room(playing=True)
    _partials(room, 3, ["yo creo que eso lo hace Kotoba"])
    assert not room.vc.stopped


def test_she_is_not_cut_off_when_she_was_not_talking():
    from kotoba.core.voice.stt import SttPartial

    room = _room(playing=False)
    spk = room.speakers[4] = voice.Speaker(4)
    marked = []

    class _StillOpen:
        async def events(self):
            yield SttPartial(text="Kotoba")
            marked.append(spk.name_in_partial)

    spk.stt = _StillOpen()
    asyncio.run(room._listen(spk))
    assert not room.vc.stopped
    assert marked == [True], "the name was still heard — it just had no mouth to close"


def test_repeating_the_name_does_not_keep_stopping_a_turn_that_already_ended():
    """Somebody saying "Kotoba, Kotoba" produces a partial per word. Once she is quiet there is
    nothing to stop, and that is what limits it — never a flag standing in for it."""
    room = _room(playing=True)
    _partials(room, 5, ["Kotoba", "Kotoba cállate", "Kotoba cállate ya"])
    assert room.vc.stopped
    assert room.vc._playing is False


def test_her_name_and_nothing_else_over_her_voice_is_the_whole_message():
    """Said while she is talking, it is an interrupt and not a question: answering "dime" out loud
    is more talking at somebody who just asked for less."""
    room = _room(playing=True)
    spk = room.speakers[6] = voice.Speaker(6)
    acknowledged = []
    room._acknowledge = lambda: acknowledged.append(True)
    asyncio.run(room._committed(spk, "Kotoba"))
    assert room.vc.stopped
    assert acknowledged == [] and room.started == []


def test_the_words_after_her_name_are_still_an_ordinary_turn():
    """No vocabulary: "cállate" reaches the model like anything else, and she decides what to do."""
    room = _room(playing=False)
    spk = room.speakers[7] = voice.Speaker(7)
    asyncio.run(room._committed(spk, "Kotoba cállate"))
    assert room.started == [(7, "cállate")], "her own words reach her, accents and all"


# --- the mark that rescues a name must never be the thing that arms the interrupt -------------------

def test_an_utterance_that_transcribed_to_nothing_leaves_no_mark():
    """A name partial whose commit came back empty. The mark belongs to THAT utterance: kept, it makes
    her answer the next unrelated thing this person says, and it used to disarm her interrupt too."""
    from kotoba.core.voice.stt import SttCommitted, SttPartial

    room = _room(playing=True)
    spk = room.speakers[8] = voice.Speaker(8)
    spk.stt = _Stt([SttPartial(text="Kotoba"), SttCommitted(text="   ")])
    asyncio.run(room._listen(spk))
    assert room.vc.stopped, "the interrupt still happened — that half is not in question"
    assert not spk.name_in_partial


def test_she_is_still_cut_off_a_second_time_after_that():
    """The regression this guards: the interrupt was armed by a mark that could stick, so one empty
    commit made her name stop working for that person for the rest of the call."""
    from kotoba.core.voice.stt import SttCommitted, SttPartial

    room = _room(playing=True)
    spk = room.speakers[9] = voice.Speaker(9)
    spk.stt = _Stt([SttPartial(text="Kotoba"), SttCommitted(text="  ")])
    asyncio.run(room._listen(spk))
    room.vc = _VC(True)                     # she is talking again
    spk.stt = _Stt([SttPartial(text="Kotoba")])
    asyncio.run(room._listen(spk))
    assert room.vc.stopped, "her name stopped working for this person"


def test_the_name_still_cuts_when_she_starts_talking_between_two_partials():
    """No stuck mark needed for this one. They call her while she is silent, she starts answering
    somebody else, and their next breath — still the same utterance, still opening with her name —
    has to stop her. A mark saying "already seen this utterance" would swallow exactly that."""
    from kotoba.core.voice.stt import SttPartial

    room = _room(playing=False)
    spk = room.speakers[10] = voice.Speaker(10)

    class _Turning:
        async def events(self):
            yield SttPartial(text="Kotoba")
            room.vc._playing = True          # she takes somebody else's turn in the gap
            yield SttPartial(text="Kotoba dime")

    spk.stt = _Turning()
    asyncio.run(room._listen(spk))
    assert room.vc.stopped, "her name stopped working inside one utterance"


class _Closable(_Stt):
    def __init__(self, events, *, hang=False) -> None:
        super().__init__(events)
        self.closed = False
        self.hang = hang
        self.blow = None

    async def events(self):
        for one in self._events:
            yield one
        while self.hang and not self.closed and self.blow is None:
            await asyncio.sleep(0.005)
        if self.blow is not None:
            raise self.blow

    async def close(self):
        self.closed = True


def test_a_socket_the_server_closes_cleanly_ends_the_utterance_with_it():
    from kotoba.core.voice.stt import SttPartial

    room = _room(playing=False)
    spk = room.speakers[11] = voice.Speaker(11)
    client = spk.stt = _Closable([SttPartial(text="Kotoba")])
    asyncio.run(room._listen(spk))
    assert not spk.name_in_partial, "a name on a socket that is gone was answered on the next one"
    assert spk.stt is None, "the pump only reopens when it is told the socket is gone"
    assert client.closed


def test_the_next_sentence_on_a_reopened_socket_is_not_answered_because_of_a_dead_mark():
    from kotoba.core.voice.stt import SttCommitted, SttPartial

    room = _room(playing=False)
    spk = room.speakers[12] = voice.Speaker(12)
    spk.stt = _Closable([SttPartial(text="Kotoba")])
    asyncio.run(room._listen(spk))
    spk.stt = _Closable([SttCommitted(text="qué hace el perro")])
    asyncio.run(room._listen(spk))
    assert room.started == []


def test_an_orphaned_listener_dying_does_not_take_the_live_transcriber_with_it():
    from kotoba.core.voice.config import VoiceStreamClosed
    from kotoba.core.voice.stt import SttPartial

    async def scenario():
        room = _room(playing=False)
        spk = room.speakers[13] = voice.Speaker(13)
        old = spk.stt = _Closable([], hang=True)
        orphan = asyncio.create_task(room._listen(spk))
        await asyncio.sleep(0.01)
        live = spk.stt = _Closable([SttPartial(text="Kotoba")], hang=True)
        listening = asyncio.create_task(room._listen(spk))
        await asyncio.sleep(0.02)
        assert spk.name_in_partial
        old.blow = VoiceStreamClosed("the old socket finally dropped")
        await asyncio.wait_for(orphan, 1)
        assert spk.stt is live, "the live transcriber was thrown away for a socket nobody was using"
        assert not live.closed
        assert spk.name_in_partial, "the live utterance's mark was cleared by a dead socket"
        live.closed = True
        await asyncio.wait_for(listening, 1)

    asyncio.run(scenario())


def test_a_bad_round_of_the_pump_closes_the_transcriber_it_drops(monkeypatch):
    monkeypatch.setattr(voice, "PUMP_SECONDS", 0.005)

    class _Refusing(_Closable):
        async def send_audio(self, pcm, *, commit=False):
            if commit:
                raise RuntimeError("one bad round")

        async def commit(self):
            await self.send_audio(b"", commit=True)

    async def scenario():
        room = _room(playing=False)
        room.channel = type("Ch", (), {"members": [type("M", (), {"id": 14})()]})()
        room._bg = set()
        spk = room.speakers[14] = voice.Speaker(14)
        client = spk.stt = _Refusing([], hang=True)
        spk.reopen_at = float("inf")
        spk.spoke_seconds = 1.0
        spk.last_audio = 0.0
        spk.last_partial = "qué hora es en Tokio"
        pump = asyncio.create_task(room._pump(spk))
        await asyncio.sleep(0.05)
        assert spk.stt is None
        assert client.closed, "the socket the pump walked away from was left open, listener and all"
        pump.cancel()
        try:
            await pump
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())


def test_her_name_alone_that_already_cut_her_off_is_not_answered_with_a_noise():
    from kotoba.core.voice.stt import SttCommitted, SttPartial

    room = _room(playing=True)
    acknowledged = []

    async def ack():
        acknowledged.append(True)
        return True

    room._acknowledge = ack
    spk = room.speakers[15] = voice.Speaker(15)
    spk.stt = _Stt([SttPartial(text="Kotoba"), SttCommitted(text="Kotoba")])
    asyncio.run(room._listen(spk))
    assert room.vc.stopped
    assert acknowledged == [], "told to be quiet, she hummed"
    assert spk.attention_until == 0.0
    assert room.started == []


def test_the_bare_name_when_she_was_not_talking_still_gets_the_noise():
    from kotoba.core.voice.stt import SttCommitted, SttPartial

    room = _room(playing=False)
    acknowledged = []

    async def ack():
        acknowledged.append(True)
        return True

    room._acknowledge = ack
    spk = room.speakers[16] = voice.Speaker(16)
    spk.stt = _Stt([SttPartial(text="Kotoba"), SttCommitted(text="Kotoba")])
    asyncio.run(room._listen(spk))
    assert acknowledged == [True]


def test_her_name_in_decomposed_kana_is_the_same_key():
    import unicodedata

    for name in ("コトバ", "ことば", "ゴマ"):
        nfd = unicodedata.normalize("NFD", name)
        assert nfd != name
        assert voice.match_key(nfd) == voice.match_key(name)

