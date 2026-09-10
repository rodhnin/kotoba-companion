"""Four ways the voice WebSocket lost a turn, none of which show up in a diff against /v1.

1. `interrupt` now carries turn identity, and the turn counter increments inside the lock, so
   a straggler interrupt cannot land on a turn just born and kill it before its first frame.
2. `_abort_segment` must send `audio_end` on its ERROR path — without it the client's gate
   stayed shut for the rest of the call, since it only clears `streaming` there or on `interrupted`.
3. A cancel inside `_open_segment` must not orphan the client and reader task as locals, or an
   orphaned reader can push a superseded turn's audio into the NEXT turn's bracket.
4. A reply the filters emptied must still speak a fallback line rather than total silence.
"""
from __future__ import annotations

import inspect


from kotoba.core.voice import session as vs


# --- 1. turn identity ------------------------------------------------------------------------------

def test_barge_in_takes_the_turn_it_should_stop():
    sig = inspect.signature(vs.VoiceSession._barge_in)
    assert "turn" in sig.parameters, "the client must be able to name the turn it was hearing"


def test_barge_in_holds_the_same_lock_as_starting_a_turn():
    body = inspect.getsource(vs.VoiceSession._barge_in)
    assert "turns.lock(self.session_id)" in body
    assert "turn != self._turn_no" in body, "a straggler interrupt must be dropped, not applied"


def test_start_turn_captures_the_turn_number_before_its_awaits():
    body = inspect.getsource(vs.VoiceSession._start_turn)
    assert "turn_no = self._turn_no" in body
    assert "self._run_turn(user_text, typed, turn_no)" in body, "pass the local, not the live counter"
    # the increment must be inside the lock, or _barge_in can read it mid-flight
    lock_at = body.index("turns.lock(self.session_id)")
    inc_at = body.index("self._turn_no += 1")
    assert lock_at < inc_at


def test_the_control_dispatch_forwards_the_turn():
    body = inspect.getsource(vs.VoiceSession._on_control)
    assert '_barge_in(' in body and 'msg.get("turn")' in body


# --- 2. the mic gate -------------------------------------------------------------------------------

def test_abort_can_close_the_audio_gate():
    sig = inspect.signature(vs.VoiceSession._abort_segment)
    assert "end_audio" in sig.parameters


def test_the_fatal_tts_path_closes_it_and_the_barge_in_path_sends_nothing_extra():
    feed = inspect.getsource(vs.VoiceSession._feed_segment)
    assert "end_audio=True" in feed, "no more audio this turn → the mic must be re-opened"
    pump = inspect.getsource(vs.VoiceSession._pump_speech)
    # Every abnormal exit closes the bracket itself: an out-of-socket supersede (POST /leave with the
    # WS still up) and a non-Voice crash have no `interrupted` to reopen the client's gate. A real
    # barge-in still sends nothing extra — `_note_gate_forced_open` drops the level BEFORE the cancel
    # lands, so the close no-ops.
    assert "except BaseException" in pump
    assert "end_audio=True" in pump.split("except BaseException")[1]


# --- 3. no orphaned stream -------------------------------------------------------------------------

def test_open_segment_cleans_up_when_cancelled():
    body = inspect.getsource(vs.VoiceSession._open_segment)
    assert "_close_quietly" in body
    assert body.count("except BaseException") >= 2, "both awaits after construction must be covered"


def test_close_quietly_never_raises_over_the_exception_in_flight():
    body = inspect.getsource(vs.VoiceSession._close_quietly)
    assert body.count("except BaseException") >= 2


# --- 4. an emptied reply still speaks --------------------------------------------------------------

def test_pump_speech_reports_whether_anything_was_spoken():
    # `from __future__ import annotations` makes annotations strings.
    assert inspect.signature(vs.VoiceSession._pump_speech).return_annotation == "bool"


def test_an_emptied_reply_falls_back_to_a_line():
    body = inspect.getsource(vs.VoiceSession._run_turn)
    assert "filtered_reply_fallback" in body
    assert "if not spoke" in body


def test_the_fallback_line_is_one_line_in_one_language():
    """A canned line per language only ever covers the ones somebody remembered to write, and every
    surface has to guess which to use. One English line, always."""
    from kotoba.core import stream as sse

    assert sse.filtered_reply_fallback() == sse.filtered_reply_fallback()
    assert "One sec" in sse.filtered_reply_fallback()


# --- memory survives a barge-in --------------------------------------------------------------------

def test_memory_extraction_is_in_the_finally():
    body = inspect.getsource(vs.VoiceSession._run_turn)
    finally_block = body.rsplit("finally:", 1)[1]
    assert "extract_and_save_memory" in finally_block, \
        "a barged-in utterance is still worth remembering; /v1 has always done this"


def test_is_trigger_is_computed_before_the_try():
    """The finally reads it, so it must exist even if the first statement of the try fails."""
    body = inspect.getsource(vs.VoiceSession._run_turn)
    # Match the STATEMENT, not the word: a comment mentioning "the try:" fooled an earlier version
    # of this assertion into passing off its own prose.
    assert body.index("is_trigger = is_trigger_sentinel") < body.index("\n        try:")


def test_the_detached_task_is_held():
    """A bare create_task can be collected mid-flight."""
    body = inspect.getsource(vs.VoiceSession._run_turn)
    assert "self._bg.add" in body
