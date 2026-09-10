// The work bracket routes by RUN, against the REAL store (node strips the types).
//
// `cancel_work` cancels the old runner and the same turn launches the next job; the old task's
// teardown lands ticks later, so the successor's `work_started` reaches this client BEFORE the
// superseded run's `work_done cancelled=true` and `working on=false`. Handled session-globally, that
// late close stopped the keepalive, cleared the chip and wiped the chibis of the NEW job — and the
// resync deliberately never re-sets `working`, so the lie stood for the rest of the call. The rule
// that closes it is stated once, at `ownsWorkBracket`, and pinned here with the REAL frames the
// backend emitted. The CLI reads the same rule in its own `_work`.
import assert from "node:assert/strict";

const { useKotobaStore, ownsWorkBracket } = await import("../lib/store.ts");
const s = () => useKotobaStore.getState();

// The wire, verbatim (session "wire-demo", run A=deacbec2 stopped, run B=b142f165 started over it).
const WIRE = [
  { kind: "work_started", goal: "investiga la prueba larga", run_id: "deacbec2" },
  { kind: "working", on: true, run_id: "deacbec2" },
  { kind: "work_started", goal: "escribe el resumen en su lugar", run_id: "b142f165" },
  { kind: "working", on: true, run_id: "b142f165" },
  { kind: "work_done", ok: true, summary: "", cancelled: true, run_id: "deacbec2" },
  { kind: "working", on: false, run_id: "deacbec2" },
  { kind: "work_done", ok: true, summary: "informe listo", run_id: "b142f165" },
  { kind: "working", on: false, run_id: "b142f165" },
];

// Mirrors components/CompanionExperience.tsx handleTask's work branches (there is no React harness
// in this repo); the ROUTING itself is the real imported `ownsWorkBracket` + the real store latch.
function shell() {
  const c = { keepalive: false, announced: [] };
  c.task = (ev) => {
    if (ev.kind === "work_started") {
      s().latchWorkRun(ev.run_id);
      s().setWorking(true);
      s().clearSubagents();
      c.keepalive = true;
    } else if (ev.kind === "working") {
      if (!ownsWorkBracket(s().workRunId, ev.run_id)) return;
      s().setWorking(ev.on);
    } else if (ev.kind === "work_done") {
      if (!ownsWorkBracket(s().workRunId, ev.run_id)) return;
      s().latchWorkRun("");
      c.keepalive = false;
      s().setWorking(false);
      s().clearSubagents();
      if (!ev.cancelled) c.announced.push(ev.ok ? ev.summary : "(failed)");
    }
  };
  return c;
}

// ── the defect: the superseded run's late close must not land on the new job ──────────────
{
  s().clearWork();
  const c = shell();
  for (const ev of WIRE.slice(0, 4)) c.task(ev);
  s().spawnSubagent("h1", "the new job's helper");
  for (const ev of WIRE.slice(4, 6)) c.task(ev); // run A's late close + chip-off
  assert.equal(s().working, true, "the old run's late close turned the new job's chip off");
  assert.equal(c.keepalive, true, "the old run's late close killed the new job's keepalive");
  assert.equal(s().subagents.length, 1, "the old run's late close wiped the new job's chibis");
  assert.deepEqual(c.announced, [], "a cancelled close announces nothing");
  for (const ev of WIRE.slice(6)) c.task(ev); // run B's own close
  assert.equal(s().working, false);
  assert.deepEqual(c.announced, ["informe listo"], "the new job's own close still announces");
  assert.equal(s().workRunId, "", "the bracket's close releases the latch");
}

// ── a deferred command's announce (no run_id) with no job latched keeps its behavior ──────
{
  s().clearWork();
  const c = shell();
  c.task({ kind: "work_done", ok: true, summary: "el comando corrió" });
  assert.deepEqual(c.announced, ["el comando corrió"]);
}

// ── against a MARKED bracket an unmarked work_done is another surface's ───────────────────
// deferred_exec._announce only emits while no job runs; if one leaks through the race anyway,
// dropping it is the safe half — the job's own close is still coming.
{
  s().clearWork();
  const c = shell();
  c.task(WIRE[0]);
  c.task({ kind: "work_done", ok: true, summary: "un shell terminó" });
  assert.equal(s().working, true, "a deferred result closed a marked bracket");
  assert.deepEqual(c.announced, []);
}

// ── a producer that predates the field never marks the latch, so nothing old breaks ───────
{
  s().clearWork();
  const c = shell();
  c.task({ kind: "work_started", goal: "an old producer's job" });
  assert.equal(s().workRunId, "");
  c.task({ kind: "working", on: true });
  c.task({ kind: "work_done", ok: true, summary: "done the old way" });
  assert.equal(s().working, false);
  assert.deepEqual(c.announced, ["done the old way"]);
}

// ── with no job latched, a turn's own `working` frames still drive the chip ───────────────
{
  s().clearWork();
  const c = shell();
  c.task({ kind: "working", on: true, run_id: "turn0001" });
  assert.equal(s().working, true);
  c.task({ kind: "working", on: false, run_id: "turn0001" });
  assert.equal(s().working, false);
}

// ── while a job is latched, the job owns the chip: a turn's frames cannot move it ─────────
// (the turn's own off is already suppressed server-side while a job runs — core/loop.py; this is
// the client half of the same statement)
{
  s().clearWork();
  const c = shell();
  c.task(WIRE[0]);
  c.task({ kind: "working", on: false, run_id: "turn0001" });
  assert.equal(s().working, true);
}

// ── the resync repair clears the latch with the flag ──────────────────────────────────────
// A close lost to an SSE gap leaves the latch marked; unreleased, it would eat the next deferred
// announce. The flag half is pinned separately; this pins the latch half.
{
  s().clearWork();
  const c = shell();
  c.task(WIRE[0]);
  // …the SSE channel drops; run A's close vanishes; on reconnect the resync reads status "idle":
  s().setWorking(false);
  s().latchWorkRun("");
  c.task({ kind: "work_done", ok: true, summary: "lo aprobado corrió" });
  assert.deepEqual(c.announced, ["lo aprobado corrió"], "a stale latch ate the deferred announce");
}

// ── clearWork (call teardown) releases the latch ──────────────────────────────────────────
{
  const c = shell();
  c.task(WIRE[0]);
  s().clearWork();
  assert.equal(s().workRunId, "");
}

console.log("work-bracket: all assertions passed");
