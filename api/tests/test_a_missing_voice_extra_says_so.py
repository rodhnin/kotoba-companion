"""The discord extra can be installed without voice, and she must SAY she cannot hear.

`websockets` lives in the voice extra; the transcriber imports it at module level. The import sat one
line above the try that exists for exactly this, so it raised out of a detached task: she joined the
room, heard nothing, said nothing, and no line anywhere explained it.
"""
from __future__ import annotations

import asyncio
import importlib.abc
import sys

import pytest

from kotoba.discord.voice import VoiceRoom


class _NoWebsockets(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "websockets" or name.startswith("websockets."):
            raise ImportError("No module named 'websockets'")
        return None


@pytest.fixture
def without_websockets(monkeypatch):
    for mod in [m for m in sys.modules if m.startswith(("websockets", "kotoba.core.voice.stt"))]:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    finder = _NoWebsockets()
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])
    return finder


def test_she_reports_the_missing_extra_instead_of_dying_silently(without_websockets, caplog):
    room = VoiceRoom.__new__(VoiceRoom)
    room.language, room.names, room.speakers = None, (), {}

    speaker = type("S", (), {"user_id": 1})()
    with caplog.at_level("WARNING"):
        asyncio.run(room._open_ears(speaker, restart=False))

    assert any("transcriber" in r.message.lower() or "websockets" in str(r.message).lower()
               for r in caplog.records), \
        f"nothing was logged; she would be silent with no explanation: {[r.message for r in caplog.records]}"
