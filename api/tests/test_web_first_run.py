"""First run over HTTP: the same wizard `kotoba setup` runs in a terminal, driven from the browser.

The setup state, the model pin, and the ElevenLabs check all live in one place shared by the CLI
and the web, so a terminal install and a browser install cannot drift into disagreeing about
whether someone is set up.

A key is verified BEFORE it is stored — an unchecked key that fails on her first real request looks
identical to a broken install. A narrow-scoped key or an unreachable network keeps the key; only a
genuine refusal drops it. The whole surface sits behind the gate.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from fastapi.testclient import TestClient

import kotoba.server as main
from kotoba.cli import wizard
from kotoba.core import app_settings, first_run, llm, providers, voice_key


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _db(client):
    return client.app.state.db


class _Reply:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def _provider(monkeypatch, ok=True, seen=None):
    """Stand in for the brain-key round trip `/api/settings/llm-key` now makes before it stores.

    Unstubbed, every test that saves a key would open a real TLS connection to the provider and post the
    made-up key to it — the same leak `_elevenlabs` exists to stop one endpoint lower."""
    async def verify(provider_id, key):
        if seen is not None:
            seen.append((provider_id, key))
        return (True, "responded.") if ok else (False, "AuthenticationError: incorrect api key")

    monkeypatch.setattr(first_run, "verify_key", verify)
    return seen


def _elevenlabs(monkeypatch, reply, seen=None):
    """Stand in for ElevenLabs, recording the path asked for."""
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            if seen is not None:
                seen.append(url)
            if isinstance(reply, Exception):
                raise reply
            return reply

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: Client())


# --- one predicate, two front doors ----------------------------------------------------------------

def test_status_answers_the_same_question_the_terminal_asks(client):
    """`GET /api/setup/status` must not be a second opinion: it is `core.first_run.needed`, which is
    also what `kotoba setup` asks, so the two installs can never disagree about who is set up."""
    r = client.get("/api/setup/status")
    assert r.status_code == 200
    body = r.json()
    assert body["needed"] is True                       # the suite runs on an empty, key-less install
    assert body["provider"] in providers.PROVIDERS
    assert body["has_voice_key"] is False


def test_a_saved_key_ends_first_run_for_both_front_doors(client, monkeypatch):
    """The terminal's answer and the browser's answer are the same call, so they move together."""
    _provider(monkeypatch)
    assert client.portal.call(wizard.needed, _db(client)) is True
    client.post("/api/settings/llm-key", json={"provider": "openai", "key": "sk-a-real-looking-key"})
    assert client.get("/api/setup/status").json()["needed"] is False
    assert client.portal.call(wizard.needed, _db(client)) is False
    assert "first_run.needed(db)" in pathlib.Path(wizard.__file__).read_text(encoding="utf-8"), \
        "the CLI grew its own copy of the predicate again"


def test_status_never_describes_a_key(client, monkeypatch):
    _provider(monkeypatch)
    client.post("/api/settings/llm-key", json={"provider": "openai", "key": "sk-secret-value-here"})
    assert "sk-secret-value-here" not in client.get("/api/setup/status").text


def test_status_carries_the_answers_a_reconfigure_is_keeping(client):
    """The reconfigure recap prints every answer, and it had no source for the three names — so somebody
    changing one of them was shown `—` against the others, which reads as an install about to be wiped.

    Written through the same endpoints the wizard uses, so a rename anywhere moves both together."""
    client.post("/api/settings/user-name", json={"name": "Ada"})
    client.post("/api/settings/soul", json={"name": "Yuki", "language": "es"})

    body = client.get("/api/setup/status").json()
    assert body["user_name"] == "Ada"
    assert body["companion_name"] == "Yuki"
    assert body["language"] == "es"


def test_status_reports_an_unnamed_install_as_unnamed_not_as_missing(client):
    """A fresh install has no person's name. The recap distinguishes "not answered" from "answered
    blank" by presence, so the field has to BE there and be empty — a key the screen has to guard
    against is how the dashes came back in the first place."""
    body = client.get("/api/setup/status").json()
    assert body["user_name"] == ""
    assert isinstance(body["companion_name"], str)
    assert isinstance(body["language"], str)


def test_the_placeholder_never_becomes_the_key_that_ends_first_run(client, monkeypatch):
    """`sk-...` out of `.env.example` was accepted, stored encrypted, and answered `needed: false` for
    good, while `llm.get_client()` handed the SDK a placeholder and every turn 401'd. Caught before the
    round trip, because a template string is not a question worth asking a provider."""
    seen = _provider(monkeypatch, seen=[])
    r = client.post("/api/settings/llm-key", json={"provider": "openai", "key": "sk-..."})
    assert r.status_code == 400, "the placeholder was stored"
    assert seen == [], "the placeholder cost a round trip"
    assert client.get("/api/setup/status").json()["needed"] is True
    assert llm.get_client() is None, "a placeholder that builds a client 401s on every turn"


# --- the brain key: verified before it is stored, like the voice key beside it ----------------------

def test_a_key_that_never_worked_is_neither_stored_nor_able_to_end_first_run(client, monkeypatch):
    """The endpoint SAVED and left `/api/settings/llm-test` to validate afterwards, and the two were
    never one decision. Measured: a key the provider refuses stayed in the keystore, `needed` read it as
    a configured machine, and the only undo was a second POST from the browser — which needs the tab to
    survive a round trip to the provider and never runs at all for somebody coming back."""
    _provider(monkeypatch, ok=False)
    r = client.post("/api/settings/llm-key", json={"provider": "openai", "key": "sk-never-worked"})
    assert r.status_code == 400, "a key that was never stored answered like a successful write"
    assert "incorrect api key" in r.json()["detail"], "the screen was told nothing it can act on"
    assert client.portal.call(_db(client).get_key, "llm:openai:api_key") in (None, "")
    assert client.get("/api/setup/status").json()["needed"] is True
    assert client.portal.call(wizard.needed, _db(client)) is True
    assert llm.get_client() is None


def test_the_refusal_never_echoes_the_key(client, monkeypatch, caplog):
    """A provider that quotes the offending request back is how a key reaches a screen and a log. The
    redaction has to live inside `verify_key`, where the key and the message meet — stubbing the probe
    would only prove the stub is clean."""
    class _Client:
        def __init__(self, **kw):
            pass

        class responses:
            @staticmethod
            async def create(**kw):
                raise RuntimeError("AuthenticationError: incorrect api key sk-secret-echo")

        async def close(self):
            pass

    import openai
    monkeypatch.setattr(openai, "AsyncOpenAI", _Client)
    with caplog.at_level("WARNING"):
        ok, detail = client.portal.call(first_run.verify_key, "openai", "sk-secret-echo")
    assert ok is False
    assert "sk-secret-echo" not in detail, "the provider echoed the key straight back to the screen"
    assert "sk-secret-echo" not in caplog.text, "the key went into the log"

    r = client.post("/api/settings/llm-key", json={"provider": "openai", "key": "sk-secret-echo"})
    assert r.status_code == 400 and "sk-secret-echo" not in r.text


def test_the_key_is_redacted_BEFORE_the_message_is_cut(client, monkeypatch, caplog):
    """Order, not presence. `_redact` replaces the key with an ellipsis by matching it whole — so cutting
    the provider's message to 160 characters FIRST and redacting the stump second cannot work: when the
    cut lands inside the key, what is left is a usable prefix that no longer matches the string being
    replaced, and it goes to the screen and to the log. Its sibling `/api/settings/llm-test` pins the
    same ordering."""
    key = "sk-liveKEY-do-not-print-0123456789abcdef"
    # Land the key so the 160-char cut falls INSIDE it, leaving a usable prefix behind.
    head = "AuthenticationError: "
    start = 133
    message = head + "x" * (start - len(head)) + key + " was rejected"
    assert message.index(key) == start
    assert start < 160 < start + len(key), "the cut must land inside the key"
    assert len(key[:24]) == 24 and start + 24 < 160, "a real prefix must survive the cut"

    class _Client:
        def __init__(self, **kw):
            pass

        class responses:
            @staticmethod
            async def create(**kw):
                raise RuntimeError(message)

        async def close(self):
            pass

    import openai
    monkeypatch.setattr(openai, "AsyncOpenAI", _Client)
    with caplog.at_level("WARNING"):
        ok, detail = client.portal.call(first_run.verify_key, "openai", key)

    assert ok is False
    for surface, text in (("screen", detail), ("log", caplog.text)):
        assert key not in text, f"the whole key reached the {surface}"
        assert key[:24] not in text, f"a usable prefix of the key reached the {surface}"


def test_a_refused_key_leaves_the_working_one_alone(client, monkeypatch):
    """Somebody replacing a good key with a typo must not be left with neither. Removing a key is an
    explicit act (the panel confirms it); a failed save is not one."""
    _provider(monkeypatch)
    client.post("/api/settings/llm-key", json={"provider": "openai", "key": "sk-the-good-one"})
    _provider(monkeypatch, ok=False)
    client.post("/api/settings/llm-key", json={"provider": "openai", "key": "sk-a-typo"})
    assert client.portal.call(_db(client).get_key, "llm:openai:api_key") == "sk-the-good-one"


def test_an_empty_key_still_clears_without_asking_the_provider(client, monkeypatch):
    _provider(monkeypatch)
    client.post("/api/settings/llm-key", json={"provider": "openai", "key": "sk-the-good-one"})
    seen = _provider(monkeypatch, seen=[])
    r = client.post("/api/settings/llm-key", json={"provider": "openai", "key": ""})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "provider": "openai", "has_key": False, "detail": ""}
    assert seen == [], "clearing a key asked the provider about the empty string"
    assert client.portal.call(_db(client).get_key, "llm:openai:api_key") in (None, "")


def test_the_probe_builds_its_own_client_and_leaves_no_trace(client, monkeypatch):
    """`llm-test` validates whatever is ALREADY ACTIVE, so it could never gate a CANDIDATE — reordering
    the two calls would only have tested the key the endpoint had just replaced. `verify_key` builds a
    client from the key it was handed, which is why a refusal has nothing to roll back."""
    calls: list = []

    class _Client:
        def __init__(self, **kw):
            calls.append(kw)

        class responses:
            @staticmethod
            async def create(**kw):
                raise RuntimeError("401 invalid")

        async def close(self):
            pass

    import openai
    monkeypatch.setattr(openai, "AsyncOpenAI", _Client)
    ok, detail = client.portal.call(first_run.verify_key, "openai", "sk-candidate")
    assert ok is False and "sk-candidate" not in detail
    assert calls and calls[0]["api_key"] == "sk-candidate"
    assert llm._provider_keys == {}, "the probe wrote the candidate into the live key cache"


def test_the_probe_never_validates_against_the_other_providers_model(client, monkeypatch):
    """A model belonging to the other company comes back 404 and gets reported to a person as a bad
    key — the same mismatch `pin_model` exists to prevent, arriving through a different door."""
    app_settings.set_runtime("provider", "openai")
    app_settings.set_runtime("model", "gpt-5.6-luna")
    asked: list = []

    class _Client:
        def __init__(self, **kw):
            pass

        class responses:
            @staticmethod
            async def create(model, **kw):
                asked.append(model)
                return object()

        async def close(self):
            pass

    import openai
    monkeypatch.setattr(openai, "AsyncOpenAI", _Client)
    client.portal.call(first_run.verify_key, "xai", "xai-candidate")
    assert asked == [providers.PROVIDERS["xai"].default_model], f"asked xAI about {asked}"


# --- can she talk RIGHT NOW, not "does a key exist somewhere" ---------------------------------------

def test_a_key_for_the_other_provider_does_not_end_first_run(client, monkeypatch):
    """`.env.example` ships a line for both companies, so this is an ordinary machine, not a contrived
    one. With XAI_API_KEY set and `provider` on its openai default, `needed` answered False while
    `llm.get_client()` answered None: the install called itself configured, `/app` mounted, and she
    could not complete one turn — and neither front door offered to fix it, because both ask this
    function. The accepted cost is that switching to a provider you have no key for sends you here."""
    monkeypatch.setenv("XAI_API_KEY", "xai-a-real-looking-key")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    app_settings.set_runtime("provider", "openai")
    app_settings.set_runtime("model", "gpt-5.6-luna")

    assert llm.get_client() is None, "this test no longer describes an install that cannot talk"
    assert client.portal.call(first_run.needed, _db(client)) is True
    assert client.get("/api/setup/status").json()["needed"] is True
    assert client.portal.call(wizard.needed, _db(client)) is True


def test_the_active_providers_own_key_still_ends_first_run(client, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-a-real-looking-key")
    app_settings.set_runtime("provider", "xai")
    assert client.portal.call(first_run.needed, _db(client)) is False


def test_a_virgin_install_still_asks(client, monkeypatch):
    """The case a stranger arrives on, verified in a container: no key anywhere, `needed: true`."""
    for var in ("OPENAI_API_KEY", "XAI_API_KEY"):
        monkeypatch.setenv(var, "")
    assert client.portal.call(first_run.needed, _db(client)) is True
    assert client.get("/api/setup/status").json()["needed"] is True


def test_a_saved_key_for_the_inactive_provider_does_not_end_first_run(client, monkeypatch):
    """The saved half moves with the env half: the keystore can hold an xAI key from a provider the
    person has since switched away from, and it configures nothing for the one answering now."""
    client.portal.call(_db(client).save_key, "llm:xai:api_key", "xai-a-real-looking-key")
    app_settings.set_runtime("provider", "openai")
    assert client.portal.call(first_run.needed, _db(client)) is True
    app_settings.set_runtime("provider", "xai")
    assert client.portal.call(first_run.needed, _db(client)) is False


def test_a_placeholder_already_in_the_keystore_still_asks_for_first_run(client):
    """The rows saved before the check above existed. Read as a key they skip first run on the one
    install that most needs it, so the filter is on the read as well as the write."""
    client.portal.call(_db(client).save_key, "llm:openai:api_key", "sk-...")
    assert client.portal.call(first_run.needed, _db(client)) is True


def test_a_placeholder_row_is_never_reported_as_a_saved_key(client):
    """The same filter, one layer out. `needed` says first run is still required while the setup
    status said a key was saved, and the key step then told somebody "one is saved; Enter keeps it" —
    on the one step first run cannot skip, where Enter does nothing and no dot leads forward.

    The settings panel reads the same row and has to agree, or the two screens describe two installs."""
    client.portal.call(_db(client).save_key, "llm:openai:api_key", "sk-...")

    status = client.get("/api/setup/status").json()
    assert status["needed"] is True
    assert status["keys"]["openai"]["saved"] is False, "a template is a row, not a key"

    panel = client.get("/api/settings").json()
    openai = next(p for p in panel["llm"]["providers"] if p["id"] == "openai")
    assert openai["has_key"] is False, "the panel and the setup status must not disagree"


def test_a_real_row_is_still_reported_as_saved(client):
    """The other direction, so the filter cannot be tightened into always answering no."""
    client.portal.call(_db(client).save_key, "llm:openai:api_key", "sk-a-perfectly-good-key")

    status = client.get("/api/setup/status").json()
    assert status["keys"]["openai"]["saved"] is True
    panel = client.get("/api/settings").json()
    assert next(p for p in panel["llm"]["providers"] if p["id"] == "openai")["has_key"] is True


def test_no_test_inherits_a_neighbours_saved_key():
    """Defined after the three tests above BECAUSE they each save one: `llm._provider_keys` is module
    state, so without the reset in conftest this file handed a live client to every test that follows it
    in the process — including the ones whose whole point is that there is no key."""
    assert not llm._provider_keys, f"entered holding {sorted(llm._provider_keys)}"
    assert llm.get_client() is None


# --- choosing a brain drags a setting with it ------------------------------------------------------

def test_choosing_xai_moves_the_model_off_the_openai_default(client):
    """`model` is one global setting. Left on the OpenAI default, the very next `llm-test` comes back 404 and
    a perfectly good xai- key is reported as a bad key — which is why this is not plain
    `/api/settings/runtime` with key=provider."""
    r = client.post("/api/setup/provider", json={"provider": "xai"})
    assert r.status_code == 200
    assert r.json()["provider"] == "xai"
    assert llm.model_name("companion") == providers.PROVIDERS["xai"].default_model
    assert providers.serves_model(llm.model_name("companion"), "xai")


def test_a_model_the_person_chose_is_never_overwritten(client):
    app_settings.set_runtime("model", "grok-4.6")
    client.post("/api/setup/provider", json={"provider": "xai"})
    assert llm.model_name("companion") == "grok-4.6"


def test_an_unknown_brain_is_refused(client):
    assert client.post("/api/setup/provider", json={"provider": "acme"}).status_code == 400
    assert client.post("/api/setup/provider", json={}).status_code == 400


# --- the three answers that are hers, and the one that is theirs -----------------------------------

def test_their_name_lands_where_the_terminal_puts_it(client):
    """`user_profile['name']` — the destination `cli/wizard._ask_your_name` writes to, not a new one."""
    assert client.post("/api/settings/user-name", json={"name": "Jordan"}).status_code == 200
    profile = client.portal.call(_db(client).fetch_user_profile_as_markdown)
    assert "name: Jordan" in profile


def test_a_skipped_name_erases_nothing(client):
    client.post("/api/settings/user-name", json={"name": "Jordan"})
    client.post("/api/settings/user-name", json={"name": "   "})
    assert "name: Jordan" in client.portal.call(_db(client).fetch_user_profile_as_markdown)


def test_her_name_comes_back_as_the_row_actually_holds_it(client):
    """`update_soul_config` refuses a value that does not look like a name, silently and on purpose (the
    recurring "she's called 42"). The caller has no other way to know, so the answer is the read-back —
    that is what lets the screen say "I'll answer to it" and be telling the truth."""
    assert client.post("/api/settings/soul", json={"name": "Aki"}).json()["name"] == "Aki"
    assert client.post("/api/settings/soul", json={"name": "42"}).json()["name"] == "Aki"


def test_a_junk_language_comes_back_as_auto(client):
    assert client.post("/api/settings/soul", json={"language": "es"}).json()["language"] == "es"
    assert client.post("/api/settings/soul", json={"language": "['a']"}).json()["language"] == "auto"


# --- the voice key: verified before it is stored ---------------------------------------------------

def test_a_good_voice_key_is_stored_and_made_live(client, monkeypatch):
    from kotoba.core.voice import config as voice_config

    monkeypatch.setattr(voice_config, "_api_key", "", raising=False)
    seen: list[str] = []
    _elevenlabs(monkeypatch, _Reply(200), seen)

    r = client.post("/api/settings/voice-key", json={"key": "sk_a_real_voice_key"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert "sk_a_real_voice_key" not in r.text, "the key was echoed back"
    assert seen == ["https://api.elevenlabs.io/v1/voices"], f"asked {seen}"
    assert voice_config.resolve_api_key() == "sk_a_real_voice_key", "stored where nothing reads it"
    body = client.get("/api/setup/status").json()
    assert body["has_voice_key"] is True
    # WHERE, not only whether. One boolean made the step offer to replace an environment key and made
    # the terminal send somebody who had typed theirs to look for a variable that does not exist.
    assert body["voice"] == {"saved": True, "env": ""}


def test_a_key_elevenlabs_calls_invalid_is_never_stored(client, monkeypatch):
    from kotoba.core.voice import config as voice_config

    monkeypatch.setattr(voice_config, "_api_key", "", raising=False)
    _elevenlabs(monkeypatch, _Reply(401, {"detail": {"status": "invalid_api_key"}}))

    r = client.post("/api/settings/voice-key", json={"key": "sk_wrong"})
    assert r.json()["ok"] is False
    assert voice_config.resolve_api_key() == ""
    assert client.portal.call(_db(client).get_key, "voice:elevenlabs:api_key") in (None, "")


def test_a_narrow_scoped_key_is_kept(client, monkeypatch):
    """A 401 is only a bad key when ElevenLabs itself says `invalid_api_key`; any other refusal is a
    real key with narrow permissions, and calling a good key bad is worse than no check at all."""
    from kotoba.core.voice import config as voice_config

    monkeypatch.setattr(voice_config, "_api_key", "", raising=False)
    _elevenlabs(monkeypatch, _Reply(403, {"detail": {"status": "missing_permissions"}}))
    assert client.post("/api/settings/voice-key", json={"key": "sk_narrow"}).json()["ok"] is True
    assert voice_config.resolve_api_key() == "sk_narrow"


def test_a_key_that_cannot_be_checked_is_kept_rather_than_lost(client, monkeypatch):
    from kotoba.core.voice import config as voice_config

    monkeypatch.setattr(voice_config, "_api_key", "", raising=False)
    _elevenlabs(monkeypatch, OSError("no route to host"))
    body = client.post("/api/settings/voice-key", json={"key": "sk_unchecked"}).json()
    assert body["ok"] is True and "couldn't reach" in body["detail"]
    assert voice_config.resolve_api_key() == "sk_unchecked"


@pytest.mark.parametrize("status", [302, 404, 407, 418])
def test_an_answer_that_did_not_come_from_elevenlabs_keeps_the_key(client, monkeypatch, status):
    """A captive portal answers the redirect; a corporate proxy answers the 404. Both used to fall past
    every branch to a final `return False` and destroy a perfectly good key — the exact blip the module
    promises to survive, and the reason the sentence "a key that cannot be checked is kept rather than
    lost" was written. Only ElevenLabs saying `invalid_api_key`, or throttling this key, is a refusal."""
    from kotoba.core.voice import config as voice_config

    monkeypatch.setattr(voice_config, "_api_key", "", raising=False)
    _elevenlabs(monkeypatch, _Reply(status))
    body = client.post("/api/settings/voice-key", json={"key": "sk_behind_a_proxy"}).json()
    assert body["ok"] is True, f"{status} threw the key away"
    assert voice_config.resolve_api_key() == "sk_behind_a_proxy"


