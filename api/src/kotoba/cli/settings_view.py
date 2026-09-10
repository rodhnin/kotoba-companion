"""What `/settings` shows, and what `/set` will take before it takes it.

A raw `runtime_all()` lies in five places and `shows()` is the ONE copy of each correction: an empty
`base_url` means the provider's own and an empty per-role model INHERITS; `reasoning_effort` is clamped
(`minimal` on OpenAI goes out as `low`); the fast engine deletes `expressive`'s tags; "" is `off`.

The tables decide the MESSAGE only — the write goes through `set_runtime`, so drift can refuse
something valid but never store something invalid; `_bool` never raises, so `/set expressive tru` would
report success and turn the setting OFF. `sandbox` is not the approval gate: `none` is the strictest.
"""
from __future__ import annotations

from kotoba.cli.render.rows import _count
from kotoba.cli.render.text import fit

ENUMS = {
    "provider": ("openai", "xai"),
    "reasoning_effort": ("off", "", "minimal", "low", "medium", "high", "xhigh", "max"),
    "voice_mode": ("agent", "local"),
    "tts_engine": ("expressive", "fast"),
    "sandbox": ("local", "docker", "none"),
}
FLOORS = {"work_timeout": (30, float), "work_max_iter": (1, int),
          "work_max_tool_calls": (1, int), "work_fail_limit": (1, int)}
BOOLS = ("expressive",)
URLS = ("base_url",)
GATED_KEYS = ("sandbox", "provider")
INHERITS = {"work_model": "same as companion", "code_model": "same as work",
            "research_model": "same as work", "utility_model": "same as companion"}

SECTIONS = (
    ("BRAIN", ("provider", "base_url", "model", "work_model", "code_model",
               "research_model", "utility_model", "reasoning_effort")),
    ("VOICE", ("voice_mode", "tts_engine", "expressive", "elevenlabs_agent_id")),
    ("WORK", ("work_timeout", "work_max_iter", "work_max_tool_calls",
              "work_fail_limit")),
    ("SECURITY", ("sandbox",)),
    ("HER", ()), ("MCP", ()), ("SKILLS", ()), ("MEMORY", ()),
    ("REMINDERS", ()), ("TOOLSETS", ()), ("KEYS", ()), ("PLUGINS", ()),
)

# `trust` and `browser_cdp` come from the environment; her name, her language and her voice are hers and
# change where they were made — the DB the Settings panel and her own turns write.
ENV_ONLY = ("trust", "browser_cdp")
HERS = ("name", "language", "voice_id")


def accepts(key: str, room: int = 999) -> str:
    """What /set will take, longest form that fits. `reasoning_effort` is the only list long enough to
    need the twin: its long form is 49 cells and `slash._setting_row` hands it the width minus 24 minus
    the current value, so the twin appears under ~76-80 columns (~70-74 before `max` joined the list)."""
    if key in ENUMS:
        vals = [v for v in ENUMS[key] if v != ""]
        return fit(room, " · ".join(vals), f"{vals[0]} … {vals[-1]}")
    if key in BOOLS:
        return "on · off"
    if key in URLS:
        return fit(room, "http(s)://… or nothing", "http(s)://…")
    if key in FLOORS:
        return fit(room, f"a number, {FLOORS[key][0]} or more", f"{FLOORS[key][0]} or more")
    return "any name"


def shows(key: str, values: dict) -> tuple[str, str]:
    """(what the value is, what it would hide) — one line for each value a raw read reports wrongly."""
    from kotoba.core import providers

    v = values[key]
    prov = str(values["provider"])
    if key in BOOLS:
        base = "on" if v else "off"
        if v and values["voice_mode"] == "local" and values["tts_engine"] == "fast":
            return base, "the tags are off anyway — tts_engine is fast"
        return base, ""
    if key in FLOORS and FLOORS[key][1] is float:
        return f"{v:g}", "seconds of her own compute"
    if key == "base_url" and not v:
        url = providers.get_spec(prov).default_base_url
        return "", f"{prov}'s own" + (f": {url}" if url else "")
    if key == "reasoning_effort":
        if not v or v == "off":
            return "off", "no reasoning at all"
        eff = providers.normalize_effort(str(v), prov)
        return str(v), "" if eff == v else f"{prov} takes it as {eff}"
    if key in INHERITS and not v:
        return "", INHERITS[key]
    if key == "sandbox":
        return str(v), {"local": _local_note(),
                        "docker": "everything she runs stays in a container — only a dangerous command asks",
                        "none": "she runs nothing on this machine at all"}[str(v)]
    if key == "voice_mode":
        return str(v), "through ElevenLabs' own tunnel" if v == "agent" else ""
    if key == "tts_engine":
        return str(v), ("eleven_v3 by REST — keeps the tags"
                        if v == "expressive" else "flash_v2_5 by socket, no tags")
    if key == "elevenlabs_agent_id" and not v:
        return "", "none saved"
    return str(v), ""


