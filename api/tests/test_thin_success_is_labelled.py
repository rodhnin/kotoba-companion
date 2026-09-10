"""A tool that SUCCEEDS but returns little gets reported as an access failure — found live across two
vendors: web_extract returned example.com's 203 real characters, and both gpt-5.4-mini and grok-4.6
opened with "could not open that page" before describing it correctly. They had the content; the bare
thin string made them read the outcome as broken. Prompt rules moved nothing, and the cross-vendor
repro rules out a training-prior cause — the fix is in what the TOOL hands back: fetch outcome and
exact size stated as facts the model cannot mistake for an error. Two more of the same class: `_jina`'s
200-char floor rejected short legitimate pages as "no readable text" (example.com survived only because
its title pushed it to 203, body alone is 187; the floor is now a shell detector at 40). And `read_file`
on an empty file returned "", read as "tool returned nothing" — a fail note on a file that existed and
was read. Both now state the fact instead."""
from __future__ import annotations

import asyncio
import json
import types

import kotoba.core.loop as loop
import kotoba.core.ssrf as ssrf
import kotoba.tools.builtin.web_extract as we
from kotoba.core import events
from kotoba.tools import ToolContext

URL = "https://example.com"


def _quoted(out: str) -> str:
    """What the tool actually read, out of the fence it is now returned inside. Fetched text is quoted
    under a random-tagged marker pair so a page cannot end the quote and address her directly."""
    import re

    m = re.search(r"<<<QUOTED ([0-9a-f]{6})>>>\n(.*)\n<<<END \1>>>", out, re.S)
    assert m, f"no quoted region in: {out[:200]!r}"
    return m.group(2)

# The canonical IANA page, verbatim structure — the page the finding was measured on.
EXAMPLE_HTML = """<!doctype html>
<html>
<head>
    <title>Example Domain</title>
    <meta charset="utf-8" />
</head>
<body>
<div>
    <h1>Example Domain</h1>
    <p>This domain is for use in illustrative examples in documents. You may use this
    domain in literature without prior coordination or asking for permission.</p>
    <p><a href="https://www.iana.org/domains/example">More information...</a></p>
</div>
</body>
</html>
"""


class _Ctx:
    pass


def _serve(monkeypatch, html=EXAMPLE_HTML):
    async def fake_get(client, url):
        return types.SimpleNamespace(status_code=200, text=html, headers={})

    monkeypatch.setattr(we, "_get_safe", fake_get)
    monkeypatch.setattr(ssrf, "url_block_reason", lambda url: None)


# --- 1. the thin success arrives as a fact ------------------------------------------------------------

def test_example_com_is_labelled_as_a_thin_success(monkeypatch):
    _serve(monkeypatch)
    ctx = _Ctx()
    out = asyncio.run(we.execute({"url": URL}, ctx))
    assert out is not None
    assert "the fetch SUCCEEDED." in out, out
    assert "203 characters" in out, out
    assert "It reads like a domain placeholder page." in out
    assert "not an access failure" in out
    assert "This domain is for use in illustrative examples" in _quoted(out), \
        "the text itself must still be there"
    assert "the fetch SUCCEEDED" not in _quoted(out), "our own note must sit outside the quote"
    assert ctx.web_extract_error == ""


def test_a_normal_article_gets_no_label(monkeypatch):
    async def ok(client, url):
        return "Live2D es una tecnología de animación en tiempo real. " * 30, ""

    monkeypatch.setattr(we, "_direct", ok)
    monkeypatch.setattr(ssrf, "url_block_reason", lambda url: None)
    out = asyncio.run(we.execute({"url": URL}, _Ctx()))
    assert _quoted(out).startswith("Live2D"), "a page with real length arrives exactly as fetched"
    assert "the fetch SUCCEEDED" not in out, "a page of real length gets no thin note"


def test_the_placeholder_claim_needs_a_signal():
    thin = "Release notes\n\nVersion 3.2.1 fixes the login redirect loop reported last week."
    note = we._thin_note(thin)
    assert f"{len(thin)} characters" in note
    assert "placeholder" not in note, "no supporting phrase → no placeholder judgement"


def test_a_long_article_mentioning_under_construction_is_untouched():
    article = "The bridge stayed under construction for a decade, and the report explains why. " * 20
    assert we._thin_note(article) == "", "a real article gets no thin note at all"


# --- 2. the _jina floor is a shell detector, not a quality bar ----------------------------------------

def _jina_client(body: str):
    class _Resp:
        text = body

        def raise_for_status(self):
            pass

    class _Client:
        async def get(self, *a, **kw):
            return _Resp()

    return _Client()


