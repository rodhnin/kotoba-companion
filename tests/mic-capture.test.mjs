// Unit tests for public/worklets/mic-capture.js — run with: node --test tests/mic-capture.test.mjs
// The worklet is plain JS with no imports, so it loads under stubbed AudioWorklet globals and the
// resampler is measured sample-for-sample: what this posts is exactly what reaches ElevenLabs STT.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const SOURCE = readFileSync(new URL("../public/worklets/mic-capture.js", import.meta.url), "utf8");

function makeProcessor(contextRate, opts = { targetRate: 16000, frameSeconds: 0.25 }) {
  const posted = [];
  const g = globalThis;
  g.sampleRate = contextRate;
  g.AudioWorkletProcessor = class {
    constructor() {
      this.port = { postMessage: (buf) => posted.push(new Int16Array(buf.slice(0))) };
    }
  };
  let cls;
  g.registerProcessor = (_name, c) => {
    cls = c;
  };
  new Function(SOURCE)();
  return { p: new cls({ processorOptions: opts }), posted };
}

function feedRamp(p, totalSamples, quantum = 128) {
  let fed = 0;
  let maxPending = 0;
  while (fed < totalSamples) {
    const ch = new Float32Array(quantum);
    for (let i = 0; i < quantum; i++) ch[i] = (fed + i) / 1e6;
    p.process([[ch]]);
    fed += quantum;
    if (p.pending.length > maxPending) maxPending = p.pending.length;
  }
  return { fed, maxPending };
}

test("48kHz→16kHz conserves the sample count exactly (the clamped-cursor regression)", () => {
  // Pre-fix, the read cursor snapped backward one sample per 128-sample quantum: +0.78% output
  // rate and a phase glitch every 2.67ms. Ten seconds must come out as exactly ten seconds.
  const { p, posted } = makeProcessor(48000);
  const { fed, maxPending } = feedRamp(p, 48000 * 10);
  const out = posted.length * 4000 + p.framePos;
  assert.equal(out, fed / 3);
  assert.ok(maxPending <= 1, `pending grew to ${maxPending}`);
});

test("48kHz→16kHz output follows the input ramp with no seam glitches", () => {
  const { p, posted } = makeProcessor(48000);
  feedRamp(p, 48000 * 2);
  let worst = 0;
  for (let f = 0; f < posted.length; f++)
    for (let i = 0; i < 4000; i++) {
      const j = f * 4000 + i;
      const err = Math.abs(posted[f][i] - (3 * j / 1e6) * 32767);
      if (err > worst) worst = err;
    }
  assert.ok(worst <= 2, `worst sample error ${worst} int16 units — a seam is glitching`);
});

test("44.1kHz→16kHz (non-integer ratio) conserves the count within rounding", () => {
  const { p, posted } = makeProcessor(44100);
  const { fed } = feedRamp(p, 44100 * 10);
  const out = posted.length * 4000 + p.framePos;
  assert.ok(Math.abs(out - fed / 2.75625) <= 1, `out ${out} for ${fed} in`);
});

test("frames are fixed-size 250ms buffers and clipping saturates instead of wrapping", () => {
  const { p, posted } = makeProcessor(48000);
  const loud = new Float32Array(128).fill(4.0); // over-range input must clamp, not wrap negative
  while (posted.length === 0) p.process([[loud]]);
  assert.equal(posted[0].length, 4000);
  assert.ok(posted[0].every((s) => s === 0x7fff));
});
