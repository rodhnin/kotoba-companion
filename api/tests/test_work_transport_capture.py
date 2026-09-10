"""A background job's compute ceiling is chosen from the transport it was CREATED under.

The timeout is evaluated once, at job start: a naive version's setting could change mid-job, and the
runner is detached from its caller. The transport is captured at start, recorded on work state, and handed
to the timeout function. Tests flip the setting DURING the job and confirm the ceiling holds.

The ceiling is not in dispute: 1500s sits under ElevenLabs' own max call duration with announce
headroom, a real clock only while an EL agent holds the call, never on our socket or the CLI --
which instead get an hour, bounded for real by the work-mode iteration/tool-call/failure limits.
"""
from __future__ import annotations

import asyncio

import pytest

import kotoba.core.work_runner as wr
import kotoba.core.work_state as ws
from kotoba.core import app_settings, transport


class _DB:
    async def insert_turn(self, *a, **k):
        pass


@pytest.fixture(autouse=True)
def _clean():
    ws._state.clear()
    ws._tasks.clear()
    yield
    ws._state.clear()
    ws._tasks.clear()


@pytest.fixture
def budget_probe(monkeypatch):
    """Replaces the compute-budget wrapper with one that reports the budget it was handed, and lets the
    test run code at the moment the job is in flight."""
    seen: dict = {}

    async def fake_budget(coro, scope, budget, interactive_seconds_fn):
        seen["budget"] = budget
        during = seen.get("during")
        if during is not None:
            during()
        seen["after_flip"] = wr._work_timeout(seen.get("captured", False))
        coro.close()
        return "done"

    async def fake_emit(session_id, kind, **data):
        pass

    monkeypatch.setattr(wr, "_run_with_compute_budget", fake_budget)
    monkeypatch.setattr(wr, "emit_task", fake_emit)
    monkeypatch.delenv("KOTOBA_WORK_TIMEOUT", raising=False)
    return seen


def _run_job(seen, el_bound: bool, sid: str):
    seen["captured"] = el_bound

    async def go():
        if el_bound:
            with transport.el_call_turn():
                wr.start(sid, "build the thing", _DB(), {}, None)
        else:
            wr.start(sid, "build the thing", _DB(), {}, None)
        await ws.pop_task(sid)

    asyncio.run(go())


def test_a_job_started_on_our_own_transport_keeps_its_hour_when_settings_flip(budget_probe):
    budget_probe["during"] = lambda: app_settings.set_runtime("voice_mode", "agent")

    _run_job(budget_probe, el_bound=False, sid="cap-local")

    assert app_settings.runtime_all()["voice_mode"] == "agent", "the flip under test never happened"
    assert budget_probe["budget"] == 3600.0
    assert budget_probe["after_flip"] == 3600.0, "a mid-job Settings flip moved a running job's ceiling"
    assert ws.get("cap-local")["el_call_bound"] is False


def test_a_job_started_inside_an_el_turn_keeps_els_ceiling_when_settings_flip(budget_probe):
    budget_probe["during"] = lambda: app_settings.set_runtime("voice_mode", "local")

    _run_job(budget_probe, el_bound=True, sid="cap-el")

    assert app_settings.runtime_all()["voice_mode"] == "local", "the flip under test never happened"
    assert budget_probe["budget"] == 1500.0
    assert budget_probe["after_flip"] == 1500.0
    assert budget_probe["budget"] < transport.EL_MAX_DURATION_SECONDS
    assert ws.get("cap-el")["el_call_bound"] is True


def test_the_setting_could_never_have_answered_this(budget_probe):
    """Why the capture is not paranoia: `voice_mode` reads the SAME value for both jobs above. It is an
    intention, not evidence of what served the turn, and /v1 is not gated on it."""
    app_settings.set_runtime("voice_mode", "local")

    _run_job(budget_probe, el_bound=True, sid="cap-proof")

    assert app_settings.runtime_all()["voice_mode"] == "local"
    assert budget_probe["budget"] == 1500.0, "the setting won over the transport — §0.1 defect"


def test_the_captured_transport_survives_the_jobs_own_ending(budget_probe):
    """finish()/fail() rebuild the state dict; the transport a reader would look up must not vanish
    with the running status."""
    _run_job(budget_probe, el_bound=True, sid="cap-end")
    ws.finish("cap-end", "done", [])
    assert ws.get("cap-end")["el_call_bound"] is True

    ws.start("cap-end2", "x", el_call_bound=True)
    ws.fail("cap-end2", "boom")
    assert ws.get("cap-end2")["el_call_bound"] is True


def test_an_idle_session_reports_the_safe_side():
    assert ws.get("never-started")["el_call_bound"] is False
