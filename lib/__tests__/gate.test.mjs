// Unit tests for lib/gate.ts — run with: node --test lib/__tests__/gate.test.mjs
// Plain .mjs so tsc ignores it; node's built-in type stripping loads the .ts module directly.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import {
  gateCookieOptions,
  gateEnabled,
  gatePassword,
  passwordMatches,
  requestIsSecure,
  signSession,
  verifySession,
} from "../gate.ts";

const VARS = ["KOTOBA_GATE_SECRET", "KOTOBA_GATE_PASSWORD", "KOTOBA_WEB_PASSWORD"];

// Every function reads process.env at call time, so a permutation is just an env swap.
function setEnv(vars) {
  for (const v of VARS) delete process.env[v];
  Object.assign(process.env, vars);
}

beforeEach(() => setEnv({}));

// ---- gate off -----------------------------------------------------------------------------------

test("no vars set → gate open, no password required", async () => {
  assert.equal(gateEnabled(), false);
  assert.equal(gatePassword(), "");
  assert.equal(await passwordMatches(""), true);
  assert.equal(await passwordMatches("anything"), true);
});

test("KOTOBA_GATE_SECRET alone does NOT enable the gate", async () => {
  setEnv({ KOTOBA_GATE_SECRET: "signing-only" });
  assert.equal(gateEnabled(), false);
  assert.equal(await passwordMatches("whatever"), true);
});

// ---- gate on: each password name alone must fully work ------------------------------------------

for (const name of ["KOTOBA_WEB_PASSWORD", "KOTOBA_GATE_PASSWORD"]) {
  test(`${name} alone: login works end to end (secret derives from it)`, async () => {
    setEnv({ [name]: "sakura" });
    assert.equal(gateEnabled(), true);
    assert.equal(gatePassword(), "sakura");
    assert.equal(await passwordMatches("sakura"), true);
    assert.equal(await passwordMatches("wrong"), false);
    assert.equal(await verifySession(await signSession()), true);
  });
}

test("KOTOBA_GATE_SECRET alongside a password: login still works", async () => {
  setEnv({ KOTOBA_GATE_SECRET: "signing-key", KOTOBA_WEB_PASSWORD: "sakura" });
  assert.equal(await passwordMatches("sakura"), true);
  assert.equal(await passwordMatches("wrong"), false);
  assert.equal(await verifySession(await signSession()), true);
});

// ---- precedence ---------------------------------------------------------------------------------

// Must stay identical to the backend's _web_password() — a fork here means login succeeds while
// every /api/* call 401s. The same order is pinned on the server side too.
test("KOTOBA_WEB_PASSWORD wins over the legacy KOTOBA_GATE_PASSWORD", async () => {
  setEnv({ KOTOBA_GATE_PASSWORD: "pw-gate", KOTOBA_WEB_PASSWORD: "pw-web" });
  assert.equal(gatePassword(), "pw-web");
  assert.equal(await passwordMatches("pw-web"), true);
  assert.equal(await passwordMatches("pw-gate"), false);
});

test("the derived signing secret follows gatePassword(), it cannot fork from it", async () => {
  setEnv({ KOTOBA_GATE_PASSWORD: "pw-gate", KOTOBA_WEB_PASSWORD: "pw-web" });
  const token = await signSession();
  setEnv({ KOTOBA_WEB_PASSWORD: "pw-web" }); // dropping the loser leaves the resolved value unchanged
  assert.equal(await verifySession(token), true);
  setEnv({ KOTOBA_GATE_PASSWORD: "pw-gate" }); // only the loser left → a different value → different key
  assert.equal(await verifySession(token), false);
});

test("KOTOBA_GATE_SECRET wins over both passwords when signing", async () => {
  setEnv({ KOTOBA_GATE_SECRET: "s1", KOTOBA_GATE_PASSWORD: "pw-gate", KOTOBA_WEB_PASSWORD: "pw-web" });
  const token = await signSession();
  setEnv({ KOTOBA_GATE_SECRET: "s1", KOTOBA_WEB_PASSWORD: "unrelated" });
  assert.equal(await verifySession(token), true); // same explicit secret → still valid
  setEnv({ KOTOBA_GATE_SECRET: "s2", KOTOBA_GATE_PASSWORD: "pw-gate", KOTOBA_WEB_PASSWORD: "pw-web" });
  assert.equal(await verifySession(token), false);
});

