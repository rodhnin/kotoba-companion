"""Every stage of the receive pipe can fail transiently, and a raise in any of them makes her deaf.

The callback runs on a reader thread. An exception there kills the thread, and from the outside that
is indistinguishable from a quiet channel: no error, no log anybody reads, she simply never hears
anything again. So every stage drops its packet and counts it.

The layout facts here were measured against a live call, not read off a page. AAD is the header up to
and INCLUDING the extension's profile+length; the extension BODY sits inside the ciphertext. Getting
that backwards fails every packet that carries an extension, which on Discord is nearly all of them —
and it fails as `corrupted stream`, never as anything that names the real cause.
"""
from __future__ import annotations

from kotoba.discord import voice_recv


def _packet(*, csrc=0, ext=0, pad=False, body=b"payload", tail=b"\x00\x00\x00\x01") -> bytes:
    first = 0x80 | (0x20 if pad else 0) | (0x10 if ext else 0) | csrc
    head = bytes([first, voice_recv.OPUS_PAYLOAD_TYPE])
    head += (7).to_bytes(2, "big") + (0).to_bytes(4, "big") + (4242).to_bytes(4, "big")
    head += b"\xcc\xcc\xcc\xcc" * csrc
    if ext:
        head += b"\xbe\xde" + ext.to_bytes(2, "big")
    return head + body + tail


def test_the_header_length_counts_the_extension_marker_but_not_its_body():
    """The one byte-offset that decides whether anything at all decodes."""
    ssrc, prefix, ext_len, padded = voice_recv.parse_rtp(_packet(ext=2))
    assert ssrc == 4242
    assert prefix == 16          # 12 + the 4-byte profile+length
    assert ext_len == 2          # its 8 bytes of body stay inside the ciphertext
    assert padded is False


def test_a_csrc_list_moves_the_header_along():
    _ssrc, prefix, _ext, _pad = voice_recv.parse_rtp(_packet(csrc=2))
    assert prefix == 20


def test_anything_that_is_not_opus_is_ignored_quietly():
    control = bytearray(_packet())
    control[1] = 0x00
    assert voice_recv.parse_rtp(bytes(control)) is None
    assert voice_recv.parse_rtp(b"short") is None


class _Session:
    def __init__(self, ready=True, blow=None):
        self.ready = ready
        self._blow = blow

    def decrypt(self, user_id, media_type, packet):
        if self._blow:
            raise self._blow
        return b"opus:" + packet


class _State:
    mode = "aead_xchacha20_poly1305_rtpsize"
    secret_key = bytes(32)

    def __init__(self, session=None):
        self.dave_session = session
        self.listeners: list = []

    def add_socket_listener(self, cb):
        self.listeners.append(cb)

    def remove_socket_listener(self, cb):
        self.listeners.remove(cb)


def _dave(session):
    return voice_recv.Dave(_State(session))


def test_a_session_that_is_not_ready_yields_nothing_and_no_exception():
    """The first second after joining, and every MLS epoch change."""
    assert _dave(_Session(ready=False)).open(7, b"x") is None


def test_an_unencrypted_passthrough_frame_survives_untouched():
    """Keepalives ride in the clear on purpose. They raise inside DAVE and decode fine as-is, so a
    frame Discord never encrypted has to pass through rather than be counted as a loss."""
    class _Unencrypted(Exception):
        pass

    _Unencrypted.__name__ = "UnencryptedWhenPassthroughDisabled"
    assert _dave(_Session(blow=_Unencrypted())).open(7, b"keepalive") == b"keepalive"


def test_any_other_failure_drops_one_packet_rather_than_the_stream():
    assert _dave(_Session(blow=ValueError("epoch moved"))).open(7, b"x") is None


def test_a_mode_she_cannot_open_is_refused_in_words_at_join_time():
    class _Odd(_State):
        mode = "aead_something_new"

    try:
        voice_recv.Transport(_Odd())
    except voice_recv.Unsupported as said:
        assert "aead_something_new" in str(said)
    else:
        raise AssertionError("an unknown mode has to be named, not guessed at")


class _Client:
    def __init__(self):
        self._connection = _State(_Session())


def test_a_packet_from_a_speaker_she_cannot_name_is_dropped_not_guessed():
    """DAVE decrypts per person; without the id there is nothing to decrypt with."""
    got = []
    rx = voice_recv.VoiceReceiver(_Client(), lambda uid, pcm: got.append(uid))
    rx._handle(_packet())
    assert got == []
    assert rx.dropped == 1


def test_the_reader_thread_survives_a_packet_that_explodes():
    """One raise here and she goes deaf with no error anywhere."""
    rx = voice_recv.VoiceReceiver(_Client(), lambda uid, pcm: None)
    rx._on_packet(None)      # not even bytes
    assert rx.dropped == 1


def test_learning_a_new_stream_for_a_known_speaker_resets_their_decoder():
    """A reconnect restarts the sequence space, and a decoder carrying state across it clicks."""
    rx = voice_recv.VoiceReceiver(_Client(), lambda uid, pcm: None)
    rx.learn(7, ssrc=100)
    rx._decoders[100] = object()
    rx.learn(7, ssrc=100)
    assert 100 in rx._decoders, "the same stream must not be reset"
    rx.learn(7, ssrc=200)
    assert rx._ssrc[200] == 7


def test_registering_and_stopping_leaves_the_tap_clean():
    client = _Client()
    rx = voice_recv.VoiceReceiver(client, lambda uid, pcm: None)
    rx.start()
    assert len(client._connection.listeners) == 1
    rx.stop()
    assert client._connection.listeners == []
