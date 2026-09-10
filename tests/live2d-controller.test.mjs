/**
 * The controller drives a model it is not allowed to know the name of, and it must write every
 * parameter INSIDE the frame. Cubism restores all parameters at the top of its own update, so a value
 * written between frames survives one partial frame and is then wiped — measured on a loaded model:
 * 0.9 written from outside came back 0.562 on that frame and 0 on every frame after.
 *
 * The controller imports through the `@/` alias, which node cannot resolve, so it is loaded from a
 * copy with the alias rewritten to this checkout's own path. The copy lives in the OS temp dir.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, rmSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

// A name built from the pid is a name anybody sharing this machine can predict and get in front of.
const scratch = mkdtempSync(join(tmpdir(), "kotoba-live2d-"));
const copy = join(scratch, "controller.mts");
writeFileSync(copy, readFileSync("lib/live2d-controller.ts", "utf8").replace(
  /@\/lib\/(\w[\w-]*)/g, (_, mod) => pathToFileURL(resolve("lib", `${mod}.ts`)).href));
const { Live2DController } = await import(pathToFileURL(copy).href);
process.on("exit", () => rmSync(scratch, { force: true, recursive: true }));

/** A model that records what is done to it, and in which order. It emits `afterMotionUpdate` from
 *  inside its own update, exactly where the engine does — between the motions and the physics. */
function fakeModel() {
  const log = [];
  const listeners = {};
  const core = {
    setParameterValueById: (id, v) => log.push({ param: id, value: v }),
    setPartOpacityByIndex: (i) => log.push({ partIndex: i }),
    setPartOpacityById: (id) => log.push({ partId: id }),
  };
  const model = {
    expression: (e) => log.push({ expression: e }),
    autoInteract: true,
    x: 0, y: 0,
    anchor: { set: () => {} },
    scale: { set: () => {} },
    internalModel: {
      coreModel: core,
      originalHeight: 1000,
      eyeBlink: { itsOwnBlinker: true },
      motionManager: { groups: { idle: "Idle" }, stopAllMotions: () => log.push({ motions: "stopped" }) },
      on: (event, fn) => { listeners[event] = fn; },
      update: () => {
        listeners.afterMotionUpdate?.();       // the seam the engine emits, before physics
        log.push({ engine: "physics" });       // stands in for the rest of the engine's frame
      },
    },
  };
  return { model, log, frame: () => model.internalModel.update() };
}

const PROFILE = {
  dir: "x", entry: "x.model3.json", scale: 5.5, anchorY: 1.88,
  hideParts: [], defaultEmotion: "neutral", expressions: { neutral: "n" },
  mouthParam: "ParamA", eyeParams: ["EyeL", "EyeR"],
};

test("the mouth is written inside the frame, after the engine — never between frames", () => {
  const { model, log, frame } = fakeModel();
  const c = new Live2DController(model, PROFILE);

  log.length = 0;
  c.setLipSync(0.8);
  assert.equal(log.length, 0, "setLipSync wrote straight to the model — the engine will wipe that");

  frame();
  const mouth = log.filter((e) => e.param === "ParamA");
  assert.equal(mouth.length, 1, "the frame must write the mouth exactly once");
  assert.equal(mouth[0].value, 0.8, "and write the amplitude it was given");
  assert.ok(log.indexOf(mouth[0]) > log.findIndex((e) => e.engine === "physics"),
    "the mouth must be written LAST, or the expression that just applied can cancel it");
});

test("the head is written before physics runs, or the hair never answers it", () => {
  const { model, log, frame } = fakeModel();
  const c = new Live2DController(model, PROFILE);
  c.settle();
  log.length = 0;
  frame();

  const physics = log.findIndex((e) => e.engine === "physics");
  const head = log.findIndex((e) => e.param === "ParamAngleX");
  const face = log.findIndex((e) => e.param === "ParamA");
  assert.ok(head >= 0, "the head was not written at all");
  // Every physics rig takes the angle parameters as its input. Writing the head after physics has
  // run leaves the rig reading a head at rest: measured on the real thing, the head swung 7.1° and
  // all 32 of free1's physics outputs stayed at exactly 0.00.
  assert.ok(head < physics, "physics ran before the head moved — rigid hair on a moving head");
  assert.ok(face > physics, "the face must still come last");

  // ParamBreath is the ENGINE's write, not ours. Measured inside physics.evaluate over a full cycle
  // with no write of our own: 0.0000 → 0.2500, range 0.2500, period 3.23s, on mao_pro and free1
  // alike. Read from OUTSIDE the frame the same parameter is a flat 0.00 — the reading that once
  // made it look dead and had a breath of ours added here.
  assert.ok(!log.some((e) => e.param === "ParamBreath"), "two breaths on one parameter");
});

