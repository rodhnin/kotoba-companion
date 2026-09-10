"""ask_user(kind='key') credential round-trip and namespace security.

Four invariants:
  1. A 'key' card with no `name` does not open a card (the value would be promised then dropped).
  2. A 'key' card with a `name` opens the card and carries `name` in the SSE frame.
  3. POST /input with kind='key' stores the value under the cred: namespace — never raw.
  4. A name containing ':' is rejected (400) so a client can never address system keys (llm:*/mcp:*).
"""
from __future__ import annotations

import asyncio
import types

import pytest

import kotoba.core.interaction as interaction
import kotoba.tools.builtin.ask_user as ask_user





def _ctx(mode: str = "companion"):
    return types.SimpleNamespace(session_id="cred-sess", mode=mode)


def _patch_emits(monkeypatch):
    emitted: list[tuple[str, dict]] = []

    async def fake_emit_task(session_id, kind, **data):
        emitted.append((kind, data))

    async def fake_emit_emotion(session_id, emotion):
        pass

    monkeypatch.setattr(interaction, "emit_task", fake_emit_task)
    monkeypatch.setattr(interaction, "emit_emotion", fake_emit_emotion)
    return emitted


# --- Invariant 1: missing name blocks the card ---------------------------------------------------

def test_key_without_name_returns_error_not_card(monkeypatch):
    emitted = _patch_emits(monkeypatch)
    out = asyncio.run(ask_user.execute({"prompt": "Paste your key", "kind": "key"}, _ctx()))
    assert out and "name" in out.lower(), "must tell the model to supply a name"
    assert not any(k == "need_input" for k, _ in emitted), "no card must be opened"
    assert not interaction.has_pending("cred-sess")


# --- Invariant 2: name present → card opened, name in frame -------------------------------------

def test_key_with_name_opens_card_and_frame_carries_name(monkeypatch):
    emitted = _patch_emits(monkeypatch)
    out = asyncio.run(ask_user.execute(
        {"prompt": "Paste your OpenAI key", "kind": "key", "name": "my_openai"},
        _ctx(),
    ))
    assert out and "secure" in out.lower()
    need_input = [d for k, d in emitted if k == "need_input" and d.get("mode") == "input"]
    assert need_input, "need_input frame must be emitted"
    assert need_input[0].get("name") == "my_openai", "name must travel in the SSE frame"
    assert not interaction.has_pending("cred-sess"), "non-blocking — no Future open"


# --- Invariants 3 & 4: POST /input endpoint security -------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "cred.db"))
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "mem"))
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    monkeypatch.setenv("KOTOBA_API_KEY", "k")
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    from fastapi.testclient import TestClient
    import kotoba.server as main
    with TestClient(main.app) as c:
        yield c


def test_key_is_stored_under_cred_prefix(client):
    from kotoba.tools.action.request_credential import _CRED_PREFIX
    r = client.post(
        "/api/session/s1/input",
        json={"kind": "key", "name": "github_token", "value": "ghp_test123"},
    )
    assert r.status_code == 200

    # Retrieve the DB from the running app and check the stored key.
    import kotoba.server as main
    db = main.app.state.db
    stored = asyncio.run(db.get_key(_CRED_PREFIX + "github_token"))
    assert stored == "ghp_test123", "value must be stored under cred: namespace"

    # The raw name must NOT exist as a top-level key.
    raw = asyncio.run(db.get_key("github_token"))
    assert raw is None, "raw (un-namespaced) name must not be stored"


def test_name_with_colon_is_rejected(client):
    r = client.post(
        "/api/session/s2/input",
        json={"kind": "key", "name": "llm:xai:api_key", "value": "sk-leaked"},
    )
    assert r.status_code == 400, "a name with ':' must be rejected to prevent namespace confusion"


def test_system_key_cannot_be_overwritten_via_input_endpoint(client):
    import kotoba.server as main
    db = main.app.state.db
    # Pre-seed a system key exactly as the llm keystore would.
    asyncio.run(db.save_key("llm:openai:api_key", "real-system-key"))

    # Attempt to overwrite via the POST /input endpoint using a colon-free name.
    # Even without ':' the prefixing makes it land on 'cred:llm_openai_api_key' — harmless.
    r = client.post(
        "/api/session/s3/input",
        json={"kind": "key", "name": "llm_openai_api_key", "value": "attacker-value"},
    )
    assert r.status_code == 200

    # System key is untouched.
    system_val = asyncio.run(db.get_key("llm:openai:api_key"))
    assert system_val == "real-system-key", "system key must be unreachable from the input endpoint"


def test_missing_name_in_post_body_does_not_save_anything(client):
    import kotoba.server as main
    db = main.app.state.db
    # Simulate the old (broken) frontend that never sends `name`.
    r = client.post(
        "/api/session/s4/input",
        json={"kind": "key", "value": "should-not-be-saved"},
    )
    assert r.status_code == 200
    # Nothing was saved (no name → no storage key → no save).
    rows = asyncio.run(db.list_key_names())
    names = [row["name"] for row in rows]
    assert not any("should-not-be-saved" in n for n in names)
