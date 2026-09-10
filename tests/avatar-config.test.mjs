import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync("lib/avatar-config.ts", "utf8");
const exprs = readFileSync("lib/expressions.ts", "utf8");

/** The 14 emotions are a contract with core/emotions.py; the MAP is what varies per model. */
const EMOTIONS = [...exprs.matchAll(/^\s{2}\| "(\w+)"/gm)].map((m) => m[1]);

test("every emotion has a fallback chain that ends at neutral", () => {
  assert.equal(EMOTIONS.length, 14, "the emotion union changed — the backend contract moved");
  for (const e of EMOTIONS) {
    assert.match(src, new RegExp(`\\n  ${e}: \\[`), `${e} has no fallback chain`);
  }
  const block = src.slice(src.indexOf("export const FALLBACK"), src.indexOf("const WORDS"));
  assert.match(block, /\n  neutral: \[\],/);
  for (const [, emotion, chain] of block.matchAll(/\n  (\w+): \[([^\]]*)\],/g)) {
    if (!chain.trim()) continue;
    assert.ok(chain.includes('"neutral"'), `${emotion}'s chain never reaches neutral, so she can freeze`);
  }
});

test("the name guesser reads what real models actually call their faces", async () => {
  const { guessFromNames, resolveExpression } = await import("../lib/avatar-config.ts");
  const guessed = guessFromNames(["Normal", "happy_01", "angry02", "泣き", "sleep"]);
  assert.equal(guessed.neutral, "Normal");
  assert.equal(guessed.happy, "happy_01");
  assert.equal(guessed.angry, "angry02");
  assert.equal(guessed.crying, "泣き");
  assert.equal(guessed.sleepy, "sleep");
  // a model with three faces still answers for all fourteen, through the chains
  const three = { neutral: "n", happy: "h", angry: "a" };
  assert.equal(resolveExpression("excited", three), "h", "excited must reach happy");
  assert.equal(resolveExpression("determined", three), "a", "determined must reach angry");
  assert.equal(resolveExpression("sleepy", three), "n");
});

test("nothing here hardcodes the model that could not be redistributed", () => {
  // free1 may be named in its own profile and in the prose explaining why it left. What must stay
  // clean is the CONTRACT above it — the half another model reuses unchanged.
  const contract = src.slice(0, src.indexOf("export const AVATAR"))
    .split("\n").filter((l) => !/^\s*[*/]/.test(l)).join("\n");
  assert.ok(!/exp3\.json/.test(contract), "a filename belongs to one model, not to the contract's logic");
  const strip = (s) => s.split("\n").filter((l) => !/^\s*[*/]/.test(l)).join("\n");
  assert.ok(!/\bfree1\b/.test(strip(contract)), "the contract must not know which model ships");
  const shipped = strip(src.slice(src.indexOf("export const AVATAR")));
  assert.ok(/\bfree1\b/.test(shipped), "the shipped default is expected to name its own model");
});

test("a shipped profile answers every emotion, and says how its mouth is driven", async () => {
  const { AVATAR_FREE1, MAO_PRO, resolveExpression } = await import("../lib/avatar-config.ts");
  for (const profile of [AVATAR_FREE1, MAO_PRO]) {
    for (const e of EMOTIONS) {
      assert.notEqual(resolveExpression(e, profile.expressions), undefined,
        `${profile.dir} leaves ${e} with no face, not even through a fallback`);
    }
    // Measured, not assumed: free1 declares LipSync EMPTY and mao_pro declares ParamA. A profile that
    // does not say which is which lets two writers fight over one mouth.
    assert.equal(typeof profile.mouthParam, "string", `${profile.dir} does not say what drives its mouth`);
  }
  assert.notEqual(AVATAR_FREE1.mouthParam, MAO_PRO.mouthParam,
    "the two models are driven differently — that is the point");
  assert.deepEqual(MAO_PRO.hideParts, ["PartHat", "PartWandA", "PartWandB"]);
  // free1 is addressed by index because all 46 of its parts are called `PartN`; mao's have names.
  assert.ok(AVATAR_FREE1.hideParts.every((p) => typeof p === "number"));
  assert.ok(MAO_PRO.hideParts.every((p) => typeof p === "string"));
});

