"""Mute-aware session state, and the lines the terminal panel shows for a step and its result.

Muted means "mic off", not "conversation off": a muted silence turn has to answer with a `skip_turn`
tool call rather than an empty completion, while a typed message — or a background task that has
just finished — still gets through. The second half pins the terminal lines themselves: terse, real,
and free of emoji."""
from __future__ import annotations

import json

import pytest

from kotoba.core import session_state
from kotoba.core.loop import _result_text, _step_text


_AUTH = {"Authorization": "Bearer k"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient over the real app, on a throwaway database and the offline LLM path.

    Booting the app and running a turn populates the registry's module-level availability cache —
    `shell` resolves to unavailable under KOTOBA_SANDBOX=none, for instance. That cache and the
    disabled-toolset set are snapshotted and restored, so this fixture leaves the global registry
    exactly as it found it and nothing bleeds into later tests."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "mute.db"))
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "mem"))
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    monkeypatch.setenv("KOTOBA_API_KEY", "k")  # load_dotenv() won't override an already-set var
    monkeypatch.setenv("OPENAI_API_KEY", "")   # offline path: deterministic, no network/cost in tests
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.setenv("KOTOBA_MCP_CONFIG", str(tmp_path / "mcp.yaml"))
    import kotoba.tools.registry as reg
    saved_cache, saved_disabled = dict(reg._check_cache), set(reg._disabled_toolsets)
    from fastapi.testclient import TestClient
    import kotoba.server as main
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        reg._check_cache.clear(); reg._check_cache.update(saved_cache)
        reg._disabled_toolsets.clear(); reg._disabled_toolsets.update(saved_disabled)


def test_muted_turn_calls_skip_turn_not_empty(client):
    """A muted session must answer ElevenLabs with a skip_turn tool call (stay silent + connected),
    NOT an empty completion — the empty response is what made ElevenLabs retry-storm and drop the call."""
    sid = "mute-sess-1"
    assert client.post(f"/api/session/{sid}/mute", json={"muted": True}).status_code == 200

    r = client.post("/v1/chat/completions", headers=_AUTH, json={
        "messages": [{"role": "user", "content": "hello?"}], "session_id": sid, "stream": True,
    })
    assert r.status_code == 200
    body = r.text
    deltas = []
    for line in body.splitlines():
        if line.startswith("data:") and "[DONE]" not in line:
            try:
                deltas.append(json.loads(line[5:].strip())["choices"][0])
            except Exception:
                pass
    tool_calls = [c["delta"]["tool_calls"][0]["function"]["name"]
                  for c in deltas if c.get("delta", {}).get("tool_calls")]
    assert "skip_turn" in tool_calls
    assert any(c.get("finish_reason") == "tool_calls" for c in deltas)
    spoken = "".join(c["delta"].get("content") or "" for c in deltas if c.get("delta"))
    assert spoken.strip() == ""


def test_muted_typed_message_is_answered_not_skipped(client):
    """A TYPED message while muted must be answered — `skip_turn` is for silence turns only.

    The frontend flags the imminent turn via /text-turn and the backend bypasses the mute skip for
    that one turn. She then has to ACTUALLY reply: with no OpenAI key the loop streams the offline
    line, and the point is that content is present at all, which is what catches the "muted →
    produce() returned early → empty completion" bug. The message itself is Spanish ("hello, typing
    while muted") because that is what the reported case was."""
    sid = "mute-sess-typed"
    client.post(f"/api/session/{sid}/mute", json={"muted": True})
    client.post(f"/api/session/{sid}/text-turn")
    r = client.post("/v1/chat/completions", headers=_AUTH, json={
        "messages": [{"role": "user", "content": "hola, escribiendo muteado"}], "session_id": sid, "stream": True,
    })
    assert r.status_code == 200
    assert "skip_turn" not in r.text
    spoken = "".join(
        json.loads(l[5:].strip())["choices"][0]["delta"].get("content") or ""
        for l in r.text.splitlines() if l.startswith("data:") and "[DONE]" not in l
    )
    assert spoken.strip()


def test_muted_silence_after_typed_still_skips(client):
    """The text-turn flag is one-shot: after the typed turn consumes it, the next silence turn still
    skips (no lingering flag turning silence turns into responses)."""
    sid = "mute-sess-oneshot"
    client.post(f"/api/session/{sid}/mute", json={"muted": True})
    client.post(f"/api/session/{sid}/text-turn")
    client.post("/v1/chat/completions", headers=_AUTH, json={
        "messages": [{"role": "user", "content": "primero"}], "session_id": sid, "stream": True,
    })
    r = client.post("/v1/chat/completions", headers=_AUTH, json={
        "messages": [{"role": "user", "content": "silence"}], "session_id": sid, "stream": True,
    })
    assert "skip_turn" in r.text


