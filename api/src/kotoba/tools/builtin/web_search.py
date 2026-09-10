"""web_search — OpenAI built-in tool.

OpenAI runs the search server-side when we pass `{"type": "web_search"}` in `tools=[...]`. It does NOT
come back as a function_call, so `execute()` is never called — it exists only to satisfy the registry
shape. The schema is `{"type": "web_search"}`, not the legacy `web_search_preview`. It streams.

It has NO voice patterns, and that absence is the point. Every narration call lives inside core.loop's
function_call branch, and a `web_search_call` arrives as an output ITEM that never enters it, so all
four phases were unreachable and a built-in search is silent by construction — which is what put a WEB
card on screen beside a voice saying nothing. The load-bearing narration is the model's own pre-tool line."""
from __future__ import annotations

SCHEMA = {"type": "web_search"}
BUILT_IN = True
TOOLSET = "web"
RISK = "read"


async def execute(args: dict, ctx) -> str:  # pragma: no cover - built-in, not invoked by the loop
    raise NotImplementedError("web_search is an OpenAI built-in tool; the model runs it server-side.")