test("a model nobody wrote a profile for still works", async () => {
  const { adaptTo, resolveExpression } = await import("../lib/avatar-config.ts");

  // 1. Live2D's own sample: opaque names, its own LipSync and EyeBlink, a hat and a wand.
  const mao = adaptTo({
    expressionNames: ["exp_01","exp_02","exp_03","exp_04","exp_05","exp_06","exp_07","exp_08"],
    lipSyncParams: ["ParamA"], eyeBlinkParams: ["ParamEyeLOpen", "ParamEyeROpen"],
    partIds: ["PartHat", "PartWandA", "PartBody", "PartHairFront"],
  }, "mao_pro", "runtime/mao_pro.model3.json");
  assert.equal(mao.mouthParam, "ParamA", "a model that declares its mouth must drive its own");
  assert.equal(mao.autoBlink, true);
  assert.deepEqual(mao.hideParts, ["PartHat", "PartWandA"], "the body and the hair are not in the way");
  for (const e of EMOTIONS) {
    assert.notEqual(resolveExpression(e, mao.expressions), undefined, `${e} has no face`);
  }
  assert.equal(mao.expressions.neutral, "exp_01");

  // 2. A model that names its faces: the names win over the ordering.
  const named = adaptTo({ expressionNames: ["Normal", "happy_01", "怒り", "泣き"] }, "x", "x.model3.json");
  assert.equal(named.expressions.neutral, "Normal");
  assert.equal(named.expressions.angry, "怒り");
  assert.equal(named.mouthParam, "ParamMouthOpenY", "no declaration means the mouth is ours to write");
  assert.equal(named.autoBlink, false);

  // 3. A model that answers nothing at all still loads and still has a face to fall back to.
  const bare = adaptTo({}, "y", "y.model3.json");
  assert.deepEqual(bare.hideParts, []);
  assert.equal(bare.mouthParam, "ParamMouthOpenY");
  assert.equal(resolveExpression("neutral", bare.expressions), undefined, "nothing to play is not a crash");
});

test("reading a model that answers nonsense never throws", async () => {
  const { readModelFacts } = await import("../lib/avatar-config.ts");
  for (const junk of [null, undefined, 42, "model", {}, { internalModel: null },
                      { internalModel: { settings: { expressions: "not an array" } } }]) {
    assert.doesNotThrow(() => readModelFacts(junk), `threw on ${JSON.stringify(junk)}`);
  }
  // The shape below is what a LOADED model actually answers, read off one in the browser. CubismModel
  // has no getPartIds(): the ids live on `_partIds`, and on `_model.parts.ids` in the moc3 itself.
  const real = readModelFacts({
    internalModel: {
      settings: { expressions: [{ Name: "a" }, { name: "b" }],
                  json: { Groups: [{ Name: "LipSync", Ids: ["ParamA"] }] } },
      coreModel: { _partIds: ["PartHat"] },
    },
  });
  assert.deepEqual(real.expressionNames, ["a", "b"], "both Name and name spellings are read");
  assert.deepEqual(real.lipSyncParams, ["ParamA"]);
  assert.deepEqual(real.partIds, ["PartHat"]);

  const viaMoc = readModelFacts({ internalModel: { coreModel: { _model: { parts: { ids: ["PartWandA"] } } } } });
  assert.deepEqual(viaMoc.partIds, ["PartWandA"], "the moc3's own list is the other way in");
});

test("a written profile wins; only its blanks are filled from the model", async () => {
  const { completeConfig, AVATAR_FREE1 } = await import("../lib/avatar-config.ts");
  const facts = { expressionNames: ["exp_01", "exp_02"], lipSyncParams: ["ParamA"],
                  eyeBlinkParams: ["ParamEyeLOpen"], partIds: ["PartHat"] };

  // free1 has answers for all of it, and the model must not talk it out of them.
  const kept = completeConfig(AVATAR_FREE1, facts);
  assert.equal(kept.mouthParam, "ParamMouthOpenY", "free1's EMPTY LipSync group is the answer, not a blank");
  assert.deepEqual(kept.hideParts, [1, 2]);
  assert.equal(kept.expressions.neutral, AVATAR_FREE1.expressions.neutral);

  // A model nobody wrote for leaves the answers ABSENT — not empty. An empty array is an answer,
  // and filling it in overrode a profile that had said "hide nothing".
  const bare = { dir: "x", entry: "x.model3.json", scale: 5.5, anchorY: 1.88,
                 defaultEmotion: "neutral" };
  const filled = completeConfig(bare, facts);
  assert.equal(filled.mouthParam, "ParamA");
  assert.deepEqual(filled.hideParts, ["PartHat"]);
  assert.equal(filled.expressions.neutral, "exp_01");
  assert.deepEqual(filled.eyeParams, ["ParamEyeLOpen"], "a model that names its eyes gets its own blinked");
});

test("only what is actually in front of her face is hidden", async () => {
  const { adaptTo } = await import("../lib/avatar-config.ts");
  // Every id below is real: PartHoodie and PartWandA/B both ship in Live2D's own sample.
  const got = adaptTo({ partIds: [
    "PartHat", "PartHood", "PartWandA", "PartWandB", "PartMask", "PartItem02",
    "PartHoodie", "PartBody", "PartHairFront", "PartCape", "Part_Hat", "PartCapture",
  ] }, "x", "x.model3.json").hideParts;
  assert.deepEqual(got,
    ["PartHat", "PartHood", "PartWandA", "PartWandB", "PartMask", "PartItem02", "Part_Hat"],
    "a hoodie is clothing and a capture is not a cap");
});

