"""Tool registry entry point.

On import we `discover()` the built-ins; the loop reads `schemas_for(...)` each iteration. Each tool
module in tools/builtin/ or tools/action/ exposes:
  SCHEMA    — Responses API tool schema (function tool, or a built-in {"type": "web_search"})
  BUILT_IN  — True if OpenAI runs it server-side, with no execute() call from our loop
  TOOLSET   — the family it belongs to; COMPANION_TOOLSETS decides which of them reach a voice turn
  RISK      — read|write|exec|network. It drives mode gating, and `write`/`exec`/`network` are what
              count as an ACTION: a step row and an audit entry. MCP tools register as `network`. It
              does NOT make a tool ask — the ones that ask call the approval gate themselves.
  ANNOUNCE / HEARTBEAT / COMPLETE / FAIL, execute(args, ctx), optional check() and EXPRESSIONS
Of the legacy names below only TOOL_REGISTRY still has a caller; the rest are back-compat surface."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from kotoba.core.transport import el_call_bound as _el_call_bound
from kotoba.tools import registry as _registry
from kotoba.tools.registry import ToolSpec, discover, register, schemas_for

# Deliberately NOT re-exporting the `registry()` accessor — the name would shadow the `tools.registry`
# submodule on the package object. Use tools.registry.registry() for the live dict.
# discover() runs at the BOTTOM of this module, after ToolResult/ToolContext exist: called up here it ran
# plugin module bodies against a half-built package, so any plugin importing ToolResult died on an
# ImportError that discover_plugins only logs — the tool silently never appeared.


@dataclass
class ToolResult:
    """A tool's output that can carry images (e.g. a browser screenshot) alongside text.

    `str(result)` yields the TEXT only — that's what the work-mode terminal panel and the DB see, so
    base64 never lands there. `.images` holds ready-to-send data URLs (`data:image/png;base64,…`) that
    the loop feeds to the vision model as `input_image` parts inside the function_call_output (the
    Responses API accepts a content-part list there). A plain string is still a valid tool result; this
    is only used when there's an image to show the model."""

    text: str = ""
    images: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return self.text or ""

    def __bool__(self) -> bool:
        return bool(self.text) or bool(self.images)


class _RecordingSandbox:
    """A Sandbox view that reports every command's EXIT CODE to core.sandbox.base.record_exits.

    The wrapper exists because the exit code is the only witness to whether a command worked, and by
    the time the tool's answer reaches core.loop it is prose: `exit=124 …timed out after 10s and was
    killed` is a string like any other, so the loop said ok and the terminal row drew a ✓ over a killed
    command. Wrapping HERE rather than inside a backend keeps the count honest in both directions — it
    covers every backend at once, and it counts only what the model asked to run, not the docker CLI
    calls a container's own start and teardown make through the same ExecResult.
    """

    def __init__(self, inner) -> None:
        self._inner = inner

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    async def run(self, command: str, cwd: str = ".", timeout: int = 60):
        from kotoba.core.sandbox.base import note_exit

        res = await self._inner.run(command, cwd=cwd, timeout=timeout)
        note_exit(getattr(res, "exit_code", 0))
        return res

    async def run_code(self, code: str, lang: str = "python", timeout: int = 60):
        from kotoba.core.sandbox.base import note_exit

        res = await self._inner.run_code(code, lang=lang, timeout=timeout)
        note_exit(getattr(res, "exit_code", 0))
        return res


