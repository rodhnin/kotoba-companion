"""Action tools — capabilities that act on the system (filesystem, shell, code, browser, MCP,
subagents). They carry RISK in {read, write, exec} and are gated by the sandbox/approval layer; only
the `browser`, `mcp` and `subagent` families sit outside COMPANION_TOOLSETS and are work-mode only.

Registration is explicit and auditable: add each new action-tool module to ACTION_TOOLS below and the
registry will pick it up via discover(). A module imported here but left out of that list is registered
by nobody — the import is not the registration.
"""
from __future__ import annotations

from kotoba.tools.action import (
    ask_secret,
    cronjob,
    delegate,
    discord_act,
    discord_apply_plan,
    discord_guild_read,
    discord_people,
    discord_plan,
    discord_read_history,
    discord_remember_person,
    discord_send_file,
    discord_voice,
    execute_code,
    file_read,
    file_write,
    get_credential,
    make_report,
    mcp_find,
    mcp_install,
    patch,
    request_credential,
    search_files,
    shell,
)

# Explicit, auditable registration list (discover() registers these).
ACTION_TOOLS: list = [
    file_read, file_write, patch, search_files, shell, execute_code, mcp_install, mcp_find, cronjob,
    make_report, delegate, request_credential, ask_secret, get_credential,
    discord_read_history, discord_people, discord_remember_person, discord_send_file,
    discord_guild_read, discord_act, discord_plan, discord_apply_plan, discord_voice,
]