test("the mouth keeps being written every frame, so the last amplitude is not left hanging", () => {
  const { model, log, frame } = fakeModel();
  const c = new Live2DController(model, PROFILE);
  c.setLipSync(0.5);
  frame();
  log.length = 0;
  frame();
  assert.ok(log.some((e) => e.param === "ParamA" && e.value === 0.5),
    "a frame with no new amplitude must still hold the mouth where it was");
});

test("the mouth parameter comes from the profile, not from the model we happen to ship", () => {
  const { model, log, frame } = fakeModel();
  new Live2DController(model, { ...PROFILE, mouthParam: "ParamMouthOpenY" });
  log.length = 0;
  frame();
  assert.ok(log.some((e) => e.param === "ParamMouthOpenY"));
  assert.ok(!log.some((e) => e.param === "ParamA"), "it wrote another model's mouth parameter");
});

test("a part is hidden by id when it has one, by index when it does not", () => {
  const byName = fakeModel();
  new Live2DController(byName.model, { ...PROFILE, hideParts: ["PartHat", "PartWandA"] });
  byName.log.length = 0;
  byName.frame();
  assert.deepEqual(byName.log.filter((e) => e.partId).map((e) => e.partId), ["PartHat", "PartWandA"]);

  const byIndex = fakeModel();
  new Live2DController(byIndex.model, { ...PROFILE, hideParts: [1, 2] });
  byIndex.log.length = 0;
  byIndex.frame();
  assert.deepEqual(byIndex.log.filter((e) => "partIndex" in e).map((e) => e.partIndex), [1, 2]);
});

test("the model's own animators are silenced — she is driven from here, by one writer", () => {
  const { model, log } = fakeModel();
  new Live2DController(model, { ...PROFILE, autoBlink: true });

  assert.ok(log.some((e) => e.motions === "stopped"), "an auto-played idle motion animates her too");
  const idle = model.internalModel.motionManager.groups.idle;
  assert.notEqual(idle, "Idle", "the engine starts a new idle the moment none is playing");
  // Live2D's own sample files its six UNGROUPED motions under the name "", so aiming idle at the
  // empty string plays all six instead of none. Measured, after it happened.
  assert.notEqual(idle, "", 'the empty string is a real group name in Live2D\'s own sample');
  assert.equal(model.internalModel.eyeBlink, undefined,
    "the engine blinks AFTER expressions, so its blinker wipes any eyelid an expression set");
});

test("a model with no blinker of its own keeps whatever it has, and every model still blinks", () => {
  const keep = fakeModel();
  new Live2DController(keep.model, { ...PROFILE, autoBlink: false });
  assert.notEqual(keep.model.internalModel.eyeBlink, undefined,
    "nothing to suppress — the model never declared a blink group");

  for (const autoBlink of [true, false]) {
    const { model, log, frame } = fakeModel();
    const c = new Live2DController(model, { ...PROFILE, autoBlink });
    c.settle();
    log.length = 0;
    for (let i = 0; i < 400; i++) frame();
    assert.ok(log.some((e) => e.param === "EyeL"),
      `autoBlink=${autoBlink}: nobody blinks her at all`);
  }
});

test("the eye parameters are the model's own, not Cubism's usual pair", () => {
  const { model, log, frame } = fakeModel();
  const c = new Live2DController(model, { ...PROFILE, eyeParams: ["ParamEyeOpen"] });
  c.setAsleep(); // asleep holds the lids closed every frame, so one frame is enough
  log.length = 0;
  frame();
  assert.ok(log.some((e) => e.param === "ParamEyeOpen"));
  assert.ok(!log.some((e) => e.param === "ParamEyeLOpen"), "it wrote a lid this model does not have");
});

const WITH_DEFAULT = {
  ...PROFILE,
  defaultEmotion: "happy",
  expressions: { neutral: "n", happy: "h", sleepy: "s" },
};

test("she settles into the profile's defaultEmotion, not a face hardcoded here", () => {
  const { model, log } = fakeModel();
  const c = new Live2DController(model, WITH_DEFAULT);
  log.length = 0;
  c.settle();
  assert.equal(c.emotion, "happy", "somebody set this on their own model and saw nothing happen");
  assert.deepEqual(log.filter((e) => e.expression).map((e) => e.expression), ["h"]);
});

test("asleep is the PHASE talking, and it outranks the model's default face", () => {
  const { model } = fakeModel();
  const c = new Live2DController(model, WITH_DEFAULT);
  // The drowsy face belongs to being offline, not to the model — so defaultEmotion must not reach it,
  // or a profile naming a cheerful default would have her grinning while she is asleep.
  assert.equal(c.emotion, "sleepy");
  c.settle();
  assert.equal(c.emotion, "happy");
  c.setAsleep();
  assert.equal(c.emotion, "sleepy");
});

test("a profile that names no default face still settles into a face", () => {
  const { model } = fakeModel();
  const c = new Live2DController(model, { ...PROFILE, defaultEmotion: undefined });
  c.settle();
  assert.equal(c.emotion, "neutral");
});