def test_the_two_answers_that_really_are_refusals_still_refuse(client, monkeypatch):
    """The keep-it posture is not "keep everything": a verdict FROM ElevenLabs about THIS key stands."""
    from kotoba.core.voice import config as voice_config

    monkeypatch.setattr(voice_config, "_api_key", "", raising=False)
    _elevenlabs(monkeypatch, _Reply(401, {"detail": {"status": "invalid_api_key"}}))
    assert client.post("/api/settings/voice-key", json={"key": "sk_wrong"}).json()["ok"] is False
    _elevenlabs(monkeypatch, _Reply(429))
    assert client.post("/api/settings/voice-key", json={"key": "sk_throttled"}).json()["ok"] is False
    assert voice_config.resolve_api_key() == ""


def test_the_placeholder_is_caught_without_a_round_trip(client, monkeypatch):
    seen: list[str] = []
    _elevenlabs(monkeypatch, _Reply(200), seen)
    assert client.post("/api/settings/voice-key", json={"key": "el_..."}).json()["ok"] is False
    assert seen == [], "the placeholder cost a round trip"


def test_an_empty_voice_key_is_refused_not_treated_as_an_erasure(client):
    assert client.post("/api/settings/voice-key", json={"key": ""}).status_code == 400


def test_the_check_never_asks_the_endpoint_that_lies():
    """`/v1/user` is what every guide reaches for and it is the wrong question here: a key that speaks
    perfectly answers it 401 for want of the `user_read` scope."""
    import re

    source = pathlib.Path(voice_key.__file__).read_text(encoding="utf-8")
    assert re.findall(r"api\.elevenlabs\.io(/v1/[\w/]+)", source) == ["/v1/voices"]