@dataclass
class ToolContext:
    """Everything a tool's execute() may need. Passed by the agentic loop.

    `workdir` is the jailed root for file/shell tools; `sandbox` the execution backend; `approval` the
    human gate; `spawn_depth` anti-recursion for subagents; `mcp` lets mcp_install connect servers
    mid-turn; `subagent_id` makes progress surface as that helper's chibi; `user_text` is the latest
    utterance, so a tool can honour intent the model drops; `call_id` lets a DEFERRED action close its row.

    `channel` ('voice' | 'text') is how this turn REACHES the user, not what it is doing (`mode`).
    `el_call_bound` is the transport underneath that clock and the only fact that may RELAX a limit
    ElevenLabs imposed. Never key a relaxation on `voice_mode` instead — that is a live hazard."""

    db: object
    session_id: Optional[str] = None
    client: object = None
    workdir: Optional[Path] = None
    sandbox: object = None
    approval: object = None
    mode: str = "companion"
    channel: str = "voice"
    el_call_bound: bool = field(default_factory=_el_call_bound)
    spawn_depth: int = 0
    emit_progress: Optional[Callable] = None
    mcp: object = None
    soul_patterns: Optional[dict] = None
    subagent_id: Optional[str] = None
    user_text: Optional[str] = None
    call_id: str = ""
    # What this turn was NOT offered. Dispatch resolves by NAME, so without it a withheld tool is only
    # hidden: a model that saw the name earlier still reaches it, and on a public surface that is the
    # difference between a guest and the host's file library.
    excluded_tools: frozenset[str] = frozenset()

    def child(self, subagent_id: str) -> "ToolContext":
        """A child context for a subagent: same session, shared workdir/sandbox/approval/mcp, one level
        deeper, tagged with subagent_id so the loop emits its steps as that subagent.

        The web-source accumulator is the PARENT'S dict, shared by reference, so URLs a helper surfaces
        flow back for the parent's final synthesis; its shown-counter starts at the current length. The
        markdown-writes list is shared the same way, so a helper's report is covered by the parent's net.

        The parent's UTTERANCE travels too: the child's conversation carries only the goal paraphrase, so
        every guard anchored on the user's OWN words ran blind under a helper — cronjob's repeat guard read
        nothing, and the one-request-one-run key came back None. The list is the child's own copy."""
        store = getattr(self, "_sources", None)
        if store is None:
            store = self._sources = {}
        writes = getattr(self, "_md_writes", None)
        if writes is None:
            writes = self._md_writes = []
        c = ToolContext(
            db=self.db,
            session_id=self.session_id,
            client=self.client,
            workdir=self.workdir,
            sandbox=self.sandbox,
            approval=self.approval,
            mode="work",
            channel=self.channel,
            el_call_bound=self.el_call_bound,   # explicit: a child may be built off the marked task
            spawn_depth=self.spawn_depth + 1,
            emit_progress=self.emit_progress,
            mcp=self.mcp,
            soul_patterns=self.soul_patterns,
            subagent_id=subagent_id,
            user_text=self.user_text,
            excluded_tools=self.excluded_tools,
        )
        c.user_texts = list(getattr(self, "user_texts", None) or ([self.user_text] if self.user_text else []))
        c._sources = store
        c._sources_shown = len(store)
        c._md_writes = writes
        return c

    _session_sandbox: bool = False  # set when our sandbox came from the per-session registry (don't kill it)

    async def ensure_sandbox(self):
        """Lazily get the execution sandbox for this Task, once.

        Prefer a PER-SESSION sandbox, reused across turns so files persist and "edit the HTML you just
        made" works; fall back to a ctx-owned one when there is no session_id. Returns None if no
        backend is available or no workdir is set.

        Companion and work turns SHARE the per-session sandbox, and turns.supersede() guarantees one active
        turn per session. cleanup() leaves a session-owned sandbox alive — only a sessionless, ctx-owned one
        is killed — so a companion turn cannot tear down the work-runner's. What comes back is a recording
        wrapper, applied after start() so a container's provisioning stays out of the exit-code count."""
        if self.sandbox is None and self.workdir is not None:
            if self.session_id:
                from kotoba.core import session_sandbox

                sb = await session_sandbox.acquire(self.session_id, self.workdir)
                if sb is not None:
                    self.sandbox = _RecordingSandbox(sb)
                    self._session_sandbox = True
                    return self.sandbox
            from kotoba.core.sandbox import create_sandbox

            try:
                sb = create_sandbox(self.workdir)
                if sb is not None:
                    await sb.start()
                    self.sandbox = _RecordingSandbox(sb)
            except Exception:
                self.sandbox = None  # backend misconfigured/unreachable (e.g. Docker down) → fail closed
        return self.sandbox

    async def cleanup(self) -> None:
        """End-of-turn cleanup. A PER-SESSION sandbox is left ALIVE (reused next turn; reaped by the
        idle reaper / session end). Only a ctx-owned (sessionless) sandbox is killed here."""
        if self.sandbox is not None:
            sb, self.sandbox = self.sandbox, None
            if not self._session_sandbox:
                await sb.kill()
            self._session_sandbox = False

    async def read_text(self, relpath: str) -> Optional[str]:
        """Read a workspace text file from the host workdir — used to snapshot a file's content for the
        Files panel after a write/patch. Best-effort → None on miss."""
        if self.workdir is None:
            return None
        try:
            from kotoba.core.path_security import PathSecurityError, validate_within_dir

            p = validate_within_dir(relpath, self.workdir)
            if not p.is_file():
                return None
            return p.read_text(errors="replace", encoding="utf-8")
        except (PathSecurityError, OSError):
            return None


discover()

# Backward-compatible exports (import-time snapshots; the loop reads the LIVE registry, and MCP registers
# tools at runtime, so treat these as legacy and never as the current truth).
TOOL_REGISTRY = _registry.modules_by_name()
TOOL_SCHEMAS = [spec.schema for spec in _registry.registry().values()]
FUNCTION_TOOLS = _registry.function_tool_names()

__all__ = [
    "ToolContext",
    "ToolResult",
    "ToolSpec",
    "TOOL_REGISTRY",
    "TOOL_SCHEMAS",
    "FUNCTION_TOOLS",
    "discover",
    "register",
    "schemas_for",
]