test("the derived secret is really the password, not a shared constant", async () => {
  setEnv({ KOTOBA_WEB_PASSWORD: "alpha" });
  const token = await signSession();
  setEnv({ KOTOBA_WEB_PASSWORD: "beta" });
  assert.equal(await verifySession(token), false);
});

// ---- session envelope ---------------------------------------------------------------------------

test("a tampered or malformed session is rejected", async () => {
  setEnv({ KOTOBA_WEB_PASSWORD: "sakura" });
  const token = await signSession();
  assert.equal(await verifySession(undefined), false);
  assert.equal(await verifySession(""), false);
  assert.equal(await verifySession("nodot"), false);
  assert.equal(await verifySession(`${token}x`), false);
});

// ---- cookie `secure`: the CONNECTION decides, never NODE_ENV ------------------------------------

// `npm start` on http://192.168.x.x:3000 is NODE_ENV=production, so the old flag marked the cookie
// secure there, the browser dropped it silently and the login looped with nothing to read. TLS is
// normally terminated at a proxy, so the socket can't answer — x-forwarded-proto can, and Next fills
// it in from the socket when no proxy sent one.
const req = (url, headers = {}) => new Request(url, { headers });

test("plain http with no proxy in front → not secure (the self-hoster's install)", () => {
  assert.equal(requestIsSecure(req("http://192.168.1.20:3000/gate")), false);
  assert.equal(requestIsSecure(req("http://localhost:3000/gate")), false);
  assert.equal(gateCookieOptions(req("http://192.168.1.20:3000/gate")).secure, false);
});

test("https, direct or behind a proxy that forwards as http → secure (production unchanged)", () => {
  assert.equal(requestIsSecure(req("https://kotoba.example.com/gate")), true);
  assert.equal(
    requestIsSecure(req("http://127.0.0.1:3000/gate", { "x-forwarded-proto": "https" })),
    true,
    "the tunnel/reverse-proxy shape: TLS outside, plain http on the hop to Next",
  );
  assert.equal(requestIsSecure(req("http://127.0.0.1:3000/gate", { "x-forwarded-scheme": "https" })), true);
});

test("a proxy that says http keeps the cookie usable", () => {
  assert.equal(requestIsSecure(req("http://127.0.0.1:3000/gate", { "x-forwarded-proto": "http" })), false);
});

// The header is client-writable, so the reading is lopsided on purpose: ANY hop claiming https wins.
// Claiming "https" over plain http only breaks the claimant's own login; claiming "http" cannot
// downgrade a real HTTPS request, because a proxy that sets or appends its own value leaves an https
// in the list.
test("a spoofed hop cannot turn a secure cookie into an open one", () => {
  assert.equal(
    requestIsSecure(req("http://127.0.0.1:3000/gate", { "x-forwarded-proto": "http, https" })),
    true,
    "client value first, the real proxy's appended after it",
  );
  assert.equal(requestIsSecure(req("http://127.0.0.1:3000/gate", { "x-forwarded-proto": "https, http" })), true);
  assert.equal(requestIsSecure(req("http://127.0.0.1:3000/gate", { "x-forwarded-proto": "  HTTPS  " })), true);
});

test("a spoofed hop over plain http only costs the spoofer their own login", () => {
  assert.equal(requestIsSecure(req("http://192.168.1.20:3000/gate", { "x-forwarded-proto": "https" })), true);
});

test("an unreadable request is treated as secure — a lost cookie loops, an open one leaks", () => {
  assert.equal(requestIsSecure({ url: "not a url", headers: new Headers() }), true);
  assert.equal(requestIsSecure(req("ws://127.0.0.1:3000/gate")), true);
});

test("only `secure` varies — httpOnly, sameSite, path and maxAge are fixed policy", () => {
  const insecure = gateCookieOptions(req("http://192.168.1.20:3000/gate"));
  const secure = gateCookieOptions(req("https://kotoba.example.com/gate"));
  for (const opts of [insecure, secure]) {
    assert.equal(opts.httpOnly, true);
    assert.equal(opts.sameSite, "lax");
    assert.equal(opts.path, "/");
    assert.equal(opts.maxAge, 12 * 60 * 60);
  }
  assert.deepEqual({ ...insecure, secure: true }, secure, "the two differ in that one flag and no other");
});
