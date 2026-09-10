"""Work budgets, and which of them ElevenLabs is still allowed to size.

The governing rule when an EL agent holds the call: work_timeout < keepalive_cap <
max_duration_seconds − headroom. That chain holds only while there IS a call, so the ceiling comes
from the TRANSPORT captured when the job was created, not a setting read later. These pin both
defaults (no env/runtime override); the EL-bound one is the value the chain protects, asserted here
so it cannot be raised by reading only the other one. A stale header once said 600s while the
assertion used 1800 — only one of the two moved. One home now: EL_MAX_DURATION_SECONDS.
"""
from __future__ import annotations

import kotoba.core.loop as loop
import kotoba.core.transport as transport
import kotoba.core.work_runner as wr


def test_work_max_iter_default_is_40(monkeypatch):
    # With no runtime override and no env var, the default is the raised value.
    monkeypatch.delenv("KOTOBA_WORK_MAX_ITER", raising=False)
    monkeypatch.setattr("kotoba.core.app_settings.runtime_value",
                        lambda key, env_var, default: str(default))
    assert loop._work_max_iter() == 40


def test_work_timeout_under_el_ceiling_when_the_job_is_el_bound(monkeypatch):
    # A job created inside an ElevenLabs turn keeps EL's number: 1500s of compute, under the hard cap
    # with announce headroom. This is the assertion that protects the agent-mode value.
    monkeypatch.delenv("KOTOBA_WORK_TIMEOUT", raising=False)
    monkeypatch.setattr("kotoba.core.app_settings.runtime_value",
                        lambda key, env_var, default: str(default))
    t = wr._work_timeout(el_call_bound=True)
    assert t == 1500.0
    assert t < transport.EL_MAX_DURATION_SECONDS  # never at/over the ElevenLabs max_duration_seconds


def test_work_timeout_off_that_transport_is_merit_based(monkeypatch):
    # The local WebSocket and the CLI hold no ElevenLabs call, so nothing external is counting. The
    # bound that matters there is the iteration / tool-call / failure caps; the clock is the backstop.
    monkeypatch.delenv("KOTOBA_WORK_TIMEOUT", raising=False)
    monkeypatch.setattr("kotoba.core.app_settings.runtime_value",
                        lambda key, env_var, default: str(default))
    assert wr._work_timeout(el_call_bound=False) == 3600.0
    assert wr._work_timeout() == 3600.0  # the default argument is the unbound one


def test_the_settings_default_is_the_same_object_as_the_runners(monkeypatch):
    # These two literals drifted apart once already, which is why neither side writes one now.
    from kotoba.core import app_settings

    assert app_settings._RUNTIME_SPEC["work_timeout"][1] is transport.WORK_TIMEOUT_SECONDS
    monkeypatch.delenv("KOTOBA_WORK_TIMEOUT", raising=False)
    monkeypatch.setattr("kotoba.core.app_settings._runtime", dict)
    assert app_settings.runtime_all()["work_timeout"] == wr._work_timeout()


def test_a_user_override_still_wins_on_both_transports(monkeypatch):
    monkeypatch.setattr("kotoba.core.app_settings.runtime_value",
                        lambda key, env_var, default: "900")
    assert wr._work_timeout(el_call_bound=True) == 900.0
    assert wr._work_timeout(el_call_bound=False) == 900.0


def test_companion_caps_unchanged():
    # Regression: the quick voice turn keeps its tight anti-over-preparation caps.
    assert loop._MAX_TOOL_CALLS == 8
    assert loop._COMPANION_FAIL_LIMIT == 2