def test_jina_now_passes_a_short_legitimate_page():
    """example.com's body WITHOUT the duplicated title is 187 chars — the old 200 floor rejected it as
    "no readable text". Short and real must pass; execute labels it."""
    body = (
        "Example Domain This domain is for use in illustrative examples in documents. You may use this "
        "domain in literature without prior coordination or asking for permission. More information..."
    )
    assert 40 <= len(body) < 200, len(body)
    assert asyncio.run(we._jina(_jina_client(body), URL)) == body


def test_jina_still_rejects_a_bare_title_shell():
    assert asyncio.run(we._jina(_jina_client("Just a moment..."), URL)) is None


def test_jina_still_rejects_the_modern_cloudflare_interstitial():
    shell = "Just a moment... Verifying you are human. This may take a few seconds."
    assert asyncio.run(we._jina(_jina_client(shell), URL)) is None


# --- 3. through the real loop: the model reads the fact, not a bare string ----------------------------

def _ev_text(t):
    return types.SimpleNamespace(type="response.output_text.delta", delta=t)


def _ev_call(name, args, i):
    item = types.SimpleNamespace(
        type="function_call", name=name, arguments=json.dumps(args), call_id=f"c{i}"
    )
    return types.SimpleNamespace(type="response.output_item.done", item=item)


class _Stream:
    def __init__(self, evs):
        self._evs = evs

    def __aiter__(self):
        async def gen():
            for e in self._evs:
                yield e
        return gen()


class _Responses:
    def __init__(self, scripts):
        self._scripts, self._i = scripts, 0

    async def create(self, **kw):
        evs = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return _Stream(evs)


class _Client:
    def __init__(self, scripts):
        self.responses = _Responses(scripts)


class _DB:
    async def insert_audit_log(self, **kw):
        pass


def _run(scripts, items, sess):
    events.register(sess)
    ctx = ToolContext(db=_DB(), session_id=sess, client=None, mode="companion", channel="text")
    ctx.approval = None

    async def go():
        await loop._run_iterations(
            _Client(scripts), ctx, items, asyncio.Queue(), {}, max_iterations=4, mode="companion",
            allow_risk={"read", "write", "exec", "network"}, toolset_filter=None,
        )
    asyncio.run(go())
    events.unregister(sess)
    return items


def test_the_function_call_output_carries_the_fact_and_no_fail_note(monkeypatch):
    _serve(monkeypatch)
    items = [{"role": "user", "content": f"Entra en {URL} y dime que dice."}]
    scripts = [[_ev_call("web_extract", {"url": URL}, 1)], [_ev_text("…")]]
    _run(scripts, items, "thin-ok")

    outs = [i for i in items if isinstance(i, dict) and i.get("type") == "function_call_output"]
    assert len(outs) == 1, items
    note = outs[0]["output"]
    assert "the fetch SUCCEEDED" in note, note
    assert "203 characters" in note
    assert "YOU DID NOT READ" not in note
    assert "tool failed" not in note


# --- 4. read_file: an empty file is a successful read, said as a fact ---------------------------------

def _read_ctx(tmp_path):
    ctx = ToolContext(db=_DB(), session_id="thin-read", client=None, mode="companion", channel="text")
    ctx.workdir = tmp_path
    ctx.call_id = "c1"
    return ctx


def _heartbeat_read(ctx, args):
    return asyncio.run(loop.execute_with_heartbeat("read_file", args, asyncio.Queue(), {}, ctx))


def test_an_empty_file_reads_ok_and_says_so(tmp_path):
    (tmp_path / "empty.txt").write_text("")
    ok, result = _heartbeat_read(_read_ctx(tmp_path), {"path": "empty.txt"})
    assert ok, "the read succeeded — ok=False was the upstream false failure"
    assert "empty (0 bytes)" in result, result
    assert result != "The tool returned nothing."


def test_a_start_past_the_end_states_the_line_count(tmp_path):
    (tmp_path / "short.txt").write_text("line1\nline2\n")
    ok, result = _heartbeat_read(_read_ctx(tmp_path), {"path": "short.txt", "start": 50})
    assert ok
    assert "only 2 lines" in result and "start=50" in result, result


def test_a_whitespace_only_file_states_the_fact(tmp_path):
    (tmp_path / "blank.txt").write_text("\n\n   \n")
    ok, result = _heartbeat_read(_read_ctx(tmp_path), {"path": "blank.txt"})
    assert ok
    assert "whitespace" in result or "empty" in result, result


def test_a_normal_read_is_unchanged(tmp_path):
    (tmp_path / "notes.txt").write_text("alpha\nbeta\n")
    ok, result = _heartbeat_read(_read_ctx(tmp_path), {"path": "notes.txt"})
    assert ok
    assert _quoted(result) == "alpha\nbeta"
