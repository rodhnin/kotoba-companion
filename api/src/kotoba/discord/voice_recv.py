"""Hearing a Discord voice channel, through the encryption Discord now requires.

The library runs the MLS handshake, holds the keys and offers a tap on the socket; decoding what
comes back is ours: parse RTP, transport decrypt, DAVE decrypt per speaker, Opus, then PCM.

Two facts carry the whole file. The AAD is the header up to and INCLUDING the four-byte extension
profile+length, and the extension BODY is inside the ciphertext, so stripping it before the AEAD
fails nearly every packet. And frames Discord sends in the clear raise inside DAVE instead of
decrypting: they must pass through untouched, never counted as a loss. The callback runs on the
reader thread and never touches the loop.
"""
from __future__ import annotations

import logging

log = logging.getLogger("kotoba.discord")

OPUS_PAYLOAD_TYPE = 0x78
OPUS_SILENCE = b"\xf8\xff\xfe"

_NONCE_TAIL = 4
_MODES = {
    "aead_xchacha20_poly1305_rtpsize": 24,
    "aead_aes256_gcm_rtpsize": 12,
}


class Unsupported(RuntimeError):
    """Said at join time, in words, rather than discovered inside a 20 ms loop."""


def parse_rtp(packet: bytes):
    """(ssrc, prefix_len, ext_len, padded) or None for anything that is not an opus RTP packet."""
    if len(packet) < 16:
        return None
    b0, b1 = packet[0], packet[1]
    if b0 >> 6 != 2 or (b1 & 0x7F) != OPUS_PAYLOAD_TYPE:
        return None
    padded = bool((b0 >> 5) & 1)
    has_ext = bool((b0 >> 4) & 1)
    csrc = b0 & 0x0F
    ssrc = int.from_bytes(packet[8:12], "big")
    prefix = 12 + 4 * csrc
    ext_len = 0
    if has_ext:
        if len(packet) < prefix + 4:
            return None
        ext_len = int.from_bytes(packet[prefix + 2:prefix + 4], "big")
        prefix += 4
    return ssrc, prefix, ext_len, padded


class Transport:
    """The outer layer, whose key and mode the gateway rotates under us — so both are read live."""

    def __init__(self, state) -> None:
        self._state = state
        mode = getattr(state, "mode", None)
        if mode not in _MODES:
            raise Unsupported(f"Discord negotiated “{mode}”, which I don't know how to open.")

    def open(self, packet: bytes, prefix: int) -> bytes | None:
        import nacl.bindings as nb

        key = bytes(self._state.secret_key or b"")
        mode = getattr(self._state, "mode", None)
        nonce_len = _MODES.get(mode)
        # Read per packet, not once: a voice reconnect renegotiates both, and a mode frozen at
        # construction fails every packet afterwards without raising anything.
        if not key or nonce_len is None:
            return None
        aad = packet[:prefix]
        nonce = packet[-_NONCE_TAIL:] + b"\x00" * (nonce_len - _NONCE_TAIL)
        body = packet[prefix:-_NONCE_TAIL]
        if not body:
            return None
        try:
            if mode == "aead_xchacha20_poly1305_rtpsize":
                return nb.crypto_aead_xchacha20poly1305_ietf_decrypt(body, aad, nonce, key)
            return nb.crypto_aead_aes256gcm_decrypt(body, aad, nonce, key)
        except Exception:
            return None


class Dave:
    """The inner layer. Duck-typed on the session so a test never needs the real one."""

    def __init__(self, state) -> None:
        self._state = state

    def open(self, user_id: int, opus: bytes) -> bytes | None:
        session = getattr(self._state, "dave_session", None)
        if session is None or not getattr(session, "ready", False):
            return None
        try:
            import davey

            return session.decrypt(user_id, davey.MediaType.audio, opus)
        except Exception as exc:
            name = type(exc).__name__
            if "Unencrypted" in name or "passthrough" in str(exc).lower():
                return opus     # keepalives ride in the clear, and decode fine as-is
            return None


