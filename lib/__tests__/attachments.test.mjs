// One list of formats, named the same everywhere:
//   node --test lib/__tests__/attachments.test.mjs
//
// lib/attachments.ts declares the invariant in its own header — "the picker's `accept`, the panel's
// kind check and the send path must all read from here — hand-kept copies drift apart, and a format
// pickable in one place but rejected in another looks broken". The affordance that NAMES the formats
// was the hand-kept copy: the attach button's tooltip read "Attach an image, PDF, .txt or .md" while
// TEXT_EXTS had grown to eight, so `accept` offered .csv, isTextAttachment took it and
// CompanionExperience.sendFile read it as text — and the only label on the control said it was not
// supported. The rejection message beside it listed all eight, so the two disagreed on one screen.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const {
  ACCEPT_ATTR, ATTACH_HINT, REJECT_MESSAGE, TEXT_EXTS,
  isImageAttachment, isPdfAttachment, isTextAttachment,
} = await import("../attachments.ts");

const file = (name, type = "") => ({ name, type });

test("every advertised text extension is one the code actually takes", () => {
  for (const ext of TEXT_EXTS) {
    assert.equal(isTextAttachment(file(`notes.${ext}`)), true, `.${ext} is offered but not accepted`);
    assert.ok(ACCEPT_ATTR.includes(`.${ext}`), `.${ext} is missing from the picker's accept`);
  }
  assert.equal(isTextAttachment(file("a.exe")), false);
  assert.equal(isImageAttachment(file("a.png")), true);
  assert.equal(isPdfAttachment(file("a.pdf")), true);
});

test("every place that NAMES the formats names the same ones", () => {
  for (const ext of TEXT_EXTS) {
    assert.ok(REJECT_MESSAGE.includes(`.${ext}`), `the rejection does not mention .${ext}`);
    assert.ok(ATTACH_HINT.includes(`.${ext}`), `the attach hint does not mention .${ext}`);
  }
});

test("the panel's tooltip is that one string, not a copy of it", () => {
  const panel = readFileSync(join(root, "components", "TranscriptPanel.tsx"), "utf8");
  assert.match(panel, /ATTACH_HINT/, "the tooltip has to come from lib/attachments.ts");
  assert.ok(
    !/Attach an image, PDF, \.txt or \.md/.test(panel),
    "a hand-written list is the drift: it advertised two of the eight formats the picker takes",
  );
});
