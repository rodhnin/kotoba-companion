// Unit tests for lib/subagent-row.ts — run with: node --test lib/__tests__/subagent-row.test.mjs
// Two things are pinned here: the row of helper heads can no longer be wider than the viewport (the
// defect — heads and their dismiss buttons walked off the right edge), and a process bubble can no
// longer hang off either margin (it was anchored at its chibi's left edge at up to 330px wide, so a
// chibi within 330px of the right edge pushed it out of the window).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BUBBLE_MAX_WIDTH,
  CHIBI_GAP,
  CHIBI_SIZE,
  ROW_INSET_CSS,
  bubblePlacement,
  overflowLabel,
  rowCapacity,
  rowInset,
  splitRow,
} from "../subagent-row.ts";

const WIDTHS = [360, 390, 768, 1024, 1280, 1366, 1440, 1600, 1920, 2560, 3440];

/** Width in px of a row of n 60px heads separated by 14px gaps — the old layout, unbounded. */
const rowWidth = (n) => (n <= 0 ? 0 : n * CHIBI_SIZE + (n - 1) * CHIBI_GAP);

// ---- the defect, as arithmetic -------------------------------------------------------------------

test("without a cap the row outgrows the viewport — the reported failure", () => {
  const vw = 1920;
  const usable = vw - 2 * rowInset(vw);
  assert.ok(rowWidth(25) <= usable, "25 helpers were the last that fit at 1920 — matches the report");
  assert.ok(rowWidth(26) > usable, "the 26th head at 1920 starts leaving the window");
  assert.ok(rowWidth(40) > usable + 1000, "at the store's 40-step ceiling the tail is far off-screen");
});

test("the CSS the row renders and the number the maths uses are the same inset", () => {
  assert.equal(ROW_INSET_CSS, "clamp(12px, 2vw, 28px)");
  assert.equal(rowInset(1920), 28, "2vw is clamped at 28");
  assert.equal(rowInset(1000), 20, "2vw between the bounds is used as-is");
  assert.equal(rowInset(360), 12, "2vw below the floor is clamped at 12");
});

// ---- the cap -------------------------------------------------------------------------------------

test("what is drawn always fits, at every width and every helper count", () => {
  for (const vw of WIDTHS) {
    const usable = vw - 2 * rowInset(vw);
    for (let total = 1; total <= 60; total++) {
      const { visible, overflow } = splitRow(total, vw);
      const slots = visible + (overflow > 0 ? 1 : 0);
      assert.ok(visible >= 1, `${total} helpers at ${vw}px must still show a head`);
      assert.equal(visible + overflow, total, "every helper is either drawn or counted");
      assert.ok(
        rowWidth(slots) <= usable,
        `${total} helpers at ${vw}px drew ${slots} slots = ${rowWidth(slots)}px into ${usable}px`,
      );
    }
  }
});

test("the counter appears only when it has to, and takes a slot of its own", () => {
  const vw = 1920;
  const cap = rowCapacity(vw);
  assert.equal(cap, 25, "measured: 25 heads fit at 1920");
  assert.deepEqual(splitRow(cap, vw), { visible: cap, overflow: 0 }, "exactly full → no counter");
  assert.deepEqual(splitRow(cap + 1, vw), { visible: cap - 1, overflow: 2 }, "one over → last head joins the count");
  assert.deepEqual(splitRow(200, vw), { visible: cap - 1, overflow: 200 - (cap - 1) });
});

test("the cap is responsive, not one constant", () => {
  assert.equal(rowCapacity(1440), 18);
  assert.equal(rowCapacity(1280), 16);
  assert.equal(rowCapacity(390), 5);
  let previous = 0;
  for (const vw of WIDTHS) {
    const cap = rowCapacity(vw);
    assert.ok(cap >= previous, "a wider window never shows fewer heads");
    previous = cap;
  }
});

test("an unmeasured viewport caps nothing — the first frame draws what it always drew", () => {
  assert.deepEqual(splitRow(40, 0), { visible: 40, overflow: 0 });
  assert.deepEqual(splitRow(0, 1920), { visible: 0, overflow: 0 });
});

test("the counter face stays at three characters so it reads at head size", () => {
  assert.equal(overflowLabel(1), "+1");
  assert.equal(overflowLabel(99), "+99");
  assert.equal(overflowLabel(100), "99+");
  assert.equal(overflowLabel(4000), "99+");
  for (const n of [1, 9, 10, 99, 100, 5000]) assert.ok(overflowLabel(n).length <= 3);
});

// ---- the process bubble --------------------------------------------------------------------------

test("the old left-anchored bubble left the viewport near the right edge", () => {
  const vw = 1440;
  const inset = rowInset(vw);
  const index = rowCapacity(vw) - 1; // the rightmost head that fits
  const chibiLeft = inset + index * (CHIBI_SIZE + CHIBI_GAP);
  assert.ok(chibiLeft + BUBBLE_MAX_WIDTH > vw, "left:0 + 330px wide ran past the window edge");
});

test("a bubble stays inside both margins wherever its chibi sits", () => {
  for (const vw of WIDTHS) {
    const inset = rowInset(vw);
    for (let index = 0; index < rowCapacity(vw); index++) {
      const chibiLeft = inset + index * (CHIBI_SIZE + CHIBI_GAP);
      const { left, width, tailX } = bubblePlacement(index, vw);
      const viewportLeft = chibiLeft + left;
      assert.ok(viewportLeft >= inset - 0.001, `bubble ${index} at ${vw}px starts at ${viewportLeft}`);
      assert.ok(
        viewportLeft + width <= vw - inset + 0.001,
        `bubble ${index} at ${vw}px ends at ${viewportLeft + width} of ${vw}`,
      );
      assert.ok(width > 0 && width <= BUBBLE_MAX_WIDTH);
      assert.ok(tailX >= 0 && tailX <= width, "the tail must stay on the bubble it hangs from");
    }
  }
});

test("the tail points at its own chibi, not at a fixed corner", () => {
  const vw = 1440;
  const inset = rowInset(vw);
  for (const index of [0, 5, rowCapacity(vw) - 1]) {
    const chibiLeft = inset + index * (CHIBI_SIZE + CHIBI_GAP);
    const { left, tailX } = bubblePlacement(index, vw);
    const tailInViewport = chibiLeft + left + tailX;
    const centre = chibiLeft + CHIBI_SIZE / 2;
    assert.ok(
      Math.abs(tailInViewport - centre) <= CHIBI_SIZE / 2,
      `tail for head ${index} landed ${tailInViewport} away from its centre ${centre}`,
    );
  }
});

test("an unmeasured viewport falls back to exactly the old bubble geometry", () => {
  assert.deepEqual(bubblePlacement(3, 0), { left: 0, width: BUBBLE_MAX_WIDTH, tailX: 24 });
});
