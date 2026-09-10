// Where the keyboard is, on every control that hides its own ring.
//
// Six controls across the app set `outline: none` in an INLINE style, which beats any normal
// stylesheet rule, so the one global ring carries `!important` to win the cascade back. What that
// cannot do is show a ring on a box that is clipped away: the attach button in the transcript
// composer is the label-wrapped-picker pattern, where the real focus target is a 1x1 input with
// `clip: rect(0 0 0 0)` and the 38x38 thing a person sees is a <label>, not focusable at all.
// Measured in the browser: the outline is painted around 1px² of clipped box and nothing appears, so a
// keyboard user tabbing through the composer had no idea they were on it. The label wears the ring
// instead, still gated on :focus-visible so a mouse click changes nothing.
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const css = readFileSync(join(root, "app", "globals.css"), "utf8");

const files = [];
const walk = (dir) => {
  for (const name of readdirSync(join(root, dir))) {
    const rel = join(dir, name);
    if (statSync(join(root, rel)).isDirectory()) walk(rel);
    else if (/\.tsx?$/.test(name)) files.push(rel);
  }
};
for (const d of ["app", "components"]) walk(d);

test("the global ring still outranks an inline outline:none", () => {
  const rule = css.match(/:focus-visible\s*\{[^}]*\}/);
  assert.ok(rule, "the one global focus ring is gone");
  assert.match(rule[0], /outline:\s*3px solid var\(--grape\)\s*!important/,
    "without !important an inline outline:none wins and a keyboard user sees nothing");
  assert.match(rule[0], /outline-offset/);
});

test("a control that hides its ring is one the global rule can reach", () => {
  const hiding = files.filter((f) => /outline:\s*"none"/.test(readFileSync(join(root, f), "utf8")));
  assert.equal(hiding.length, 6, "six files hide their own ring — a seventh has to be checked by hand");
  for (const f of hiding) {
    const src = readFileSync(join(root, f), "utf8");
    assert.match(src, /<(input|textarea|select)\b/,
      `${f} hides the ring on something that is not a native form control — :focus-visible may not reach it`);
  }
});

test("the one clipped focus target puts its ring on the label a person can see", () => {
  const panel = readFileSync(join(root, "components", "TranscriptPanel.tsx"), "utf8");
  assert.match(panel, /clip: "rect\(0 0 0 0\)"/, "the label-wrapped picker is what this guards");
  assert.match(
    css,
    /label:has\(>\s*input:focus-visible\)\s*\{[^}]*outline:\s*3px solid var\(--grape\)/,
    "the outline lands on a box clipped to nothing unless the LABEL wears it",
  );
  assert.ok(
    !/label:focus-within\s*\{/.test(css),
    ":focus-within would also fire on a mouse click, which is the one thing the global rule avoids",
  );
});
