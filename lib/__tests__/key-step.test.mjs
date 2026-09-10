// The three decisions of the key step, run rather than read. Text-matching the component proved
// nothing: the guard for the provider bug matched the text whichever side of the await `p` was read
// on, and the guard for "an empty field keeps what you have" matched its own inversion.
import assert from "node:assert/strict";
import { test } from "node:test";

const { attempt, writesFor, settle } = await import("../key-step.ts");

test("the provider travels inside the attempt, so switching mid-check cannot move the key", () => {
  const a = attempt("openai", "sk-real", false);
  // Everything the screen does between these two lines is exactly the hazard.
  const done = settle(a, true);
  assert.deepEqual(done, { kind: "accepted", provider: "openai", kept: false });
  assert.equal(settle(a, false, "nope").provider, "openai", "a refusal is filed under it too");
});

test("an empty field over a held key keeps it, and over nothing does nothing", () => {
  assert.deepEqual(attempt("xai", "", true), { kind: "keep", provider: "xai" });
  assert.deepEqual(attempt("xai", "   ", true), { kind: "keep", provider: "xai" });
  assert.deepEqual(attempt("xai", "", false), { kind: "nothing" });
  assert.deepEqual(settle(attempt("xai", "", true), false), {
    kind: "accepted", provider: "xai", kept: true,
  }, "keeping is not a check, so no verdict can refuse it");
  assert.equal(settle(attempt("xai", "", false), true), null);
});

test("nothing but a real check is allowed to write, and a refusal writes nothing at all", () => {
  assert.deepEqual(writesFor(attempt("openai", "", true)), [], "keeping must not touch the key");
  assert.deepEqual(writesFor(attempt("openai", "", false)), []);
  const writes = writesFor(attempt("openai", "sk-real", false));
  assert.equal(writes.length, 2);
  assert.deepEqual(writes[0], { path: "/api/setup/provider", body: { provider: "openai" } });
  assert.deepEqual(writes[1], {
    path: "/api/settings/llm-key", body: { provider: "openai", key: "sk-real" },
  });
  for (const w of writes) {
    assert.notEqual(w.body.key, "", "an empty key is a DELETE, never a way of reporting a refusal");
  }
});

test("the key is trimmed once, where the decision is taken", () => {
  assert.deepEqual(attempt("openai", "  sk-real  ", false),
                   { kind: "check", provider: "openai", key: "sk-real" });
});

test("a missing provider falls back to the default rather than writing an empty one", () => {
  assert.equal(attempt("", "sk-real", false).provider, "openai");
  assert.equal(writesFor(attempt("", "sk-real", false))[0].body.provider, "openai");
});
