// Unit tests for lib/head-probe.ts — run with: node --test lib/__tests__/head-probe.test.mjs
// Every trap below was measured on a real model before it was written down here.
import assert from "node:assert/strict";
import { test } from "node:test";

import { probeFraming } from "../head-probe.ts";
import { completeConfig, FIXED_FRAMING } from "../avatar-config.ts";

const PPU = 1000;

/** A Cubism core model reduced to what the probe reads. Each drawable spans `y0..y1` and its `move`
 *  answers a parameter reader with how far it travels, so a rig that keys nothing past its default is
 *  as expressible as one that keys everything. */
function coreModel({ params, drawables, parts = [], canvasHeight = 2000 }) {
  const ids = params.map((p) => p.id);
  const parameters = {
    count: ids.length,
    ids,
    values: Float32Array.from(params.map((p) => p.def)),
    minimumValues: Float32Array.from(params.map((p) => p.min)),
    maximumValues: Float32Array.from(params.map((p) => p.max)),
  };
  const rest = drawables.map((d) => Float32Array.from([-0.1, d.y0, 0.1, d.y0, 0.1, d.y1, -0.1, d.y1]));
  const live = rest.map((v) => Float32Array.from(v));
  const model = {
    parameters,
    drawables: {
      count: drawables.length,
      vertexPositions: live,
      dynamicFlags: Uint8Array.from(drawables.map((d) => (d.invisible ? 0 : 1))),
      opacities: Float32Array.from(drawables.map((d) => d.opacity ?? 1)),
      parentPartIndices: Int32Array.from(drawables.map((d) => d.part ?? -1)),
    },
    parts: {
      ids: parts.map((p) => p.id),
      parentIndices: Int32Array.from(parts.map((p) => p.parent ?? -1)),
    },
    canvasinfo: { CanvasHeight: canvasHeight, PixelsPerUnit: PPU },
    update() {
      const read = (id) => parameters.values[ids.indexOf(id)];
      drawables.forEach((d, j) => {
        const by = d.move ? d.move(read) : 0;
        // A drawable can travel with only some of its points, which is how a lash drags a cheek.
        const carried = d.moving ?? [0, 1, 2, 3];
        for (let k = 0; k < rest[j].length; k += 2) {
          live[j][k] = rest[j][k];
          live[j][k + 1] = rest[j][k + 1] + (carried.includes(k / 2) ? by : 0);
        }
      });
    },
  };
  model.update();
  return { getModel: () => model };
}

const HEAD = { id: "ParamAngleZ", def: 0, min: -30, max: 30 };
const TURN = { id: "ParamAngleX", def: 0, min: -30, max: 30 };
const LIDS = [
  { id: "ParamEyeLOpen", def: 1, min: 0, max: 2 },
  { id: "ParamEyeROpen", def: 1, min: 0, max: 2 },
];
/** A rig with no keys past its default: closing moves geometry, opening past 1 moves nothing. */
const closes = (read) => Math.max(0, 1 - read("ParamEyeLOpen")) * 0.05;
const tilts = (read) => Math.abs(read("ParamAngleZ")) * 0.001;
const turns = (read) => Math.abs(read("ParamAngleX")) * 0.001;

const near = (got, want, why) => assert.ok(Math.abs(got - want) < 1e-3, `${why} — got ${got}, want ${want}`);

test("the head is found on the tilt, not on the turn", () => {
  const got = probeFraming(coreModel({
    params: [HEAD, TURN],
    drawables: [
      { y0: 0.2, y1: 0.6, move: (r) => tilts(r) + turns(r) },
      { y0: -0.4, y1: 0.1, move: turns },
    ],
  }));
  near(got.scale, 4.8, "the turn's neck and collar are inside the head box, which overreached by 25%");
});

test("the lids are driven toward their minimum", () => {
  const got = probeFraming(coreModel({
    params: [HEAD, ...LIDS],
    drawables: [
      { y0: 0.2, y1: 0.6, move: tilts },
      { y0: 0.38, y1: 0.42, move: closes },
    ],
  }));
  near(got.anchorY, 1.41,
    "a lid of min 0 / default 1 / max 2 keys nothing past 1.1, so the max moves zero vertices");
});

test("a drawable dragged less than a fifth as far is not part of the eye", () => {
  const got = probeFraming(coreModel({
    params: [HEAD, ...LIDS],
    drawables: [
      { y0: 0.2, y1: 0.6, move: tilts },
      { y0: 0.38, y1: 0.42, move: closes },
      { y0: 0.36, y1: 0.46, move: (r) => closes(r) * 0.5 },
      { y0: 0.0, y1: 0.9, move: (r) => closes(r) * 0.1 },
    ],
  }));
  near(got.anchorY, 1.434,
    "the dragged body is in the eye box — on a real model 3.8x taller than the eye itself");
});

