// Unit tests for lib/voice-playback.ts — run with: node --test lib/__tests__/voice-playback.test.mjs
// Plain .mjs so tsc ignores it; node's built-in type stripping loads the .ts module directly.
import assert from "node:assert/strict";
import { test } from "node:test";

import { PLAYBACK_LEAD_SECONDS, PlaybackScheduler } from "../voice-playback.ts";

const LEAD = PLAYBACK_LEAD_SECONDS;

test("frames before any audio_start are dropped", () => {
  const s = new PlaybackScheduler();
  assert.equal(s.accepting, false);
  assert.equal(s.schedule(0.5, 1.0), null);
});

test("arms on audio_start; chunks queue contiguously with the lead", () => {
  const s = new PlaybackScheduler();
  assert.equal(s.onAudioStart(1), "new-turn");
  const at1 = s.schedule(1.0, 0.5);
  assert.equal(at1, 0.5 + LEAD);
  const at2 = s.schedule(0.25, 0.9); // ctx behind the queue → sample-accurate continuation
  assert.equal(at2, at1 + 1.0);
  assert.equal(s.playheadTime, at2 + 0.25);
});

test("a chunk after a long mid-turn stall is scheduled in the FUTURE, never in the past", () => {
  const s = new PlaybackScheduler();
  s.onAudioStart(1);
  s.schedule(1.28, 0.5); // playhead now 1.84
  const at = s.schedule(0.5, 50.0); // 48 s of silence went by
  assert.ok(at !== null && at >= 50.0, `scheduled at ${at}, ctx.currentTime was 50.0`);
  assert.equal(at, 50.0 + LEAD);
});

test("playhead resets per turn; first chunk after a long idle gap lands at now + lead", () => {
  const s = new PlaybackScheduler();
  s.onAudioStart(1);
  s.schedule(2.0, 10.0);
  s.onAudioEnd(1);
  assert.equal(s.accepting, false);
  assert.equal(s.schedule(0.5, 11.0), null); // in-flight frames after audio_end are dropped
  assert.equal(s.onAudioStart(2), "new-turn"); // 60+ s later
  assert.equal(s.playheadTime, 0);
  const at = s.schedule(1.28, 77.63);
  assert.ok(at !== null && at >= 77.63);
  assert.equal(at, 77.63 + LEAD);
});

test("second segment of the same turn resumes without a reset", () => {
  const s = new PlaybackScheduler();
  s.onAudioStart(3);
  s.schedule(1.0, 5.0);
  s.onAudioEnd(3); // tool gap closes the TTS segment mid-turn
  assert.equal(s.onAudioStart(3), "resume");
  assert.ok(s.accepting);
});

test("audio_end of an OLDER turn cannot disarm the live one", () => {
  const s = new PlaybackScheduler();
  s.onAudioStart(1);
  s.onAudioEnd(1);
  s.onAudioStart(2);
  s.onAudioEnd(1); // late straggler
  assert.equal(s.accepting, true);
  assert.notEqual(s.schedule(0.5, 3.0), null);
  s.onAudioEnd(2);
  assert.equal(s.accepting, false);
});

test("audio_start of an older turn is stale and never re-arms", () => {
  const s = new PlaybackScheduler();
  s.onAudioStart(2);
  s.onAudioEnd(2);
  assert.equal(s.onAudioStart(1), "stale");
  assert.equal(s.accepting, false);
});

test("flush (interrupt) kills the current turn for good — its late segments stay dead", () => {
  const s = new PlaybackScheduler();
  s.onAudioStart(4);
  s.schedule(1.0, 2.0);
  s.flush(4);
  assert.equal(s.accepting, false);
  assert.equal(s.playheadTime, 0);
  assert.equal(s.onAudioStart(4), "stale"); // segment raced past the interrupt
  assert.equal(s.schedule(0.5, 3.0), null);
  assert.equal(s.onAudioStart(5), "new-turn"); // the next real turn arms normally
  assert.ok(s.accepting);
});

