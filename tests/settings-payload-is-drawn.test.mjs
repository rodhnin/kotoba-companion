// Every block the panel asks /api/settings for has to reach the screen. The panel has JSX, so nothing
// here can import it; it is read as text.
//
// `brain` — {companion_model, work_model} — was requested, typed, and read exactly zero times, while
// `runtime`, which carries the same two values live-settable, was read 29 times. One of its fields
// was the string "(same as companion)", a label baked into an API payload: it belonged to an earlier
// panel and the rebuild moved on without it. Dead weight on the wire reads as a control somebody
// forgot to draw. A block counts as drawn if anything in the file accesses it off the payload — `s.x`,
// `s?.x` or `d.x` — since a child component is handed the value read here first.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(root, "components", "panels", "SettingsPanel.tsx"), "utf8");

const start = src.indexOf("type Settings = {");
assert.notEqual(start, -1, "the Settings type moved — this test is looking at nothing");
const end = src.indexOf("\n};", start);
const block = src.slice(start, end);
const rest = src.slice(0, start) + src.slice(end);

const keys = [...block.matchAll(/^ {2}(\w+):/gm)].map((m) => m[1]);

test("the payload's shape is still readable here", () => {
  assert.ok(keys.length >= 10, `only ${keys.length} keys parsed out of the Settings type`);
  assert.ok(keys.includes("runtime") && keys.includes("personality"), "the two the panel is built on");
});

for (const key of keys) {
  test(`the panel draws the "${key}" block it asks for`, () => {
    assert.match(rest, new RegExp(`\\b[sd]\\??\\.${key}\\b`),
      `Settings.${key} is requested and typed but never read — draw it or stop asking the backend for it`);
  });
}
