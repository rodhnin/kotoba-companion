// The work keepalive — run with: node --test lib/__tests__/work-keepalive.test.mjs
// Plain .mjs so tsc ignores it; node's built-in type stripping loads the .ts module directly.
//
// B4/B5 of the inherited-ElevenLabs-limits audit. The ticker exists to hold an EL agent call open
// while a background job runs. In voice_mode=local there is no call, and the guard used to sit INSIDE
// the tick: the interval started anyway and woke every 8s for the whole job — 212 times at the
// 1700s ceiling — to return immediately. These drive the real timer through injected clock functions,
// so what is asserted is whether a timer was ever CREATED, which is where the defect was.
import assert from "node:assert/strict";
import { test } from "node:test";

const { WorkKeepalive, WORK_KEEPALIVE_MAX_TICKS, WORK_KEEPALIVE_TICK_MS, keepaliveHoldsACall } =
  await import("../work-keepalive.ts");

// A clock we own: `set` records the interval, `fire` runs one tick.
function fakeTimers() {
  const state = { created: 0, cleared: 0, fn: null, ms: 0 };
  return {
    state,
    timers: {
      set: (fn, ms) => {
        state.created += 1;
        state.fn = fn;
        state.ms = ms;
        return state.created;
      },
      clear: () => {
        state.cleared += 1;
        state.fn = null;
      },
    },
    fire: (n = 1) => {
      for (let i = 0; i < n && state.fn; i++) state.fn();
    },
  };
}

test("local mode creates no timer at all", () => {
  const clock = fakeTimers();
  let pings = 0;
  const ka = new WorkKeepalive(() => pings++, clock.timers);

  ka.start("local");

  assert.equal(clock.state.created, 0, "a timer was started with no call to hold");
  assert.equal(ka.running, false);
  assert.equal(pings, 0);
});

test("agent mode ticks sendUserActivity at the EL silence cadence", () => {
  const clock = fakeTimers();
  let pings = 0;
  const ka = new WorkKeepalive(() => pings++, clock.timers);

  ka.start("agent");

  assert.equal(clock.state.created, 1);
  assert.equal(clock.state.ms, WORK_KEEPALIVE_TICK_MS);
  clock.fire(3);
  assert.equal(pings, 3);
});

test("the ticker stops itself at the wall-clock ceiling under max_duration_seconds", () => {
  const clock = fakeTimers();
  let pings = 0;
  const ka = new WorkKeepalive(() => pings++, clock.timers);

  ka.start("agent");
  clock.fire(WORK_KEEPALIVE_MAX_TICKS + 5);

  assert.equal(pings, WORK_KEEPALIVE_MAX_TICKS, "it kept pinging past its own ceiling");
  assert.equal(ka.running, false);
  assert.equal(clock.state.cleared, 1);
});

test("start is idempotent, and stop is safe before any start", () => {
  const clock = fakeTimers();
  const ka = new WorkKeepalive(() => {}, clock.timers);

  ka.stop();
  ka.start("agent");
  ka.start("agent");
  assert.equal(clock.state.created, 1, "a second start opened a second interval");

  ka.stop();
  ka.stop();
  assert.equal(clock.state.cleared, 1);
});

test("a job started on the local transport stays silent even if Settings flips mid-job", () => {
  // The component passes the LATCHED mode (modeRef ← callMode ?? voiceMode). A flip in Settings
  // reaches this class only through a later start(), never through the running timer.
  const clock = fakeTimers();
  let pings = 0;
  const ka = new WorkKeepalive(() => pings++, clock.timers);

  ka.start("local");
  clock.fire(50);

  assert.equal(pings, 0);
  assert.equal(clock.state.created, 0);
});

test("a throwing ping does not kill the ticker", () => {
  const clock = fakeTimers();
  let calls = 0;
  const ka = new WorkKeepalive(() => {
    calls++;
    throw new Error("SDK connection is gone");
  }, clock.timers);

  ka.start("agent");
  clock.fire(3);

  assert.equal(calls, 3);
  assert.equal(ka.running, true);
});

test("keepaliveHoldsACall names the transport, not the setting", () => {
  assert.equal(keepaliveHoldsACall("agent"), true);
  assert.equal(keepaliveHoldsACall("local"), false);
});
