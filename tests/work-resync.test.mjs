// The work flag after an events-channel gap, run against the REAL store (Node strips the types).
//
// Frames emitted while the SSE channel is down are DROPPED, not queued, and `working` has exactly one
// writer: those frames. So a job that ENDS during a gap — a backend restart is the everyday one —
// never delivers its `work_done`, and the flag stays true for the rest of the call. It is not
// cosmetic: "Focused — working…" outranks muted, mic-blocked and voice-down, so the status line then
// contradicts the badge above the avatar until the user hangs up. The repair is one-directional:
// clearing a flag the backend says is not running can only remove a lie, while SETTING one from a
// REST read a live frame may already have overtaken could add one. The run latch is released on every
// landed reply — releasing a guess is a repair too.
import assert from "node:assert/strict";

const { useKotobaStore, ownsWorkBracket } = await import("../lib/store.ts");
const s = () => useKotobaStore.getState();

// Mirrors components/CompanionExperience.tsx: handleTask's work branches + resyncWork, with the
// fetch replaced by a settable answer so the reply can be made to land late. The run-latch guard is
// mirrored too, transparent here because these frames are unmarked; the latch half is pinned apart.
function shell() {
  let workEventAt = 0;
  let now = 1000;
  return {
    tick: (ms = 1) => (now += ms),
    task(ev) {
      if (ev.kind === "work_started") {
        workEventAt = now;
        s().latchWorkRun(ev.run_id);
        s().setWorking(true);
      } else if (ev.kind === "working") {
        if (!ownsWorkBracket(s().workRunId, ev.run_id)) return;
        workEventAt = now;
        s().setWorking(ev.on);
      } else if (ev.kind === "work_done") {
        if (!ownsWorkBracket(s().workRunId, ev.run_id)) return;
        workEventAt = now;
        s().latchWorkRun("");
        s().setWorking(false);
      }
    },
    /** `deliver` runs the .then() body: askedAt is captured at call time, the answer lands later. */
    resyncWork(status) {
      const askedAt = now;
      return () => {
        const w = { status };
        if (!w || workEventAt >= askedAt) return;
        if (w.status !== "running") s().setWorking(false);
        s().latchWorkRun("");
      };
    },
  };
}

// ── the defect: a work_done lost to an SSE gap leaves the flag stuck ──────────────────────
{
  s().clearWork();
  const c = shell();
  c.task({ kind: "work_started" });
  assert.equal(s().working, true);
  // …the channel drops here. work_done and working{on:false} are emitted into no queue and vanish.
  c.tick(60000);
  assert.equal(s().working, true, "reproduced: nothing else in the frontend ever clears this");

  // …the channel comes back. onReconnect → resyncWork.
  const land = c.resyncWork("idle");
  c.tick(3);
  land();
  assert.equal(s().working, false, "the re-sync must clear a work the backend says is over");
}

// ── a work that is genuinely still running must survive the re-sync ───────────────────────
{
  s().clearWork();
  const c = shell();
  c.task({ kind: "work_started" });
  const land = c.resyncWork("running");
  c.tick(3);
  land();
  assert.equal(s().working, true, "a running job must not be cleared by its own re-sync");
}

// ── the race: a live frame that beats the REST reply home wins ────────────────────────────
{
  s().clearWork();
  const c = shell();
  const land = c.resyncWork("idle"); // asked while the backend was still idle
  c.tick(2);
  c.task({ kind: "work_started" }); // a job starts before the reply arrives
  c.tick(2);
  land();
  assert.equal(s().working, true, "a stale reply must never cancel a work that started after it was asked");
}

// ── repair only: the re-sync must never CLAIM a work, only withdraw one ───────────────────
{
  s().clearWork();
  const c = shell();
  assert.equal(s().working, false);
  const land = c.resyncWork("running");
  c.tick(3);
  land();
  assert.equal(s().working, false, "a REST read must not raise the chip on its own — frames do that");
}

// ── an unreachable backend must change nothing (the .catch path) ──────────────────────────
{
  s().clearWork();
  const c = shell();
  c.task({ kind: "work_started" });
  // no deliver() at all — the fetch rejected
  assert.equal(s().working, true, "a failed re-sync leaves the state exactly as it was");
  s().clearWork();
}

// ── a latch that outlived its run inside the gap must not eat the successor's close ────────
// SSE_STALE_MS is 40 s: long enough for "stop that, do X instead" to land entirely inside a gap. Run A
// ends there and run B starts there, both frames lost, and on reconnect the backend says "running" —
// truthfully, about B. A resync that released the latch only on "idle" left it saying A, so every
// frame B emitted was another run's and dropped: its close never cleared the chip, never stopped the
// keepalive, never announced. The latch is a guess about the channel; after a gap it is released
// whatever the answer, and B's own frames route again (an empty latch routes everything).
{
  s().clearWork();
  const c = shell();
  c.task({ kind: "work_started", run_id: "run-A" });
  assert.equal(s().workRunId, "run-A");
  // …gap: A's close and B's work_started are emitted into no queue. Reconnect → resyncWork.
  c.tick(60000);
  const land = c.resyncWork("running");
  c.tick(3);
  land();
  assert.equal(s().working, true, "a running job must not be cleared by the resync");
  c.task({ kind: "work_done", ok: true, summary: "B finished", run_id: "run-B" });
  assert.equal(s().working, false, "the successor's own close was dropped by a latch that outlived its run");
  assert.equal(s().workRunId, "");
}

// ── and the flag the whole repair exists for: the status line stops overriding everything ──
{
  const { callStatusLine } = await import("../lib/call-status.ts");
  const stuck = callStatusLine({ phase: "live", working: true, isMuted: true, micBlocked: false, voiceLost: "hearing" });
  assert.equal(stuck.text, "Focused — working…", "work outranks every other claim — pinned in voice-status.test.mjs");
  const repaired = callStatusLine({ phase: "live", working: false, isMuted: true, micBlocked: false, voiceLost: "hearing" });
  assert.match(repaired.text, /can't hear you/i, "with the flag cleared the true fact reaches the line again");
}

console.log("work-resync.test.mjs: all assertions passed");