# --- and all of it behind the gate -----------------------------------------------------------------

@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/setup/status", None),
    ("POST", "/api/setup/provider", {"provider": "openai"}),
    ("POST", "/api/settings/user-name", {"name": "x"}),
    ("POST", "/api/settings/voice-key", {"key": "sk_x"}),
])
def test_first_run_is_behind_the_password_gate(client, monkeypatch, method, path, body):
    """A virgin install has no password and setup calls the API freely. A CONFIGURED one does, and the
    setup screen has to reach it through the gate rather than 401 in silence.

    ElevenLabs is stood in for even though this test is about the gate: unstubbed, the authorised half of
    the voice-key case opened a real TLS connection to api.elevenlabs.io and posted `sk_x` to it on every
    run of the suite — measured. Offline it still passed, twelve seconds later, off the timeout path."""
    _elevenlabs(monkeypatch, _Reply(200))
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "letmein")
    assert client.request(method, path, json=body).status_code == 401
    ok = client.request(method, path, json=body, headers={"Authorization": "Bearer letmein"})
    assert ok.status_code != 401


@pytest.mark.parametrize("body", [{}, {"key": 42}, {"key": ["a"]}, {"provider": ["x"]}, {"name": 999}])
def test_no_5xx_on_garbage(client, monkeypatch, body):
    _elevenlabs(monkeypatch, _Reply(200))
    for path in ("/api/setup/provider", "/api/settings/user-name", "/api/settings/voice-key"):
        assert client.post(path, json=body).status_code < 500, path
