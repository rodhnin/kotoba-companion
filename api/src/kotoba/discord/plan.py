"""A whole server described as a shape, turned into the smallest set of changes that reaches it.

Two rules carry the safety of the entire feature.

**Silence means keep.** `remove` defaults to all-false, so a plan can never delete something by
forgetting to mention it — a "reorganise by topic" that omits a channel leaves that channel alone. Only
an explicit removal flag lets a deletion into a plan at all.

**Destructive last.** Deletions are ordered after everything else, so any earlier failure aborts the
run before a single one has happened.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

# Positions shift under every create and delete, so they are settled once at the end rather than
# churned through the middle.
ORDER = (
    "create_role", "create_category", "update_category", "create_channel", "update_channel",
    "update_role", "set_position",
    "delete_channel", "delete_category", "delete_role",
)


class StalePlan(ValueError):
    """A plan or journal that only recorded positions cannot be mapped once the server has moved under
    it; refusing is the only answer that never deletes the wrong thing."""


@dataclass
class Change:
    id: str
    op: str
    kind: str
    key: str
    name: str
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    ref: str = ""

    def __post_init__(self) -> None:
        if not self.ref:
            self.ref = f"{self.op}:{self.key}"

    @property
    def destructive(self) -> bool:
        return self.op.startswith("delete_")

    def line(self) -> str:
        verb = self.op.replace("_", " ")
        if self.op.startswith("create_"):
            return f"{verb} “{self.name}”"
        if self.destructive:
            return f"{verb} “{self.name}”"
        bits = ", ".join(f"{k}: {self.before.get(k)!r} -> {v!r}" for k, v in self.after.items())
        return f"{verb} “{self.name}” ({bits})"


def _match(existing: list[dict], key: str, name: str):
    """An id is exact; a name is how the model naturally refers to what it just read."""
    if key and key.isdigit():
        return next((e for e in existing if e["id"] == key), None)
    wanted = (name or key or "").strip().lower().lstrip("#@")
    return next((e for e in existing if e["name"].lower() == wanted), None)


def _protected(entry: dict, protect: set[str]) -> bool:
    low = {p.strip().lower().lstrip("#@") for p in protect}
    return entry["id"] in protect or entry["name"].lower() in low


def diff(snap: dict, desired: dict, *, protect: set[str] | None = None,
         remove: dict | None = None) -> list[Change]:
    protect = set(protect or ())
    remove = remove or {}
    out: list[Change] = []

    for kind, plural in (("role", "roles"), ("category", "categories"), ("channel", "channels")):
        existing = list(snap.get(plural) or [])
        seen: set[str] = set()
        for want in (desired.get(plural) or []):
            key = str(want.get("key") or "")
            name = str(want.get("name") or "")
            found = _match(existing, key, name)
            if found is None:
                out.append(Change("", f"create_{kind}", kind, key or name, name, after=dict(want)))
                continue
            seen.add(found["id"])
            # Only fields the plan actually names, and only where the server differs. `is not None`
            # guards the value asked for, never the one already there — reading it the other way
            # made "give this channel a topic" a no-op on every channel that had none.
            changed = {k: v for k, v in want.items()
                       if k not in ("key", "position") and v is not None and found.get(k) != v}
            if changed:
                out.append(Change("", f"update_{kind}", kind, found["id"], found["name"],
                                  before={k: found.get(k) for k in changed}, after=changed))
        if not remove.get(plural):
            continue
        for entry in existing:
            if entry["id"] in seen or _protected(entry, protect):
                continue
            if entry.get("managed") or entry["name"] == "@everyone":
                continue
            out.append(Change("", f"delete_{kind}", kind, entry["id"], entry["name"],
                              before=dict(entry)))

    times: dict[str, int] = {}
    for change in out:
        times[change.ref] = times.get(change.ref, 0) + 1
        if times[change.ref] > 1:
            change.ref = f"{change.ref}#{times[change.ref]}"
    return out


def order(changes: list[Change]) -> list[Change]:
    rank = {op: i for i, op in enumerate(ORDER)}
    return sorted(changes, key=lambda c: rank.get(c.op, len(ORDER)))


def render(plan: dict, changes: list[Change]) -> str:
    """Numbered, because the numbers are how somebody says which ones to drop."""
    if not changes:
        return "Nothing would change — the server already looks like that."
    lines = [f"Plan `{plan['plan_id']}` (revision {plan['rev']}) for “{plan['guild_name']}”:"]
    if plan.get("intent"):
        lines.append(f"What for: {plan['intent']}")
    keep, gone = [], []
    for change in order(changes):
        (gone if change.destructive else keep).append(f"  {change.id}. {change.line()}")
    if keep:
        lines.append("\nChanges:")
        lines += keep
    if gone:
        lines.append("\nDELETIONS — these remove messages with them:")
        lines += gone
    if plan.get("protect"):
        lines.append("\nProtected, never touched: " + ", ".join(plan["protect"]))
    lines.append(f"\n{len(changes)} change(s). Say which numbers to drop, or tell me to apply it.")
    return "\n".join(lines)


def new_plan(guild_id: str, guild_name: str, intent: str, desired: dict) -> dict:
    return {
        "version": 2,
        "plan_id": uuid.uuid4().hex[:4],
        "rev": 1,
        "guild_id": str(guild_id),
        "guild_name": guild_name,
        "intent": intent,
        "desired": desired,
        "protect": [],
        "remove": {"channels": False, "categories": False, "roles": False},
        "excluded": [],
        "ids": {},
        "updated_at": time.time(),
    }


def label(plan: dict, ref: str) -> str:
    """An alias handed out on first sight and never reused, so a number read off one card still names the
    same change on the next. `ref` is what exclusions and the journal store: positions shift under every
    partial apply, and a withdrawn deletion came back that way."""
    ids = plan.setdefault("ids", {})
    for known, target in ids.items():
        if target == ref:
            return known
    n = 1 + max((int(k[1:]) for k in ids if k[1:].isdigit()), default=0)
    ids[f"c{n}"] = ref
    return f"c{n}"


def resolve(plan: dict, handle: str) -> str | None:
    ids = plan.get("ids") or {}
    handle = str(handle).strip()
    if handle.isdigit():
        handle = f"c{handle}"
    if handle in ids:
        return ids[handle]
    if ":" in handle:
        return handle
    return None


def excluded_refs(plan: dict) -> set[str]:
    out: set[str] = set()
    for handle in (plan.get("excluded") or ()):
        ref = resolve(plan, handle)
        if ref is None:
            raise StalePlan(
                f"I can't safely use plan {plan.get('plan_id')}: it predates the way I now keep "
                "track of which changes they said no to, so I can't tell which those were. Make a "
                "fresh plan from the server as it is now.")
        out.add(ref)
    return out


def live_changes(plan: dict, snap: dict) -> list[Change]:
    """Re-diffed against a fresh reading every time, so a plan cannot act on a server that moved.

    Dropped ids stay dropped: they are recorded on the plan rather than edited out of the desired
    state, or a later re-diff would quietly bring back the very change somebody said no to.
    """
    dropped = excluded_refs(plan)
    changes = diff(snap, plan.get("desired") or {},
                   protect=set(plan.get("protect") or ()),
                   remove=plan.get("remove") or {})
    for change in order(changes):
        change.id = label(plan, change.ref)
    return [c for c in changes if c.ref not in dropped]


def plan_dir(workdir, guild_id: str):
    from pathlib import Path

    path = Path(workdir) / "discord" / str(guild_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(workdir, plan: dict) -> None:
    from kotoba.core import atomic_file

    plan["updated_at"] = time.time()
    target = plan_dir(workdir, plan["guild_id"]) / f"plan-{plan['plan_id']}.json"
    atomic_file.write_text(target, json.dumps(plan, ensure_ascii=False, indent=1))


def load(workdir, guild_id: str, plan_id: str) -> dict | None:
    path = plan_dir(workdir, guild_id) / f"plan-{plan_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_snapshot(workdir, snap: dict) -> str:
    """Written BEFORE the first mutation. It is the record of what was there — not an undo, and it
    must never be described as one: recreating a deleted channel does not bring its messages back."""
    from kotoba.core import atomic_file

    stamp = time.strftime("%Y%m%dT%H%M%S")
    gid = snap["guild"]["id"]
    target = plan_dir(workdir, gid) / f"snapshot-{stamp}.json"
    atomic_file.write_text(target, json.dumps(snap, ensure_ascii=False, indent=1))
    return str(target)
