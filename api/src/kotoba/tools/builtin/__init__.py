"""Built-in tools — the safe, conversational toolset offered in companion mode.

Each module here exposes the standard tool shape (SCHEMA, BUILT_IN, ANNOUNCE/HEARTBEAT/COMPLETE/FAIL,
execute) plus the registry metadata TOOLSET / RISK. Nothing is auto-discovered: `discover()`
imports each one by name, so a module added here and left out of it is offered to nobody.
"""
