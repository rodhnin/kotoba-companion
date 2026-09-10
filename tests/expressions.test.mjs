// The emotion guard on the SSE channel — run against the REAL lib/expressions.ts:
//   node tests/expressions.test.mjs
// Guards: isEmotion accepts exactly the declared emotions and nothing the prototype chain offers —
// a frame carrying "constructor" once passed the `in` test and reached the Live2D model as a
// function, which pixi-live2d-display answers with a RANDOM expression.
import assert from "node:assert/strict";

const { isEmotion, EMOTIONS } = await import("../lib/expressions.ts");

for (const emotion of EMOTIONS) {
  assert.equal(isEmotion(emotion), true, `declared emotion ${emotion} must pass`);
}

for (const key of ["constructor", "toString", "hasOwnProperty", "valueOf", "__proto__"]) {
  assert.equal(isEmotion(key), false, `inherited prototype key "${key}" must be rejected`);
}

assert.equal(isEmotion("banana"), false, "an unknown emotion is rejected");
assert.equal(isEmotion(""), false);
assert.equal(isEmotion(undefined), false);
assert.equal(isEmotion(null), false);
assert.equal(isEmotion(3), false);
assert.equal(isEmotion({}), false, "a non-string never passes, whatever keys it has");

console.log("expressions.test.mjs: all assertions passed");