test("a hidden part, and everything hanging off it, is not her head", () => {
  const wearing = {
    params: [HEAD],
    parts: [{ id: "PartFace" }, { id: "PartHat" }, { id: "PartBrim", parent: 1 }],
    drawables: [
      { y0: 0.2, y1: 0.6, move: tilts, part: 0 },
      { y0: 0.55, y1: 0.9, move: tilts, part: 1 },
      { y0: 0.5, y1: 0.7, move: tilts, part: 2 },
    ],
  };
  near(probeFraming(coreModel(wearing)).scale, 2.7429,
    "nothing is being hidden — the default model's hat made its head box 26% too tall");
  near(probeFraming(coreModel(wearing), ["PartHat"]).scale, 4.8,
    "the brim hangs off the hat, and only walking the part parents reaches it");
  near(probeFraming(coreModel(wearing), [1]).scale, 4.8, "a part named by index must hide the same drawables");
});

test("the framing degrades in layers, not all at once", () => {
  const noLids = probeFraming(coreModel({
    params: [HEAD],
    drawables: [{ y0: 0.2, y1: 0.6, move: tilts }],
  }));
  assert.notEqual(noLids, null, "a model with no lids must still be framed on its head, not given up on");
  near(noLids.scale, 4.8, "head framing keeps the same scale");
  near(noLids.anchorY, 1.46, "her crown sits just under the top edge — a face, never a chest");

  const noHead = probeFraming(coreModel({
    params: LIDS,
    drawables: [{ y0: 0.38, y1: 0.42, move: closes }],
  }));
  assert.equal(noHead, null, "with nothing to measure the caller's fixed numbers must be allowed to stand");
  assert.equal(probeFraming(null), null);
  assert.equal(probeFraming({}), null);
});

test("a written pair beats the probe; the probe beats the fixed pair", () => {
  const bare = { dir: "x", entry: "x.model3.json", defaultEmotion: "neutral" };
  const measured = { scale: 3.27, anchorY: 1.56 };

  assert.equal(completeConfig(bare, {}, measured).scale, 3.27);
  assert.equal(completeConfig(bare, {}, measured).anchorY, 1.56);
  assert.equal(completeConfig(bare, {}, null).scale, FIXED_FRAMING.scale);
  assert.equal(completeConfig(bare, {}, null).anchorY, FIXED_FRAMING.anchorY);

  const written = { ...bare, scale: 5.5, anchorY: 1.88 };
  assert.equal(completeConfig(written, {}, measured).scale, 5.5,
    "a model somebody already runs must not move under them");
  assert.equal(completeConfig(written, {}, measured).anchorY, 1.88);
});

// Head and eye boxes measured on the three real models, in model units, with the pairs the rig
// recorded. `whole` is what the head-only layer answers when a model names no lids.
const MEASURED = [
  { name: "free1", hu: 1.6738, head: [0.2322, 0.6263], eye: [0.3736, 0.4209],
    pinned: { scale: 4.0782, anchorY: 1.4178 }, whole: 1.546 },
  { name: "mao_pro", hu: 1.4483, head: [0.2704, 0.5144], eye: [0.3523, 0.3919],
    pinned: { scale: 5.7005, anchorY: 1.9146 }, whole: 2.0447 },
  { name: "huohuo", hu: 1.4145, head: [0.2445, 0.6596], eye: [0.4698, 0.4928],
    pinned: { scale: 3.2705, anchorY: 1.5628 }, whole: 1.5451 },
];

test("the rule reproduces what was measured on the three real models", () => {
  // The boxes above are recorded to four decimals, so a reconstruction lands within a thousandth of
  // the pair rather than on it.
  const close = (got, want, why) =>
    assert.ok(Math.abs(got - want) / want < 1e-3, `${why} — got ${got}, want ${want}`);

  for (const m of MEASURED) {
    const spec = (drawables) => coreModel({ params: [HEAD, ...LIDS], canvasHeight: m.hu * PPU, drawables });
    const both = probeFraming(spec([
      { y0: m.head[0], y1: m.head[1], move: tilts },
      { y0: m.eye[0], y1: m.eye[1], move: closes },
    ]));
    close(both.scale, m.pinned.scale, `${m.name} scale`);
    close(both.anchorY, m.pinned.anchorY, `${m.name} anchorY`);

    const headOnly = probeFraming(spec([{ y0: m.head[0], y1: m.head[1], move: tilts }]));
    close(headOnly.anchorY, m.whole, `${m.name} head-only anchorY`);
  }
});

test("the model the shipped pair was tuned against barely moves", () => {
  const mao = MEASURED.find((m) => m.name === "mao_pro");
  const off = (got, want) => Math.abs(got - want) / want;
  assert.ok(off(mao.pinned.scale, FIXED_FRAMING.scale) < 0.04,
    "the derived scale reframes every model already installed, on the call screen and the passport both");
  assert.ok(off(mao.pinned.anchorY, FIXED_FRAMING.anchorY) < 0.04, "same for the derived anchor");
});
