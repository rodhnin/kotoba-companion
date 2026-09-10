// Every answer first run writes has to be READ back: a step that keeps only local state prints a
// recap the backend never agreed to. Read as text, since the component has JSX and cannot import.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(root, "components", "Onboarding.tsx"), "utf8");

const body = (fn) => {
  const start = src.indexOf(`const ${fn} = `);
  assert.notEqual(start, -1, `${fn} is gone`);
  return src.slice(start, src.indexOf("\n  };", start));
};

test("no answer is posted into the void any more", () => {
  const dropped = src
    .split("\n")
    .filter((l) => /void post\(/.test(l) && !/\.then\(/.test(l))
    .map((l) => l.trim());
  assert.deepEqual(
    dropped,
    [],
    "a write whose result nothing reads is a screen that cannot be contradicted — that is the whole " +
      "defect. Read it, and settle() what the recap may claim.",
  );
});

test("every recap row settles against what came back", () => {
  // The backend coerces a language and refuses a name that is not one, so the recap must print what
  // it ended up with, never the click.
  assert.match(body("chooseProvider"), /\.then\(\(sent\) =>/);
  assert.match(body("chooseModel"), /settle\(sent, "model", m\.id/);
  assert.match(body("submitUserName"), /settle\(sent, "name", n/);
  assert.match(body("chooseLanguage"), /settle\(sent, "language"/);
  assert.match(src.slice(src.indexOf("const submitCompanionName")), /settle\(sent, "name"/);
});

test("a refused write puts the answer back where the backend still has it", () => {
  const undo = body("undoWrite");
  assert.match(undo, /setAnswers\(was\)/, "the optimistic tick has to come off again");
  assert.match(undo, /refusalText\(sent, fallback\)/, "the backend's own reason is the best thing to say");
  assert.match(undo, /say\("wrong"/, "she reacts to it, like she does to a key that did not open");
  assert.match(undo, /clearTimers\(\)/,
    "three of these steps advance on a timer, and a notice on a screen already left is no notice");
  for (const fn of ["chooseProvider", "chooseModel", "submitUserName", "chooseLanguage"]) {
    assert.match(body(fn), /undoWrite\(was, sent,/, `${fn} never undoes a write that failed`);
  }
});

test("the failure wears the <Notice> the two key steps already wear", () => {
  const notices = src.match(/<Notice title="[^"]+">/g) ?? [];
  assert.equal(notices.length, 7, "seven things to report, seven notices — and no second visual language");
  assert.equal(
    src.split("{writeFailed && (").length - 1,
    5,
    "brain, model, your name, her name and language are the five that could fail in silence — the " +
      "face step reports the same `writeFailed` through faceReport() instead, because a block that " +
      "grows the step pushes everything under it down the page",
  );
});

test("the face step reads back what she WEARS, and holds the screen after an install", () => {
  // Her face takes a second or two to draw, so a timer would advance past the thing worth seeing.
  const done = src.slice(src.indexOf("const faceInstalled"), src.indexOf("const installDefault"));
  assert.match(done, /undoWrite\(was, sent,/, "a refused install undoes like every other write");
  assert.match(done, /await getJson\("\/api\/avatar"\)[\s\S]*?wornDir\(avatar\)/,
    "the recap must print the face the backend selected, not the one that was clicked");
  assert.ok(!/goSoon\(/.test(done),
    "a screen already left can show neither the receipt, nor the dropped files, nor her");
  assert.match(src, /accept_license: true/, "the licence endpoint refuses without it and never dials out");
  assert.match(src, /label: "Face"/, "the Ready recap names a step it never mentions otherwise");
});

test("nothing the face step reports can move what is under it", () => {
  // Measured drawn and not drawn: section 477.67, scrollHeight 800, both identical.
  const region = src.slice(src.indexOf('className="ob-report"'));
  assert.match(region.slice(0, 300), /position: "absolute"/,
    "the report sits outside the flow, so appearing cannot push the rest of the step down");
  assert.match(region.slice(0, 300), /pointerEvents: "none"/,
    "and it must never take a click from the document it floats over");
  const out = src.slice(src.indexOf("function WayOut"), src.indexOf("function Rubric"));
  assert.match(out, /position: "absolute", left: 0, right: 0, bottom: 3/,
    "the way out rides the panel's own bottom padding — a reserved row for it would stand empty, and " +
      "one added to the flow would move everything above it");
  assert.match(src, /className="ob-hush" onClick=\{onDismiss\}/,
    "it floats over the passport, so it has to be dismissable — measured: dismissing left section " +
      "524.859375, scrollHeight 933 and the Keep button at 497.0625, all unchanged");
  assert.match(src, /onDismiss=\{clearReports\}/,
    "and one dismisser clears whichever of the three reports is showing");
});

test("the card states what is on disk, not only what one install happened to report", () => {
  // The installer's count belongs to that install alone, so a later visit had no row at all.
  const card = readFileSync(join(root, "components", "FaceReceipt.tsx"), "utf8");
  assert.match(card, /const counted = landed \?\? onDisk \?\? null/,
    "the fresh receipt wins, and the disk answers whenever there is none");
  assert.match(card, /note=\{landed \? "unpacked and checked, one at a time" : "counted on your disk just now"\}/,
    "and the row may not claim it watched an unpack it did not watch");
  assert.match(src, /setOnDisk\(wornSize\(avatar\)\)/,
    "read at the door and again after an install — both are `/api/avatar` answers");
});

test("the way on from this step is the stamp on her card", () => {
  assert.ok(!/Keep this face<\/SealButton>|>\s*Keep this face\s*</.test(src),
    "the button is gone: a row of its own is height the step spends on something the document can say");
  assert.match(src, /aria-label="Keep this face and go on"/,
    "it is decorative and it is the only door forward, so it has to name itself to a screen reader");
  const stamp = src.slice(src.indexOf("function KeepStamp"), src.indexOf("function DropPlate"));
  assert.match(stamp, /position: "absolute"/, "and it costs the step no row, being ON the card");
  assert.match(src, /animation: ob-press/, "it lands like a stamp rather than fading in");
  assert.match(stamp, /className="ob-mark-well"[\s\S]*?transform: "rotate\(-9deg\)"/,
    "the resting tilt is inline on the well, so reduced motion — which kills the animation — still " +
      "leaves it stamped rather than upright or mid-fall");
  const stilled = src.match(/prefers-reduced-motion[\s\S]*?\{([\s\S]*?)\n {8}\}/)?.[1] ?? "";
  for (const half of [".ob-mark", ".ob-mark-well"]) {
    assert.match(stilled, new RegExp(`\\${half}\\b`),
      `${half} must stop under reduced motion — both halves, not just the one the hover touches`);
  }
  assert.match(stilled, /transition: none !important; animation: none !important/);
  assert.ok(!/\.ob-mark:not\(:disabled\):hover \{ animation: none/.test(src),
    "hover must not cancel the well's animation: leaving the hover restarted it, and the stamp fell " +
      "in from off the page again instead of settling back");
});

test("the step cannot be skipped, and cannot be a room without a door", () => {
  assert.ok(!/R_FACE_SKIP|Skip — she can wait/.test(src),
    "there is no skip: she leaves this step wearing the one that was picked or the default");
  const refused = src.slice(src.indexOf("const faceInstalled"), src.indexOf("const installDefault"));
  assert.match(refused, /setFaceRefused\(true\)/,
    "only a door that actually refused opens the way past — never an invitation");
  assert.match(src, /\{!answers\.face && <WayOut show=\{faceRefused\}/,
    "and only while she has no face: with one on, the stamp on her card is the door");
  const offered = src.slice(src.indexOf("const offerArchive"), src.indexOf("const keepFace"));
  assert.ok(!/setFaceRefused/.test(offered),
    "a file refused on its NAME was never sent, so it has proved nothing about either door");
});

test("the key step trusts the one verdict there is, and un-saves nothing", () => {
  // The key is already verified before it is stored, so a second opinion afterwards can only be a
  // blip — and that blip used to delete a key the backend had just accepted. What may be sent is
  // decided in lib/key-step.ts, where a test can RUN it; this reads the same rules at their source.
  const step = readFileSync(join(root, "lib", "key-step.ts"), "utf8");
  assert.ok(!/llm-test/.test(step),
    "a second round trip after a verified store can only disagree by being wrong");
  assert.ok(!/key: ""/.test(step),
    "nothing was stored to roll back on a refusal, so clearing the key can only destroy a good one");
  assert.match(step, /llm-key/, "the key still has to be sent somewhere");
  assert.match(step, /setup\/provider/, "and the provider is still re-asserted before it is");
  assert.doesNotMatch(body("keyRefused"), /post\(/, "a refusal sends nothing at all");
});