test("no model can leave her wearing the last face she happened to have", async () => {
  const { adaptTo, resolveExpression, completeConfig } = await import("../lib/avatar-config.ts");
  // A model whose faces are named after feelings but NOT after `neutral` — ordinary, and it used to
  // leave neutral, thinking, embarrassed and sleepy with nothing. Since `settle()` asks for neutral,
  // her face would stop changing for the rest of the session.
  const m = adaptTo({ expressionNames: ["happy", "sad", "angry", "surprised"] }, "x", "x.model3.json");
  for (const e of EMOTIONS) {
    assert.notEqual(resolveExpression(e, m.expressions), undefined,
      `${e} resolves to nothing, so asking for it silently keeps the previous face`);
  }
  // A model with literally one face still answers all fourteen.
  const one = adaptTo({ expressionNames: ["only"] }, "y", "y.model3.json");
  for (const e of EMOTIONS) assert.equal(resolveExpression(e, one.expressions), "only");

  // A rig that lists its mouth parameters shape-first must still be driven by the one that OPENS.
  const shapeFirst = adaptTo({ lipSyncParams: ["ParamMouthForm", "ParamMouthOpenY"] }, "z", "z.model3.json");
  assert.equal(shapeFirst.mouthParam, "ParamMouthOpenY", "her mouth would change width and never open");
  const vowels = adaptTo({ lipSyncParams: ["ParamI", "ParamU", "ParamA"] }, "z", "z.model3.json");
  assert.equal(vowels.mouthParam, "ParamA", "a five-vowel rig must open, not purse");

  // An empty array is an ANSWER — "this model wears nothing in the way" — not a blank to fill in.
  const saysNothing = completeConfig(
    { dir: "x", entry: "x", scale: 5.5, anchorY: 1.88, hideParts: [], defaultEmotion: "neutral", expressions: {} },
    { partIds: ["PartHat"], expressionNames: ["a"] });
  assert.deepEqual(saysNothing.hideParts, [], "it hid a part the profile explicitly refused to hide");
  assert.deepEqual(saysNothing.expressions, {});
});

test("the renderer asks the model about itself — the seam the last dead-code bug lived at", () => {
  // adaptTo/readModelFacts/completeConfig were all unit-tested and called by NOTHING for weeks. The
  // functions are wired now; this pins the WIRING, which is the part a unit test cannot see. Delete
  // the call in Live2DCanvas and every other test in this repo still passes.
  const canvas = readFileSync("components/Live2DCanvas.tsx", "utf8");
  assert.match(canvas, /readModelFacts\(loaded\)/,
    "the loaded model is never asked about itself — a third model loses its mouth, lids and hidden parts");
  assert.match(canvas, /completeConfig\(\s*profile\s*,\s*facts\s*,\s*probeFraming\(/,
    "the measurement never reaches the config — every unprofiled model is framed by one model's numbers");
  // WHICH model is a runtime answer now. An env read here is inlined at `next build`, which is exactly
  // the freeze that made the model unchoosable, and it would come back looking like a harmless default.
  assert.doesNotMatch(canvas, /NEXT_PUBLIC_LIVE2D/,
    "the canvas reads the model out of the bundle again — a prebuilt image can never change it");
  assert.match(canvas, /Live2DModel\.from\(modelUrl\)/, "the model URL must arrive as a prop");
  assert.match(canvas, /new Live2DController\([^)]*,\s*config\s*\)/,
    "the completed config is built and then not handed to the controller");
  // The framing must come from the profile, not from one model's measurements.
  assert.doesNotMatch(canvas, /\b7532\b/, "a particular model's native height is back as a fallback");
  assert.match(canvas, /renderer\.height \* framing\.scale/,
    "the reframe still reads the prop, so the measured scale is computed and then thrown away");
  assert.match(canvas, /renderer\.height \* framing\.anchorY/, "same for the measured anchor");
  assert.ok(canvas.indexOf("probeFraming(") < canvas.indexOf("new Live2DController"),
    "the probe must run before the controller, which installs the hook that ends the load-time frame");
});

test("a model that numbers its parts is read by the names its author typed", async () => {
  const { adaptTo } = await import("../lib/avatar-config.ts");

  // Most free models number every part `PartN`, which says nothing about what it is. The author's own
  // names live in the DisplayInfo file; these are the real ids and names from a model with 94 parts.
  const numbered = adaptTo({
    partIds: ["Part4", "Part69", "Part78", "Part81", "Part82", "Part89", "Part90", "Part12"],
    partNames: {
      Part4: "旗子", Part69: "外套袖子2 后面", Part78: "护肩后面", Part81: "旗子",
      Part82: "帽子摆带", Part89: "帽子沿", Part90: "帽子装饰", Part12: "头发",
    },
  }, "hh", "hh.model3.json");
  assert.deepEqual(numbered.hideParts, ["Part4", "Part81", "Part82", "Part89", "Part90"],
    "the hat and the banner come off; the sleeve, the shoulder guard and the hair stay on");

  // No DisplayInfo, or one that says nothing: exactly the behaviour before names were read.
  const blind = adaptTo({ partIds: ["Part1", "Part2", "PartHat"] }, "x", "x.model3.json");
  assert.deepEqual(blind.hideParts, ["PartHat"], "a numbered part with no name is left alone");
});
