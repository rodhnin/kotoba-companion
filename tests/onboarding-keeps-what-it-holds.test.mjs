// Walking a key step a second time must not cost the key. Skip wrote "" over the marker that says the
// backend HAS one, so the recap read back `not yet` about a key still saved — and the field promised
// "Enter keeps it" while the only button that could do it stayed disabled.
//
// These read the component AS TEXT, which JSX forces and which proves nothing about behaviour: the
// model key step's decisions moved to lib/key-step.ts for exactly that reason, and what is left here
// is the wiring — that the screen asks the module instead of deciding again on its own.
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

test("skipping the voice step never writes to the key it is skipping", () => {
  const skip = body("skipVoice").replace(/^\s*\/\/.*$/gm, "");
  assert.doesNotMatch(
    skip,
    /setAnswers/,
    "skip declines a NEW key; it makes no claim about the one already saved, and the recap reads " +
      "that same field to decide between `••••••` and `not yet`",
  );
});

test("the voice step can be walked past when a key is already held", () => {
  const fnBody = body("submitVoiceKey");
  assert.doesNotMatch(
    fnBody,
    /if \(!k \|\| busy\) return;/,
    "submitVoiceKey still refuses an empty field outright, so a saved key cannot be kept",
  );
  assert.match(fnBody, /heldVoice\) voiceAccepted\(true\)/, "it must keep what is held");
});

test("the model key step decides through the module rather than deciding again here", () => {
  const fnBody = body("submitApiKey");
  assert.match(fnBody, /keyAttempt\(/, "what the button means is decided in one place, before any send");
  assert.match(fnBody, /a\.kind === "keep"/, "an empty field over a held key still means keep it");
  assert.match(fnBody, /sendAll\(writesFor\(a\)\)/,
    "what may be sent comes from the attempt, so a refusal cannot invent a write of its own");
  assert.match(fnBody, /settleKey\(a, sent\.ok/, "the verdict is filed against the attempt, not the screen");
  const afterTheAwait = fnBody.slice(fnBody.indexOf(".then("));
  assert.doesNotMatch(afterTheAwait, /answersRef/,
    "reading the provider again after the await is the bug this shape exists to prevent");
});

test("the button that keeps a held key is not disabled", () => {
  for (const draft of ["keyDraft", "elDraft"]) {
    const held = draft === "keyDraft" ? "heldKey" : "heldVoice";
    assert.match(
      src,
      new RegExp(`disabled=\\{busy \\|\\| \\(!${draft}\\.trim\\(\\) && !${held}\\)\\}`),
      `an empty field over a saved key left the only way forward disabled, which is the promise ` +
        `"Enter keeps it" with nothing behind it`,
    );
  }
});

test("a key saved during this visit is not still described as missing", () => {
  assert.match(
    body("keyAccepted"),
    /setKeys\(/,
    "`keys` is read once on the way in; without this the field goes on offering to receive a key it " +
      "has just been given",
  );
});

test("the screen asks the shared question rather than answering it again", () => {
  assert.match(
    src,
    /const heldKey = heldFor\(keys, answers\.provider/,
    "`heldFor` lives in lib/setup-nav.ts so a test can RUN it — see lib/__tests__/setup-nav-secrets",
  );
  assert.match(src, /const heldVoice = voiceHeld \|\| /, "the voice answer comes from the status too");
  assert.match(
    src,
    /knownRows\(answers, secretRows\(/,
    "the recap must read the same source the steps read, or it contradicts them",
  );
});

test("the provider a key is filed under comes from the attempt", () => {
  // The behaviour itself is proven by running the module; this only pins that the screen uses it.
  assert.match(body("submitApiKey"), /keyAccepted\(done\.kept, done\.provider/,
    "pressing Back mid-check must not move a verified key to the provider now on screen");
});
