"""Live QA: handed a link, she searched instead of opening it, then reported the search snippets
as if she had read the page. Two separate causes, both pinned here.

A dead link's fail note used to say only "couldn't be read"; live runs then showed her claiming
"the page says..." 4/5 of the time. The note must name the reason and forbid that claim.

The web_extract gate also scanned only the last user message, so a link from one turn back left
the tool off the table (4/4 searched instead); the lookback window fixes it (6/6 opened the link).
"""
from __future__ import annotations

import asyncio
import json
import socket
import types

import pytest

import kotoba.core.loop as loop
import kotoba.tools.builtin.web_extract as we
from kotoba.core import events, ssrf
from kotoba.tools import ToolContext

URL = "https://es.wikipedia.org/wiki/Live2D"


@pytest.fixture(autouse=True)
def _no_lookup_for_a_real_site(monkeypatch):
    """The SSRF guard resolves the host before a single byte is fetched, so every case in this file
    sent a DNS query for a site that belongs to somebody else. The answer is stubbed public: the guard
    still runs and still has to say yes, and nothing leaves the machine to get there."""
    def _public(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", _public)


# --- 1. the tool records the reason ------------------------------------------------------------------

class _Ctx:
    pass


def _fetch(monkeypatch, status=None, exc=None, body="<html><body></body></html>"):
    async def fake_get(client, url):
        if exc:
            raise exc
        return types.SimpleNamespace(status_code=status, text=body, headers={})

    async def no_jina(client, url):
        return None

    monkeypatch.setattr(we, "_get_safe", fake_get)
    monkeypatch.setattr(we, "_jina", no_jina)


@pytest.mark.parametrize("status,expected", [
    (404, "does not exist (HTTP 404)"),
    (410, "does not exist (HTTP 410)"),
    (403, "refused to serve it (HTTP 403)"),
    (503, "down or erroring (HTTP 503)"),
])
def test_http_status_becomes_a_reason(monkeypatch, status, expected):
    _fetch(monkeypatch, status=status)
    ctx = _Ctx()
    assert asyncio.run(we.execute({"url": URL}, ctx)) is None
    assert expected in ctx.web_extract_error, ctx.web_extract_error


def test_a_non_url_and_an_internal_address_each_get_their_own_reason():
    ctx = _Ctx()
    assert asyncio.run(we.execute({"url": "not a link"}, ctx)) is None
    assert "usable http(s) link" in ctx.web_extract_error
    assert asyncio.run(we.execute({"url": "http://127.0.0.1:9777/api/settings"}, ctx)) is None
    assert "internal" in ctx.web_extract_error


def test_a_reason_never_leaks_from_an_earlier_call(monkeypatch):
    """A stale reason would make her explain a failure that didn't happen."""
    _fetch(monkeypatch, status=404)
    ctx = _Ctx()
    asyncio.run(we.execute({"url": URL}, ctx))
    assert "404" in ctx.web_extract_error

    async def ok(client, url):
        return "Live2D es una tecnología de animación. " * 20, ""

    monkeypatch.setattr(we, "_direct", ok)
    assert asyncio.run(we.execute({"url": URL}, ctx))
    assert ctx.web_extract_error == ""


def test_execute_still_survives_a_ctx_it_cannot_write_to(monkeypatch):
    _fetch(monkeypatch, status=404)
    assert asyncio.run(we.execute({"url": URL}, None)) is None


# --- 1b. the non-2xx BODY is kept, bounded, and labelled ----------------------------------------------

# Verbatim from the live es.wikipedia 404 body: the readable text really is quotable.
WIKI_404 = (
    "<html><body><div id='content'><p>De Wikipedia, la enciclopedia libre</p>"
    "<p>Wikipedia todavía no tiene una página llamada «Live2D». "
    "Si el artículo incluso así no existe:</p></div></body></html>"
)


def test_the_404_body_survives_into_the_reason(monkeypatch):
    """The whole point: the site's own message is the useful answer, and it used to be discarded."""
    _fetch(monkeypatch, status=404, body=WIKI_404)
    ctx = _Ctx()
    assert asyncio.run(we.execute({"url": URL}, ctx)) is None
    assert "does not exist (HTTP 404)" in ctx.web_extract_error
    assert "todavía no tiene una página llamada" in ctx.web_extract_error


def test_the_kept_body_is_labelled_as_the_error_page_not_the_article(monkeypatch):
    """It must be impossible to mistake for the article she was asked to read: it rides the FAILURE
    path (execute returns None) and is introduced as the site's error page."""
    _fetch(monkeypatch, status=404, body=WIKI_404)
    ctx = _Ctx()
    assert asyncio.run(we.execute({"url": URL}, ctx)) is None
    assert "error page said:" in ctx.web_extract_error


def test_the_kept_body_is_bounded(monkeypatch):
    _fetch(monkeypatch, status=500, body="<html><body><p>" + ("boom " * 4000) + "</p></body></html>")
    ctx = _Ctx()
    asyncio.run(we.execute({"url": URL}, ctx))
    assert len(ctx.web_extract_error) < we._MAX_ERROR_BODY + 200, len(ctx.web_extract_error)
    assert ctx.web_extract_error.rstrip('"').endswith("…")


def test_a_non_html_error_body_still_yields_something(monkeypatch):
    _fetch(monkeypatch, status=429, body='{"error":"rate limit exceeded, retry in 60s"}')
    ctx = _Ctx()
    asyncio.run(we.execute({"url": URL}, ctx))
    assert "rate limit exceeded" in ctx.web_extract_error, ctx.web_extract_error


def test_an_empty_error_body_adds_no_noise(monkeypatch):
    _fetch(monkeypatch, status=404, body="")
    ctx = _Ctx()
    asyncio.run(we.execute({"url": URL}, ctx))
    assert ctx.web_extract_error == "that page does not exist (HTTP 404)"


def test_a_gone_page_does_not_fall_through_to_jina(monkeypatch):
    """Live-measured: r.jina.ai renders the es.wikipedia 404 shell and returns 1386 chars, which passed
    every check and was handed to the model AS THE ARTICLE. A 404 is a verdict — do not proxy-retry it."""
    called = []

    async def fake_get(client, url):
        return types.SimpleNamespace(status_code=404, text=WIKI_404, headers={})

    async def loud_jina(client, url):
        called.append(url)
        return "Ir al contenido Menú principal " + ("nav chrome " * 100)

    monkeypatch.setattr(we, "_get_safe", fake_get)
    monkeypatch.setattr(we, "_jina", loud_jina)
    ctx = _Ctx()
    assert asyncio.run(we.execute({"url": URL}, ctx)) is None
    assert called == [], "Jina must not be asked to re-fetch a page that does not exist"


def test_a_blocked_page_still_falls_through_to_jina(monkeypatch):
    """403 is an obstacle, not a verdict — the proxy fallback that exists for it must stay."""
    async def fake_get(client, url):
        return types.SimpleNamespace(status_code=403, text="<html>denied</html>", headers={})

    async def good_jina(client, url):
        return "Live2D es una tecnología de animación en tiempo real. " * 20

    monkeypatch.setattr(we, "_get_safe", fake_get)
    monkeypatch.setattr(we, "_jina", good_jina)
    assert "Live2D" in asyncio.run(we.execute({"url": URL}, _Ctx()))


# --- 1c. _is_junk matched its block-page words against the ARTICLE ------------------------------------

def test_a_long_article_about_captchas_is_not_a_block_page():
    """_is_junk scans the EXTRACTED text, so an article discussing captchas or access denials was
    thrown away as an anti-bot shell."""
    article = "This paper studies how a captcha and an access denied page affect users. " * 40
    assert len(article) > we._JUNK_MAX_CHARS
    assert not we._is_junk(article)


def test_a_short_block_page_is_still_junk():
    assert we._is_junk("Verify you are human. Please enable JavaScript and cookies to continue.")
    assert we._is_junk("Access denied. You are blocked by network security.")


def _jina_client(body: str):
    class _Resp:
        text = body

        def raise_for_status(self):
            pass

    class _Client:
        async def get(self, *a, **kw):
            return _Resp()

    return _Client()


def test_jina_also_stops_killing_long_articles_that_say_security_verification():
    """The same false positive had a second, inline copy in _jina."""
    article = "Security verification is a broad topic in modern web engineering. " * 40
    assert asyncio.run(we._jina(_jina_client(article), URL)) is not None


def test_jina_still_rejects_a_short_verification_shell():
    shell = "Security verification required. " * 12
    assert asyncio.run(we._jina(_jina_client(shell), URL)) is None


# --- the loop harness --------------------------------------------------------------------------------

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
        self.calls: list[dict] = []

    async def create(self, **kw):
        self.calls.append({"tools": list(kw.get("tools") or [])})
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
    client = _Client(scripts)

    async def go():
        await loop._run_iterations(
            client, ctx, items, asyncio.Queue(), {}, max_iterations=4, mode="companion",
            allow_risk={"read", "write", "exec", "network"}, toolset_filter=None,
        )
    asyncio.run(go())
    events.unregister(sess)
    return client.responses.calls, items


def _offers_extract(tools) -> bool:
    return any(t.get("name") == "web_extract" for t in tools)


# --- 2. the loop hands the model the reason AND forbids the false claim -------------------------------

def test_the_model_is_told_it_did_not_read_the_page(monkeypatch):
    """Through the REAL loop: web_extract 404s, and the function_call_output the model reads next says
    so, names the reason, and bans "the page says…". Before this fix it said only "That page couldn't be
    read", which is what let her answer "La página explica que…"."""
    _fetch(monkeypatch, status=404)
    items = [{"role": "user", "content": f"Entra en {URL} y dime que dice la pagina."}]
    scripts = [[_ev_call("web_extract", {"url": URL}, 1)], [_ev_text("…")]]
    _run(scripts, items, "wx-honesty")

    outs = [i for i in items if isinstance(i, dict) and i.get("type") == "function_call_output"]
    assert len(outs) == 1, items
    note = outs[0]["output"]
    assert "YOU DID NOT READ THAT PAGE" in note, note
    assert "does not exist (HTTP 404)" in note, note
    assert "would be a lie" in note, note
    assert "web_search" in note, "the fallback must still be prescribed"
    # A first wording said "FIRST tell the user … THEN use web_search" and live runs ended the turn on
    # the honest clause, promising a search they never ran. The note must forbid that.
    assert "announcing that you are about to search" in note, note
    assert "in this turn" in note, note


def test_a_page_that_reads_fine_gets_no_scolding(monkeypatch):
    async def ok(client, url):
        return "Live2D es una tecnología de animación en tiempo real. " * 30, ""

    monkeypatch.setattr(we, "_direct", ok)
    items = [{"role": "user", "content": f"Entra en {URL} y dime que dice."}]
    _run([[_ev_call("web_extract", {"url": URL}, 1)], [_ev_text("…")]], items, "wx-ok")

    outs = [i for i in items if isinstance(i, dict) and i.get("type") == "function_call_output"]
    assert "YOU DID NOT READ" not in outs[0]["output"]
    assert "Live2D" in outs[0]["output"]


# --- 3. a link one turn back still puts web_extract on the table --------------------------------------

def test_url_in_an_earlier_user_turn_still_offers_web_extract():
    """"Aquí tienes un link" … "ábrelo". The URL is one turn back; without this, web_extract was not
    offered at all and web_search was the only web tool she had."""
    items = [
        {"role": "user", "content": f"Mira esto: {URL}"},
        {"role": "assistant", "content": "Vale, lo tengo."},
        {"role": "user", "content": "Ábrelo y dime qué dice."},
    ]
    calls, _ = _run([[_ev_text("ok")]], items, "wx-back1")
    assert _offers_extract(calls[0]["tools"])


def test_url_in_the_current_message_still_offers_web_extract():
    items = [{"role": "user", "content": f"Entra en {URL}"}]
    calls, _ = _run([[_ev_text("ok")]], items, "wx-now")
    assert _offers_extract(calls[0]["tools"])


def test_no_url_anywhere_keeps_web_extract_off():
    """The original guard: with no link in play the model invents one to fetch. That stays."""
    items = [{"role": "user", "content": "¿Quién ganó el último Balón de Oro?"}]
    calls, _ = _run([[_ev_text("ok")]], items, "wx-nourl")
    assert not _offers_extract(calls[0]["tools"])


def test_an_assistant_citation_url_does_not_open_the_gate():
    """web_search answers are full of citation URLs she was never asked to open."""
    items = [
        {"role": "user", "content": "¿Qué es Live2D?"},
        {"role": "assistant", "content": "Es una tecnología ([live2d.com](https://www.live2d.com/))"},
        {"role": "user", "content": "Ah, interesante."},
    ]
    calls, _ = _run([[_ev_text("ok")]], items, "wx-cite")
    assert not _offers_extract(calls[0]["tools"])


# --- the lookback window itself ----------------------------------------------------------------------

def _user(*texts):
    return [{"role": "user", "content": t} for t in texts]


def test_lookback_window_is_bounded():
    assert loop._has_url(_user(f"mira {URL}", "a", "b"))
    assert not loop._has_url(_user(f"mira {URL}", "a", "b", "c")), \
        "a link 4 user-turns back is stale — she should search, not re-open it"
    assert loop._has_url([{"role": "developer", "content": "x"}] + _user(f"{URL}"))


def test_lookback_default():
    assert loop._URL_LOOKBACK == 3  # override with KOTOBA_URL_LOOKBACK


def test_list_content_is_scanned_too():
    """Attachment turns carry a content LIST, not a string."""
    items = [{"role": "user", "content": [{"type": "input_text", "text": f"lee {URL}"}]}]
    assert loop._has_url(items)