test("interrupted during synthesis (before its audio_start) also kills that turn", () => {
  const s = new PlaybackScheduler();
  s.onAudioStart(4);
  s.onAudioEnd(4);
  s.flush(5); // server killed turn 5 before any of its audio started
  assert.equal(s.onAudioStart(5), "stale");
  assert.equal(s.onAudioStart(6), "new-turn");
});

// ---- stream generation + s16 byte alignment ----------------------------------------------------

import { PcmChunkAligner } from "../voice-playback.ts";

test("streamGeneration advances on every accepted audio_start and never on a stale one", () => {
  const s = new PlaybackScheduler();
  const g0 = s.streamGeneration;
  s.onAudioStart(1); // new-turn
  const g1 = s.streamGeneration;
  assert.ok(g1 > g0);
  s.onAudioEnd(1);
  s.onAudioStart(1); // resume = a fresh TTS stream of the same turn
  const g2 = s.streamGeneration;
  assert.ok(g2 > g1);
  s.flush(1);
  s.onAudioStart(1); // stale
  assert.equal(s.streamGeneration, g2);
});

test("aligner carries a split byte within one stream and reassembles the sample", () => {
  const a = new PcmChunkAligner();
  const first = a.align(new Uint8Array([1, 2, 3]), 1);
  assert.deepEqual(Array.from(first), [1, 2]);
  const second = a.align(new Uint8Array([4, 5, 6]), 1);
  assert.deepEqual(Array.from(second), [3, 4, 5, 6]);
});

test("a stream truncated at an odd byte cannot byte-shift the stream that follows it", () => {
  const a = new PcmChunkAligner();
  const dead = a.align(new Uint8Array([1, 2, 3]), 7); // segment died mid-sample
  assert.deepEqual(Array.from(dead), [1, 2]);
  // The recovery segment (new audio_start → new generation): its first sample must be [9, 8],
  // not the stale [3, 9] the pre-aligner carry produced.
  const fresh = a.align(new Uint8Array([9, 8, 7, 6]), 8);
  assert.deepEqual(Array.from(fresh), [9, 8, 7, 6]);
});

test("reset drops the carry (playback teardown)", () => {
  const a = new PcmChunkAligner();
  a.align(new Uint8Array([1, 2, 3]), 1);
  a.reset();
  assert.deepEqual(Array.from(a.align(new Uint8Array([4, 5]), 1)), [4, 5]);
});

// ---- adversarial fuzz: a killed turn can never reach the speakers -------------------------------

test("fuzz: after any flush, nothing schedules until a start newer than every killed turn", () => {
  let seed = 20260819;
  const rand = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
  for (let round = 0; round < 3000; round++) {
    const s = new PlaybackScheduler();
    let ctx = 0;
    let maxKilled = 0; // highest turn any flush declared dead
    let liveTurn = 0; // turn of the last non-stale audio_start
    let armed = false; // a non-stale start happened after the last flush
    for (let step = 0; step < 50; step++) {
      const r = rand();
      const turn = 1 + Math.floor(rand() * 6);
      if (r < 0.35) {
        const action = s.onAudioStart(turn);
        if (action !== "stale") {
          assert.ok(turn > maxKilled, `armed turn ${turn} after killing through ${maxKilled}`);
          liveTurn = turn;
          armed = true;
        }
      } else if (r < 0.5) {
        s.onAudioEnd(turn);
      } else if (r < 0.78) {
        ctx += rand() * 3;
        const at = s.schedule(0.05 + rand(), ctx);
        if (at !== null) {
          assert.ok(armed, "scheduled audio with no live start since the last flush");
          assert.ok(liveTurn > maxKilled, `played turn ${liveTurn}, killed through ${maxKilled}`);
          assert.ok(at >= ctx, `scheduled at ${at}, before ctx time ${ctx}`);
        }
      } else if (r < 0.9) {
        maxKilled = Math.max(maxKilled, s.currentTurn); // client barge-in: flush()
        s.flush();
        armed = false;
      } else {
        maxKilled = Math.max(maxKilled, turn, s.currentTurn); // server `interrupted`, any turn no.
        s.flush(turn);
        armed = false;
      }
    }
  }
});
