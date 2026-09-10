// Which model she wears is a RUNTIME answer now.
//   node --test tests/avatar-runtime.test.mjs
// It used to be `NEXT_PUBLIC_LIVE2D_MODEL`, and `next build` inlines that into the bundle — so a
// person running a prebuilt image could not change model, and no setup screen could ever choose one.
// The backend answers instead, out of a directory that is writable at runtime. What is pinned here:
// the backend outranks the frozen env var (or the freeze is simply back), a model already sitting in
// `public/models/` still loads when the backend has nothing, an install with no model anywhere says
// so in words that are TRUE, and the shell does not mount a canvas for a model that is not there.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const { pickAvatar, legacyChoice, profileFor, MAO_PRO } = await import("../lib/avatar-config.ts");
const { MODEL_FAILED_TEXT, modelMissingText, pickShellNotice } = await import("../lib/notices.ts");

const ENV = ["NEXT_PUBLIC_LIVE2D_MODEL", "NEXT_PUBLIC_LIVE2D_ENTRY",
             "NEXT_PUBLIC_LIVE2D_SCALE", "NEXT_PUBLIC_LIVE2D_ANCHOR_Y"];
const clearEnv = () => ENV.forEach((k) => delete process.env[k]);

const ANSWER = {
  installed: [{ dir: "mao_pro", entry: "runtime/mao_pro.model3.json" }],
  selected: { dir: "mao_pro", entry: "runtime/mao_pro.model3.json" },
  models_dir: "/home/someone/.kotoba/models",
};

test("the installed model is served by the backend, under its own real path", () => {
  clearEnv();
  const got = pickAvatar(ANSWER, "");
  assert.equal(got.served, "api");
  assert.equal(got.url, "/api/models/raw/mao_pro/runtime/mao_pro.model3.json");
  // Real paths are the whole point: pixi fetches the textures, motions and expressions ITSELF,
  // relative to this URL. A URL that does not resolve them loads a model that shows nothing.
  assert.ok(got.url.endsWith("/mao_pro.model3.json"));
  assert.equal(got.config.mouthParam, MAO_PRO.mouthParam, "a known model still gets its profile");
});

test("a split-origin install keeps the backend's own origin on the model URL", () => {
  clearEnv();
  assert.equal(
    pickAvatar(ANSWER, "https://backend.example").url,
    "https://backend.example/api/models/raw/mao_pro/runtime/mao_pro.model3.json",
  );
});

test("the backend outranks a build-time value that can no longer be changed", () => {
  clearEnv();
  process.env.NEXT_PUBLIC_LIVE2D_MODEL = "free1";
  const got = pickAvatar(ANSWER, "");
  assert.equal(got.config.dir, "mao_pro", "a frozen env var overruled the model actually installed");
  assert.equal(got.served, "api");
  clearEnv();
});

test("a model already in public/models keeps working when the backend has none", () => {
  clearEnv();
  process.env.NEXT_PUBLIC_LIVE2D_MODEL = "free1";
  for (const nothing of [null, {}, { installed: [], selected: null }]) {
    const got = pickAvatar(nothing, "");
    assert.equal(got.served, "public", "the pre-runtime path was dropped from under an existing user");
    assert.equal(got.url, "/models/free1/free1.model3.json");
    assert.equal(got.config.mouthParam, "ParamMouthOpenY");
  }
  clearEnv();
});

test("with nothing installed and nothing configured there is no model at all", () => {
  clearEnv();
  assert.equal(pickAvatar(null, ""), null);
  assert.equal(pickAvatar({ installed: [], selected: null }, ""), null);
  assert.equal(legacyChoice(), null);
});

test("the entry found on disk beats the one written in a profile", () => {
  clearEnv();
  // Somebody unpacked mao_pro flat instead of under runtime/. The scan saw where it really is.
  const got = pickAvatar({ selected: { dir: "mao_pro", entry: "mao_pro.model3.json" } }, "");
  assert.equal(got.url, "/api/models/raw/mao_pro/mao_pro.model3.json");
  assert.equal(got.config.entry, "mao_pro.model3.json");
});

test("a model nobody wrote a profile for still gets a framed, drivable config", () => {
  clearEnv();
  const got = pickAvatar({ selected: { dir: "somebody_else", entry: "a/b.model3.json" } }, "");
  assert.equal(got.config.dir, "somebody_else");
  assert.equal(got.config.entry, "a/b.model3.json");
  assert.equal(got.config.defaultEmotion, "neutral");
  assert.equal(got.config.hideParts, undefined, "unset is what lets completeConfig fill it from the model");
  assert.equal(got.config.scale, undefined, "a written number here would outvote the model's own measurement");
  assert.equal(got.config.anchorY, undefined, "same: unset is what lets the probe frame this model");
});

