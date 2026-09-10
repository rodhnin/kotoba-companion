import { test } from "node:test";
import assert from "node:assert/strict";

/**
 * The framing probe must answer the same thing whatever pose is on screen.
 *
 * Between frames the parameter array and the rendered vertices describe DIFFERENT poses, so measuring
 * the difference from the vertices charges the head with everything the last frame moved — a head box
 * the size of the whole figure, and a body where a face should be. It showed only on a model with no
 * hand-set numbers, and only when a frame landed first, which is why it came and went.
 */

const HEAD = { y0: 0.30, y1: 0.60 };
const EYE = { y0: 0.44, y1: 0.46 };
const BODY = { y0: -0.70, y1: 0.30 };
const HEIGHT = 1.0;

/** A rig with three parts, each moved by one parameter and by nothing else. */
function aRig() {
  const ids = ["ParamAngleZ", "ParamBodyAngleZ", "ParamEyeLOpen"];
  const p = {
    ids,
    count: 3,
    // Deliberately NOT the rest pose: with the two equal, putting the pose back and resetting it to
    // the defaults are the same act, and a probe that did the wrong one would look right.
    values: Float32Array.from([7, -3, 1]),
    defaultValues: Float32Array.from([0, 0, 1]),
    minimumValues: Float32Array.from([-30, -10, 0]),
    maximumValues: Float32Array.from([30, 10, 1]),
  };
  const span = (b) => [0, b.y0, 0, b.y1];
  const d = {
    count: 3,
    vertexPositions: [Float32Array.from(span(HEAD)), Float32Array.from(span(BODY)),
                      Float32Array.from(span(EYE))],
    dynamicFlags: Uint8Array.from([1, 1, 1]),
    opacities: Float32Array.from([1, 1, 1]),
    parentPartIndices: Int32Array.from([0, 1, 0]),
  };
  const raw = {
    parameters: p,
    drawables: d,
    parts: { ids: ["Head", "Body"], parentIndices: Int32Array.from([-1, -1]) },
    canvasinfo: { CanvasHeight: 1000, PixelsPerUnit: 1000 },
    update() {
      raw.renderFrom(p.values);
    },
    /** Each part is displaced sideways by its own parameter, so a box is exactly one part. */
    renderFrom(values) {
      const move = [values[0] / 100, values[1] / 100, (1 - values[2]) / 20];
      for (let j = 0; j < 3; j++) {
        const box = [HEAD, BODY, EYE][j];
        d.vertexPositions[j] = Float32Array.from([move[j], box.y0, move[j], box.y1]);
      }
    },
  };
  raw.update();
  return { raw, coreModel: { getModel: () => raw } };
}

/** What a frame leaves behind: the screen shows a tilted, swaying pose, `values` are back at rest. */
function aFrameHasRun(rig) {
  rig.raw.renderFrom(Float32Array.from([-6, 8, 0]));
}

test("the framing is the same before and after a frame has moved her", async () => {
  const { probeFraming } = await import("../lib/head-probe.ts");

  const fresh = aRig();
  const atRest = probeFraming(fresh.coreModel);
  assert.ok(atRest, "the probe could not measure a rig it should read perfectly");

  const moved = aRig();
  aFrameHasRun(moved);
  const afterAFrame = probeFraming(moved.coreModel);
  assert.ok(afterAFrame, "a pose on screen made the model unmeasurable");

  assert.equal(afterAFrame.scale.toFixed(4), atRest.scale.toFixed(4),
    "the head box grew to take in whatever else the frame had moved");
  assert.equal(afterAFrame.anchorY.toFixed(4), atRest.anchorY.toFixed(4));
});

test("the numbers are the head's own, not the whole figure's", async () => {
  const { probeFraming } = await import("../lib/head-probe.ts");
  const { coreModel } = aRig();
  const framing = probeFraming(coreModel);

  // 0.96 of the frame filled by a head 0.30 tall on a canvas 1.0 tall.
  assert.equal(framing.scale.toFixed(4), (0.96 / (HEAD.y1 - HEAD.y0)).toFixed(4));
  // The eyes land on the fixed line, wherever the head sits.
  const eyeCy = (EYE.y0 + EYE.y1) / 2;
  assert.equal(framing.anchorY.toFixed(4), (0.45 + (eyeCy * framing.scale) / HEIGHT).toFixed(4));
  // The body is a whole model unit tall; a scale that had swallowed it would be near 1.
  assert.ok(framing.scale > 2.5, `the box took in the body: scale ${framing.scale}`);
});

