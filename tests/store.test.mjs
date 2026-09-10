// Store invariants for the work-mode UI, run against the REAL lib/store.ts (Node strips the types):
//   node tests/store.test.mjs
// Guards: exactly one chibi per spawned subagent id (re-spawn and step/finish events never add
// entries), step events for unknown ids create nothing, and the work lifecycle clears chibis +
// the working chip (clearSubagents on work_started/work_done, clearWork on call end).
import assert from "node:assert/strict";

const { useKotobaStore, stepStatus } = await import("../lib/store.ts");
const s = () => useKotobaStore.getState();

s().clearWork();
assert.equal(s().subagents.length, 0);
assert.equal(s().working, false);

s().spawnSubagent("a1", "research React");
s().spawnSubagent("a1", "research React");
s().spawnSubagent("a1", "research React (retry frame)");
assert.equal(s().subagents.length, 1, "re-spawning the same id must not add a chibi");

s().addSubagentStep("a1", "step 1");
s().addSubagentStep("a1", "step 2");
s().addSubagentStep("a1", "step 2");
assert.equal(s().subagents.length, 1, "step events must never add chibis");
assert.deepEqual(s().subagents[0].steps, ["step 1", "step 2", "step 2"]);

s().addSubagentStep("ghost", "step for a never-spawned id");
s().finishSubagent("ghost", "summary");
assert.equal(s().subagents.length, 1, "events for unknown ids must not create chibis");

s().spawnSubagent("a2", "research Vue");
s().spawnSubagent("a3", "research Svelte");
s().finishSubagent("a2", "done", true);
s().finishSubagent("a2", "done again", true);
assert.equal(s().subagents.length, 3, "finish must not add or remove chibis");
assert.equal(s().subagents.filter((a) => a.status === "working").length, 2);

s().dismissSubagent("a3");
assert.equal(s().subagents.length, 2);

s().setWorking(true);
assert.equal(s().working, true);
s().clearSubagents();
assert.equal(s().subagents.length, 0, "work_done/work_started must clear every chibi");
assert.equal(s().working, true, "clearSubagents must not touch the chip");

s().spawnSubagent("b1", "leftover");
s().clearWork();
assert.equal(s().subagents.length, 0);
assert.equal(s().working, false);

// A DEFERRED step (approval card up, nothing run yet) showed a ✓ next to "I asked for
// your permission" and was never updated with the real run. The row must park as `pending`, then be
// completed in place by core/deferred_exec re-sending the SAME call_id with the real output.
s().clearWork();
s().pushStep({ phase: "start", id: "call_1", step_kind: "code", action: "run: primes.py" });
assert.equal(s().logs.length, 1);
assert.equal(s().logs[0].running, true);

s().pushStep({ phase: "done", id: "call_1", ok: true, pending: true, result: "I asked for your permission on screen." });
assert.equal(s().logs.length, 1, "closing a step must not add a second row");
assert.equal(s().logs[0].running, false);
assert.equal(s().logs[0].pending, true, "a deferred step must not read as done");

s().pushStep({ phase: "done", id: "call_1", ok: true, pending: false, step_kind: "code", action: "run: primes.py", result: "exit=0\n168" });
assert.equal(s().logs.length, 1, "the deferred result must update the row, not append a new one");
assert.equal(s().logs[0].pending, false, "pending must be cleared once it actually ran");
assert.equal(s().logs[0].ok, true);
assert.match(s().logs[0].result, /168/, "the real output must reach the row");

// A normal (non-deferred) done frame omits `pending` entirely — it must never look deferred.
s().pushStep({ phase: "start", id: "call_2", step_kind: "shell", action: "ls" });
s().pushStep({ phase: "done", id: "call_2", ok: true, result: "exit=0" });
assert.equal(s().logs[1].pending, false);

