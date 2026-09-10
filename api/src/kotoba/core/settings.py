"""Aggregate everything the Settings panel shows — the user's "control panel" over Kotoba.

Read-only gather; mutations are individual endpoints of their own. Secrets are never included (only key
NAMES). Pulls from: soul_config (DB), the runtime knobs (`app_settings.runtime_all`: user override > env
> default — provider, models, sandbox, voice, work caps), env for the two display-only values (trust,
browser_cdp), the MCP manager, skill docs, the tool registry (toolsets + on/off), USER.md memory,
saved-key names, and cronjobs.
"""
from __future__ import annotations

import os

from kotoba.core import app_settings, model_library, skill_docs, user_memory
from kotoba.core.mcp import pending as mcp_pending
from kotoba.core.plugins import loaded_plugins
from kotoba.tools import registry


async def _llm_settings(rt: dict, key_names: list[str], db) -> dict:
    """Provider/endpoint info for the panel: active provider+base_url, the provider catalog, and which
    providers have a USABLE in-app key (never values). A row that exists but no longer decrypts — the
    master key changed or its file was lost — used to render as "saved", so the panel insisted the key
    was there while every call failed for want of one.

    Each provider carries the models it serves, so the panel's model menus can follow the ACTIVE provider.
    They used to be one hardcoded list mixing both companies, which offered an xAI install GPT models it
    could only 404 on; `core.providers` is the same source first run reads, so the two can't drift."""
    import os

    from kotoba.core import llm, providers

    def _env_key(spec) -> bool:
        raw = os.getenv(spec.key_env, "")
        return bool(raw) and not llm.looks_placeholder(raw)

    saved = set()
    for n in key_names:
        if not (n.startswith("llm:") and ":" in n):
            continue
        # Filtered like the env half above and like first_run.needed: a `sk-...` copied out of
        # .env.example is a row in the table and not a key, and rendering it as one left a panel
        # reporting a configured brain on an install that cannot complete a turn.
        if not llm.looks_placeholder(await db.get_key(n) or ""):
            saved.add(n.split(":")[1])
    catalogue = providers.catalogue()
    return {
        "provider": rt.get("provider", providers.DEFAULT_PROVIDER),
        "base_url": rt.get("base_url", ""),
        "providers": [
            {"id": s.id, "label": s.label, "default_base_url": s.default_base_url,
             "key_hint": s.key_prefix_hint, "has_key": s.id in saved,
             # Where the key she is ACTUALLY using comes from. `has_key` is the app's own store, so a
             # working environment key rendered as an empty box and looked like nothing was set.
             "key_env": s.key_env if _env_key(s) else "",
             "models": catalogue[s.id]["models"], "code_models": catalogue[s.id]["code_models"]}
            for s in providers.PROVIDERS.values()
        ],
    }


def _avatar_settings(configured: str) -> dict:
    """`models_dir` travels with the list so the panel can name the folder to unpack into when the
    list is empty — which is every fresh install."""
    models = model_library.installed()
    return {
        "installed": models,
        "selected": model_library.selection(configured, models),
        "models_dir": str(model_library.models_dir()),
    }


async def build_settings(db, mcp) -> dict:
    soul = await db.fetch_soul_config()

    mcp_servers = []
    if mcp is not None:
        for name, tools in getattr(mcp, "server_tools", {}).items():
            mcp_servers.append({"name": name, "tools": len(tools), "connected": True})

    disabled = registry.disabled_toolsets()
    counts: dict[str, int] = {}
    for spec in registry.registry().values():
        counts[spec.toolset] = counts.get(spec.toolset, 0) + 1
    toolsets = [
        {"name": ts, "tools": counts.get(ts, 0), "enabled": ts not in disabled}
        for ts in registry.all_toolsets()
    ]

    # Community plugins — each is toggled via its 'plugin:<name>' toolset.
    plugins = [
        {
            "name": name,
            "source": info.get("source", "folder"),
            "tools": info.get("tools", []),
            "enabled": f"plugin:{name}" not in disabled,
        }
        for name, info in loaded_plugins().items()
    ]

    topics = [{"slug": s, "count": n} for s, n in user_memory.list_topics()]
    key_names = [k["name"] for k in await db.list_key_names()]
    crons = await db.list_cronjobs()
    # Command families the user chose to "always allow" (smart exec approval) — revocable here.
    try:
        approvals = [{"pattern": r["pattern"], "scope": r.get("scope", "command")}
                     for r in await db.list_approved_commands()]
    except Exception:
        approvals = []

    # Effective runtime values (user override > env > default) for every settable knob — the panel renders
    # each control already populated from here. trust/browser_cdp are display-only (not in the editable set).
    rt = app_settings.runtime_all()

    return {
        "personality": {
            "name": soul.get("name") or "",
            "language": soul.get("language") or "auto",
            "voice_id": soul.get("voice_id") or "",
        },
        # Which Live2D models are installed and which she wears. The choice lives in soul_config, not
        # in a build-time env var — that is the whole point — so the panel is where it is made.
        "avatar": _avatar_settings(str(soul.get("avatar_model") or "")),
        # Multi-provider: which LLM provider/endpoint is active, the catalog of providers, and which ones
        # have an in-app key saved (names only — never the key value or whether it came from env).
        "llm": await _llm_settings(rt, key_names, db),
        "security": {
            "trust": os.getenv("KOTOBA_TRUST", "workspace"),  # display-only (no live setter yet)
            "sandbox": rt.get("sandbox", "local"),
            "approvals": approvals,
        },
        # Editable runtime knobs, current effective values — drives the Brain / Security / Advanced controls.
        "runtime": rt,
        "browser_cdp": os.getenv("KOTOBA_BROWSER_CDP", ""),  # display-only
        "mcp_servers": mcp_servers,
        # Servers Kotoba found but couldn't connect because they need auth — shown under "Needs connection".
        "pending_mcp": mcp_pending.list_pending(),
        "plugins": plugins,
        "skills": [{"name": s["name"], "description": s["description"]} for s in skill_docs.list_skills()],
        "toolsets": toolsets,
        "memory": {"topics": topics, "recent": user_memory.facts_for_prompt()[:8]},
        "keys": key_names,
        "reminders": [
            {"id": c["id"], "message": c["message"], "due_at": c["due_at"], "recurring": c["recurring"]}
            for c in crons
        ],
    }