test("a writer that shut her lids does not cost the eye line", async () => {
  const { probeFraming } = await import("../lib/head-probe.ts");
  const shut = aRig();
  // What a live re-probe finds: a writer left `values` holding a shut lid, and the screen shows a
  // pose older still. Closing an already-shut lid moves nothing, and the anchor loses the eyes.
  shut.raw.parameters.values.set(Float32Array.from([-6, 8, 0]));
  shut.raw.renderFrom(Float32Array.from([-4, 6, 0]));
  const framing = probeFraming(shut.coreModel);
  const eyeCy = (EYE.y0 + EYE.y1) / 2;
  assert.equal(framing.anchorY.toFixed(4), (0.45 + (eyeCy * framing.scale) / HEIGHT).toFixed(4),
    "the anchor fell back to the crown because the lids were already down");
});

test("the rig's rest pose is restored, bit for bit", async () => {
  const { probeFraming } = await import("../lib/head-probe.ts");
  const { raw, coreModel } = aRig();
  const before = Float32Array.from(raw.parameters.values);
  probeFraming(coreModel);
  assert.deepEqual(Array.from(raw.parameters.values), Array.from(before),
    "the probe left the model wearing the pose it measured with");
});

test("an older core with no rest pose still measures", async () => {
  const { probeFraming } = await import("../lib/head-probe.ts");
  const { raw, coreModel } = aRig();
  delete raw.parameters.defaultValues;
  assert.ok(probeFraming(coreModel), "the probe now requires a field an older core does not have");
});

test("a rig that throws mid-measurement is still put back", async () => {
  const { probeFraming } = await import("../lib/head-probe.ts");
  const { raw, coreModel } = aRig();
  const before = Float32Array.from(raw.parameters.values);
  let calls = 0;
  const good = raw.update;
  raw.update = () => {
    if (++calls === 2) throw new Error("the rig gave up");
    good();
  };
  assert.equal(probeFraming(coreModel), null);
  assert.deepEqual(Array.from(raw.parameters.values), Array.from(before),
    "the model kept the pose the probe used to measure it");
});

test("a rig whose lids are shut at rest still gets its eye line", async () => {
  const { probeFraming } = await import("../lib/head-probe.ts");
  const good = probeFraming(aRig().coreModel);

  // Closing a lid that the rig already declares closed moves nothing, so the eye box came back empty
  // and the anchor fell to the crown — measured on a real model as nine per cent off.
  const shut = aRig();
  shut.raw.parameters.defaultValues[2] = 0;
  const framing = probeFraming(shut.coreModel);
  assert.equal(framing.anchorY.toFixed(4), good.anchorY.toFixed(4));
});

test("a rig whose head parameter rests at its own extreme is still measurable", async () => {
  const { probeFraming } = await import("../lib/head-probe.ts");
  const good = probeFraming(aRig().coreModel);

  // Driving it to the max from a rest pose that IS the max moves nothing: the head box came back
  // null and the caller fell back to one particular model's hand-set numbers.
  const tilted = aRig();
  tilted.raw.parameters.defaultValues[0] = tilted.raw.parameters.maximumValues[0];
  const framing = probeFraming(tilted.coreModel);
  assert.ok(framing, "the model became unmeasurable");
  assert.equal(framing.scale.toFixed(4), good.scale.toFixed(4));
  assert.equal(framing.anchorY.toFixed(4), good.anchorY.toFixed(4));
});

test("a lid the rig cannot open fully is measured where it stops", async () => {
  const { probeFraming } = await import("../lib/head-probe.ts");
  // Asking for a value past the rig's own ceiling would measure nothing at all, so the ask is clamped.
  const capped = aRig();
  capped.raw.parameters.maximumValues[2] = 0.9;
  capped.raw.parameters.defaultValues[2] = 0.9;
  assert.ok(probeFraming(capped.coreModel), "a capped lid made the model unmeasurable");
});
