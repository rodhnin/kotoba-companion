// Unit tests for lib/text-security.ts — run with: node --test lib/__tests__/text-security.test.mjs
// Plain .mjs so tsc ignores it; node's built-in type stripping loads the .ts module directly.
//
// The module is a MIRROR of api/src/kotoba/core/text_security.py, and these tests pin the mirror:
// every class the backend neutralises or removes, every fate (space vs gone), and every deliberate
// exception (ZWNJ, ZWJ, variation selectors, combining marks, the right-to-left scripts themselves).
// Every attack codepoint is written as an escape and never as itself.
import assert from "node:assert/strict";
import { test } from "node:test";

import { scrub, scrubDeep } from "../text-security.ts";

// ---- cursor moves become a SPACE: every readable byte still shows --------------------------------

test("C0, DEL and C1 are neutralised to a space, never deleted", () => {
  assert.equal(scrub("rm -rf ~\x1b[2K\x1b[1Gls -la"), "rm -rf ~ [2K [1Gls -la");
  assert.equal(scrub("a\x00b\x07c\x7fd\x85e"), "a b c d e");
});

test("LS and PS — the separators a browser honours — are moves, not line ends", () => {
  assert.equal(scrub("one\u2028two\u2029three"), "one two three");
});

test("newlines: false folds \\n; newlines: true keeps it and still kills the CR beside it", () => {
  assert.equal(scrub("a\nb\r"), "a b ");
  assert.equal(scrub("a\nb\r", { newlines: true }), "a\nb ");
});

// ---- the invisible family is REMOVED: no glyph, no handed-over word boundary ---------------------

test("the Trojan Source family, isolates and direction marks are removed outright", () => {
  assert.equal(scrub("safe\u202erekcatta\u202c"), "saferekcatta");
  assert.equal(scrub("a\u202ab\u202bc\u202dd\u2066e\u2067f\u2068g\u2069h"), "abcdefgh");
  assert.equal(scrub("x\u061cy\u200ez\u200fw"), "xyzw");
});

test("zero-width space, soft hyphen, word joiner, BOM, interlinear and tag block are removed", () => {
  assert.equal(scrub("one\u200btoken"), "onetoken");
  assert.equal(scrub("de\xadfused w\u2060j \u2061\u2062\u2063\u2064."), "defused wj .");
  assert.equal(scrub("\ufeffbom \u180emvs \ufff9hidden\ufffa\ufffb"), "bom mvs hidden");
  assert.equal(scrub("totally\u{e0041}\u{e0074}\u{e007f} safe"), "totally safe");
});

// ---- deliberately left alone: the SCRIPT, never the OVERRIDE --------------------------------------

test("Arabic and Hebrew survive codepoint-for-codepoint", () => {
  assert.equal(scrub("دمشق"), "دمشق");
  assert.equal(scrub("ירושלים"), "ירושלים");
});

test("ZWNJ and ZWJ decide letter joining and survive", () => {
  const persian = "می\u200cخواهم";
  const family = "\u{1f468}\u200d\u{1f469}\u200d\u{1f467}";
  assert.equal(scrub(persian), persian);
  assert.equal(scrub(family), family);
});

test("variation selectors and combining marks survive", () => {
  assert.equal(scrub("snow❄\ufe0f"), "snow❄\ufe0f");
  assert.equal(scrub("cafe\u0301"), "cafe\u0301");
});

// ---- the shapes -----------------------------------------------------------------------------------

test("scrub coerces like the backend: null/undefined to '', numbers to their digits", () => {
  assert.equal(scrub(null), "");
  assert.equal(scrub(undefined), "");
  assert.equal(scrub(42), "42");
});

test("scrubDeep reaches strings inside arrays and nested objects, keeps newlines, leaves keys", () => {
  const dirty = {
    label: "line one\u202e\nline two\r",
    notice: { facts: [["Server", "safe\u202erekcatta\u200b"]], quote: { text: "ok\ufeff" } },
    wait: true,
    count: 3,
    missing: null,
  };
  const clean = scrubDeep(dirty);
  assert.equal(clean.label, "line one\nline two ");
  assert.deepEqual(clean.notice.facts, [["Server", "saferekcatta"]]);
  assert.equal(clean.notice.quote.text, "ok");
  assert.equal(clean.wait, true);
  assert.equal(clean.count, 3);
  assert.equal(clean.missing, null);
  assert.equal(dirty.label.includes("\u202e"), true, "the input object is not mutated");
});
