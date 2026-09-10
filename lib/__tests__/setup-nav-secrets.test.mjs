// What the backend HOLDS, run rather than read. The screen used to answer this from its own answers
// and contradicted the backend twice: skipping the voice step printed "not yet" over a key it held,
// and switching provider printed "locked away safe" over one it never had. Both rows now read here.
//
// These import and CALL the module. Text-matching a .tsx proved nothing: ten plausible breaks of that
// component passed green, including one that made a refused key walk through as accepted.
import assert from "node:assert/strict";
import { test } from "node:test";

const { afterKey, heldFor, secretRows } = await import("../setup-nav.ts");

test("a key is held per provider, never as one flag for both", () => {
  const keys = { openai: { saved: true, env: "" }, xai: { saved: false, env: "" } };
  assert.equal(heldFor(keys, "openai"), true);
  assert.equal(
    heldFor(keys, "xai"),
    false,
    "one boolean for two providers offered to keep a key xAI never had, and walked the person past " +
      "the step that would have fixed their install",
  );
});

test("a key arriving from the environment is already answering", () => {
  assert.equal(heldFor({ openai: { saved: false, env: "OPENAI_API_KEY" } }, "openai"), true);
  assert.equal(heldFor({ openai: { saved: false, env: "" } }, "openai"), false);
});

test("a malformed or missing answer means NOT held, which is the safe direction", () => {
  for (const bad of [null, undefined, {}, [], "nope", 7, { openai: null }, { openai: "yes" },
                     { openai: { saved: "true" } }, { openai: { env: 1 } }]) {
    assert.equal(
      heldFor(bad, "openai"),
      false,
      `${JSON.stringify(bad)} must not read as held: believing a key exists that does not is the one ` +
        `direction that walks somebody past the step they needed`,
    );
  }
});

test("an unknown provider is not held", () => {
  assert.equal(heldFor({ openai: { saved: true } }, "nobody"), false);
});

test("the recap says what the backend holds, not what the visit typed", () => {
  const keys = { openai: { saved: true, env: "" }, xai: { saved: false, env: "" } };
  assert.deepEqual(secretRows(keys, "openai", true), { key: true, voice: true });
  assert.deepEqual(
    secretRows(keys, "xai", false),
    { key: false, voice: false },
    "switching provider printed `locked away safe` over a key xAI never had",
  );
  assert.deepEqual(
    secretRows(keys, "openai", false),
    { key: true, voice: false },
    "a held model key must not make the recap claim a voice",
  );
  assert.deepEqual(
    secretRows({}, null, true),
    { key: false, voice: true },
    "a first run with only ELEVENLABS_API_KEY set was told `not yet` about the voice it was about to use",
  );
});

test("no provider named falls back to the default one rather than reading as held", () => {
  assert.equal(secretRows({ openai: { saved: true } }, null, false).key, true);
  assert.equal(secretRows({ xai: { saved: true } }, null, false).key, false);
});

test("keeping an environment key does not turn it into a saved one", () => {
  const keys = { openai: { saved: false, env: "OPENAI_API_KEY" } };
  assert.deepEqual(
    afterKey(keys, "openai", true),
    { openai: { saved: false, env: "OPENAI_API_KEY" } },
    "pressing Next over an untouched field sends nothing, so it cannot move a key into the app",
  );
  assert.deepEqual(afterKey(keys, "openai", false), { openai: { saved: true, env: "OPENAI_API_KEY" } });
});

test("a key kept in the app stays kept, and the other provider is left alone", () => {
  const keys = { openai: { saved: true, env: "" }, xai: { saved: false, env: "XAI_API_KEY" } };
  const next = afterKey(keys, "openai", true);
  assert.deepEqual(next.openai, { saved: true, env: "" });
  assert.deepEqual(next.xai, { saved: false, env: "XAI_API_KEY" });
  assert.notEqual(next, keys, "the map is replaced, never mutated under React");
});

test("a missing or malformed map still yields a usable one", () => {
  assert.deepEqual(afterKey(null, "xai", false), { xai: { saved: true, env: "" } });
  assert.deepEqual(afterKey({ xai: "nope" }, "xai", true), { xai: { saved: false, env: "" } });
});
