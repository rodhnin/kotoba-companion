// Unit tests for the token side of lib/api.ts — run with: node --test lib/__tests__/api-token.test.mjs
// Plain .mjs so tsc ignores it; node's built-in type stripping loads the .ts module directly.
//
// The gate password doubles as the backend token and is cached in sessionStorage. Clearing it has to
// clear BOTH: setAuthToken("") used to leave the stored entry behind, so the next authToken() read the
// old token straight back and "sign out" signed nothing out.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

// A minimal sessionStorage — the module reads it lazily, so defining it before import is enough.
const store = new Map();
globalThis.sessionStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

const { authToken, setAuthToken, gateIsOn, signOut, tokenUrl } = await import("../api.ts");
const { GATE_OK_MARK, markGateAccepted, takeGateLoopSignal } = await import("../gate-diagnostics.ts");

beforeEach(() => {
  store.clear();
  setAuthToken("");
});

test("a token round-trips through storage", () => {
  setAuthToken("sakura");
  assert.equal(authToken(), "sakura");
  assert.equal(store.get("kotoba_token"), "sakura");
});

test("clearing the token also removes it from storage", () => {
  setAuthToken("sakura");
  setAuthToken("");
  assert.equal(store.has("kotoba_token"), false, "a stale entry would be read back immediately");
  assert.equal(authToken(), "", "so the very next read must be empty");
});

test("gateIsOn follows the token, which is what the sign-out row keys on", () => {
  assert.equal(gateIsOn(), false, "no token → gate open, nothing to sign out of");
  setAuthToken("sakura");
  assert.equal(gateIsOn(), true);
  setAuthToken("");
  assert.equal(gateIsOn(), false, "after signing out the row must disappear");
});

test("tokenUrl appends the token only while one is held", () => {
  assert.equal(tokenUrl("/api/events/x"), "/api/events/x");
  setAuthToken("a b&c");
  assert.equal(tokenUrl("/api/events/x"), "/api/events/x?token=a%20b%26c");
  assert.equal(tokenUrl("/api/events/x?y=1"), "/api/events/x?y=1&token=a%20b%26c");
  setAuthToken("");
  assert.equal(tokenUrl("/api/events/x"), "/api/events/x", "no token after sign-out → clean URL");
});

test("signing out clears the login diagnostic too, and not only the token", async () => {
  // The login screen records the instant a password was accepted and reads that mark when it next
  // mounts: coming back to /login seconds after a success means the browser dropped the cookie, and it
  // says so — "your browser just didn't keep the session", pointing at cookie settings and the
  // self-hosting docs. Sign out lands on /login by design, within the same 60s window, and cleared the
  // token beside the mark while leaving the mark itself. So a deliberate sign-out was reported as a
  // broken browser and sent the person off debugging one. Same shape as setAuthToken's own bug above:
  // two entries in one store, one of them cleared.
  markGateAccepted(Date.now());
  setAuthToken("sakura");
  globalThis.fetch = async () => ({ ok: true });
  await signOut();
  assert.equal(store.has("kotoba_token"), false, "the token is the half that already worked");
  assert.equal(store.has(GATE_OK_MARK), false, "the mark outlived the session it describes");
  assert.equal(takeGateLoopSignal(), false, "so /login accused the browser of dropping the cookie");
});