def _local_note() -> str:
    from kotoba.core import approval

    if approval._windows_shell():
        return "she runs on this machine — she asks first"
    return "she runs on this machine — a plain read in her workdir passes, the rest asks"


def unasked() -> str:
    from kotoba.core import approval, sandbox

    backend = sandbox.backend_name()
    if backend == "none":
        return "she runs no commands at all"
    if backend == "docker":
        return "in a container anything that isn't dangerous runs without a card"
    if approval._windows_shell():
        return "every command asks first"
    return "only a plain read in her workdir runs without a card"


def value_note(key: str, value: str, values: dict) -> str:
    """What one value of a fixed-list setting would mean, in the words `/settings` already uses for
    whichever one is current. Asked by putting the value in and reading `shows()` back, so the menu
    cannot drift from the listing: there is one copy of that sentence and it lives in `shows`."""
    v = value in ("on", "1", "true", "yes") if key in BOOLS else value
    return shows(key, {**values, key: v})[1]


def check(key: str, raw: str, values: dict) -> tuple[object, str]:
    """(the value it would take, "") or (None, why it wouldn't — in her voice). Nothing is written."""
    if key in ENV_ONLY:
        return None, f"{key} isn't mine to change — it comes from the environment"
    if key in HERS:
        return None, (f"{key} isn't something /set reaches — it's what she's got, and it changes "
                      "where it was made")
    if key not in values:
        return None, (f"I don't have a setting called {key} — /settings lists every one of mine")
    s = raw.strip()
    if key in ENUMS:
        low = s.lower()
        if low not in ENUMS[key]:
            return None, f"{key} takes {accepts(key)} — {s!r} isn't one of them"
        return low, ""
    if key in BOOLS:
        low = s.lower()
        if low in ("1", "true", "yes", "on"):
            return True, ""
        if low in ("0", "false", "no", "off"):
            return False, ""
        return None, f"{key} is on or off — {s!r} is neither"
    if key in URLS:
        if s and not s.startswith(("http://", "https://")):
            return None, ("a base_url has to start with http:// or https:// — or be nothing at all, "
                          "and I'll use the provider's")
        return s, ""
    if key in FLOORS:
        lo, cast = FLOORS[key]
        try:
            n = cast(_finite(s))
        except (ValueError, OverflowError):
            return None, f"{key} is a number — {s!r} isn't one"
        if n < lo:
            return None, f"{key} can't go under {lo} — that's the floor"
        return n, ""
    return s, ""


def _finite(s: str) -> float:
    """`float(s)`, refusing the two values it accepts that no clock can be set to.

    `inf` and `nan` are the rule this mirror was missing, and the backend already has it
    (`app_settings._finite`, written after an infinite work_timeout and a nan that loses every
    comparison). Missing here it did not merely drift: `int(float("inf"))` raises OverflowError,
    which `check` did not catch, and `nan < 30` is False, so a NaN passed the floor and reached
    `set_runtime` — and neither exception is caught in `slash._set` either. Both took the whole
    session down with a traceback, on the surface whose rule is that she never shows one."""
    n = float(s)
    if n != n or n in (float("inf"), float("-inf")):
        raise ValueError(s)
    return n


def consequence(key: str, value: str, values: dict) -> str:
    """The two that change the trust model say what they change, in her words."""
    if key == "sandbox":
        return {"none": ("that takes my hands away — shell and execute_code stop being offered and "
                         "I start no process on this machine. The MCP servers and the browser are "
                         "approved separately and still open when you use them"),
                "docker": ("that moves everything I run into a container that sees only my workdir — "
                           "and I stop asking before anything that isn't dangerous"),
                "local": ("that puts me back on your machine — I'll ask you before anything that "
                          "isn't a plain read")}[value]
    return (f"that sends everything you say to {value} instead of {values['provider']} — a different "
            "company, a different privacy policy, and the keys are kept apart")


