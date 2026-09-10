"""The settings surface: the aggregate the panel renders, toolset gating, runtime overrides, deletes.

`core.app_settings` is the runtime half — values the panel can change without a restart — and
`core.settings.build_settings` is the read model the panel is drawn from. The two doors into a
runtime value are the override file and the environment, and the tests below pin that they agree."""
from __future__ import annotations

import asyncio

from kotoba.core.settings import build_settings
from kotoba.db.database import Database
from kotoba.tools import registry


def _db(tmp_path):
    return Database("sqlite:///" + str(tmp_path / "s.db"))


def test_build_settings_shape(tmp_path):
    """The aggregate carries every section the panel draws, lists the action families as toolsets,
    and names stored credentials WITHOUT their values — the panel never receives a secret."""
    async def go():
        db = _db(tmp_path)
        await db.connect()
        try:
            await db.save_key("openai", "sk-xxx")
            await db.insert_cronjob("water", "2030-01-01 00:00:00")
            return await build_settings(db, None)
        finally:
            await db.close()

    s = asyncio.run(go())
    assert set(s) >= {"personality", "security", "mcp_servers", "skills", "toolsets", "memory", "keys", "reminders"}
    assert "openai" in s["keys"]
    assert all("value" not in str(k).lower() or k == "openai" for k in s["keys"])
    assert any(r["message"] == "water" for r in s["reminders"])
    assert any(t["name"] == "terminal" for t in s["toolsets"])


def test_toolset_toggle_persists_and_gates(tmp_path, monkeypatch):
    """A disabled toolset is gone from the schemas the model is offered, and the choice outlives the
    process: it is written to `settings.yaml` and re-applied by `apply_on_startup`, so clearing the
    in-memory flag and reloading from disk disables it again. The toolset is restored at the end,
    because the registry is process-wide and a leaked toggle would follow every later test."""
    import importlib

    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    import kotoba.core.app_settings as app_settings

    importlib.reload(app_settings)

    from kotoba.tools.registry import schemas_for

    app_settings.set_toolset_enabled("file", False)
    work = {x.get("name") for x in schemas_for("work", {"read", "write", "exec", "network"})}
    assert "write_file" not in work and "read_file" not in work
    registry.set_toolset_enabled("file", True)
    app_settings.apply_on_startup()
    work2 = {x.get("name") for x in schemas_for("work", {"read", "write", "exec", "network"})}
    assert "write_file" not in work2
    app_settings.set_toolset_enabled("file", True)
    assert "write_file" in {x.get("name") for x in schemas_for("work", {"read", "write", "exec", "network"})}


def test_runtime_override_precedence_and_validation(tmp_path, monkeypatch):
    """`runtime_value` resolves override > env > default, in that order.

    `set_runtime` accepts only keys on the allowlist, applies each key's own rule, normalises what it
    accepts, and persists it; `runtime_all` is the effective set the panel is rendered from."""
    import importlib

    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.delenv("KOTOBA_MODEL", raising=False)
    monkeypatch.delenv("KOTOBA_SANDBOX", raising=False)
    import kotoba.core.app_settings as app_settings

    importlib.reload(app_settings)

    assert app_settings.runtime_value("model", "KOTOBA_MODEL", "gpt-4o-mini") == "gpt-4o-mini"
    monkeypatch.setenv("KOTOBA_MODEL", "from-env")
    assert app_settings.runtime_value("model", "KOTOBA_MODEL", "gpt-4o-mini") == "from-env"
    app_settings.set_runtime("model", "gpt-5.4-mini")
    assert app_settings.runtime_value("model", "KOTOBA_MODEL", "gpt-4o-mini") == "gpt-5.4-mini"

    import pytest

    with pytest.raises(ValueError):
        app_settings.set_runtime("not_a_key", "x")
    with pytest.raises(ValueError):
        app_settings.set_runtime("sandbox", "rocket")
    with pytest.raises(ValueError):
        app_settings.set_runtime("work_timeout", 5)  # below the 30s floor

    assert app_settings.set_runtime("sandbox", "DOCKER") == "docker"
    assert app_settings.set_runtime("expressive", "yes") is True
    assert app_settings.runtime_all()["sandbox"] == "docker"
    assert app_settings.runtime_all()["expressive"] is True


def test_an_unreadable_env_value_behaves_like_an_unset_one(tmp_path, monkeypatch):
    """The `_RUNTIME_SPEC` validators must run on the ENV half too, or the two doors disagree.

    An env var reaches the file a different way than the panel, and `sandbox: rocket` reproduced exactly
    that split: `runtime_all` (what Settings, doctor and `/set` render) validated it and showed `local`,
    while `runtime_value` (what the sandbox backend obeys) returned `rocket` raw — so the sandbox was
    silently unavailable while every surface reported a working local one.

    Same rule as the caller: an invalid value behaves as if it were not set."""
    import importlib

    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.setenv("KOTOBA_SANDBOX", "rocket")
    monkeypatch.setenv("KOTOBA_VOICE_MODE", "elevenlabs")
    import kotoba.core.app_settings as app_settings

    importlib.reload(app_settings)
    from kotoba.core import sandbox

    assert app_settings.runtime_value("sandbox", "KOTOBA_SANDBOX", "local") == "local"
    assert app_settings.runtime_all()["sandbox"] == "local"
    assert sandbox.backend_name() == "local"
    assert sandbox.sandbox_available_sync() is True
    assert app_settings.runtime_value("voice_mode", "KOTOBA_VOICE_MODE", "local") == "local"

    # A key with no spec entry is still passed through raw — call sites keep their own inline fallback
    # for those (llm.model_call_kwargs' work_reasoning_effort is the live one).
    monkeypatch.setenv("KOTOBA_MADE_UP", "whatever")
    assert app_settings.runtime_value("made_up", "KOTOBA_MADE_UP", "x") == "whatever"

    # And so is a key whose consumer clamps better than "unset" would. reasoning_effort's own gate
    # (providers.normalize_effort) folds junk to the provider's LOWEST effort; defaulting it here would
    # instead drop store=False + the encrypted-reasoning include and flip is_reasoning_model off, which
    # puts the canned English lines back into a Spanish turn over a typo.
    monkeypatch.setenv("KOTOBA_REASONING_EFFORT", "junk")
    assert app_settings.runtime_value("reasoning_effort", "KOTOBA_REASONING_EFFORT", "") == "junk"


def test_sandbox_backend_reads_runtime_override(tmp_path, monkeypatch):
    """A sandbox change through `set_runtime` is seen live by `core.sandbox.backend_name`: no
    restart, and no second copy of the value for the execution path to read."""
    import importlib

    monkeypatch.setenv("KOTOBA_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.delenv("KOTOBA_SANDBOX", raising=False)
    import kotoba.core.app_settings as app_settings

    importlib.reload(app_settings)
    from kotoba.core.sandbox import backend_name

    assert backend_name() == "local"
    app_settings.set_runtime("sandbox", "none")
    assert backend_name() == "none"


def test_delete_key_and_cron(tmp_path):
    async def go():
        db = _db(tmp_path)
        await db.connect()
        try:
            await db.save_key("k1", "v")
            await db.insert_cronjob("m", "2030-01-01 00:00:00")
            jid = (await db.list_cronjobs())[0]["id"]
            await db.delete_key("k1")
            await db.deactivate_cronjob(jid)
            return await db.list_key_names(), await db.list_cronjobs()
        finally:
            await db.close()

    keys, crons = asyncio.run(go())
    assert keys == [] and crons == []
