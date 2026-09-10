// The receipt card on first run. The install ALREADY succeeded before it renders, so a canvas that
// cannot draw must not take the facts down with it — and the step is walked back and forth, so a
// context left alive is a leak per visit. Pinned as text: the component has JSX and cannot import.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const card = readFileSync(join(root, "components", "FaceReceipt.tsx"), "utf8");
const canvas = readFileSync(join(root, "components", "Live2DCanvas.tsx"), "utf8");
const onboarding = readFileSync(join(root, "components", "Onboarding.tsx"), "utf8");

test("every way she can fail to draw ends at the stand-in, not at a blank frame", () => {
  assert.match(card, /onError=\{\(\) => setDrawn\("stood-in"\)\}/, "a failed model must reach the card");
  assert.match(card, /avatar\.status === "missing"\) setDrawn\("stood-in"\)/,
    "no model to draw is not a loading state that never ends");
  assert.match(card, /setTimeout\(\(\) => setDrawn\("stood-in"\), PATIENCE\)/,
    "a load that resolves neither way would leave an empty frame under a card claiming she is here");
  assert.match(canvas, /catch \(err\) \{[\s\S]*?onErrorRef\.current\?\.\(\)/,
    "a canvas that cannot be built at all has to say so, not throw through the render");
});

test("the facts are not inside the branch that draws her", () => {
  const facts = card.indexOf("unpacked and checked");
  const where = card.indexOf("on your machine — move it");
  const conditional = card.indexOf("drawn !== \"stood-in\" &&");
  assert.ok(facts > 0 && where > 0, "the receipt still has to name what landed and where");
  assert.ok(facts > conditional && where > conditional,
    "the rows sit after the canvas branch, never inside it");
  assert.match(card, /drawn === "stood-in" && \(/, "and the stand-in explains itself in words");
});

test("nothing keeps a WebGL context after the step is left", () => {
  assert.match(canvas, /app\.destroy\(true\)/, "removeView:true is what frees the context with the canvas");
  assert.match(canvas, /model\?\.destroy\(\)/, "and the model is destroyed by us, since pixi will not");
  assert.match(card, /drawn !== "stood-in" && \(\s*<Live2DCanvas/,
    "the stand-in must UNMOUNT the canvas, not cover it — a covered one still holds its context");
});

test("the card is remounted for each install, so it reads the disk again", () => {
  assert.match(onboarding, /key=\{`\$\{answers\.face\}:\$\{faceRun\}`\}/,
    "reinstalling the same model changes neither the folder nor the URL — only the run does");
  assert.match(onboarding, /setFaceRun\(\(n\) => n \+ 1\)/, "and the run is bumped where an install lands");
});

test("the arrival animation is a preference away from being off", () => {
  assert.match(card, /@media \(prefers-reduced-motion: reduce\)/,
    "the card announces itself with motion, and that is exactly what some people ask not to have");
  for (const frame of ["fr-arrive", "fr-ring", "fr-open"]) {
    assert.ok(card.includes(frame), `${frame} is gone — the reduced-motion rule now guards nothing`);
  }
});

console.log("face-receipt.test.mjs: all assertions passed");
