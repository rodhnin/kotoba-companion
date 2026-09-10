"""The announce-on-reconnect backstop. If a long background work outlives the ElevenLabs call (it dies
at ElevenLabs' own hard cap), the work_done frame lands on a dead session and the result is lost. On
reconnect the frontend asks GET /api/session/{id}/work; if a finished work hasn't been announced yet, it triggers the
announcement. This pins that endpoint's contract."""
from __future__ import annotations

import pytest

_AUTH = {"Authorization": "Bearer k"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "w.db"))
    monkeypatch.setenv("KOTOBA_MEMORY_DIR", str(tmp_path / "mem"))
    monkeypatch.setenv("KOTOBA_SANDBOX", "none")
    monkeypatch.setenv("KOTOBA_API_KEY", "k")
    monkeypatch.setenv("KOTOBA_WEB_PASSWORD", "")  # no gate in this test
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    from fastapi.testclient import TestClient
    import kotoba.server as main
    with TestClient(main.app) as c:
        yield c


def test_session_work_reports_pending_then_clears(client):
    from kotoba.core import work_state
    work_state.clear("sx")

    # nothing running → not pending
    r = client.get("/api/session/sx/work", headers=_AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["pending"] is False

    # a finished, not-yet-announced work → pending with its summary, ok True
    work_state.start("sx", "publish the post")
    work_state.finish("sx", "Posted to Facebook.", [])
    r = client.get("/api/session/sx/work", headers=_AUTH).json()
    assert r["pending"] is True and r["ok"] is True
    assert "Facebook" in r["summary"]

    # once announced, it stops being pending (so a later reconnect doesn't re-announce)
    work_state.mark_announced("sx")
    r = client.get("/api/session/sx/work", headers=_AUTH).json()
    assert r["pending"] is False


def test_session_work_failed_is_pending_not_ok(client):
    from kotoba.core import work_state
    work_state.clear("sf")
    work_state.start("sf", "do the thing")
    work_state.fail("sf", "it took too long and I stopped it")
    r = client.get("/api/session/sf/work", headers=_AUTH).json()
    assert r["pending"] is True and r["ok"] is False


def test_leave_aborts_running_work_and_clears_pending(client):
    import asyncio

    from kotoba.core import work_state

    async def _never():
        await asyncio.sleep(3600)

    # a running work task registered for the session
    work_state.clear("sl")
    work_state.start("sl", "long job")
    loop = asyncio.new_event_loop()
    task = loop.create_task(_never())
    work_state.register_task("sl", task)

    r = client.post("/api/session/sl/leave", headers=_AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["aborted_work"] is True
    # Bounded on purpose: were the cancel dropped, the sleep(3600) would still be pending after the wait
    # and this fails in a second, instead of hanging the suite for an hour.
    loop.run_until_complete(asyncio.wait([task], timeout=1))
    assert task.cancelled(), "leave must cancel the running work task"
    # state is cleared → nothing pending to announce on a future reconnect (explicit leave = gone)
    assert work_state.is_running("sl") is False
    r2 = client.get("/api/session/sl/work", headers=_AUTH).json()
    assert r2["pending"] is False
    loop.close()


def test_leave_is_noop_when_nothing_running(client):
    from kotoba.core import work_state
    work_state.clear("sn")
    r = client.post("/api/session/sn/leave", headers=_AUTH)
    assert r.status_code == 200 and r.json()["aborted_work"] is False
