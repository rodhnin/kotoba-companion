// Unit tests for lib/gate-diagnostics.ts — node --test lib/__tests__/gate-diagnostics.test.mjs
// The mark exists to name one specific failure: the password was accepted and the browser dropped
// the cookie anyway, so /login redraws itself with no error at all. It must fire for exactly that,
// and stay quiet on an ordinary visit, a stale tab, or a browser with no storage to speak of.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { GATE_OK_MARK, isLoopSignal, markGateAccepted, takeGateLoopSignal } from "../gate-diagnostics.ts";

function fakeStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    size: () => map.size,
  };
}

beforeEach(() => {
  globalThis.sessionStorage = fakeStorage();
});

test("no mark → nothing to say", () => {
  assert.equal(isLoopSignal(null, 1000), false);
  assert.equal(takeGateLoopSignal(), false);
});

test("a success moments ago, and we are back on the gate → the cookie did not stick", () => {
  const now = 1_700_000_000_000;
  markGateAccepted(now);
  assert.equal(takeGateLoopSignal(now + 1_200), true);
});

test("the mark is consumed, so the SECOND visit is an ordinary one", () => {
  const now = 1_700_000_000_000;
  markGateAccepted(now);
  assert.equal(takeGateLoopSignal(now + 500), true);
  assert.equal(takeGateLoopSignal(now + 600), false);
  assert.equal(globalThis.sessionStorage.size(), 0, "nothing left behind in storage");
});

test("an old mark or a clock that ran backwards says nothing", () => {
  const now = 1_700_000_000_000;
  assert.equal(isLoopSignal(String(now), now + 61_000), false, "a whole minute later is a new visit");
  assert.equal(isLoopSignal(String(now), now - 5_000), false, "a mark from the future is not evidence");
  assert.equal(isLoopSignal("", now), false);
  assert.equal(isLoopSignal("not-a-number", now), false);
  assert.equal(isLoopSignal("0", now), false);
});

test("a browser with no sessionStorage degrades to silence, never to a crash", () => {
  globalThis.sessionStorage = {
    getItem() { throw new Error("blocked"); },
    setItem() { throw new Error("blocked"); },
    removeItem() { throw new Error("blocked"); },
  };
  assert.doesNotThrow(() => markGateAccepted());
  assert.equal(takeGateLoopSignal(), false);
});

test("the key is namespaced, so it cannot collide with the token entry", () => {
  assert.equal(GATE_OK_MARK, "kotoba_gate_ok_at");
  assert.notEqual(GATE_OK_MARK, "kotoba_token");
});
