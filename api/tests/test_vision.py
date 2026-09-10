"""Vision wiring — a tool (e.g. browser screenshot) can return images that reach the vision model.

Covers: (1) the function_call_output builder turns a ToolResult-with-images into a content-part list
with input_image data URLs (string otherwise); (2) _MCPProxy.execute extracts MCP image blocks into a
ToolResult with ready data URLs, honoring the count/size caps; str() of the result never carries base64.
"""
from __future__ import annotations

import asyncio
import types

import kotoba.core.loop as loop
from kotoba.core.mcp.client import _MCPProxy
from kotoba.tools import ToolResult


# --- function_call_output builder -------------------------------------------------------------------

def test_call_output_plain_string():
    out = loop._call_output("c1", "just text", "")
    assert out == {"type": "function_call_output", "call_id": "c1", "output": "just text"}


def test_call_output_appends_fail_note():
    out = loop._call_output("c1", "x", " [tool failed]")
    assert out["output"] == "x [tool failed]"


def test_call_output_with_images_builds_content_parts():
    r = ToolResult(text="Here's the page.", images=["data:image/png;base64,AAAA", "data:image/png;base64,BBBB"])
    out = loop._call_output("c2", r, "")
    assert out["type"] == "function_call_output" and out["call_id"] == "c2"
    content = out["output"]
    assert isinstance(content, list)
    assert content[0] == {"type": "input_text", "text": "Here's the page."}
    imgs = [c for c in content if c["type"] == "input_image"]
    assert [c["image_url"] for c in imgs] == ["data:image/png;base64,AAAA", "data:image/png;base64,BBBB"]
    assert all(c.get("detail") == "auto" for c in imgs)


def test_call_output_image_with_empty_text_has_placeholder():
    """An image with no text of its own still leads with a non-empty text part."""
    r = ToolResult(text="", images=["data:image/png;base64,AAAA"])
    out = loop._call_output("c3", r, "")
    assert out["output"][0]["type"] == "input_text" and out["output"][0]["text"]


# --- _MCPProxy image extraction ---------------------------------------------------------------------

def _img_block(data, mime="image/png"):
    return types.SimpleNamespace(type="image", data=data, mimeType=mime, text=None)


def _txt_block(text):
    return types.SimpleNamespace(type="text", text=text)


class _FakeGroup:
    def __init__(self, content, is_error=False):
        self._content = content
        self._is_error = is_error

    async def call_tool(self, name, args):
        return types.SimpleNamespace(content=self._content, isError=self._is_error)


def _proxy_with(content, is_error=False):
    mgr = types.SimpleNamespace(group=_FakeGroup(content, is_error))
    return _MCPProxy(mgr, "browser__browser_take_screenshot")


def test_proxy_returns_toolresult_with_data_url():
    """An MCP image block becomes a ready data URL, and `str()` of the result stays text-only — so no
    base64 reaches the terminal or the database."""
    res = asyncio.run(_proxy_with([_img_block("QUJD")]).execute({}, None))
    assert isinstance(res, ToolResult)
    assert res.images == ["data:image/png;base64,QUJD"]
    assert "base64" not in str(res)


def test_proxy_mixes_text_and_image():
    res = asyncio.run(_proxy_with([_txt_block("Page title: Example"), _img_block("QUJD")]).execute({}, None))
    assert isinstance(res, ToolResult)
    assert res.text == "Page title: Example"
    assert res.images == ["data:image/png;base64,QUJD"]


def test_proxy_text_only_stays_string():
    """No images means the plain string every existing caller already handles."""
    res = asyncio.run(_proxy_with([_txt_block("just text")]).execute({}, None))
    assert res == "just text"


def test_proxy_caps_image_count():
    blocks = [_img_block(f"IMG{i}") for i in range(10)]
    res = asyncio.run(_proxy_with(blocks).execute({}, None))
    assert len(res.images) == _MCPProxy._MAX_IMAGES


def test_proxy_skips_oversized_image():
    """The text is kept and the oversized image dropped, which leaves a plain text result."""
    big = "A" * (_MCPProxy._MAX_IMAGE_B64 + 1)
    res = asyncio.run(_proxy_with([_txt_block("ok"), _img_block(big)]).execute({}, None))
    assert str(res) == "ok" and not getattr(res, "images", [])


def test_proxy_browser_error_returns_recovery_hint():
    """A browser tool error returns a short actionable hint rather than None. None made the model
    flail; the hint makes it re-snapshot and retry instead of repeating the failing call."""
    res = asyncio.run(_proxy_with([_img_block("QUJD")], is_error=True).execute({}, None))
    assert isinstance(res, str) and "snapshot" in res.lower()


# --- screenshot arg sanitizer ------------------------------------------------------------------------
# It forces a viewport capture: element and target screenshots come back text-only, with no inline
# image, so the screenshot never reached vision or Files.

def _shot_proxy():
    return _MCPProxy(types.SimpleNamespace(group=None), "browser__browser_take_screenshot")


def test_sanitize_keeps_only_type_for_inline_screenshot():
    """`element`/`target`/`ref`/`fullPage` ask for an element screenshot and `filename` saves to disk;
    both come back text-only. Allowing just `type` is what makes the server return text plus image."""
    a = _shot_proxy()._sanitize_args(
        {"element": "captura", "target": "body", "ref": "e3", "fullPage": True, "type": "png", "filename": "x.png"}
    )
    assert a == {"type": "png"}


def test_sanitize_drops_filename_even_alone():
    """`filename` on its own is still enough to make @playwright/mcp save to disk instead of returning
    the image."""
    a = _shot_proxy()._sanitize_args({"type": "png", "filename": "shot.png"})
    assert a == {"type": "png"}


def test_sanitize_empty_screenshot_args():
    """No arguments at all is already a plain viewport capture."""
    assert _shot_proxy()._sanitize_args({}) == {}


def test_sanitize_ignores_non_screenshot_tools():
    """Only the screenshot tool is special-cased; every other tool's arguments pass through."""
    p = _MCPProxy(types.SimpleNamespace(group=None), "browser__browser_navigate")
    a = p._sanitize_args({"fullPage": True, "element": "x"})
    assert a == {"fullPage": True, "element": "x"}


# --- snapshot sanitizing: avoid the snapshot-to-file-to-re-snapshot waste ----------------------------

def _proxy(ns_name):
    mgr = types.SimpleNamespace(group=None)
    return _MCPProxy(mgr, ns_name)


def test_snapshot_filename_stripped_so_it_returns_inline():
    """With `filename`, @playwright/mcp writes a file and returns a stub, so the model cannot read the
    refs and snapshots again. Dropping it brings the snapshot back inline; every other argument is
    preserved."""
    p = _proxy("browser__browser_snapshot")
    out = p._sanitize_args({"filename": "fb.md", "depth": 4, "boxes": True})
    assert "filename" not in out
    assert out["depth"] == 4 and out["boxes"] is True


def test_screenshot_keeps_only_type():
    """Everything but `type` is dropped, which leaves an inline viewport capture."""
    p = _proxy("browser__browser_take_screenshot")
    out = p._sanitize_args({"type": "png", "filename": "x.png", "element": "page", "target": "e5", "fullPage": True})
    assert out == {"type": "png"}


def test_other_browser_tools_unchanged():
    """Click, type and the rest pass through untouched."""
    p = _proxy("browser__browser_click")
    args = {"target": "e5", "element": "the button"}
    assert p._sanitize_args(args) == args
