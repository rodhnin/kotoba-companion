/**
 * The chibi art has geometry the layout depends on, and getting it wrong is invisible in code review
 * — it only shows on screen. Both invariants below were broken at some point and reported by eye.
 *
 * Margins are measured at alpha > 8, not with a plain bounding box: lossy WebP leaves a faint halo of
 * alpha 1..8 out to the canvas edge, so a bounding box says "fills the canvas" for art that plainly
 * does not.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import sharp from "sharp";

/** Where the drawing really starts and stops inside its own canvas. */
async function bounds(file) {
  const { data, info } = await sharp(file).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  const { width, height, channels } = info;
  let left = width, right = -1, top = height, bottom = -1;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (data[(y * width + x) * channels + 3] > 8) {
        if (x < left) left = x;
        if (x > right) right = x;
        if (y < top) top = y;
        if (y > bottom) bottom = y;
      }
    }
  }
  return { width, height, left, right, top, bottom,
           marginLeft: left, marginRight: width - 1 - right };
}

test("the loader chibi is centred in her own canvas", async () => {
  const file = "public/art/kotoba-chibi.webp";
  assert.ok(existsSync(file), `${file} is missing`);
  const b = await bounds(file);
  // CallLoader sizes this one by WIDTH and centres the canvas inside a 220px ring, so an uneven
  // margin is not cropped — it is a shove. At 160px on screen, 42px of canvas is 7.5px off-centre,
  // which is exactly how far she once sat to the right of the ring.
  const off = Math.abs(b.marginLeft - b.marginRight);
  assert.ok(off <= 8,
    `she sits off-centre: ${b.marginLeft}px of margin on the left, ${b.marginRight}px on the right`);
});

test("the two mouth frames are the same drawing, registered, differing only at the mouth", async () => {
  const open = "public/subagents/chibi-open.webp";
  const closed = "public/subagents/chibi-closed.webp";
  for (const f of [open, closed]) assert.ok(existsSync(f), `${f} is missing`);

  const [a, b] = await Promise.all([bounds(open), bounds(closed)]);
  assert.deepEqual(
    { w: a.width, h: a.height }, { w: b.width, h: b.height },
    "different canvases — swapping them resizes her mid-animation",
  );
  // The lip-sync swap alternates these two. Anything that moves between them is a jump the eye reads
  // as a glitch, so the silhouettes must land on each other.
  for (const edge of ["left", "right", "top", "bottom"]) {
    assert.ok(Math.abs(a[edge] - b[edge]) <= 2,
      `the ${edge} edge moves by ${Math.abs(a[edge] - b[edge])}px between the open and closed mouth`);
  }
});
