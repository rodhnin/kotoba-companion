// The first-run strip is also the way back IN, and the two uses want opposite rules — run against the
// real lib/setup-nav.ts (Node strips the types):
//   node tests/setup-nav.test.mjs
// Guards: a first run can only reopen answers already given, reconfiguring reaches every step, the
// step you are standing on is never a button, and the label promises the right direction.
import assert from "node:assert/strict";

const { stepReachable, stepAction } = await import("../lib/setup-nav.ts");

const STEPS = 9;

// --- first run: forward is where the unanswered questions are ---------------------------------------
for (let i = 0; i < STEPS; i++) {
  assert.equal(stepReachable(i, 4, false), i < 4, `first run, step ${i}`);
}

// --- reconfiguring: every answer already exists, so all of them are a way in ------------------------
for (let i = 0; i < STEPS; i++) {
  assert.equal(stepReachable(i, 4, true), i !== 4, `reconfiguring, step ${i}`);
}

// The one you are on is never clickable, whichever screen this is — it is where you already are.
assert.equal(stepReachable(0, 0, true), false);
assert.equal(stepReachable(0, 0, false), false);
assert.equal(stepReachable(8, 8, true), false);

// Landing on the first step with nothing behind it is exactly the reconfigure case that was stuck:
// nothing was reachable, so the button led into a wizard you had to walk end to end.
assert.equal(stepReachable(5, 0, false), false, "a first run must not skip ahead");
assert.equal(stepReachable(5, 0, true), true, "reconfiguring from step one must reach step six");

// --- the label has to say which way it goes ---------------------------------------------------------
assert.equal(stepAction(2, 5), "back");
assert.equal(stepAction(7, 5), "forward");
assert.equal(stepAction(5, 5), "forward", "never labelled Back to where you are");

console.log("setup-nav.test.mjs: all assertions passed");

// --- what a reconfigure already has -----------------------------------------------------------------
// A person who came in to change ONE answer was shown "—" against the seven they were keeping, which
// reads as an install about to be wiped. These are the fields /api/setup/status already returns.
const { settled, HELD } = await import("../lib/setup-nav.ts");
const LABEL = (c) => ({ auto: "Auto — match you", es: "Español" })[c] || "";

const full = settled(
  { needed: false, provider: "xai", model: "grok-4.6", has_voice_key: true,
    user_name: "  Ada  ", companion_name: "Yuki", language: "es" },
  LABEL,
);
assert.deepEqual(full, {
  provider: "xai", model: "grok-4.6", apiKey: HELD, elevenKey: HELD,
  userName: "Ada", companionName: "Yuki", language: "Español",
});

// A language the chips do not carry still has to print as SOMETHING: the raw code beats a dash.
assert.equal(settled({ language: "nl" }, LABEL).language, "nl");
assert.equal(settled({ language: "auto" }, LABEL).language, "Auto — match you");

// Absence is absence. A field the backend did not send must not be invented, or the recap starts
// promising answers nobody gave — the exact failure this seeding exists to end, pointed the other way.
assert.deepEqual(settled({}, LABEL), {});
assert.deepEqual(settled(null, LABEL), {});
assert.deepEqual(settled({ user_name: "   ", companion_name: "", model: "" }, LABEL), {});

// Neither key is ever described beyond existing, so a seeded key must not look like one you could read.
assert.equal(settled({ needed: true, has_voice_key: false }, LABEL).apiKey, undefined);
assert.equal(settled({ needed: false }, LABEL).apiKey, HELD);
assert.ok(!/[-_a-z]{8}/.test(HELD), "the marker must not read as a key");

// A provider the catalogue cannot serve is dropped rather than passed through: the brain step compares
// this value against exactly two ids, and a third would select neither chip and disable Continue.
assert.equal(settled({ provider: "a-provider-the-catalogue-never-had" }, LABEL).provider, undefined);
assert.equal(settled({ provider: "openai" }, LABEL).provider, "openai");

console.log("setup-nav.test.mjs: recap seeding assertions passed");
