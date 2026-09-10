"""Settings → Personality → Language must reach the TRANSCRIBER, not only her replies.

It used to set the soul's `language` and nothing else, so choosing "Spanish" left ElevenLabs
auto-detecting every utterance. Measured live: three consecutive Spanish sentences came
back labelled Portuguese, and she answered in Portuguese — correctly, since the transcript is the whole
of what she gets. A control named Language that cannot fix a language problem is worse than none.

KOTOBA_STT_LANGUAGE still wins over both, and `auto` still means auto on both sides.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from kotoba.core.voice.config import stt_language_for


@pytest.fixture(autouse=True)
def _no_env_pin(monkeypatch):
    monkeypatch.delenv("KOTOBA_STT_LANGUAGE", raising=False)


def test_choosing_spanish_pins_the_transcriber_to_spanish():
    assert stt_language_for("es") == "es"


def test_auto_still_means_auto():
    """`language: auto` is what the soul promises: talk to her in any language and she answers in it."""
    assert stt_language_for("auto") == ""
    assert stt_language_for("") == ""
    assert stt_language_for(None) == ""


def test_a_hand_written_soul_that_says_spanish_in_words_does_not_get_sent():
    """SOUL.md is hand-editable. EL would ignore junk silently, leaving us believing it was pinned."""
    assert stt_language_for("Spanish") == ""
    assert stt_language_for("español") == ""


def test_the_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("KOTOBA_STT_LANGUAGE", "ja")
    assert stt_language_for("es") == "ja"
    assert stt_language_for("auto") == "ja"


def test_case_and_padding_from_the_dropdown_are_tolerated():
    assert stt_language_for(" ES ") == "es"


def test_a_junk_env_override_does_not_shadow_the_soul_pin(monkeypatch):
    """A refused KOTOBA_STT_LANGUAGE steps aside — the next authority in line still pins."""
    monkeypatch.setenv("KOTOBA_STT_LANGUAGE", "espanol")
    assert stt_language_for("es") == "es"


class _RecordingStt:
    """Stand-in for SttClient that records its constructor kwargs and then idles."""

    kwargs: list[dict] = []

    def __init__(self, **kwargs) -> None:
        _RecordingStt.kwargs.append(kwargs)

    async def connect(self):
        from kotoba.core.voice.stt import SttSessionStarted

        return SttSessionStarted(session_id="fake", config={})

    async def send_audio(self, pcm, *, commit: bool = False) -> None:
        pass

    async def events(self):
        await asyncio.get_running_loop().create_future()  # idle until the session tears us down
        yield  # pragma: no cover

    async def close(self) -> None:
        pass


def _first_frame_kwargs(client, session_id: str) -> dict:
    """Open the voice WS, push one mic frame, and return the kwargs the STT client was built with.
    The trailing bad control synchronizes: its error reply proves the audio frame was processed."""
    _RecordingStt.kwargs = []
    with client.websocket_connect(f"/api/voice/{session_id}") as ws:
        ws.receive()  # ready
        ws.send_bytes(b"\x00\x01" * 320)
        ws.send_text(json.dumps({"type": "__sync__"}))
        ws.receive()  # bad_message error → the frame before it has been handled
    assert len(_RecordingStt.kwargs) == 1
    return _RecordingStt.kwargs[0]


def test_the_settings_language_reaches_the_stt_connect(monkeypatch):
    """End to end: Settings → Personality → Language lands on the realtime STT session itself."""
    import kotoba.core.voice.session as vs
    import kotoba.server as main

    monkeypatch.setattr(vs, "SttClient", _RecordingStt)
    with TestClient(main.app) as client:
        assert client.post("/api/settings/soul", json={"language": "es"}).status_code == 200
        assert _first_frame_kwargs(client, "vlang-es")["language_code"] == "es"
        assert client.post("/api/settings/soul", json={"language": "auto"}).status_code == 200
        assert _first_frame_kwargs(client, "vlang-auto")["language_code"] is None