def test_unmuted_turn_does_not_skip(client):
    """An UNmuted session takes the normal path (no skip_turn). With no OPENAI_API_KEY it returns the
    offline line, but crucially it must NOT emit a skip_turn tool call."""
    sid = "mute-sess-2"
    client.post(f"/api/session/{sid}/mute", json={"muted": False})
    r = client.post("/v1/chat/completions", headers=_AUTH, json={
        "messages": [{"role": "user", "content": "hi"}], "session_id": sid, "stream": True,
    })
    assert r.status_code == 200
    assert "skip_turn" not in r.text


def test_mcp_find_panel_shows_terse_progress_not_chatty_message():
    """The progress panel and the log show PROGRESS, not the user-facing sentence the tool returns
    for the MODEL to speak.

    mcp_find's chatty "I looked in the registry... want me to try different words?" belongs in the
    companion's voice, not in the terminal. So the before-line names what she is doing — the query,
    not the tool name alone — and the result-line is a terse status rather than the return string."""
    assert _step_text("mcp_find", {"query": "spotify"}) == "search MCP registry: spotify"
    not_found = "I looked in the MCP registry but couldn't find a server for \"spotify\". Want me to try different words?"
    line = _result_text("mcp_find", True, not_found)
    assert "want me to try" not in line.lower()
    assert "no match" in line.lower()
    connected = "Connected 'spotify-mcp' — I can now: play, pause, search."
    assert "connected" in _result_text("mcp_find", True, connected).lower()


def test_muted_announces_finished_work_once(client):
    """A background task finishes while the mic is muted, and she never says it ended.

    A finished-but-unannounced task must let ONE muted silence turn through so it can be announced,
    then go straight back to skipping. The frontend triggers that turn with a "__work_done__"
    sentinel message, which while muted would normally skip; the pending completion is what lets it
    past. The turn after it has nothing pending, so it skips again with the mute still on."""
    from kotoba.core import work_state

    sid = "mute-sess-work-done"
    client.post(f"/api/session/{sid}/mute", json={"muted": True})
    work_state.finish(sid, "I connected the Spotify server — play, pause, search are ready.", [])
    assert work_state.has_pending_announcement(sid)

    r = client.post("/v1/chat/completions", headers=_AUTH, json={
        "messages": [{"role": "user", "content": "__work_done__"}], "session_id": sid, "stream": True,
    })
    assert r.status_code == 200
    assert "skip_turn" not in r.text
    assert not work_state.has_pending_announcement(sid)

    r2 = client.post("/v1/chat/completions", headers=_AUTH, json={
        "messages": [{"role": "user", "content": ""}], "session_id": sid, "stream": True,
    })
    assert "skip_turn" in r2.text


def test_work_done_sentinel_not_saved_to_conversation_log(client, monkeypatch):
    """The "__work_done__" trigger is plumbing, not user content, so it must never be written to the
    conversation log: it used to pollute the history, get mined for memory, and be echoed back by
    the model. A real message on the same handler IS persisted, which is what proves only the
    sentinel is skipped rather than everything. (That real message is Spanish — "a real one".)"""
    import kotoba.server as main

    saved: list[tuple[str, str]] = []
    orig = main.app.state.db.insert_turn

    async def _spy(session_id, role, content):
        saved.append((role, content))
        return await orig(session_id, role, content)

    monkeypatch.setattr(main.app.state.db, "insert_turn", _spy)

    sid = "sentinel-not-saved"
    client.post("/v1/chat/completions", headers=_AUTH, json={
        "messages": [{"role": "user", "content": "__work_done__"}], "session_id": sid, "stream": True,
    })
    user_saved = [c for (role, c) in saved if role == "user"]
    assert "__work_done__" not in user_saved

    client.post("/v1/chat/completions", headers=_AUTH, json={
        "messages": [{"role": "user", "content": "hola de verdad"}], "session_id": sid, "stream": True,
    })
    assert "hola de verdad" in [c for (role, c) in saved if role == "user"]


def test_mute_state_roundtrip():
    sid = "sess-abc"
    assert session_state.is_muted(sid) is False
    session_state.set_muted(sid, True)
    assert session_state.is_muted(sid) is True
    session_state.set_muted(sid, False)
    assert session_state.is_muted(sid) is False
    session_state.set_muted(None, True)  # no-op, must not raise
    assert session_state.is_muted(None) is False


def test_step_text_is_real_and_emoji_free():
    assert _step_text("shell", {"command": "python solve.py"}) == "$ python solve.py"
    assert _step_text("write_file", {"path": "solve.py"}) == "write_file solve.py"
    assert _step_text("execute_code", {"code": "import sympy\nprint(1)"}).startswith("python> import sympy")
    for s in [
        _step_text("shell", {"command": "ls"}),
        _step_text("read_file", {"path": "a.txt"}),
        _step_text("search_files", {"query": "todo"}),
    ]:
        assert all(ord(ch) < 0x2190 for ch in s), f"emoji/sym leaked: {s!r}"


def test_result_text_shows_real_output():
    r = _result_text("execute_code", True, "exit=0\nstdout:\n42\n")
    assert "exit=0" in r and "42" in r
    fail = _result_text("shell", False, "boom: command not found")
    assert fail.strip().startswith("!") and "boom" in fail