test("a path segment with a space or a hash survives the URL", () => {
  clearEnv();
  const got = pickAvatar({ selected: { dir: "my model", entry: "run time/a#b.model3.json" } }, "");
  assert.equal(got.url, "/api/models/raw/my%20model/run%20time/a%23b.model3.json");
  assert.ok(!got.url.includes(" "), "an unescaped space truncates the request");
});

test("the framing overrides still retune whichever model was chosen", () => {
  clearEnv();
  process.env.NEXT_PUBLIC_LIVE2D_SCALE = "3.25";
  process.env.NEXT_PUBLIC_LIVE2D_ANCHOR_Y = "1.1";
  const got = profileFor("mao_pro", "runtime/mao_pro.model3.json");
  assert.equal(got.scale, 3.25);
  assert.equal(got.anchorY, 1.1);
  clearEnv();
});

test("the empty-install notice is TRUE, and names the folder to put a model in", () => {
  const text = modelMissingText("/home/someone/.kotoba/models");
  assert.match(text, /\/home\/someone\/\.kotoba\/models/, "it must name where the backend actually looks");
  assert.doesNotMatch(text, /NEXT_PUBLIC_LIVE2D/, "it sends the reader at a control that no longer decides");
  assert.doesNotMatch(text, /public\/models/, "and at a folder the backend does not read");
  assert.match(modelMissingText(""), /~\/\.kotoba\/models/, "with no answer it still names the default");
  assert.doesNotMatch(MODEL_FAILED_TEXT, /NEXT_PUBLIC_LIVE2D|public\/models/,
    "the load-failure notice still quotes the dead control");
});

test("absence and failure are different notices, and absence wins", () => {
  const base = {
    bridgeDown: false, voiceNotice: null, dismissVoice: () => {}, isLocalMode: true,
    agentStatus: "disconnected", modelFailed: false, modelMissing: false,
    modelsDir: "/m", dismissModel: () => {},
  };
  assert.equal(pickShellNotice({ ...base, modelFailed: true }).text, MODEL_FAILED_TEXT);
  assert.equal(pickShellNotice({ ...base, modelMissing: true }).text, modelMissingText("/m"));
  assert.equal(pickShellNotice({ ...base, modelMissing: true, modelFailed: true }).text,
    modelMissingText("/m"), "a model that was never there cannot have failed to load");
  assert.equal(pickShellNotice(base), null);
  assert.equal(pickShellNotice({ ...base, modelMissing: true }).fatal, true);
});

test("the shell asks at startup, and draws no canvas for a model that is not there", () => {
  // The wiring is the half a unit test cannot see — the adaptive layer in this very file shipped
  // written, tested and called by nothing for weeks.
  const shell = readFileSync("components/CompanionExperience.tsx", "utf8");
  assert.match(shell, /useAvatar\(API_URL\)/, "nothing asks which model is installed");
  assert.match(shell, /avatar\.choice\s*&&\s*\(\s*\n?\s*<Live2DCanvas/,
    "the canvas mounts unconditionally — with no model it loads `undefined` and reports a load failure");
  assert.match(shell, /modelUrl=\{avatar\.choice\.url\}/);
  assert.match(shell, /config=\{avatar\.choice\.config\}/);
  // The loader is fullscreen and swallows every click, so it must come down on absence too.
  assert.match(shell, /failed=\{modelFailed \|\| avatar\.status === "missing"\}/,
    "with no model installed the loading overlay spins forever over a dead page");
});

test("pixi's own resolver reaches the model's siblings, and carries no token to them", async () => {
  // Asked of the installed library rather than assumed: `resolveURL` is `url.resolve(entry, path)`,
  // so the entry URL has to be a real path or the textures resolve somewhere that does not exist and
  // the model loads showing nothing. The same call drops the query, which is why the credential for
  // those fetches has to be a cookie and why the token never reaches a texture URL.
  const { url } = await import("@pixi/utils");
  clearEnv();
  const entry = `${pickAvatar(ANSWER, "").url}?token=SECRET`;
  for (const [ref, expected] of [
    ["mao_pro.moc3", "/api/models/raw/mao_pro/runtime/mao_pro.moc3"],
    ["mao_pro.4096/texture_00.png", "/api/models/raw/mao_pro/runtime/mao_pro.4096/texture_00.png"],
    ["expressions/exp_01.exp3.json", "/api/models/raw/mao_pro/runtime/expressions/exp_01.exp3.json"],
  ]) {
    const got = url.resolve(entry, ref);
    assert.equal(got, expected);
    assert.ok(!got.includes("SECRET"), "the token followed a texture request into the browser's cache");
  }
});

test("only the backend-served URL is given a token", () => {
  const src = readFileSync("lib/avatar.ts", "utf8");
  assert.match(src, /served === "api"\s*\?\s*\{ \.\.\.choice, url: tokenUrl\(choice\.url\) \}/,
    "either the first model fetch carries no credential, or a static path gets one it has no use for");
  assert.match(src, /ensureAuthToken\(\)/, "a fresh tab holds no token yet and the ask would 401");
});