def grant_permits(pattern: str, scope: str = "command") -> str:
    """What a saved 'always allow' buys NOW — the one copy of that sentence, so `/settings security` and
    `/approvals` cannot disagree about whether a grant is live. An interpreter or exec-wrapper grant
    (`sh`, `python`, `env`) no longer grants anything, so it must not read as live; a read grant
    (`ls`, `cat`) is kept inside her workdir.

    An 'exact' grant is a different WIDTH, not a different flavour: a family covers every command that
    starts with its token, an exact one covers a single line and nothing adjacent to it. Somebody
    deciding what to take back has to be able to tell those apart at a glance, so the sentence says
    which it is rather than leaving the two to look alike in a list."""
    from kotoba.core import approval

    if scope == "exact":
        return "runs this exact line without asking — nothing else"
    if approval.family_never_auto_approves(pattern):
        return "asks every time — this no longer grants anything"
    if pattern == "execute_code":
        return "runs code without asking — still asks for anything risky"
    if approval.is_auto_safe_family(pattern):
        return "runs inside her workdir without asking"
    return f"runs {pattern} without asking"


def section_info(name: str, data: dict) -> list[tuple[str, str, str]]:
    """The rows of a section that `/set` cannot touch, as data: (name, value, note). One source, because
    a menu that disagreed with the listing about what is read-only would be worse than no menu.

    Everything comes off the same gather the web panel renders, so the
    two surfaces cannot drift about what she is configured with."""
    if name == "SECURITY":
        approved = data["security"]["approvals"]
        rows = [("trust", data["security"]["trust"], ""),
                ("browser_cdp", data["browser_cdp"], "")]
        for i, grant in enumerate(approved):
            pattern, scope = grant["pattern"], grant.get("scope", "command")
            rows.append((pattern, grant_permits(pattern, scope),
                         "always allowed" if i == 0 else ""))
        return rows
    if name == "HER":
        return [(k, str(data["personality"].get(k) or ""), "") for k in HERS]
    if name == "MCP":
        return ([(s["name"], _count(s["tools"], "tool"), "connected") for s in data["mcp_servers"]]
                + [(p["name"], p["reason"], p["kind"]) for p in data["pending_mcp"]])
    if name == "SKILLS":
        return [(s["name"], s["description"], "") for s in data["skills"]]
    if name == "MEMORY":
        return [(t["slug"], _count(t["count"], "thing") + " I've kept", "")
                for t in data["memory"]["topics"]]
    if name == "REMINDERS":
        return [(str(r["id"]), r["message"], r["recurring"] or when(r["due_at"]))
                for r in data["reminders"]]
    if name == "TOOLSETS":
        return [(t["name"], _count(t["tools"], "tool"), "" if t["enabled"] else "off")
                for t in data["toolsets"]]
    if name == "KEYS":
        return [(n, "", "saved — the name is all that leaves") for n in data["keys"]]
    if name == "PLUGINS":
        return [(p["name"], f"{p['source']} · {_count(len(p['tools']), 'tool')}",
                 "" if p["enabled"] else "off") for p in data["plugins"]]
    return []


def when(stamp: str) -> str:
    """A TIMESTAMP as a person reads it. SQLite writes CURRENT_TIMESTAMP in naive UTC, so it is read as
    UTC and shown in the local clock — the prototype's fixtures were already local and did not have to.

    `sessions.ended_at` is never written (a real column no query touches), so there is no duration to
    show and this is the only clock a row gets: when it STARTED, not how long it went on."""
    import time
    from datetime import datetime, timezone

    if not stamp:
        return ""
    text = str(stamp).strip().replace("T", " ").rstrip("Z").split(".")[0]
    try:
        utc = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return str(stamp)
    t = utc.astimezone().timetuple()
    days = (time.mktime(time.localtime()[:3] + (0,) * 6)
            - time.mktime(t[:3] + (0,) * 6)) / 86400
    clock = time.strftime("%H:%M", t)
    if days < 1:
        return f"today {clock}"
    if days < 2:
        return f"yesterday {clock}"
    return time.strftime(f"%a %d %b {clock}", t)
