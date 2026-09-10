// The terminal row's status mark, run against the REAL lib/store.ts (Node strips the types):
//   node tests/terminal-status.test.mjs
// A command that exited non-zero, one killed at its timeout, and an action the
// user refused all drew a green ✓, because the row read `ok` and `ok` only means the tool returned text.
// A cut turn was told from a real failure by the English word "(interrupted)" in its output — which this
// store dropped anyway. These pin the four endings apart, and pin the rule that decides the doubtful ones:
// a ✓ is only ever drawn for an ending that explicitly says it succeeded.
import assert from "node:assert/strict";

const { stepStatus, useKotobaStore } = await import("../lib/store.ts");
const s = () => useKotobaStore.getState();

const after = (start, done) => {
  s().clearWork();
  if (start) s().pushStep({ phase: "start", id: "c1", step_kind: "shell", action: "$ cmd" });
  s().pushStep({ phase: "done", id: "c1", ...done });
  return s().logs[0];
};

// ── the four endings, as core/loop puts them on the wire ─────────────────────
assert.equal(stepStatus(after(true, { ok: true, outcome: "ok", result: "exit=0" })), "ok");
assert.equal(
  stepStatus(after(true, { ok: true, outcome: "failed", result: "exit=2\nstderr:\nno such file" })),
  "failed",
  "a command that exited non-zero ran and FAILED, whatever text it handed back",
);
assert.equal(
  stepStatus(after(true, { ok: true, outcome: "failed", result: "exit=124\nstderr:\ntimed out after 10s and was killed" })),
  "failed",
  "killed at its timeout is a failure, and nothing here reads the word 'timed'",
);
assert.equal(
  stepStatus(after(true, { ok: true, outcome: "refused", result: "I held off on that one." })),
  "refused",
  "the user said no: it never ran, and that is not a failure",
);
assert.equal(
  stepStatus(after(true, { ok: false, outcome: "interrupted", interrupted: true, result: "(interrupted)" })),
  "interrupted",
  "cut mid-run is not a failure either — and must not need the display text to be known",
);
assert.equal(
  stepStatus(after(true, { ok: true, outcome: "pending", pending: true, result: "I asked you on screen." })),
  "pending",
  "carded and unanswered: it has not run at all",
);

// A row still running keeps its spinner whatever else the frame says.
s().clearWork();
s().pushStep({ phase: "start", id: "c1", step_kind: "shell", action: "$ cmd" });
assert.equal(stepStatus(s().logs[0]), "running");

// ── the bar: an ending nobody anticipated must not look like success ─────────
assert.equal(
  stepStatus(after(true, { ok: true, outcome: "quarantined", result: "?" })),
  "unknown",
  "an outcome this build has never heard of is unknown, NOT ok",
);
assert.equal(
  stepStatus(after(true, { result: "who knows" })),
  "unknown",
  "a done frame that claims nothing at all is unknown, not a ✓",
);
assert.equal(stepStatus(after(true, { ok: true, outcome: "" , result: "x" })), "ok", "an empty outcome falls back to ok");

// ── frames from a backend that predates `outcome` ────────────────────────────
assert.equal(stepStatus(after(true, { ok: true, result: "exit=0" })), "ok");
assert.equal(stepStatus(after(true, { ok: false, result: "The tool errored." })), "failed");
assert.equal(stepStatus(after(true, { ok: false, interrupted: true, result: "(interrupted)" })), "interrupted");
assert.equal(stepStatus(after(true, { ok: true, pending: true, result: "asked" })), "pending");

// core/deferred_exec completes a carded row from OUTSIDE the turn, minutes later, with an `outcome` of
// its own off the exit code of what was finally approved. `ok` stays true for anything that ran at all.
const deferred = (done) => {
  s().clearWork();
  s().pushStep({ phase: "start", id: "c7", step_kind: "shell", action: "$ pip install requests" });
  s().pushStep({ phase: "done", id: "c7", ok: true, outcome: "pending", pending: true, result: "asked" });
  s().pushStep({ phase: "done", id: "c7", pending: false, step_kind: "shell", action: "$ pip install requests", ...done });
  return s().logs[0];
};
assert.equal(stepStatus(deferred({ ok: true, outcome: "ok", result: "I ran it (exit code 0)." })), "ok");
assert.equal(
  stepStatus(deferred({ ok: true, outcome: "failed", result: "I ran it (exit code 1)." })),
  "failed",
  "the approved command exited non-zero — worded her way, marked the process's",
);
assert.equal(
  stepStatus(deferred({ ok: false, outcome: "refused", result: "not approved — didn't run" })),
  "refused",
  "the card went unanswered: nothing ran, and `ok=false` alone would call that a fault",
);
assert.equal(
  stepStatus(deferred({ ok: true, result: "I ran it (exit code 0)." })),
  "ok",
  "even wordless, a completion clears the parked status — a row stuck on '…' after it ran is its own lie",
);
assert.equal(
  stepStatus(deferred({ ok: false, outcome: "interrupted", interrupted: true, result: "stopped mid-run" })),
  "interrupted",
  "the user said stop with the approved command already running — the frame that closes that row",
);

// ── the row must be UPDATED in place, and must lose a stale status ───────────
// A deferred row is re-sent later with the real ending; the old `pending`/`outcome` cannot survive it.
s().clearWork();
s().pushStep({ phase: "start", id: "c9", step_kind: "code", action: "python> primes.py" });
s().pushStep({ phase: "done", id: "c9", ok: true, outcome: "pending", pending: true, result: "asked" });
assert.equal(stepStatus(s().logs[0]), "pending");
s().pushStep({ phase: "done", id: "c9", ok: true, outcome: "failed", pending: false, step_kind: "code", action: "python> primes.py", result: "exit=1" });
assert.equal(s().logs.length, 1, "completing a row must not append a second one");
assert.equal(stepStatus(s().logs[0]), "failed", "the real ending replaces the parked one");

// The same in the other direction: an interrupted row that a later frame lands cleanly.
s().pushStep({ phase: "done", id: "c9", ok: false, outcome: "interrupted", interrupted: true, result: "(interrupted)" });
assert.equal(stepStatus(s().logs[0]), "interrupted");
s().pushStep({ phase: "done", id: "c9", ok: true, outcome: "ok", result: "exit=0" });
assert.equal(stepStatus(s().logs[0]), "ok", "`interrupted` must be rewritten, never merged");

// ── a row rebuilt from scratch (the log was cleared while a card was up) ─────
s().clearWork();
s().pushStep({ phase: "done", id: "c3", ok: true, outcome: "refused", step_kind: "shell", action: "rm -rf build", result: "not approved" });
assert.equal(s().logs.length, 1);
assert.equal(s().logs[0].stepKind, "shell", "a rebuilt row still needs its type chip");
assert.equal(stepStatus(s().logs[0]), "refused");
s().clearWork();

console.log("terminal-status.test.mjs: all assertions passed");
