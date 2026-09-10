"""The net that keeps the builder's machine out of the shipped bundle, tested on planted leaks.

Its only guard asserted that the real artifact is clean, which stays true when the net itself is
disarmed — so a broken scanner would have been invisible until a release carried somebody's home path,
LAN address or tunnel URL to everyone who installed it.
"""
from __future__ import annotations

import importlib.util
import struct
import zlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(not (REPO / "scripts" / "build_web.py").is_file(),
                                reason="the build script lives in the repository, not the wheel")


def _scanner():
    spec = importlib.util.spec_from_file_location("_build_web", REPO / "scripts" / "build_web.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PLANTS = {
    "home path": '/home/jordan/projects/app.js',
    "windows home": r'C:\\Users\\jordan\\app',
    "private suffix host": 'fetch("http://buildbox.lan/api/x")',
    "loopback url": 'new WebSocket("ws://localhost:9777/api/voice/1")',
    "private ip": 'fetch("http://192.168.1.50:3000/")',
    "tunnel": 'fetch("https://shiny-cat.ngrok-free.app/api")',
    "agent id": 'const a="agent_01jxk2v9q7e8r6t5y4u3i2o1p0";',
}


@pytest.mark.parametrize("shape", sorted(PLANTS))
def test_each_shape_is_named(tmp_path, shape):
    bw = _scanner()
    (tmp_path / "chunk.js").write_text(f'export const x = 1;\n{PLANTS[shape]}\n', encoding="utf-8")
    hits = bw.strangers(tmp_path)
    assert hits, f"the scanner read nothing of a planted {shape}"


def test_a_clean_tree_is_quiet(tmp_path):
    bw = _scanner()
    (tmp_path / "chunk.js").write_text('export const x = 1;\nfetch("/api/health");\n', encoding="utf-8")
    assert bw.strangers(tmp_path) == []


def _png_with_text(path: Path) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00")
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"tEXt", b"Author\x00jordan") + chunk(b"IDAT", idat)
                     + chunk(b"IEND", b""))


def test_a_png_carrying_a_label_is_named(tmp_path):
    bw = _scanner()
    _png_with_text(tmp_path / "art.png")
    assert bw.fingerprints(tmp_path), "a PNG shipped a tEXt chunk and the scanner said nothing"


def test_an_svg_carrying_metadata_is_named(tmp_path):
    bw = _scanner()
    (tmp_path / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><metadata>made by jordan</metadata></svg>',
        encoding="utf-8")
    assert bw.fingerprints(tmp_path), "an SVG shipped a metadata block and the scanner said nothing"


def test_a_plain_png_is_quiet(tmp_path):
    bw = _scanner()
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    (tmp_path / "clean.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00\x00")) + chunk(b"IEND", b""))
    assert bw.fingerprints(tmp_path) == []