// A step cannot un-finish. One ordered SSE connection never delivers a `start`
// after its `done`, but a row REBUILT by a re-sent `done` (the deferred / evicted-row paths above)
// would be flipped back into `running` by one — and no second `done` is coming, so the row would
// spin for the rest of the call. Reproduced against this store before the guard existed.
s().clearWork();
s().pushStep({ phase: "done", id: "oo", ok: true, outcome: "ok", step_kind: "shell", action: "ls", result: "exit=0" });
assert.equal(stepStatus(s().logs[0]), "ok");
s().pushStep({ phase: "start", id: "oo", step_kind: "shell", action: "ls" });
assert.equal(s().logs.length, 1, "a re-ordered start must not add a row");
assert.equal(stepStatus(s().logs[0]), "ok", "a finished step must never go back to running");
assert.equal(s().logs[0].result, "exit=0", "and it must keep the result it already reported");
// A start for a row that IS running is still the ordinary case and must merge as before.
s().clearWork();
s().pushStep({ phase: "start", id: "n1", step_kind: "shell", action: "sleep" });
s().pushStep({ phase: "start", id: "n1", step_kind: "shell", action: "sleep 10" });
assert.equal(s().logs.length, 1);
assert.equal(s().logs[0].action, "sleep 10", "a repeated start still refreshes the open row");
assert.equal(stepStatus(s().logs[0]), "running");
s().clearWork();

// The log can be cleared between the card and the approval — the completion must still render a full row.
s().clearWork();
s().pushStep({ phase: "done", id: "call_3", ok: true, pending: false, step_kind: "shell", action: "pip install requests", result: "exit=0" });
assert.equal(s().logs.length, 1);
assert.equal(s().logs[0].stepKind, "shell", "a rebuilt row still needs its type chip");
assert.equal(s().logs[0].action, "pip install requests");
s().clearWork();

// The UI held ONE card, and a card timing out emitted a global need_input{clear}
// that dismissed whatever else was on screen. Cards are now a stack keyed by request_id: a clear names
// its own card, and only a bare clear (leave / cancel_work) wipes the lot.
s().clearWork();
s().pushInputRequest({ id: "r1", requestId: "r1", mode: "approval", label: "rm -rf build" });
s().pushInputRequest({ id: "r2", requestId: "r2", mode: "input", label: "paste the URL" });
assert.equal(s().inputRequests.length, 2, "two outstanding cards must both survive");

s().pushInputRequest({ id: "r2", requestId: "r2", mode: "input", label: "paste the URL (retry frame)" });
assert.equal(s().inputRequests.length, 2, "a repeated frame for the same id must not stack a duplicate");

s().dismissInputRequest("r1");                       // r1 timed out
assert.deepEqual(s().inputRequests.map((r) => r.id), ["r2"], "a timeout must dismiss only its own card");

s().pushInputRequest({ id: "r3", requestId: "r3", mode: "approval", label: "npm run build" });
assert.equal(s().inputRequests[s().inputRequests.length - 1].id, "r3", "the newest card is the one shown");
s().dismissInputRequest("ghost");
assert.equal(s().inputRequests.length, 2, "clearing an unknown id must not touch anything");

s().dismissInputRequest();                            // bare clear = leave / cancel_work
assert.equal(s().inputRequests.length, 0);

s().pushInputRequest({ id: "r4", mode: "approval", label: "still up when the call ends" });
s().clearWork();
assert.equal(s().inputRequests.length, 0, "ending the call takes the cards with it");

// Push scrubs through scrubDeep (newlines KEPT); dismiss must use the same gate,
// or an id carrying a character the two settings disagree about never matches its own card and the
// card can never be dismissed. Backend ids are uuid4 hex today, so this is a latent hole, not a
// live one — pinned so it stays shut if the id shape ever changes.
for (const id of ["cr\rlf\n", "line\none", "plain-8f3a91c2", "  padded  ", "tab\tid"]) {
  s().clearWork();
  s().pushInputRequest({ id, mode: "approval", label: "x" });
  assert.equal(s().inputRequests.length, 1, `${JSON.stringify(id)} must open a card`);
  s().dismissInputRequest(id); // the wire re-sends the RAW id
  assert.equal(s().inputRequests.length, 0, `${JSON.stringify(id)} must dismiss its own card`);
}
s().clearWork();

