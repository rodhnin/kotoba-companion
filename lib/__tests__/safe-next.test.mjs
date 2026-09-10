// Unit tests for lib/safe-next.ts — run with: node --test lib/__tests__/safe-next.test.mjs
// Plain .mjs so tsc ignores it; node's built-in type stripping loads the .ts module directly.
import assert from "node:assert/strict";
import { test } from "node:test";

import { safeNext } from "../safe-next.ts";

const ORIGIN = "http://localhost:3000";

// ---- the bypasses the old "startsWith('/') && !startsWith('//')" guard let through ---------------

// Each of these passed that guard AND resolved to a foreign host once handed to location.assign().
// The tab/LF/CR forms arrive decoded from searchParams when the attacker sends ?next=%2F%09%2Fevil.com.
const ESCAPES = [
  "/\\evil.com", //        URL parser folds "/\" into "//" for special schemes
  "/\\\\evil.com", //      same, doubled
  "/\t/evil.com", //       raw TAB stripped before parsing → "//evil.com"
  "/\n/evil.com", //       raw LF stripped before parsing
  "/\r/evil.com", //       raw CR stripped before parsing
  "//evil.com", //         the one the old guard did catch — must stay caught
  "/\\/evil.com",
];

for (const bad of ESCAPES) {
  test(`refuses ${JSON.stringify(bad)} — it resolves off-origin`, () => {
    // First prove the input really is an escape, so this test cannot rot into a tautology.
    assert.notEqual(new URL(bad, ORIGIN).origin, ORIGIN, "fixture no longer escapes; pick a new one");
    assert.equal(safeNext(bad, ORIGIN), "/app");
    assert.equal(safeNext(bad), "/app"); // default throwaway origin
  });
}

// ---- absolute / scheme-bearing values ------------------------------------------------------------

for (const bad of [
  "http://evil.com",
  "https://evil.com",
  "javascript:alert(1)",
  "\\\\evil.com",
  "evil.com",
  "",
  undefined,
  null,
]) {
  test(`refuses ${JSON.stringify(bad)}`, () => {
    assert.equal(safeNext(bad, ORIGIN), "/app");
  });
}

// ---- the values that must keep working -----------------------------------------------------------

test("a plain in-app path survives untouched", () => {
  assert.equal(safeNext("/app", ORIGIN), "/app");
  assert.equal(safeNext("/app/settings", ORIGIN), "/app/settings");
});

test("the query string survives — it is where the app carries state", () => {
  assert.equal(safeNext("/app?panel=files", ORIGIN), "/app?panel=files");
  assert.equal(safeNext("/app/x?a=1&b=2", ORIGIN), "/app/x?a=1&b=2");
});

test("the fragment is dropped, not a reason to refuse", () => {
  assert.equal(safeNext("/app#top", ORIGIN), "/app");
});

test("the returned path is normalised, so it cannot smuggle control characters", () => {
  const out = safeNext("/app/\u0000x", ORIGIN);
  assert.ok(out.startsWith("/app"), out);
  assert.ok(!out.includes("\u0000"), "NUL survived into the navigation target");
});

test("output is always a rooted path, never a network-path reference", () => {
  for (const v of [...ESCAPES, "/app", "/app?q=1", "http://evil.com", ""]) {
    const out = safeNext(v, ORIGIN);
    assert.ok(out.startsWith("/"), `${JSON.stringify(v)} → ${JSON.stringify(out)}`);
    assert.ok(!out.startsWith("//"), `${JSON.stringify(v)} → ${JSON.stringify(out)}`);
    assert.equal(new URL(out, ORIGIN).origin, ORIGIN);
  }
});