class VoiceReceiver:
    """One voice connection, turned into per-speaker PCM.

    `sink(user_id, pcm)` is called on the READER THREAD. Anything it does that touches the event loop
    has to hop there itself — resolving a card from this thread records the answer and leaves the
    turn asleep for its whole window.
    """

    def __init__(self, voice_client, sink, *, candidates=None) -> None:
        self._sink = sink
        self._candidates = candidates
        self._state = getattr(voice_client, "_connection", None) or voice_client
        self._transport = Transport(self._state)
        self._dave = Dave(self._state)
        self._decoders: dict[int, object] = {}
        self._ssrc: dict[int, int] = {}
        self._started = False
        self.dropped = 0
        self.decoded = 0

    def _identify(self, ssrc: int, plain: bytes):
        """Ask the ENCRYPTION whose stream this is, rather than waiting to be told.

        Discord announces a speaker only when they start, and that event does not always arrive —
        measured: 2021 packets in ninety seconds with not one of them attributable, so every one was
        dropped. DAVE decrypts per sender, so the wrong person fails and the right one works. Try the
        people in the room, cache the answer, and the guessing happens once per stream.
        """
        if self._candidates is None:
            return None, None
        for candidate in self._candidates():
            if candidate == self._ssrc.get(ssrc):
                continue
            got = self._dave.open(candidate, plain)
            if got is not None:
                self._ssrc[ssrc] = candidate
                self._decoders.pop(ssrc, None)
                log.info("voice: stream %s belongs to %s", ssrc, candidate)
                return got, candidate
        return None, None

    def learn(self, user_id: int, ssrc: int) -> None:
        """From the voice gateway's SPEAKING op. discord.py keeps no such map, and DAVE cannot
        decrypt without knowing whose stream it is."""
        if self._ssrc.get(ssrc) != user_id:
            self._ssrc[ssrc] = user_id
            self._decoders.pop(ssrc, None)     # a new stream restarts the sequence space

    def start(self) -> None:
        add = getattr(self._state, "add_socket_listener", None)
        if add is None:
            raise Unsupported("my Discord library moved the voice socket reader.")
        add(self._on_packet)
        self._started = True

    def stop(self) -> None:
        drop = getattr(self._state, "remove_socket_listener", None)
        if drop is not None and self._started:
            drop(self._on_packet)
        self._started = False
        self._decoders.clear()

    def _decoder(self, ssrc: int):
        import discord

        got = self._decoders.get(ssrc)
        if got is None:
            got = self._decoders[ssrc] = discord.opus.Decoder()
        return got

    def _on_packet(self, packet: bytes) -> None:
        try:
            self._handle(packet)
        except Exception:
            # The reader thread must survive anything: one raise here and she goes deaf in silence.
            self.dropped += 1
            log.debug("voice packet dropped", exc_info=True)

    def _handle(self, packet: bytes) -> None:
        parsed = parse_rtp(packet)
        if parsed is None:
            return
        ssrc, prefix, ext_len, padded = parsed
        plain = self._transport.open(packet, prefix)
        if plain is None:
            self.dropped += 1
            return
        if ext_len:
            plain = plain[4 * ext_len:]     # the body was INSIDE the ciphertext
        if padded and plain:
            plain = plain[:-plain[-1]]
        if not plain or plain == OPUS_SILENCE:
            return
        user_id = self._ssrc.get(ssrc)
        opus = self._dave.open(user_id, plain) if user_id is not None else None
        if opus is None:
            opus, user_id = self._identify(ssrc, plain)
        if opus is None or user_id is None:
            self.dropped += 1
            return
        try:
            pcm = self._decoder(ssrc).decode(opus, fec=False)
        except Exception:
            self.dropped += 1
            self._decoders.pop(ssrc, None)
            return
        self.decoded += 1
        self._sink(user_id, pcm)


def voice_client_class():
    """A VoiceClient that reports SPEAKING to us. `cls=` on connect is public API even though the
    method it overrides is not, which is what makes this an extension point rather than a patch."""
    import discord
    from discord.voice_state import VoiceConnectionState

    class KotobaVoiceClient(discord.VoiceClient):
        on_speaking = None      # set by whoever connects

        def create_connection_state(self) -> VoiceConnectionState:
            return VoiceConnectionState(self, hook=self._kotoba_hook)

        async def _kotoba_hook(self, ws, msg) -> None:
            if msg.get("op") != 5 or not callable(self.on_speaking):
                return
            data = msg.get("d") or {}
            if "ssrc" in data and "user_id" in data:
                try:
                    self.on_speaking(int(data["user_id"]), int(data["ssrc"]))
                except Exception:
                    log.debug("speaking hook raised", exc_info=True)

    return KotobaVoiceClient
