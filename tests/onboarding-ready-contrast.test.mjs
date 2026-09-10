// The light Ready card, measured rather than eyeballed.
//
// When the finale went from dark to light it kept the ink it had. "what I know now" is 12.8px/700 —
// normal text by WCAG, so 4.5:1 — and it measured 2.71 in coral on the card's cream. The four
// sparkles measured 1.34-1.50 against 3:1 for a non-text mark. Both were left standing once because
// small coral text is house style elsewhere and the sparkles are aria-hidden decoration; they were
// shut off anyway. The GROUNDS below are real pixels read off a screenshot of the Ready step with the
// sparkles hidden, so each mark is scored against the cream it actually sits on rather than a flat
// token — the card is a radial gradient and no two of them sit on the same colour.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const css = readFileSync(join(root, "app", "globals.css"), "utf8");
const src = readFileSync(join(root, "components", "Onboarding.tsx"), "utf8");

const token = (name) => {
  const m = css.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`));
  assert.ok(m, `--${name} is not defined`);
  return rgb(m[1]);
};
const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const chan = (v) => (v / 255 <= 0.03928 ? v / 255 / 12.92 : ((v / 255 + 0.055) / 1.055) ** 2.4);
const lum = (c) => 0.2126 * chan(c[0]) + 0.7152 * chan(c[1]) + 0.0722 * chan(c[2]);
const ratio = (a, b) => {
  const [x, y] = [lum(a), lum(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
};
const over = (ink, ground, alpha) => ink.map((v, i) => alpha * v + (1 - alpha) * ground[i]);

// Measured on the live page, sparkles hidden, at 1804x933.
const HEADING_GROUND = [253, 238, 212];
const STAR_GROUNDS = [
  [253, 237, 211],
  [248, 239, 219],
  [251, 241, 227],
  [255, 255, 255],
];

/** The ink a mark is DRAWN in, resolved from the component rather than assumed: a token that exists but
 *  is used nowhere would let this whole file pass on a screen that never changed. */
const inkOf = (block, after) => {
  const m = block.slice(block.indexOf(after)).match(/color: "var\(--([a-z-]+)\)"/);
  assert.ok(m, `nothing after ${after} says what colour it is`);
  return token(m[1]);
};

const FINALE = src.slice(src.indexOf('{step === "ready" && ('));
const HEADING_INK = inkOf(FINALE.slice(0, FINALE.indexOf("what I know now")), 'display, fontWeight: 700, fontSize: "0.8rem"');
const STAR_INK = inkOf(src, "fontSize: s.size,");

test("the recap heading clears 4.5:1 for small bold text", () => {
  const got = ratio(HEADING_INK, HEADING_GROUND);
  assert.ok(got >= 4.5, `"what I know now" measures ${got.toFixed(2)}:1 on the card's cream`);
  // The shade it replaces has to FAIL, or this measurement proves nothing: --coral read 2.71 here.
  assert.ok(ratio(token("coral"), HEADING_GROUND) < 4.5, "--coral would have passed, so nothing was wrong");
});

test("it is still the coral, only lower — the heading stays the heading", () => {
  // Same channel ratios as --coral, so the hue is untouched and only the level moves. A different hue
  // would be a redesign of a card that is signed off.
  const [r, g, b] = token("coral-deep");
  const [R, G, B] = token("coral");
  assert.ok(Math.abs(g / r - G / R) < 0.01 && Math.abs(b / r - B / R) < 0.01,
    `#${[r, g, b].map((v) => v.toString(16)).join("")} is not the same hue as --coral`);
  assert.ok(lum(token("coral-deep")) < lum(token("coral")), "it has to be darker, not lighter");

  const finale = src.slice(src.indexOf('{step === "ready" && ('));
  const heading = finale.slice(finale.indexOf("what I know now") - 700, finale.indexOf("what I know now"));
  assert.match(heading, /fontSize: "0\.8rem"/, "the size is part of the design and did not move");
  assert.match(heading, /fontWeight: 700/);
  assert.match(heading, /letterSpacing: "0\.04em"/);
  assert.deepEqual(HEADING_INK, token("coral-deep"), "the heading has to be drawn in it, not just near it");
});

test("all four sparkles clear 3:1, at their own opacity, on their own ground", () => {
  const stars = [...src.matchAll(/\{ top: "[^"]+", (?:left|right): "[^"]+", size: "([^"]+)", o: ([\d.]+) \}/g)];
  assert.equal(stars.length, 4, "the finale's four static stars");
  stars.forEach(([, size, o], i) => {
    const got = ratio(over(STAR_INK, STAR_GROUNDS[i], Number(o)), STAR_GROUNDS[i]);
    assert.ok(got >= 3, `sparkle ${i + 1} (${size}, opacity ${o}) measures ${got.toFixed(2)}:1`);
  });
});

test("they stay faint decoration — no bigger, no brighter, no more of them", () => {
  const stars = [...src.matchAll(/\{ top: "([^"]+)", (left|right): "([^"]+)", size: "([^"]+)", o: [\d.]+ \}/g)];
  assert.deepEqual(
    stars.map((m) => [m[1], m[2], m[3], m[4]]),
    [
      ["12%", "right", "6%", "0.95rem"],
      ["34%", "left", "3.5%", "0.7rem"],
      ["58%", "right", "3%", "0.65rem"],
      ["80%", "left", "6%", "0.8rem"],
    ],
    "sizes and places are the design; the ink is what was wrong",
  );
  assert.ok(lum(STAR_INK) < lum(token("sun")), "a brighter sparkle would be loud, not fixed");
  assert.match(src, /<StageDressing \/>/, "still one decoration block, still aria-hidden");
});
