// The avatar's silent-failure surface — run against the REAL lib/audio.ts, lib/expressions.ts, the
// shipped Live2D model and the installed pixi:
//   node tests/avatar-contract.test.mjs
// Guards, in the order they bite: the mouth amplitude never reaches setParameterValueById as NaN or
// out of range whatever the analyser returns; every mapped expression is one its model actually
// declares AND ships, because pixi-live2d-display answers an unknown expression name with a resolved
// `false` — no throw, no log, just a face that never changes; each profile's mouth and blink agree
// with what its model declares, since writing a parameter a model does not drive moves nothing at all
// and writing one it drives itself puts two writers on one mouth; and pixi's Container.destroy leaves
// children alive unless told otherwise, which is why the canvas destroys the model itself.
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const { getMouthAmplitude } = await import("../lib/audio.ts");
const { AVATAR_FREE1, MAO_PRO } = await import("../lib/avatar-config.ts");

// ---- mouth amplitude: nothing the analyser can hand back may escape 0..1 ----
const src = (data) => ({ isSpeaking: true, getOutputByteFrequencyData: () => data });

assert.equal(getMouthAmplitude(src(new Uint8Array(0))), 0, "an inactive analyser (empty bins) is silence, not NaN");
assert.equal(getMouthAmplitude(src(undefined)), 0, "a source with no buffer at all is silence");
assert.equal(getMouthAmplitude(src(null)), 0);
assert.equal(getMouthAmplitude(src(new Uint8Array(1024))), 0, "all-zero bins are silence");
assert.equal(getMouthAmplitude(src(new Uint8Array(1024).fill(255))), 1, "a saturated spectrum clamps at 1");
assert.equal(getMouthAmplitude(src(new Uint8Array([255, 0]))), 0.5);

for (const bins of [128, 512, 1024]) {
  const v = getMouthAmplitude(src(new Uint8Array(bins).fill(120)));
  assert.ok(Number.isFinite(v), "amplitude must be finite");
  assert.ok(v >= 0 && v <= 1, `amplitude ${v} out of range`);
}
// bin count differs per transport (EL WebRTC 48 kHz vs local 24 kHz) — a whole-spectrum average
// must give the SAME answer for the same signal at any resolution.
assert.equal(
  getMouthAmplitude(src(new Uint8Array(256).fill(200))),
  getMouthAmplitude(src(new Uint8Array(1024).fill(200))),
  "amplitude must not depend on the analyser's bin count",
);

// ---- every shipped profile, against the model it is a profile OF ----
// No model is committed — they are downloaded — so a profile whose model is not installed here is
// skipped rather than failed. What is checked is the pair: names that resolve, and a profile whose
// mouth and blink agree with what the model declares about itself.
// Both places a model can live are searched: the runtime models directory the backend serves, and
// `public/models/`, which is where anyone who set it up before the switch still keeps theirs.
const MODEL_ROOTS = [
  process.env.KOTOBA_MODELS_DIR || join(homedir(), ".kotoba", "models"),
  join(root, "public/models"),
];
let checked = 0;

for (const profile of [AVATAR_FREE1, MAO_PRO]) {
  const found = MODEL_ROOTS.map((r) => join(r, profile.dir, profile.entry)).find(existsSync);
  if (!found) {
    console.log(`avatar-contract: ${profile.dir} is in none of [${MODEL_ROOTS.join(", ")}] — SKIPPED`);
    continue;
  }
  checked += 1;
  const modelPath = found;
  const model = JSON.parse(readFileSync(modelPath, "utf8"));
  const entryDir = dirname(modelPath);
  const expressions = model.FileReferences.Expressions ?? [];
  const declared = new Map(expressions.map((e) => [e.Name, e.File]));

  for (const [emotion, name] of Object.entries(profile.expressions)) {
    assert.ok(declared.has(name), `${profile.dir}: ${emotion} → ${name} is not declared in ${profile.entry}`);
    assert.ok(existsSync(join(entryDir, declared.get(name))),
      `${profile.dir}: ${emotion} → ${declared.get(name)} is missing on disk`);
  }

  // The trap this whole config exists for: writing a mouth parameter the model does not drive moves
  // nothing at all, silently — and writing one it drives ITSELF puts two writers on one mouth.
  const groups = Object.fromEntries((model.Groups ?? []).map((g) => [g.Name, g.Ids ?? []]));
  const lip = groups.LipSync ?? [];
  assert.equal(profile.mouthParam, lip.length ? lip[0] : "ParamMouthOpenY",
    `${profile.dir}: the profile's mouth disagrees with the model's LipSync group`);
  assert.equal(!!profile.autoBlink, (groups.EyeBlink ?? []).length > 0,
    `${profile.dir}: autoBlink disagrees with the model's EyeBlink group — one lid, two blinkers`);
}

// The default must be one that may be redistributed: a stranger's installer can fetch no other. It is
// a BACKEND value now — soul_config.avatar_model, seeded from the shipped soul file — because the
// build-time env var it used to be could never be changed on an image somebody else built.
const shippedDefault = readFileSync(join(root, "soul/default.md"), "utf8")
  .match(/^avatar_model:\s*(\S+)/m)?.[1];
assert.ok(shippedDefault, "soul/default.md no longer states an avatar_model — nothing seeds the choice");
assert.equal(shippedDefault.split("/")[0], MAO_PRO.dir,
  "the shipped default names a model we are not allowed to hand anybody");
assert.equal(shippedDefault, `${MAO_PRO.dir}/${MAO_PRO.entry}`,
  "the default's entry path and the profile's disagree");

console.log(`avatar-contract: ${checked} of 2 profiles were checked against a model on disk`);

// ---- pixi: app.destroy(true) does NOT destroy the stage's children ----
const { Container } = await import("@pixi/display");
const stage = new Container();
const child = new Container();
let childDestroyed = false;
child.destroy = () => {
  childDestroyed = true;
};
stage.addChild(child);
stage.destroy(undefined); // exactly the stageOptions app.destroy(true) forwards
assert.equal(childDestroyed, false, "pixi leaves children alive — the canvas must destroy the model itself");

console.log("avatar-contract.test.mjs: all assertions passed");