// ── TaskTab state ────────────────────────────────────────────────────────────
// setTasks, auto-open logic, stale-rev guard, clearWork wipe.
s().clearWork();
assert.equal(s().tasks, null, "clearWork leaves tasks null");
assert.equal(s().tasksOpen, false);
assert.equal(s().tasksAutoOpenedFor, null);

const planA = { list_id: "l1", title: "informe", tasks: [], status: "open", rev: 1 };
s().setTasks(planA);
assert.deepEqual(s().tasks, planA, "setTasks stores the list");
assert.equal(s().tasksOpen, true, "first set auto-opens the panel");
assert.equal(s().tasksAutoOpenedFor, "l1", "auto-open records the list_id");

// User closes the panel manually — subsequent updates must not force it back open.
s().setTasksOpen(false);
assert.equal(s().tasksOpen, false);

const planAv2 = { ...planA, rev: 2 };
s().setTasks(planAv2);
assert.equal(s().tasks.rev, 2, "higher rev replaces the stored list");
assert.equal(s().tasksOpen, false, "same list_id must not re-trigger auto-open");

// Stale frame (lower rev than what we hold) — must be silently dropped.
s().setTasks({ ...planA, rev: 1 });
assert.equal(s().tasks.rev, 2, "frame older than stored rev is dropped");

// Equal rev — treated as the same frame, does replace (idempotent update).
s().setTasks({ ...planAv2 });
assert.equal(s().tasks.rev, 2, "equal rev replaces (idempotent)");

// A completely new list_id auto-opens again.
const planB = { list_id: "l2", title: "research", tasks: [], status: "open", rev: 1 };
s().setTasks(planB);
assert.equal(s().tasks.list_id, "l2", "new plan replaces old one");
assert.equal(s().tasksOpen, true, "new list_id triggers auto-open again");
assert.equal(s().tasksAutoOpenedFor, "l2");

// A LATE REST rehydrate of a plan that has since been replaced must not resurrect it. `rev` only orders
// frames within one list, so without its own guard the old plan comes back AND re-opens the panel.
s().setTasksOpen(false);
s().setTasks({ list_id: "l1", title: "informe", tasks: [], status: "open", rev: 9 }, true);
assert.equal(s().tasks.list_id, "l2", "a rehydrate of a replaced plan is ignored, whatever its rev");
assert.equal(s().tasksOpen, false, "and it must not re-open the panel on the stale plan");

// A rehydrate of the CURRENT plan is still how a reconnect catches up.
s().setTasks({ ...planB, rev: 3 }, true);
assert.equal(s().tasks.rev, 3, "rehydrate of the current list still applies");

// clearWork wipes the whole task slice.
s().clearWork();
assert.equal(s().tasks, null, "clearWork clears tasks");
assert.equal(s().tasksOpen, false, "clearWork clears tasksOpen");
assert.equal(s().tasksAutoOpenedFor, null, "clearWork clears tasksAutoOpenedFor");

console.log("store.test.mjs: all assertions passed");

// ── the face does not outlive the call it belonged to ───────────────────────
// components/Live2DCanvas re-applies `emotion` whenever `phase` flips back to "live", and the gate is
// on the phase rather than on the emotion changing — so a call that ended on `sad` opened the NEXT one
// wearing it, until the model happened to emit a tag. Everything else the call owns is wiped here;
// this slice was the visible one that was not.
s().setEmotion("sad");
assert.equal(s().emotion, "sad");
s().clearWork();
assert.equal(s().emotion, "neutral", "the next call must not open wearing the last one's face");
